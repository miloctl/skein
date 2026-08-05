"""Session bridge: slash-command exchanges land in the thread's model session."""

import pytest

from app import config, db
from app.agents import session_log
from app.agents.session_store import SqliteSessionRepository


@pytest.fixture(autouse=True)
def _own_db(fresh_db):
    """The bridge seeds conversation-manager state via team_agent.
    _conversation_manager, which reads effective_context_strategy from the
    DB; without a fresh DB these tests only pass when an earlier test
    initialized the shared one — order-dependence, not verification."""
    return fresh_db


def _live(monkeypatch):
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "")


def _messages(thread):
    return SqliteSessionRepository().list_messages(thread, "default")


def _nothing_stored():
    return db.query_one("SELECT 1 AS x FROM sessions") is None


def test_command_first_thread_creates_session(monkeypatch):
    _live(monkeypatch)
    session_log.log_exchange("t1", "/briefing", "**My Day** — 3 tasks")
    stored = _messages("t1")
    assert [m.message_id for m in stored] == [0, 1]
    assert stored[0].message["role"] == "user"
    assert stored[0].message["content"] == [{"text": "/briefing"}]
    assert stored[1].message["role"] == "assistant"
    assert "My Day" in stored[1].message["content"][0]["text"]


def test_second_exchange_appends_after_existing(monkeypatch):
    _live(monkeypatch)
    session_log.log_exchange("t2", "/briefing", "briefing text")
    session_log.log_exchange("t2", "/search vendor", "1 match")
    stored = _messages("t2")
    assert [m.message_id for m in stored] == [0, 1, 2, 3]
    assert stored[3].message == {"role": "assistant", "content": [{"text": "1 match"}]}


def test_agent_record_restores_on_next_turn(monkeypatch):
    """The stored conversation_manager_state must satisfy the SDK's own
    restore validation, or the first real agent turn would blow up."""
    from strands.agent.conversation_manager import SlidingWindowConversationManager

    _live(monkeypatch)
    session_log.log_exchange("t3", "/playbooks", "the playbooks")
    agent = SqliteSessionRepository().read_agent("t3", "default")
    assert agent is not None
    SlidingWindowConversationManager().restore_from_session(agent.conversation_manager_state)
    assert [m.message["role"] for m in _messages("t3")] == ["user", "assistant"]


def test_mock_provider_writes_nothing(monkeypatch):
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "mock")
    session_log.log_exchange("t4", "/briefing", "briefing text")
    assert _nothing_stored()


def test_provider_error_writes_nothing(monkeypatch):
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "bad host")
    session_log.log_exchange("t5", "/briefing", "briefing text")
    assert _nothing_stored()


def test_empty_output_writes_nothing(monkeypatch):
    _live(monkeypatch)
    session_log.log_exchange("t6", "/help", "   ")
    assert _nothing_stored()


def test_fb_line_never_bridged(monkeypatch):
    _live(monkeypatch)
    session_log.log_exchange("t7", "/remember fb: dana — private thing", "refused")
    session_log.log_exchange("t7", "fb: dana — private thing", "refused")
    assert _nothing_stored()


def test_write_failure_is_swallowed_and_leaves_no_half_session(monkeypatch):
    """Best-effort by contract: the command reply already streamed, so a
    session write failure only logs. And because the bridge write is ONE
    transaction, failure leaves no half-written session — the file store
    could strand an agent record with no messages."""
    _live(monkeypatch)

    def boom(self, *a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(SqliteSessionRepository, "create_message", boom)
    session_log.log_exchange("t8", "/briefing", "briefing text")  # must not raise
    assert _nothing_stored()


def test_stranded_user_turn_is_folded(monkeypatch):
    """A failed model call leaves a trailing user message; the bridge must
    fold into it (bedrock rejects non-alternating roles), not stack on it."""
    from strands.agent.conversation_manager import SlidingWindowConversationManager
    from strands.types.session import Session, SessionAgent, SessionMessage, SessionType

    _live(monkeypatch)
    repo = SqliteSessionRepository()
    repo.create_session(Session(session_id="t9", session_type=SessionType.AGENT))
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
    restored = _messages("t9")
    assert [m.message["role"] for m in restored] == ["user", "assistant"]
    assert restored[0].message["content"] == [{"text": "?"}, {"text": "/briefing"}]
    assert restored[1].message["content"] == [{"text": "briefing text"}]
