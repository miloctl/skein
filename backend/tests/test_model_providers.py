"""Provider dispatch: every provider in the registry builds the right model
class, misconfiguration degrades loudly rather than silently, and none of it
needs a key or a socket."""

import importlib
import json

import pytest

from app import config
from app.agents import team_agent

EXPECTED_CLASS = {
    "anthropic": "AnthropicModel",
    "openai": "OpenAIModel",
    "openai_compatible": "OpenAIModel",
    "ollama": "OllamaModel",
    "bedrock": "BedrockModel",
}


def _configure(monkeypatch, provider, **over):
    monkeypatch.setattr(config, "MODEL_PROVIDER", provider)
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", provider)
    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "")
    monkeypatch.setattr(config, "MODEL_ID", over.pop("model_id", "test-model"))
    monkeypatch.setattr(config, "MODEL_BASE_URL", over.pop("base_url", ""))
    monkeypatch.setattr(config, "MODEL_API_KEY", over.pop("api_key", ""))
    monkeypatch.setattr(config, "MODEL_PARAMS", over.pop("params", {}))
    monkeypatch.setattr(config, "MAX_TOKENS", over.pop("max_tokens", 4096))
    for k, v in over.items():
        monkeypatch.setattr(config, k.upper(), v)


@pytest.mark.parametrize("provider,cls", sorted(EXPECTED_CLASS.items()))
def test_every_provider_builds_its_class(monkeypatch, provider, cls):
    _configure(monkeypatch, provider, base_url="http://x/v1" if "compatible" in provider else "")
    assert type(team_agent._model()).__name__ == cls


def test_registry_and_dispatch_agree():
    """A provider added to the registry without a builder would raise at
    runtime; catch it here instead."""
    assert set(config.PROVIDERS) == set(EXPECTED_CLASS) | {"mock"}


# ---- every provider socket is bounded, because a deadline cannot reach it ----

# bedrock alone passes no timeout, and it is NOT an oversight: strands applies
# its own read_timeout (120s) while no boto_client_config is passed, and
# passing one replaces that default. Recorded at the branch in _model() too. A
# NEW provider joins this set only by someone editing this line and saying why.
TIMEOUT_EXEMPT = {"bedrock"}


def _socket_timeout(model):
    """Where each SDK parks the timeout _model() handed it. openai and ollama
    keep client_args verbatim; anthropic builds its client in __init__ and
    keeps only that."""
    if hasattr(model, "client_args"):
        return model.client_args.get("timeout")
    return model.client.timeout


@pytest.mark.parametrize("provider", sorted(set(EXPECTED_CLASS) - TIMEOUT_EXEMPT))
def test_every_provider_bounds_its_socket(monkeypatch, provider):
    """An unbounded socket outlives the deadline in routes/chat.py. plan_project
    is a sync @tool, so strands runs it via asyncio.to_thread, and cancelling
    that await orphans the THREAD — it keeps reading a stalled socket while
    holding a slot in the event loop's default executor (min(32, cpu+4), so
    eight on a 4-vCPU box). Ollama's own default is None, so for the keyless
    default provider this is the only bound its socket will ever have."""
    _configure(monkeypatch, provider, base_url="http://x/v1" if "compatible" in provider else "")
    timeout = _socket_timeout(team_agent._model())
    assert timeout is not None, f"{provider} builds an unbounded client"
    assert timeout.read == team_agent.READ_TIMEOUT_S
    assert timeout.connect == team_agent.CONNECT_TIMEOUT_S


def test_the_socket_outlives_the_turn_deadline():
    """Ordering invariant, and the whole reason READ_TIMEOUT_S is the larger
    number. MEMBER_TIMEOUT_S must be what fires on a live-but-slow provider. If
    the socket bound were smaller, a cold model load — which sends no bytes for
    as long as it takes to page the weights in — would die as a failed member
    on every first request after a restart."""
    from app.routes.chat import MEMBER_TIMEOUT_S

    assert team_agent.READ_TIMEOUT_S > MEMBER_TIMEOUT_S


# ---- openai-compatible: the actual point of the feature ----


def test_openai_compatible_sends_base_url_and_key(monkeypatch):
    _configure(
        monkeypatch, "openai_compatible", base_url="http://localhost:8001/v1", api_key="sk-local"
    )
    model = team_agent._model()
    assert model.client_args["base_url"] == "http://localhost:8001/v1"
    assert model.client_args["api_key"] == "sk-local"


