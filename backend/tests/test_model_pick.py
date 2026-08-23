"""The admin model pick: stored as (provider, id), honored only while the
provider and the menu still agree with it, reported when they do not, and
actually reaching the model the agent builds — a pick the build path ignores
is worse than no picker (the tuning.py rule)."""

import pytest

from app import config
from app.agents import team_agent
from app.services import settings

MENU = {
    "opus": {
        "id": "opus",
        "label": "Opus",
        "detail": "deep work",
        "max_tokens": 8192,
        "context_tokens": 200_000,
        "price": (15.0, 75.0),
        "params": {"temperature": 0.6},
        "attachments": None,
    },
    "mini": {
        "id": "mini",
        "label": "mini",
        "detail": "",
        "max_tokens": None,
        "context_tokens": None,
        "price": None,
        "params": {},
        "attachments": None,
    },
}


@pytest.fixture()
def real_provider(monkeypatch):
    monkeypatch.setattr(config, "MODEL_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "")
    monkeypatch.setattr(config, "MODEL_ID", "env-default")
    monkeypatch.setattr(config, "MODEL_ID_SOURCE", "env", raising=False)
    monkeypatch.setattr(config, "MODEL_PROVIDER_SOURCE", "env", raising=False)
    monkeypatch.setattr(config, "MAX_TOKENS_SOURCE", "env", raising=False)
    monkeypatch.setattr(config, "CONTEXT_STRATEGY_SOURCE", "default", raising=False)
    monkeypatch.setattr(config, "MODEL_API_KEY", "sk-test")
    monkeypatch.setattr(config, "MODEL_PARAMS", {})
    monkeypatch.setattr(config, "MODEL_PARAMS_SOURCE", "unset", raising=False)
    monkeypatch.setattr(config, "MODEL_PRICES", {})
    monkeypatch.setattr(config, "MODEL_PRICES_ERROR", "")
    monkeypatch.setattr(config, "MODEL_PRICE_TABLE_ERROR", "", raising=False)
    monkeypatch.setattr(config, "MODEL_PRICES_SOURCE", "unset", raising=False)
    monkeypatch.setattr(config, "MODELS", dict(MENU))
    monkeypatch.setattr(config, "MODELS_SOURCE", "file", raising=False)
    monkeypatch.setattr(config, "MODELS_ERROR", "")
    monkeypatch.setattr(config, "VISION_MODEL", "")


def test_the_menu_serves_merged_prices_and_zero_reads_as_unknown(
    fresh_db, real_provider, client, monkeypatch
):
    """G7: the picker compares what accounting will actually charge. A model
    priced only in SKEIN_MODEL_PRICES still shows its price, and a zero pair
    reads as unknown — an unfilled rate and a real $0 are indistinguishable."""
    monkeypatch.setattr(
        config,
        "MODELS",
        dict(MENU)
        | {
            "zeroed": {**MENU["mini"], "id": "zeroed", "price": (0.0, 0.0)},
        },
    )
    monkeypatch.setattr(
        config,
        "MODEL_PRICES",
        {"mini": (1.0, 2.0), "zeroed": (9.0, 10.0)},
    )
    menu = {m["id"]: m for m in client.get("/api/settings/model").json()["menu"]}
    assert menu["opus"]["price"] == [15.0, 75.0]  # registry entry wins
    assert menu["mini"]["price"] == [1.0, 2.0]  # the operator table fills in
    assert menu["zeroed"]["price"] is None  # zero is unknown, never free
    # the same rule holds in accounting: a zeroed model logs cost NULL and
    # counts as unpriced, never as a $0 total
    from app.services import usage

    assert usage.model_price("zeroed") == (None, "model_menu")
    assert usage.cost_for("zeroed", 1000, 1000) is None
    assert usage.cost_for("mini", 1_000_000, 0) == 1.0


def test_a_pick_becomes_the_effective_model(fresh_db, real_provider):
    settings.set_model_pick("opus", actor="admin")
    assert settings.picked_model() == "opus"
    state = settings.model_pick_state()
    assert state["model"] == "opus"
    assert state["override"]["set_by"] == "admin"
    assert state["ignored"] == ""


def test_clearing_returns_to_the_env_default_not_a_guess(fresh_db, real_provider):
    settings.set_model_pick("opus", actor="admin")
    settings.set_model_pick("", actor="admin")
    assert settings.picked_model() == ""
    state = settings.model_pick_state()
    assert state["model"] == "env-default"
    assert state["override"] is None


def test_an_id_outside_the_menu_is_refused_and_never_echoed(fresh_db, real_provider):
    """The refusal lists the menu instead of the submitted id — the id is
    caller-supplied, and a refusal must not echo the rejected value."""
    with pytest.raises(ValueError) as e:
        settings.set_model_pick("gpt-x-imaginary", actor="admin")
    assert "gpt-x-imaginary" not in str(e.value)
    assert "opus" in str(e.value)


def test_mock_refuses_the_pick(fresh_db, monkeypatch):
    """Hiding the picker on mock is UI. This refusal is the enforcement."""
    monkeypatch.setattr(config, "MODELS", dict(MENU))
    monkeypatch.setattr(config, "MODELS_ERROR", "")
    with pytest.raises(ValueError, match="mock"):
        settings.set_model_pick("opus", actor="admin")


def test_a_faulted_menu_refuses_the_pick(fresh_db, monkeypatch):
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "MODELS", {})
    monkeypatch.setattr(config, "MODELS_ERROR", "SKEIN_MODELS is unusable: x.")
    with pytest.raises(ValueError, match="registry"):
        settings.set_model_pick("opus", actor="admin")


