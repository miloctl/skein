"""The SQLite session store: FileSessionManager's contract over db.py, the
transaction that replaced the per-thread bridge locks, the one-time file
import, and chat deletion taking the model-side sessions with it."""

import threading

import pytest

from app import config, db
from app.agents.session_store import (
    IMPORTED_FLAG,
    SqliteSessionRepository,
    delete_thread_sessions,
    import_file_sessions,
)


def _seed_session(repo, sid="s1"):
    from strands.types.session import Session, SessionType

    repo.create_session(Session(session_id=sid, session_type=SessionType.AGENT))


def _seed_agent(repo, sid="s1", aid="default"):
    from strands.types.session import SessionAgent

    repo.create_agent(sid, SessionAgent(agent_id=aid, state={}, conversation_manager_state={}))


def _msg(i, text="hi"):
    from strands.types.session import SessionMessage

    return SessionMessage.from_message({"role": "user", "content": [{"text": text}]}, i)


def test_round_trip_and_ordering(fresh_db):
    repo = SqliteSessionRepository()
    _seed_session(repo)
    _seed_agent(repo)
    for i in (2, 0, 1):  # written out of order — the store owns ordering
        repo.create_message("s1", "default", _msg(i, f"m{i}"))
    assert [m.message_id for m in repo.list_messages("s1", "default")] == [0, 1, 2]
    assert [m.message_id for m in repo.list_messages("s1", "default", limit=1, offset=1)] == [1]
    assert [m.message_id for m in repo.list_messages("s1", "default", offset=2)] == [2]
    assert repo.read_message("s1", "default", 2).message["content"] == [{"text": "m2"}]
    assert repo.read_session("s1") is not None
    assert repo.read_session("missing") is None
    assert repo.read_agent("s1", "missing") is None
    assert repo.read_message("s1", "default", 99) is None


def test_update_requires_a_row_and_keeps_created_at(fresh_db):
    from strands.types.exceptions import SessionException
    from strands.types.session import SessionAgent

    repo = SqliteSessionRepository()
    _seed_session(repo)
    _seed_agent(repo)
    repo.create_message("s1", "default", _msg(0))
    stored = repo.read_message("s1", "default", 0)
    born = stored.created_at
    stored.message["content"] = [{"text": "edited"}]
    stored.created_at = "2099-01-01T00:00:00+00:00"  # must not survive the update
    repo.update_message("s1", "default", stored)
    again = repo.read_message("s1", "default", 0)
    assert again.message["content"] == [{"text": "edited"}]
    assert again.created_at == born
    with pytest.raises(SessionException):
        repo.update_message("s1", "default", _msg(99))
    with pytest.raises(SessionException):
        repo.update_agent(
            "s1", SessionAgent(agent_id="ghost", state={}, conversation_manager_state={})
        )


def test_the_transaction_is_the_lock(fresh_db):
    """What replaced session_log's per-thread lock dict: writers doing
    read-last-id-then-append inside db.transaction() never lose a write —
    and unlike the lock, the guarantee holds across processes, because
    BEGIN IMMEDIATE serializes on the database, not the interpreter."""
    repo = SqliteSessionRepository()
    _seed_session(repo)
    _seed_agent(repo)
    writers, per_writer = 4, 25
    failures: list[BaseException] = []

    def append_loop():
        try:
            for _ in range(per_writer):
                with db.transaction():
                    stored = repo.list_messages("s1", "default")
                    nxt = stored[-1].message_id + 1 if stored else 0
                    repo.create_message("s1", "default", _msg(nxt))
        except BaseException as exc:
            failures.append(exc)

    threads = [threading.Thread(target=append_loop) for _ in range(writers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not failures, f"writer raised under contention: {failures[:3]}"
    ids = [m.message_id for m in repo.list_messages("s1", "default")]
    assert ids == list(range(writers * per_writer)), "writes were lost or collided"


def test_import_brings_files_over_once(fresh_db, tmp_path, monkeypatch):
    from strands.session import FileSessionManager
    from strands.types.session import SessionAgent

    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path)
    files = FileSessionManager(session_id="old-thread", storage_dir=str(tmp_path))
    files.create_agent(
        "old-thread", SessionAgent(agent_id="default", state={}, conversation_manager_state={})
    )
    files.create_message("old-thread", "default", _msg(0, "from the file era"))

    import_file_sessions()
    repo = SqliteSessionRepository()
    msgs = repo.list_messages("old-thread", "default")
    assert [m.message["content"] for m in msgs] == [[{"text": "from the file era"}]]

    # the flag, not the table, decides: a chat deleted from the database
    # must not be resurrected from its leftover files on the next boot
    delete_thread_sessions("old-thread")
    import_file_sessions()
    assert repo.read_session("old-thread") is None


def test_a_corrupt_session_dir_does_not_brick_the_import(fresh_db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path)
    bad = tmp_path / "session_broken"
    (bad / "agents").mkdir(parents=True)
    (bad / "session.json").write_text("NOT JSON")
    import_file_sessions()  # must not raise
    assert db.query_one("SELECT 1 AS x FROM app_settings WHERE key = ?", (IMPORTED_FLAG,))


def test_deleting_a_thread_takes_its_persona_sessions(fresh_db):
    repo = SqliteSessionRepository()
    for sid in ("t-del", "t-del--muse", "t-delta"):
        _seed_session(repo, sid)
    delete_thread_sessions("t-del")
    assert repo.read_session("t-del") is None
    assert repo.read_session("t-del--muse") is None
    assert repo.read_session("t-delta") is not None  # shares a prefix, not a variant


def test_create_agent_twice_keeps_the_thread_history(fresh_db):
    """create_agent is last-writer-wins on the AGENT payload only. An OR
    REPLACE here deletes the old row to resolve the conflict, and
    session_messages CASCADEs off that PK — so the second concurrent first
    turn on a thread wiped the whole conversation the first one saved."""
    repo = SqliteSessionRepository()
    _seed_session(repo)
    _seed_agent(repo)
    for i in range(3):
        repo.create_message("s1", "default", _msg(i, f"turn {i}"))

    _seed_agent(repo)  # the losing racer re-creates the same agent

    msgs = repo.list_messages("s1", "default")
    assert len(msgs) == 3, "re-creating the agent destroyed the history"