def test_openai_compatible_supplies_placeholder_key(monkeypatch):
    """Local servers ignore the key but the openai client demands one."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _configure(monkeypatch, "openai_compatible", base_url="http://localhost:8001/v1")
    assert team_agent._model().client_args["api_key"] == "not-needed"


def test_plain_openai_sends_no_base_url(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _configure(monkeypatch, "openai")
    assert "base_url" not in team_agent._model().client_args


# ---- max_tokens reaches the providers that accept it, and skips those that don't ----


@pytest.mark.parametrize("provider", ["anthropic", "ollama", "bedrock"])
def test_max_tokens_delivered(monkeypatch, provider):
    _configure(monkeypatch, provider, max_tokens=1234)
    assert team_agent._model().config["max_tokens"] == 1234


def test_anthropic_entry_cap_beats_the_global_param_in_the_request(monkeypatch):
    _configure(monkeypatch, "anthropic", params={"max_tokens": 512})
    monkeypatch.setattr(
        config,
        "MODELS",
        {"test-model": {"max_tokens": 8192, "context_tokens": None, "params": {}}},
    )
    model = team_agent._model()
    request = model.format_request([{"role": "user", "content": [{"text": "hi"}]}])
    assert model.config["params"]["max_tokens"] == 8192
    assert request["max_tokens"] == 8192


def test_openai_omits_max_tokens_unless_asked(monkeypatch):
    """gpt-5 and other reasoning models reject max_tokens (they want
    max_completion_tokens), so injecting it would 400 a working provider."""
    _configure(monkeypatch, "openai", max_tokens=1234)
    cfg = team_agent._model().config
    assert "max_tokens" not in cfg
    assert "max_tokens" not in cfg.get("params", {})


def test_model_params_passthrough(monkeypatch):
    _configure(monkeypatch, "openai", params={"max_completion_tokens": 999, "temperature": 0.2})
    params = team_agent._model().config["params"]
    assert params["max_completion_tokens"] == 999
    assert params["temperature"] == 0.2


def test_openai_compatible_forwards_custom_headers(monkeypatch):
    headers = {"X-Tenant": "acme", "X-API-Key": "test-key"}
    _configure(
        monkeypatch,
        "openai_compatible",
        base_url="http://localhost:8001/v1",
        params={"extra_headers": headers},
    )
    model = team_agent._model()
    request = model.format_request([{"role": "user", "content": [{"text": "hi"}]}])
    assert request["extra_headers"] == headers


# ---- misconfiguration: loud at the agent, harmless everywhere else ----


def _reload_config(monkeypatch, **env):
    """Reload config against ONLY the env this test sets.

    config calls load_dotenv() at import, so a plain reload would pull in the
    developer's backend/.env — these tests would then pass or fail depending
    on whose machine they run on. Neutralise dotenv and clear every model var
    first, so the reload sees exactly what is passed in.
    """
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    for var in (
        "SKEIN_MODEL_PROVIDER",
        "SKEIN_MODEL_ID",
        "SKEIN_MODEL_BASE_URL",
        "SKEIN_MODEL_API_KEY",
        "SKEIN_MODEL_PARAMS",
        "SKEIN_MAX_TOKENS",
        "SKEIN_OLLAMA_HOST",
    ):
        monkeypatch.delenv(var, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return importlib.reload(config)


@pytest.fixture
def restore_config(monkeypatch):
    """Undo the env BEFORE reloading. Fixture teardown runs ahead of
    monkeypatch's, so reloading first would re-import config against this
    test's broken env and leave it broken for every test after it."""
    yield
    monkeypatch.undo()
    importlib.reload(config)


def test_unknown_provider_does_not_break_import(monkeypatch, restore_config):
    cfg = _reload_config(monkeypatch, SKEIN_MODEL_PROVIDER="olama")
    assert "olama" in cfg.MODEL_PROVIDER_ERROR
    assert cfg.EFFECTIVE_PROVIDER == "mock"
    # the old bug: a typo silently became Anthropic with model_id "mock"
    assert cfg.MODEL_ID == "mock"


def test_unknown_provider_raises_at_agent_build(monkeypatch, restore_config):
    _reload_config(monkeypatch, SKEIN_MODEL_PROVIDER="olama")
    with pytest.raises(ValueError, match="olama"):
        team_agent._model()
    # and must not quietly hand back the mock agent instead
    with pytest.raises(ValueError, match="olama"):
        team_agent.build_agent("t", "someone")