def test_a_provider_switch_invalidates_the_pick_visibly(fresh_db, real_provider, monkeypatch):
    """The id means nothing on another endpoint, so the pick must fall back
    to the env default — reported, never hidden, and never guessed."""
    settings.set_model_pick("opus", actor="admin")
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "openai")
    assert settings.picked_model() == ""
    state = settings.model_pick_state()
    assert state["model"] == "env-default"
    assert state["ignored"] != ""
    assert state["override"] is not None  # still shown, so the admin sees why


def test_a_menu_shrink_invalidates_the_pick_visibly(fresh_db, real_provider, monkeypatch):
    """Provider-match alone would let a pick survive its model's removal from
    the menu — the registry-membership check is what this pins."""
    settings.set_model_pick("opus", actor="admin")
    monkeypatch.setattr(config, "MODELS", {"mini": MENU["mini"]})
    assert settings.picked_model() == ""
    assert settings.model_pick_state()["ignored"] != ""


def test_the_pick_reaches_the_built_model(fresh_db, real_provider):
    """The whole point of the surface: the model the agent runs is the model
    the picker claims, with the entry's tuning applied."""
    settings.set_model_pick("opus", actor="admin")
    cfg = team_agent._model().get_config()
    assert cfg["model_id"] == "opus"
    assert cfg["max_tokens"] == 8192
    assert cfg["context_window_limit"] == 200_000
    assert cfg["params"]["temperature"] == 0.6


def test_a_persona_override_wins_over_the_pick(fresh_db, real_provider):
    """persona > admin pick > env default — and registry tuning follows the
    model that WON, not the picked one."""
    settings.set_model_pick("opus", actor="admin")
    cfg = team_agent._model(model_id="mini").get_config()
    assert cfg["model_id"] == "mini"
    # mini has no entry tuning: the deployment cap applies, no context limit
    assert cfg["max_tokens"] == config.MAX_TOKENS
    assert cfg.get("context_window_limit") is None


def test_an_unlisted_model_gets_no_registry_tuning(fresh_db, real_provider):
    cfg = team_agent._model(model_id="somewhere-else").get_config()
    assert cfg["max_tokens"] == config.MAX_TOKENS
    assert cfg.get("context_window_limit") is None


