"""Session bridge: slash-command exchanges land in the thread's model session."""

import json

import pytest

from app import config
from app.agents import session_log


@pytest.fixture(autouse=True)
def _own_db(fresh_db):
    """The strategy work added a DB read (effective_context_strategy) into the
    bridge; without a fresh DB these tests only passed when an earlier test
    had initialized the shared one — order-dependence, not verification."""
    return fresh_db


def _messages_dir(tmp, thread):
    return tmp / "sessions" / f"session_{thread}" / "agents" / "agent_default" / "messages"


def _live(monkeypatch, tmp_path):
    # a SUBDIR of tmp_path: fresh_db parks test.db in tmp_path itself, and
    # the emptiness assertions below must see only session artifacts
    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "")


def test_command_first_thread_creates_session(monkeypatch, tmp_path):
    _live(monkeypatch, tmp_path)
    session_log.log_exchange("t1", "/briefing", "**My Day** — 3 tasks")
    files = sorted(p.name for p in _messages_dir(tmp_path, "t1").iterdir())
    assert files == ["message_0.json", "message_1.json"]
    m0 = json.loads((_messages_dir(tmp_path, "t1") / "message_0.json").read_text())
    m1 = json.loads((_messages_dir(tmp_path, "t1") / "message_1.json").read_text())
    assert m0["message"]["role"] == "user"
    assert m0["message"]["content"] == [{"text": "/briefing"}]
    assert m1["message"]["role"] == "assistant"
    assert "My Day" in m1["message"]["content"][0]["text"]


def test_second_exchange_appends_after_existing(monkeypatch, tmp_path):
    _live(monkeypatch, tmp_path)
    session_log.log_exchange("t2", "/briefing", "briefing text")
    session_log.log_exchange("t2", "/search vendor", "1 match")
    files = sorted(p.name for p in _messages_dir(tmp_path, "t2").iterdir())
    assert files == [f"message_{i}.json" for i in range(4)]
    m3 = json.loads((_messages_dir(tmp_path, "t2") / "message_3.json").read_text())
    assert m3["message"] == {"role": "assistant", "content": [{"text": "1 match"}]}


def test_agent_record_restores_on_next_turn(monkeypatch, tmp_path):
    """The stored conversation_manager_state must satisfy the SDK's own
    restore validation, or the first real agent turn would blow up."""
    from strands.agent.conversation_manager import SlidingWindowConversationManager
    from strands.session import FileSessionManager

    _live(monkeypatch, tmp_path)
    session_log.log_exchange("t3", "/playbooks", "the playbooks")
    repo = FileSessionManager(session_id="t3", storage_dir=str(tmp_path / "sessions"))
    agent = repo.read_agent("t3", "default")
    assert agent is not None
    SlidingWindowConversationManager().restore_from_session(agent.conversation_manager_state)
    restored = repo.list_messages("t3", "default")
    assert [m.message["role"] for m in restored] == ["user", "assistant"]


def test_mock_provider_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "mock")
    session_log.log_exchange("t4", "/briefing", "briefing text")
    assert not (tmp_path / "sessions").exists()


def test_provider_error_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "bad host")
    session_log.log_exchange("t5", "/briefing", "briefing text")
    assert not (tmp_path / "sessions").exists()


def test_empty_output_writes_nothing(monkeypatch, tmp_path):
    _live(monkeypatch, tmp_path)
    session_log.log_exchange("t6", "/help", "   ")
    assert not (tmp_path / "sessions").exists()


def test_fb_line_never_bridged(monkeypatch, tmp_path):
    _live(monkeypatch, tmp_path)
    session_log.log_exchange("t7", "/remember fb: dana — private thing", "refused")
    session_log.log_exchange("t7", "fb: dana — private thing", "refused")
    assert not (tmp_path / "sessions").exists()


def test_write_failure_is_swallowed(monkeypatch, tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("a file where the sessions dir should be")
    monkeypatch.setattr(config, "SESSIONS_DIR", blocker)
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "")
    session_log.log_exchange("t8", "/briefing", "briefing text")  # must not raise


def test_stranded_user_turn_is_folded(monkeypatch, tmp_path):
    """A failed model call leaves a trailing user message; the bridge must
    fold into it (bedrock rejects non-alternating roles), not stack on it."""
    from strands.agent.conversation_manager import SlidingWindowConversationManager
    from strands.session import FileSessionManager
    from strands.types.session import SessionAgent, SessionMessage

    _live(monkeypatch, tmp_path)
    repo = FileSessionManager(session_id="t9", storage_dir=str(tmp_path / "sessions"))
    repo.create_agent(
        "t9",
        SessionAgent(
            agent_id="default",
            state={},
            conversation_manager_state=SlidingWindowConversationManager().get_state(),
        ),
    )
    stranded = {"role": "user", "content": [{"text": "?"}]}
    repo.create_message("t9", "default", SessionMessage.from_message(stranded, 0))

    session_log.log_exchange("t9", "/briefing", "briefing text")
    restored = FileSessionManager(
        session_id="t9", storage_dir=str(tmp_path / "sessions")
    ).list_messages("t9", "default")
    assert [m.message["role"] for m in restored] == ["user", "assistant"]
    assert restored[0].message["content"] == [{"text": "?"}, {"text": "/briefing"}]
    assert restored[1].message["content"] == [{"text": "briefing text"}]
