"""Provider dispatch: every provider in the registry builds the right model
class, misconfiguration degrades loudly rather than silently, and none of it
needs a key or a socket."""

import importlib

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
        monkeypatch, SKEIN_MODEL_PROVIDER="anthropic", SKEIN_MODEL_PARAMS="{not json"
    )
    assert "SKEIN_MODEL_PARAMS" in cfg.MODEL_PROVIDER_ERROR
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
        health = client.get("/health")
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


# ---- back-compat: this box runs ollama + glm-5.2:cloud ----


def test_ollama_config_on_this_box_is_unchanged(monkeypatch, restore_config):
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