def test_persona_temperature_wins_over_the_entry_params(fresh_db, real_provider):
    """SKEIN_MODEL_PARAMS < entry params < persona — per key."""
    settings.set_model_pick("opus", actor="admin")
    cfg = team_agent._model(temperature=0.1).get_config()
    assert cfg["params"]["temperature"] == 0.1


@pytest.mark.parametrize("provider", ["ollama", "bedrock"])
def test_the_entry_cap_beats_the_global_params_on_the_merge_branches(
    fresh_db, real_provider, monkeypatch, provider
):
    """On ollama and bedrock the registry entry's typed fields ride the same
    merge as SKEIN_MODEL_PARAMS. Layered wrong, a global max_tokens silently
    beats the per-model cap. test_model_providers.py pins the equivalent
    Anthropic request-body merge."""
    monkeypatch.setattr(config, "MODEL_PROVIDER", provider)
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", provider)
    monkeypatch.setattr(config, "MODEL_PARAMS", {"max_tokens": 512, "context_window_limit": 1234})
    monkeypatch.setattr(config, "MODEL_PARAMS_SOURCE", "inline")
    settings.set_model_pick("opus", actor="admin")
    cfg = team_agent._model().get_config()
    assert cfg["max_tokens"] == 8192
    assert cfg["context_window_limit"] == 200_000
    summary = settings.model_configuration_summary()
    assert _row(summary, "output_cap")["value"] == "8,192 tokens"
    assert _row(summary, "parameters") == {
        "id": "parameters",
        "label": "Parameters",
        "value": "1 parameter",
        "source": "selected model entry",
    }
    # and the global knobs still win for an entry that sets no cap — that is
    # the documented "params reach what we did not model" contract
    assert team_agent._model(model_id="mini").get_config()["max_tokens"] == 512
    settings.set_model_pick("mini", actor="admin")
    assert _row(settings.model_configuration_summary(), "output_cap") == {
        "id": "output_cap",
        "label": "Output cap",
        "value": "Set in parameters (value hidden)",
        "source": "SKEIN_MODEL_PARAMS",
    }


def test_the_env_default_outside_the_menu_warns_on_health(fresh_db, real_provider):
    """The same drift class as a persona model the menu does not list: an id
    in force that the menu does not govern. Warns, never faults — the menu
    constrains the admin pick, not the operator's env."""
    assert config.menu_warnings() == ["SKEIN_MODEL_ID is not in the SKEIN_MODELS menu."]


def test_the_env_default_inside_the_menu_is_quiet(fresh_db, real_provider, monkeypatch):
    monkeypatch.setattr(config, "MODEL_ID", "opus")
    assert config.menu_warnings() == []


def test_no_menu_means_no_default_warning(fresh_db, monkeypatch):
    """An absent menu constrains nothing — warning on it would nag every
    deployment that never configured SKEIN_MODELS."""
    monkeypatch.setattr(config, "MODELS", {})
    assert config.menu_warnings() == []


def test_a_settings_read_failure_never_stops_the_build(fresh_db, real_provider, monkeypatch):
    """The env default is a correct model, just not the picked one — a chat
    turn must not die because a settings lookup did."""
    from app.services import settings as settings_module

    def _boom():
        raise RuntimeError("db is on fire")

    monkeypatch.setattr(settings_module, "picked_model", _boom)
    assert team_agent._model().get_config()["model_id"] == "env-default"


def test_the_status_and_health_surfaces_report_the_effective_model(fresh_db, real_provider, client):
    settings.set_model_pick("opus", actor="admin")
    assert client.get("/api/agents/status").json()["model"] == "opus"
    assert client.get("/api/health").json()["model"] == "opus"