def test_openai_compatible_without_base_url_is_rejected(monkeypatch, restore_config):
    cfg = _reload_config(monkeypatch, SKEIN_MODEL_PROVIDER="openai_compatible")
    assert "SKEIN_MODEL_BASE_URL" in cfg.MODEL_PROVIDER_ERROR
    assert cfg.EFFECTIVE_PROVIDER == "mock"


def test_malformed_model_params_does_not_break_boot(monkeypatch, restore_config):
    cfg = _reload_config(
        monkeypatch,
        SKEIN_MODEL_PROVIDER="anthropic",
        SKEIN_MODEL_API_KEY="sk-test",  # isolate the params fault from the key check
        SKEIN_MODEL_PARAMS="{not json",
    )
    assert "SKEIN_MODEL_PARAMS" in cfg.MODEL_PROVIDER_ERROR
    assert cfg.MODEL_PARAMS == {}
    assert cfg.EFFECTIVE_PROVIDER == "mock"


@pytest.mark.parametrize(
    "field",
    [
        "model",
        "model_id",
        "endpoint_url",
        "region_name",
        "boto_session",
        "boto_client_config",
        "messages",
        "tools",
        "system",
        "tool_choice",
        "stream",
        "stream_options",
        "timeout",
        "host",
        "ollama_client_args",
    ],
)
def test_model_params_cannot_set_routing_or_client_controls(monkeypatch, restore_config, field):
    cfg = _reload_config(
        monkeypatch,
        SKEIN_MODEL_PROVIDER="ollama",
        SKEIN_MODEL_PARAMS=f'{{"{field}": "hidden-model"}}',
    )
    assert field in cfg.MODEL_PROVIDER_ERROR
    assert "hidden-model" not in cfg.MODEL_PROVIDER_ERROR
    assert cfg.MODEL_PARAMS == {}
    assert cfg.EFFECTIVE_PROVIDER == "mock"


@pytest.mark.parametrize(
    ("params", "path"),
    [
        ({"extra_body": {"model": "hidden-model"}}, "extra_body.model"),
        (
            {"extra_body": {"metadata": {"model": "hidden-model"}}},
            "extra_body.metadata.model",
        ),
        ({"extra_body": {"messages": []}}, "extra_body.messages"),
        ({"extra_body": {"max_completion_tokens": 1}}, "extra_body.max_completion_tokens"),
        ({"extra_body": {"provider": {"order": ["other"]}}}, "extra_body.provider"),
        ({"extra_query": {"model": "hidden-model"}}, "extra_query.model"),
        ({"extra_query": {"provider": "other"}}, "extra_query.provider"),
        ({"additional_args": {"model": "hidden-model"}}, "additional_args.model"),
        ({"additional_args": {"modelId": "hidden-model"}}, "additional_args.modelId"),
        ({"additional_args": {"options": {"num_predict": 1}}}, "additional_args.options"),
        (
            {"additional_args": {"additionalModelRequestFields": {"system": "hidden"}}},
            "additional_args.additionalModelRequestFields",
        ),
        (
            {"additional_args": {"inferenceConfig": {"maxTokens": 1}}},
            "additional_args.inferenceConfig",
        ),
    ],
)
def test_model_params_cannot_replace_nested_routing_or_request_fields(
    monkeypatch, restore_config, params, path
):
    cfg = _reload_config(
        monkeypatch,
        SKEIN_MODEL_PROVIDER="ollama",
        SKEIN_MODEL_PARAMS=json.dumps(params),
    )
    assert path in cfg.MODEL_PROVIDER_ERROR
    assert "hidden-model" not in cfg.MODEL_PROVIDER_ERROR
    assert cfg.MODEL_PARAMS == {}
    assert cfg.EFFECTIVE_PROVIDER == "mock"


def test_provider_without_default_model_demands_one(monkeypatch, restore_config):
    cfg = _reload_config(
        monkeypatch,
        SKEIN_MODEL_PROVIDER="openai_compatible",
        SKEIN_MODEL_BASE_URL="http://localhost:8001/v1",
    )
    assert "SKEIN_MODEL_ID" in cfg.MODEL_PROVIDER_ERROR
    assert cfg.EFFECTIVE_PROVIDER == "mock"


# ---- keyless-first: a bad model setting must never take the product down ----


