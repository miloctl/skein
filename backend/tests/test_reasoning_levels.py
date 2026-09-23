"""Reasoning levels: the team level an administrator sets, the level a person
sets for one chat with /reasoning, and the rule that only the main chat turn
carries one (agents/team_agent.py::build_agent)."""

import pytest

from app import config, db
from app.services import chat_threads, settings

ENTRY = {
    "label": "",
    "detail": "",
    "max_tokens": None,
    "context_tokens": None,
    "price": None,
    "params": {},
    "attachments": None,
}


def _key(name: str = "operator") -> dict:
    from app.services.api_keys import create_key

    return {"Authorization": f"Bearer {create_key(name, 'test')['key']}"}


def _read_chat(client, message, thread_id="t"):
    with client.stream(
        "POST", "/api/chat", json={"thread_id": thread_id, "message": message}
    ) as resp:
        assert resp.status_code == 200
        return resp.read().decode()


@pytest.fixture()
def menu(monkeypatch):
    monkeypatch.setattr(config, "MODEL_PROVIDER", "openai_compatible")
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "openai_compatible")
    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "")
    monkeypatch.setattr(config, "MODEL_ID", "thinker")
    monkeypatch.setattr(config, "MODELS_ERROR", "")
    monkeypatch.setattr(
        config,
        "MODELS",
        {
            "thinker": {
                **ENTRY,
                "id": "thinker",
                "reasoning": {
                    "low": {"reasoning_effort": "low"},
                    "high": {"reasoning_effort": "high"},
                },
            },
            "plain": {**ENTRY, "id": "plain", "reasoning": {}},
        },
    )


@pytest.fixture()
def builds(monkeypatch):
    """What the chat route asks build_agent for, without a real provider."""
    from app.agents import team_agent

    seen: list[dict] = []

    class Quiet:
        async def stream_async(self, message):
            yield {"data": "ok"}

    def build(*_args, **kwargs):
        seen.append(kwargs)
        return Quiet()

    monkeypatch.setattr(team_agent, "build_agent", build)
    monkeypatch.setattr("app.routes.chat.build_agent", build)
    monkeypatch.setattr("app.routes.chat._summarize_title", lambda *_a: _none())
    return seen


async def _none():
    return None


# ---- the team level ----


def test_the_team_level_offers_what_the_team_model_declares(client, fresh_db, menu):
    got = client.get("/api/settings/reasoning").json()
    assert got == {
        "level": "",
        "override": "",
        "levels": ["low", "high"],
        "ignored": "",
        "applies": True,
    }
    r = client.post("/api/settings/reasoning", json={"level": "high"}, headers=_key())
    assert r.status_code == 200
    assert r.json()["level"] == "high"
    row = db.query_one("SELECT actor, detail FROM activity WHERE action = 'set_reasoning_level'")
    assert row["actor"] == "operator" and "high" in row["detail"]
    client.post("/api/settings/reasoning", json={"level": ""}, headers=_key())
    assert client.get("/api/settings/reasoning").json()["level"] == ""


def test_a_level_the_team_model_does_not_declare_is_refused_without_echo(client, fresh_db, menu):
    r = client.post("/api/settings/reasoning", json={"level": "xhigh"}, headers=_key())
    assert r.status_code == 400
    assert "xhigh" not in r.json()["detail"]
    assert "low, high" in r.json()["detail"]


def test_a_team_model_without_levels_says_where_levels_come_from(client, fresh_db, menu):
    settings.set_model_pick("plain", actor="operator")
    r = client.post("/api/settings/reasoning", json={"level": "high"}, headers=_key())
    assert r.status_code == 400
    assert "SKEIN_MODELS" in r.json()["detail"]


def test_the_mock_provider_refuses_a_team_level(client, fresh_db):
    r = client.post("/api/settings/reasoning", json={"level": "high"}, headers=_key())
    assert r.status_code == 400
    assert "mock provider" in r.json()["detail"]


def test_setting_the_team_level_needs_an_administrator(client, fresh_db, menu):
    assert client.post("/api/settings/reasoning", json={"level": "high"}).status_code == 403


def test_a_mistyped_field_is_refused_rather_than_read_as_a_clear(client, fresh_db, menu):
    settings.set_reasoning_level("high", actor="operator")
    r = client.post("/api/settings/reasoning", json={"levle": "low"}, headers=_key())
    assert r.status_code == 422
    assert settings.reasoning_level_state()["level"] == "high"


def test_a_menu_change_reports_the_stored_level_as_ignored(fresh_db, menu, monkeypatch):
    settings.set_reasoning_level("high", actor="operator")
    thinker = {**config.MODELS["thinker"], "reasoning": {"low": {"reasoning_effort": "low"}}}
    monkeypatch.setattr(config, "MODELS", {**config.MODELS, "thinker": thinker})
    got = settings.reasoning_level_state()
    assert (got["level"], got["override"], got["levels"]) == ("", "high", ["low"])
    assert got["ignored"] == "The team model does not offer this level."


def test_the_model_menu_serves_level_names_and_never_level_params(client, fresh_db, menu):
    got = client.get("/api/settings/model").json()
    rows = {m["id"]: m for m in got["menu"]}
    assert rows["thinker"]["reasoning"] == ["low", "high"]
    assert rows["plain"]["reasoning"] == []
    assert "reasoning_effort" not in str(got)


def test_the_model_summary_names_the_team_level_and_its_source(fresh_db, menu):
    def row():
        rows = settings.model_configuration_summary()["rows"]
        return next(r for r in rows if r["id"] == "reasoning")

    assert (row()["value"], row()["source"]) == ("Model default", "")
    settings.set_reasoning_level("high", actor="operator")
    assert row() == {
        "id": "reasoning",
        "label": "Reasoning",
        "value": "high",
        "source": "Settings → AI runtime → Reasoning (team)",
    }