def test_health_reports_which_tier_the_model_came_from(fresh_db, real_provider, client):
    """An operator reading a model they did not put in the ConfigMap needs to
    know a pick is in force before they go looking for the typo in env."""
    assert client.get("/api/health").json()["model_origin"] == "env"
    settings.set_model_pick("opus", actor="admin")
    assert client.get("/api/health").json()["model_origin"] == "admin"


def test_an_ignored_pick_reports_the_env_origin(fresh_db, real_provider, client, monkeypatch):
    """The origin names where the REPORTED value came from. A pick the
    deployment stopped honoring leaves the env default in force, and calling
    that "admin" sends the operator to the wrong surface."""
    settings.set_model_pick("opus", actor="admin")
    monkeypatch.setattr(config, "MODELS", {"mini": config.MODELS["mini"]})
    got = client.get("/api/health").json()
    assert got["model_origin"] == "env"
    assert got["model"] == config.MODEL_ID


def test_mock_reports_no_model_on_status(fresh_db, client):
    """The strip must never make mock mode look like a live model."""
    assert client.get("/api/agents/status").json()["model"] == ""


def test_the_model_summary_requires_a_named_reader(fresh_db, real_provider, client):
    anonymous = client.get("/api/settings/model", headers={"X-User": ""})
    assert anonymous.status_code == 403
    assert anonymous.json() == {
        "detail": "A named identity is required. Select a name in Settings, then try again."
    }

    named = client.get("/api/settings/model", headers={"X-User": "reader"})
    assert named.status_code == 200
    assert named.json()["model"] == "env-default"


def test_the_get_serves_the_menu_without_entry_params(fresh_db, real_provider, client):
    """Entry params are operator-authored request bodies — a token parked
    there must not reach every signed-in browser."""
    got = client.get("/api/settings/model").json()
    assert {m["id"] for m in got["menu"]} == {"opus", "mini"}
    assert all("params" not in m for m in got["menu"])
    assert got["applies"] is True


def test_the_get_redacts_model_menu_fault_details(fresh_db, real_provider, client, monkeypatch):
    monkeypatch.setattr(
        config,
        "MODELS_ERROR",
        "SKEIN_MODELS params.extra_headers.secret at /tmp/models.yaml is invalid.",
    )
    got = client.get("/api/settings/model").json()
    assert got["menu_error"] == (
        "The model menu is not usable. Check /api/health for the configuration fault."
    )
    assert "extra_headers" not in got["menu_error"]
    assert "/tmp/" not in got["menu_error"]
    assert "secret" not in got["menu_error"]


def _row(summary: dict, row_id: str) -> dict:
    return next(row for row in summary["rows"] if row["id"] == row_id)


def test_the_summary_reports_the_team_default_from_one_state(fresh_db, real_provider):
    settings.set_model_pick("opus", actor="admin")
    summary = settings.model_configuration_summary()

    assert summary["scope"] == "team_default"
    assert summary["note"] == (
        "This is the team default. Persona overrides can use a different model or parameters."
    )
    assert [row["id"] for row in summary["rows"]] == [
        "provider",
        "model",
        "output_cap",
        "attachments",
        "vision_sidecar",
        "long_chat",
        "model_menu",
        "prices",
        "parameters",
    ]
    assert _row(summary, "provider") == {
        "id": "provider",
        "label": "Provider",
        "value": "anthropic",
        "source": "SKEIN_MODEL_PROVIDER",
    }
    assert _row(summary, "model")["value"] == "opus"
    assert _row(summary, "model")["source"] == "Settings → AI runtime → Model (team)"
    assert _row(summary, "output_cap")["value"] == "8,192 tokens"
    assert _row(summary, "output_cap")["source"] == "selected model entry"
    assert _row(summary, "attachments")["value"] == "Direct: image, document. Images: direct."
    assert _row(summary, "vision_sidecar")["value"] == "Not set"
    assert _row(summary, "long_chat")["value"] == "sliding"
    assert _row(summary, "model_menu")["value"] == "2 models"
    assert _row(summary, "model_menu")["source"] == "SKEIN_MODELS_FILE"
    assert _row(summary, "prices")["source"] == "selected model entry"
    assert _row(summary, "parameters")["value"] == "1 parameter"
    assert _row(summary, "parameters")["source"] == "selected model entry"


