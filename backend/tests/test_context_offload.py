"""Context offload: oversized tool results leave the replayed session context.

The strands ContextOffloader swaps a fat tool result for a preview plus a
retrieval reference BEFORE persistence; DbOffloadStorage holds the bytes per
session; the persistence-time backstop in session_store catches every path
the plugin does not ride (plugin off, wake turns, persona allowlists)."""

import asyncio
import json

from strands.types.session import Session, SessionAgent, SessionMessage, SessionType

from app import config
from app.agents import session_store, team_agent


def _run(coro):
    return asyncio.run(coro)


def _session(repo, sid):
    repo.create_session(Session(session_id=sid, session_type=SessionType.AGENT))


def test_offload_storage_scopes_by_session(fresh_db):
    repo = session_store.DatabaseSessionRepository()
    _session(repo, "s-one")
    _session(repo, "s-two")
    a = session_store.DbOffloadStorage("s-one")
    b = session_store.DbOffloadStorage("s-two")
    _run(a.write("offloader/t1_0", b"alpha bytes"))
    assert _run(a.read("offloader/t1_0")) == b"alpha bytes"
    # the other session's storage cannot see it — this scoping IS the
    # authorization story: the blob is as private as the session row it left
    assert _run(b.read("offloader/t1_0")) is None
    assert _run(a.list("offloader/")) == ["offloader/t1_0"]
    assert _run(b.list("")) == []
    _run(a.write("offloader/t1_0", b"replaced"))
    assert _run(a.read("offloader/t1_0")) == b"replaced"
    _run(a.delete("offloader/t1_0"))
    assert _run(a.read("offloader/t1_0")) is None


def test_offload_rows_die_with_their_session(fresh_db):
    repo = session_store.DatabaseSessionRepository()
    _session(repo, "s-gone")
    _run(session_store.DbOffloadStorage("s-gone").write("offloader/t9_0", b"orphan?"))
    fresh_db.execute("DELETE FROM sessions WHERE session_id = 's-gone'")
    assert fresh_db.query("SELECT * FROM session_offload") == []


class _FakeModel:
    stateful = False

    def __init__(self):
        self.config = {"model_id": "fake"}

    def get_config(self):
        return self.config

    def update_config(self, **kwargs):
        self.config.update(kwargs)


def test_the_plugin_rides_only_the_plain_chat_agent(fresh_db, monkeypatch):
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "")
    monkeypatch.setattr(team_agent, "_model", lambda **_: _FakeModel())

    agent = team_agent.build_agent("t-offload", user="mira")
    assert "retrieve_offloaded_content" in agent.tool_names

    # the wake runner's narrow WAKE_TOOLS contract holds
    woken = team_agent.build_agent("t-wake", user="mira", allowed_tools={"my_agent_inbox"})
    assert "retrieve_offloaded_content" not in woken.tool_names

    monkeypatch.setattr(config, "OFFLOAD_RESULT_TOKENS", 0)
    off = team_agent.build_agent("t-off", user="mira")
    assert "retrieve_offloaded_content" not in off.tool_names


def test_a_persona_allowlist_stays_literally_exact(fresh_db, tmp_path, monkeypatch):
    from app.services import personas

    monkeypatch.setattr(personas, "PERSONAS_DIR", tmp_path)
    monkeypatch.setattr(personas, "PACK_FILE", tmp_path / "pack.json")
    (tmp_path / "lens.md").write_text(
        "---\nname: Lens\ndescription: probes the offload allowlist exclusion\n"
        "tools: save_note, ask_question\n---\nYou are a probe.",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "")
    monkeypatch.setattr(team_agent, "_model", lambda **_: _FakeModel())

    agent = team_agent.build_agent("t-lens", persona="lens")
    assert sorted(agent.tool_names) == ["ask_question", "save_note"]


def test_bulky_tool_results_shrink_at_persistence(fresh_db):
    repo = session_store.DatabaseSessionRepository()
    _session(repo, "s-bulk")
    repo.create_agent(
        "s-bulk", SessionAgent(agent_id="default", state={}, conversation_manager_state={})
    )
    big = "x" * (session_store._TOOL_RESULT_MAX_STORED_BYTES + 1)
    msg = {
        "role": "user",
        "content": [
            {"toolResult": {"toolUseId": "t-1", "status": "success", "content": [{"text": big}]}},
            {"text": "beside it"},
        ],
    }
    repo.create_message("s-bulk", "default", SessionMessage.from_message(msg, 0))
    stored = repo.read_message("s-bulk", "default", 0).message
    block = stored["content"][0]["toolResult"]
    # toolUseId and status survive: restore-time pairing and the trim-point
    # walk both need a structurally valid result per toolUse
    assert block["toolUseId"] == "t-1" and block["status"] == "success"
    assert "truncated at storage" in block["content"][0]["text"]
    assert big not in json.dumps(stored)
    assert stored["content"][1] == {"text": "beside it"}


def test_small_tool_results_store_byte_identical(fresh_db):
    repo = session_store.DatabaseSessionRepository()
    _session(repo, "s-small")
    repo.create_agent(
        "s-small", SessionAgent(agent_id="default", state={}, conversation_manager_state={})
    )
    msg = {
        "role": "user",
        "content": [
            {"toolResult": {"toolUseId": "t-2", "status": "success", "content": [{"text": "ok"}]}}
        ],
    }
    repo.create_message("s-small", "default", SessionMessage.from_message(msg, 0))
    assert repo.read_message("s-small", "default", 0).message == msg