# ---- the level for one chat ----


def test_reasoning_command_lists_picks_and_clears_per_chat(client, fresh_db, menu):
    settings.set_reasoning_level("low", actor="operator")
    out = _read_chat(client, "/reasoning")
    assert "team level, **low**" in out
    assert "- **low**" in out and "- high" in out
    assert "now uses the **high** reasoning level" in _read_chat(client, "/reasoning high")
    assert chat_threads.thread_reasoning("t") == "high"
    assert chat_threads.thread_reasoning("other") == ""
    assert "**high** level (picked here)" in _read_chat(client, "/reasoning")
    assert "team reasoning level" in _read_chat(client, "/reasoning default")
    assert chat_threads.thread_reasoning("t") == ""
    row = db.query_one("SELECT detail FROM activity WHERE action = 'set_chat_reasoning'")
    assert row["detail"].startswith("t:")


def test_reasoning_command_refuses_an_undeclared_level_without_echoing_it(client, fresh_db, menu):
    out = _read_chat(client, "/reasoning xhigh")
    assert "not changed" in out and "low, high" in out
    assert "xhigh" not in out
    assert chat_threads.thread_reasoning("t") == ""


def test_reasoning_command_checks_the_model_of_this_chat(client, fresh_db, menu):
    """The chat's own /model pick decides which levels exist, not the team model."""
    _read_chat(client, "/model plain")
    assert "not changed" in _read_chat(client, "/reasoning high")
    assert "has no reasoning levels" in _read_chat(client, "/reasoning")


def test_reasoning_command_names_the_mock_provider(client, fresh_db):
    assert "mock provider" in _read_chat(client, "/reasoning")


def test_reasoning_command_needs_a_chat(fresh_db):
    import asyncio

    from app.agents import commands

    async def first():
        stream = commands.dispatch("/reasoning", "tester")
        assert stream is not None
        async for event in stream:
            if "data" in event:
                return event["data"]
        return ""

    assert "inside a chat" in asyncio.run(first())


# ---- which level a turn runs with ----


def test_the_chat_level_outranks_the_team_level_on_the_turn(client, fresh_db, menu, builds):
    settings.set_reasoning_level("low", actor="operator")
    _read_chat(client, "hello")
    assert builds[-1]["reasoning"] == "low"
    chat_threads.set_thread_reasoning("t", "tester", "high")
    _read_chat(client, "hello again")
    assert builds[-1]["reasoning"] == "high"


def test_a_level_the_turn_model_does_not_declare_defers_to_the_next_layer(
    client, fresh_db, menu, builds, monkeypatch
):
    """A chat level picked on one model must not vanish silently when the
    model changes: it defers to the team level, then to the model default."""
    settings.set_reasoning_level("low", actor="operator")
    chat_threads.claim_thread("t", "tester")
    chat_threads.set_thread_reasoning("t", "tester", "high")
    thinker = {**config.MODELS["thinker"], "reasoning": {"low": {"reasoning_effort": "low"}}}
    monkeypatch.setattr(config, "MODELS", {**config.MODELS, "thinker": thinker})
    _read_chat(client, "hello")
    assert builds[-1]["reasoning"] == "low"
    chat_threads.set_thread_model("t", "tester", "plain")
    _read_chat(client, "hello again")
    assert builds[-1]["reasoning"] == ""


def test_an_anonymous_caller_cannot_read_the_team_level(client, fresh_db, menu):
    """The same rule as GET /api/settings/model: a named identity reads it."""
    r = client.get("/api/settings/reasoning", headers={"X-User": ""})
    assert r.status_code == 403


def test_the_chat_level_of_another_person_is_not_found(client, fresh_db, menu):
    """Ownership comes first: a refusal that names levels describes the
    model of a chat the caller does not own."""
    chat_threads.claim_thread("t", "tester")
    with pytest.raises(db.NotFound):
        chat_threads.set_thread_reasoning("t", "mallory", "xhigh")
    assert chat_threads.thread_reasoning("t") == ""


def test_the_summary_reports_a_team_level_that_sets_the_output_cap(fresh_db, menu, monkeypatch):
    """A level that sets max_tokens changes the cap every turn sends. The
    summary must not keep naming the entry cap as the one in force."""
    thinker = {
        **config.MODELS["thinker"],
        "max_tokens": 8192,
        "reasoning": {"high": {"max_tokens": 32000, "reasoning_effort": "high"}},
    }
    monkeypatch.setattr(config, "MODELS", {**config.MODELS, "thinker": thinker})
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "anthropic")
    settings.set_reasoning_level("high", actor="operator")
    rows = {r["id"]: r for r in settings.model_configuration_summary()["rows"]}
    assert rows["output_cap"]["value"] == "Set in parameters (value hidden)"
    assert "Reasoning (team)" in rows["output_cap"]["source"]
    assert rows["parameters"]["value"] == "2 parameters"
    assert "32000" not in str(rows)


def test_a_persona_turn_resolves_the_level_against_the_persona_model(
    client, fresh_db, menu, builds, monkeypatch
):
    from app.services import personas

    settings.set_reasoning_level("low", actor="operator")
    real = personas.behavior

    def with_model(slug):
        return {**real(slug), "model": "plain"}

    monkeypatch.setattr("app.routes.chat.personas.behavior", with_model)
    _read_chat(client, "/as code-reviewer hello")
    assert builds[-1]["resolved_model"] == "plain"
    assert builds[-1]["reasoning"] == ""
