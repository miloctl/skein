"""The ambient-connection transaction context manager: rollback, nesting, and the compound services that depend on it being atomic."""

import sqlite3
import threading

import pytest


def test_a_write_lock_timeout_reports_the_lock_not_the_rollback(fresh_db, monkeypatch):
    """A BEGIN IMMEDIATE that times out opened no transaction, so the handler's
    ROLLBACK raises "cannot rollback - no transaction is active" and REPLACES
    the real "database is locked". sqlite3.OperationalError has no handler in
    main.py, so the caller gets a 500 and whoever runs the server gets the
    wrong diagnosis to chase."""
    from app import db

    real_connect = db.connect

    def impatient():
        conn = real_connect()
        conn.execute("PRAGMA busy_timeout = 50")  # 5000 default would stall the suite
        return conn

    monkeypatch.setattr(db, "connect", impatient)
    holding, release = threading.Event(), threading.Event()

    def hold_the_write_lock():
        conn = real_connect()
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        holding.set()
        release.wait(10)
        conn.execute("ROLLBACK")
        conn.close()

    holder = threading.Thread(target=hold_the_write_lock)
    holder.start()
    try:
        assert holding.wait(5), "the holder thread never took the write lock"
        with pytest.raises(sqlite3.OperationalError) as exc, db.transaction():
            pass  # BEGIN IMMEDIATE raises before the body ever runs
        assert "locked" in str(exc.value)
        assert "cannot rollback" not in str(exc.value)
    finally:
        release.set()
        holder.join(10)


def test_transaction_rolls_back_all_writes(fresh_db):
    from app import db

    with pytest.raises(RuntimeError), db.transaction():
        db.execute(
            "INSERT INTO notes (topic, content, author, origin, created_by, created_at)"
            " VALUES ('t', 'c', 'a', 'human', 'a', ?)",
            (db.now(),),
        )
        raise RuntimeError("boom")
    assert db.query("SELECT * FROM notes") == []


def test_transaction_commits_and_nests(fresh_db):
    from app import db

    with db.transaction():
        db.execute(
            "INSERT INTO notes (topic, content, author, origin, created_by, created_at)"
            " VALUES ('t', 'c', 'a', 'human', 'a', ?)",
            (db.now(),),
        )
        with db.transaction():  # joins the outer transaction
            assert db.query_row("SELECT COUNT(*) AS n FROM notes")["n"] == 1
    assert db.query_row("SELECT COUNT(*) AS n FROM notes")["n"] == 1


def test_playbook_instantiate_is_atomic(fresh_db, monkeypatch):
    from app.services import engagements, playbooks, schedule

    def explode(**kwargs):
        raise RuntimeError("ritual scheduling failed")

    monkeypatch.setattr(schedule, "schedule_event", explode)
    with pytest.raises(RuntimeError):
        playbooks.instantiate("prototype", "Doomed Launch", lead="ava", actor="tester")
    assert engagements.list_engagements() == []
    assert fresh_db.query("SELECT * FROM milestones") == []
    assert fresh_db.query("SELECT * FROM search_index") == []


def test_on_commit_defers_until_commit_and_drops_on_rollback(fresh_db):
    from app import db

    ran: list[str] = []
    # no ambient transaction: the caller is told to run the work inline
    assert db.on_commit(lambda: ran.append("inline")) is False

    with db.transaction():
        assert db.on_commit(lambda: ran.append("committed")) is True
        assert ran == []  # deferred: nothing runs before COMMIT
    assert ran == ["committed"]

    with pytest.raises(RuntimeError), db.transaction():
        db.on_commit(lambda: ran.append("rolled back"))
        raise RuntimeError("boom")
    assert ran == ["committed"]


def test_on_commit_isolates_a_raising_callback(fresh_db):
    from app import db

    def boom():
        raise RuntimeError("callback failure")

    ran: list[str] = []
    # the write committed, so the caller must see no exception and the
    # callbacks queued after the raising one must still run
    with db.transaction():
        db.on_commit(boom)
        db.on_commit(lambda: ran.append("after"))
    assert ran == ["after"]


def test_index_record_defers_embeds_to_commit(fresh_db, monkeypatch):
    from app import db
    from app.services import search

    embedded: list[tuple] = []
    monkeypatch.setattr(search, "_maybe_embed", lambda e, i, t: embedded.append((e, i)))

    with db.transaction():
        search.index_record("note", 1, "t", "b")
        assert embedded == []  # deferred: the embed must not hold the write lock
    assert embedded == [("note", 1)]

    with pytest.raises(RuntimeError), db.transaction():
        search.index_record("note", 2, "t", "b")
        raise RuntimeError("boom")
    assert embedded == [("note", 1)]  # a rollback drops the embed with the row

    search.index_record("note", 3, "t", "b")  # no transaction: embeds inline
    assert embedded == [("note", 1), ("note", 3)]


def test_the_helpers_close_their_connections(fresh_db, monkeypatch):
    """sqlite3's `with conn:` scopes the transaction and never closes — written
    that way the three helpers leaked one connection per call into a reference
    cycle (Connection ↔ cursors) that refcounting cannot free: 84k open fds
    measured over 30k queries between gc runs, each holding a WAL reader mark
    that stalls checkpoints."""
    from app import db

    handed_out = []
    real_connect = db.connect

    def tracking_connect():
        conn = real_connect()
        handed_out.append(conn)
        return conn

    monkeypatch.setattr(db, "connect", tracking_connect)
    db.query("SELECT 1 AS one")
    db.execute(
        "INSERT INTO job_runs (job, run_key, created_at) VALUES (?, ?, ?)", ("t", "k", db.now())
    )
    db.execute_rowcount("UPDATE job_runs SET run_key = run_key WHERE job = ?", ("t",))
    assert len(handed_out) == 3
    for conn in handed_out:
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")