def test_the_summary_names_sidecar_and_direct_image_modes(fresh_db, real_provider, monkeypatch):
    monkeypatch.setattr(config, "MODEL_PROVIDER", "ollama")
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "MODEL_ID", "mini")
    monkeypatch.setattr(config, "VISION_MODEL", "qwen3.5:cloud")
    monkeypatch.setattr(config, "MODELS", {"mini": {**MENU["mini"], "attachments": ()}})

    summary = settings.model_configuration_summary()
    assert _row(summary, "attachments")["value"] == "Direct: none. Images: vision sidecar."
    assert _row(summary, "vision_sidecar")["value"] == "qwen3.5:cloud"
    assert _row(summary, "vision_sidecar")["source"] == "SKEIN_VISION_MODEL"

    monkeypatch.setattr(config, "MODELS", {"mini": {**MENU["mini"], "attachments": ("image",)}})
    summary = settings.model_configuration_summary()
    assert _row(summary, "attachments")["value"] == "Direct: image. Images: direct."
    assert _row(summary, "vision_sidecar")["value"] == ("qwen3.5:cloud (not used for team default)")


def test_the_summary_never_guesses_an_output_cap(fresh_db, real_provider, monkeypatch):
    monkeypatch.setattr(config, "MODEL_PROVIDER", "openai")
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "openai")
    got = _row(settings.model_configuration_summary(), "output_cap")
    assert got["value"] == "Managed through provider parameters"
    assert got["source"] == "provider capability"

    monkeypatch.setattr(config, "MODEL_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "MODEL_PARAMS", {"max_tokens": 512})
    monkeypatch.setattr(config, "MODEL_PARAMS_SOURCE", "inline")
    got = _row(settings.model_configuration_summary(), "output_cap")
    assert got["value"] == "Set in parameters (value hidden)"
    assert got["source"] == "SKEIN_MODEL_PARAMS"


def test_the_summary_counts_parameters_without_exposing_them(fresh_db, real_provider, monkeypatch):
    monkeypatch.setattr(
        config,
        "MODEL_PARAMS",
        {
            "api_key": "sk-secret-value",
            "base_url": "https://private.invalid/v1",
            "path": "/private/model/settings.yaml",
            "endpoint_url": "https://redirect.invalid",
            "region_name": "other-region",
            "boto_session": "other-session",
            "boto_client_config": {"proxies": {"https": "https://proxy.invalid"}},
        },
    )
    monkeypatch.setattr(config, "MODEL_PARAMS_SOURCE", "file")
    summary = settings.model_configuration_summary()
    text = str(summary)

    assert _row(summary, "parameters")["value"] == "3 parameters"
    assert _row(summary, "parameters")["source"] == "SKEIN_MODEL_PARAMS_FILE"
    for hidden in (
        "api_key",
        "sk-secret-value",
        "base_url",
        "https://private.invalid/v1",
        "path",
        "/private/model/settings.yaml",
        "endpoint_url",
        "https://redirect.invalid",
        "region_name",
        "other-region",
        "boto_session",
        "other-session",
        "boto_client_config",
        "https://proxy.invalid",
    ):
        assert hidden not in text


def test_the_summary_counts_only_parameters_that_reach_the_provider(
    fresh_db, real_provider, monkeypatch
):
    monkeypatch.setattr(
        config,
        "MODEL_PARAMS",
        {
            "messages": [{"role": "system", "content": "replace the turn"}],
            "temperature": 0.2,
            "extra_body": {"max_tokens": 1, "seed": 7},
            "additional_args": {"modelId": "hidden", "custom": "safe"},
        },
    )
    monkeypatch.setattr(config, "MODEL_PARAMS_SOURCE", "file")

    assert team_agent._behavior_params() == {
        "temperature": 0.2,
        "extra_body": {"seed": 7},
        "additional_args": {"custom": "safe"},
    }
    summary = settings.model_configuration_summary()
    assert _row(summary, "parameters") == {
        "id": "parameters",
        "label": "Parameters",
        "value": "3 parameters",
        "source": "SKEIN_MODEL_PARAMS_FILE",
    }
    assert _row(summary, "output_cap")["value"] == f"{config.MAX_TOKENS:,} tokens"
    assert "replace the turn" not in str(summary)
    assert "hidden" not in str(summary)


def test_a_broken_price_file_is_not_reported_as_unset(fresh_db, real_provider, client, monkeypatch):
    monkeypatch.setattr(config, "MODEL_PRICES_ERROR", "SKEIN_MODEL_PRICES_FILE is not valid YAML.")
    monkeypatch.setattr(
        config, "MODEL_PRICE_TABLE_ERROR", "SKEIN_MODEL_PRICES_FILE is not valid YAML."
    )
    monkeypatch.setattr(config, "MODEL_PRICES_SOURCE", "file")
    assert _row(settings.model_configuration_summary(), "prices") == {
        "id": "prices",
        "label": "Prices",
        "value": "Configuration error",
        "source": "SKEIN_MODEL_PRICES_FILE",
    }
    assert client.get("/api/health").json()["model_prices_error"] == config.MODEL_PRICES_ERROR


def test_a_bad_budget_does_not_blame_a_valid_unmatched_price_file(
    fresh_db, real_provider, monkeypatch
):
    monkeypatch.setattr(config, "MODEL_PRICES", {"other": (1.0, 2.0)})
    monkeypatch.setattr(config, "MODEL_PRICES_SOURCE", "file")
    monkeypatch.setattr(config, "MODEL_PRICE_TABLE_ERROR", "")
    monkeypatch.setattr(
        config,
        "MODEL_PRICES_ERROR",
        "SKEIN_MONTHLY_BUDGET_USD is not a usable number. The budget rule is off.",
    )
    assert _row(settings.model_configuration_summary(), "prices") == {
        "id": "prices",
        "label": "Prices",
        "value": "Not set",
        "source": "",
    }


def test_the_model_endpoint_carries_the_shared_summary(fresh_db, real_provider, client):
    got = client.get("/api/settings/model").json()
    assert got["summary"] == settings.model_configuration_summary()


def _key(name: str = "operator") -> dict:
    from app.services.api_keys import create_key

    return {"Authorization": f"Bearer {create_key(name, 'test')['key']}"}


def test_the_post_is_admin_gated_and_refuses_unknown_fields(fresh_db, real_provider, client):
    # no key = no admin: the picker write must not ride the name-picker header
    assert client.post("/api/settings/model", json={"model": "opus"}).status_code == 403
    ok = client.post("/api/settings/model", json={"model": "opus"}, headers=_key())
    assert ok.status_code == 200
    assert ok.json()["model"] == "opus"
    # a mistyped field must be a 422, not a silent clear
    assert (
        client.post("/api/settings/model", json={"modle": "opus"}, headers=_key()).status_code
        == 422
    )
    bad = client.post("/api/settings/model", json={"model": "gpt-x-imaginary"}, headers=_key())
    assert bad.status_code == 400
    assert "gpt-x-imaginary" not in bad.json()["detail"]


def test_the_pick_survives_in_activity(fresh_db, real_provider):
    settings.set_model_pick("opus", actor="admin")
    row = fresh_db.query_one(
        "SELECT * FROM activity WHERE action = 'set_model_pick' ORDER BY id DESC LIMIT 1"
    )
    assert row is not None
    assert row["actor"] == "admin"
