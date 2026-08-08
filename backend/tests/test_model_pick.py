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
    },
    "mini": {
        "id": "mini",
        "label": "mini",
        "detail": "",
        "max_tokens": None,
        "context_tokens": None,
        "price": None,
        "params": {},
    },
}


@pytest.fixture()
def real_provider(monkeypatch):
    monkeypatch.setattr(config, "MODEL_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "")
    monkeypatch.setattr(config, "MODEL_ID", "env-default")
    monkeypatch.setattr(config, "MODEL_API_KEY", "sk-test")
    monkeypatch.setattr(config, "MODELS", dict(MENU))
    monkeypatch.setattr(config, "MODELS_ERROR", "")


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
    assert client.get("/health").json()["model"] == "opus"


def test_mock_reports_no_model_on_status(fresh_db, client):
    """The strip must never make mock mode look like a live model."""
    assert client.get("/api/agents/status").json()["model"] == ""


def test_the_get_serves_the_menu_without_entry_params(fresh_db, real_provider, client):
    """Entry params are operator-authored request bodies — a token parked
    there must not reach every signed-in browser."""
    got = client.get("/api/settings/model").json()
    assert {m["id"] for m in got["menu"]} == {"opus", "mini"}
    assert all("params" not in m for m in got["menu"])
    assert got["applies"] is True


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