def test_rest_api_survives_a_broken_provider(monkeypatch, restore_config):
    """The whole deterministic core has to keep serving. This is the
    constraint that decides where validation is allowed to raise."""
    _reload_config(monkeypatch, SKEIN_MODEL_PROVIDER="nonsense")
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["ok"] is True
        assert "nonsense" in health.json()["provider_error"]
        # and it must not claim a live model
        assert health.json()["model"] == ""
        assert client.get("/api/tasks", headers={"X-User": "t"}).status_code == 200


def test_agents_status_reports_the_fault(monkeypatch, restore_config):
    _reload_config(monkeypatch, SKEIN_MODEL_PROVIDER="nonsense")
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        body = client.get("/api/agents/status", headers={"X-User": "t"}).json()
    assert body["model"] == ""
    assert "nonsense" in body["provider_error"]


# ---- free-form model ids: cloud-suffixed ollama ids pass through untouched ----


def test_ollama_cloud_model_id_passes_untouched(monkeypatch, restore_config):
    cfg = _reload_config(
        monkeypatch,
        SKEIN_MODEL_PROVIDER="ollama",
        SKEIN_MODEL_ID="glm-5.2:cloud",
        SKEIN_OLLAMA_HOST="http://localhost:11434",
    )
    assert cfg.MODEL_PROVIDER_ERROR == ""
    assert cfg.EFFECTIVE_PROVIDER == "ollama"
    assert cfg.MODEL_ID == "glm-5.2:cloud"  # free-form, never allowlisted
    assert cfg.OLLAMA_HOST == "http://localhost:11434"


# ---- key hygiene and operator-input traps: leaks, leftovers, collisions ----


def test_openai_key_never_leaks_to_a_third_party_endpoint(monkeypatch, restore_config):
    """The whole reason openai_compatible is a separate provider. A paid
    OPENAI_API_KEY must not be posted to whatever host the operator named —
    and it IS set on any box using semantic search."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-REAL-PAID-KEY")
    _reload_config(
        monkeypatch,
        SKEIN_MODEL_PROVIDER="openai_compatible",
        SKEIN_MODEL_BASE_URL="https://someone-elses-host.example/v1",
        SKEIN_MODEL_ID="whatever",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-REAL-PAID-KEY")
    args = team_agent._model().client_args
    assert args["base_url"] == "https://someone-elses-host.example/v1"
    assert args["api_key"] == "not-needed"
    assert "sk-REAL-PAID-KEY" not in str(args)


def test_base_url_is_refused_where_it_does_not_belong(monkeypatch, restore_config):
    """A leftover SKEIN_MODEL_BASE_URL must not silently redirect a paid
    provider on the next switch back to openai."""
    cfg = _reload_config(
        monkeypatch,
        SKEIN_MODEL_PROVIDER="openai",
        SKEIN_MODEL_BASE_URL="https://leftover-shim.example/v1",
    )
    assert "does not accept SKEIN_MODEL_BASE_URL" in cfg.MODEL_PROVIDER_ERROR
    assert cfg.EFFECTIVE_PROVIDER == "mock"


@pytest.mark.parametrize(
    "provider,key_env", [("anthropic", "ANTHROPIC_API_KEY"), ("openai", "OPENAI_API_KEY")]
)
def test_a_provider_that_cannot_answer_without_a_key_degrades_to_mock(
    monkeypatch, restore_config, provider, key_env
):
    """Unchecked, the fault surfaces once per chat as raw SDK internals — a 401
    body with the provider's request id — while /health reports no error at
    all. Degrading at boot is what makes MODEL_PROVIDER_ERROR the one place to
    look."""
    monkeypatch.delenv(key_env, raising=False)
    cfg = _reload_config(monkeypatch, SKEIN_MODEL_PROVIDER=provider)
    assert cfg.EFFECTIVE_PROVIDER == "mock"
    assert key_env in cfg.MODEL_PROVIDER_ERROR
    # the provider-native env var satisfies it, not only SKEIN_MODEL_API_KEY
    monkeypatch.setenv(key_env, "sk-test")
    keyed = _reload_config(monkeypatch, SKEIN_MODEL_PROVIDER=provider)
    assert provider == keyed.EFFECTIVE_PROVIDER


@pytest.mark.parametrize("provider", ["ollama", "openai_compatible", "bedrock"])
def test_keyless_providers_are_not_degraded_by_the_key_check(monkeypatch, restore_config, provider):
    """Keyless-first: a local ollama and a local openai_compatible endpoint take
    no credential, and bedrock resolves the ambient AWS chain. Marking any of
    them key_required would degrade a working keyless box to mock at boot."""
    for var in ("OLLAMA_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    extra = {"SKEIN_MODEL_BASE_URL": "http://localhost:1234/v1"} if "compat" in provider else {}
    cfg = _reload_config(
        monkeypatch, SKEIN_MODEL_PROVIDER=provider, SKEIN_MODEL_ID="some-model", **extra
    )
    assert provider == cfg.EFFECTIVE_PROVIDER
    assert cfg.MODEL_PROVIDER_ERROR == ""


@pytest.mark.parametrize("bad", ["4k", "", "  ", "4096.5"])
def test_bad_max_tokens_does_not_break_import(monkeypatch, restore_config, bad):
    """int() on operator input is the same import-time-raise trap the provider
    validation exists to avoid. .env.example ships this key uncommented."""
    cfg = _reload_config(monkeypatch, SKEIN_MODEL_PROVIDER="anthropic", SKEIN_MAX_TOKENS=bad)
    assert cfg.MAX_TOKENS == 4096
    if bad.strip():
        assert "SKEIN_MAX_TOKENS" in cfg.MODEL_PROVIDER_ERROR


@pytest.mark.parametrize("provider", ["ollama", "bedrock"])
def test_model_params_max_tokens_does_not_crash(monkeypatch, provider):
    """`f(max_tokens=x, **{"max_tokens": y})` is a TypeError, and max_tokens is
    the most obvious thing to put in SKEIN_MODEL_PARAMS."""
    _configure(monkeypatch, provider, max_tokens=4096, params={"max_tokens": 2048})
    assert team_agent._model().config["max_tokens"] == 2048  # operator wins


@pytest.mark.parametrize(
    "provider,field",
    [("ollama", "model_id"), ("bedrock", "model_id"), ("anthropic", "model"), ("openai", "model")],
)
def test_model_params_defensively_cannot_change_the_selected_model(monkeypatch, provider, field):
    _configure(monkeypatch, provider, params={field: "override"})
    model = team_agent._model()
    assert model.config["model_id"] == "test-model"
    assert field not in model.config.get("params", {})


def test_behavior_params_strip_app_controls_but_keep_safe_siblings(monkeypatch):
    headers = {"X-Tenant": "acme"}
    monkeypatch.setattr(
        config,
        "MODEL_PARAMS",
        {
            "endpoint_url": "https://redirect.invalid",
            "messages": [{"role": "system", "content": "replace the turn"}],
            "extra_headers": headers,
            "extra_body": {"model": "hidden", "provider": {"order": ["other"]}, "seed": 7},
            "extra_query": {"model_id": "hidden", "provider": "other", "tenant": "safe"},
            "additional_args": {"modelId": "hidden", "custom": "safe"},
        },
    )
    assert team_agent._behavior_params(
        {
            "boto_session": "other-session",
            "timeout": None,
            "temperature": 0.2,
        }
    ) == {
        "extra_headers": headers,
        "extra_body": {"seed": 7},
        "extra_query": {"tenant": "safe"},
        "additional_args": {"custom": "safe"},
        "temperature": 0.2,
    }


def test_ollama_honours_the_generic_api_key(monkeypatch):
    """.env.example advertises SKEIN_MODEL_API_KEY as the override for the
    provider-native key; it must not be silently ignored here."""
    _configure(monkeypatch, "ollama", api_key="sk-generic")
    model = team_agent._model()
    assert model.client_args["headers"]["Authorization"] == "Bearer sk-generic"


def test_providers_without_a_key_env_read_no_ambient_key(monkeypatch, restore_config):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-be-used")
    _reload_config(monkeypatch, SKEIN_MODEL_PROVIDER="bedrock", SKEIN_MODEL_ID="some.model")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-be-used")
    assert config.provider_key() == ""


def test_bedrock_demands_an_explicit_model_id(monkeypatch, restore_config):
    """Claude ids on Bedrock need a region-dependent inference-profile prefix,
    so any hardcoded default would 400 for someone."""
    cfg = _reload_config(monkeypatch, SKEIN_MODEL_PROVIDER="bedrock")
    assert "SKEIN_MODEL_ID" in cfg.MODEL_PROVIDER_ERROR
    assert cfg.PROVIDERS["bedrock"]["default_model"] is None
