"""The ambient-connection transaction context manager: rollback, nesting, and the compound services that depend on it being atomic."""

import threading
from decimal import Decimal

import pytest


def test_a_lock_timeout_surfaces_as_load_not_as_a_rollback_failure(fresh_db):
    """A statement that gives up waiting for a row lock must raise the LOCK
    error, classified as load.

    Under SQLite this was a BEGIN IMMEDIATE that timed out, where an
    unguarded ROLLBACK in the cleanup raised "cannot rollback - no transaction
    is active" and REPLACED the real "database is locked". The cleanup path is
    different now, and the property it has to keep is the same: the error the
    caller sees names the contention, and db.BUSY_ERRORS classes it as load so
    main.py answers 503 rather than 500."""
    import psycopg

    from app import db

    db.execute(
        "INSERT INTO job_runs (job, run_key, created_at) VALUES (?, ?, ?)",
        ("lockme", "k", db.now()),
    )
    holding, release = threading.Event(), threading.Event()

    def hold_the_row():
        with db.transaction():
            db.query("SELECT job FROM job_runs WHERE job = ? FOR UPDATE", ("lockme",))
            holding.set()
            release.wait(10)

    holder = threading.Thread(target=hold_the_row)
    holder.start()
    try:
        assert holding.wait(5), "the holder thread never took the row lock"
        with pytest.raises(psycopg.errors.LockNotAvailable) as exc, db.transaction():
            db.execute("SET LOCAL lock_timeout = '50ms'")
            db.query("SELECT job FROM job_runs WHERE job = ? FOR UPDATE", ("lockme",))
        assert "lock" in str(exc.value).lower()
        assert isinstance(exc.value, db.BUSY_ERRORS)
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


def test_read_transaction_holds_one_snapshot(fresh_db):
    """Every read in the block sees the same instant.

    REPEATABLE READ, not the engine default: under READ COMMITTED each
    statement takes a fresh snapshot, so a commit landing mid-block makes two
    counts that must agree disagree. Pinned by behaviour rather than by the
    SQL emitted, because the isolation level is the contract and the statement
    that sets it is not."""
    from app import db

    def insert_and_commit():
        db.execute(
            "INSERT INTO job_runs (job, run_key, created_at) VALUES (?, ?, ?)",
            ("snapshot", "k", db.now()),
        )

    with db.read_transaction():
        before = db.query_row("SELECT COUNT(*) AS n FROM job_runs")["n"]
        writer = threading.Thread(target=insert_and_commit)
        writer.start()
        writer.join(5)
        after = db.query_row("SELECT COUNT(*) AS n FROM job_runs")["n"]
    assert before == after, "a committed write leaked into an open read snapshot"
    # and the row really was committed — the snapshot hid it, nothing dropped it
    assert db.query_row("SELECT COUNT(*) AS n FROM job_runs")["n"] == before + 1


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


def test_savepoint_rollback_discards_only_its_deferred_callbacks(fresh_db):
    from app import db

    ran: list[str] = []
    with db.transaction():
        db.on_commit(lambda: ran.append("before"))
        with pytest.raises(RuntimeError), db.savepoint():
            db.on_commit(lambda: ran.append("rolled-back apply"))
            raise RuntimeError("apply failed")
        db.on_commit(lambda: ran.append("after"))

    assert ran == ["before", "after"]


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


def test_the_helpers_return_their_connections_to_the_pool(fresh_db):
    """A helper that leaks its connection exhausts the pool.

    Under SQLite the same bug leaked file descriptors — 84k open fds measured
    over 30k queries — because the connection context manager scopes the
    TRANSACTION and never closes. Here the ceiling is the pool: once max_size
    connections are checked out and never returned, the next caller waits for
    PoolTimeout instead of running."""
    from app import db

    pool = db.pool()
    for i in range(pool.max_size + 5):
        db.query("SELECT 1 AS one")
        db.execute(
            "INSERT INTO job_runs (job, run_key, created_at) VALUES (?, ?, ?)",
            ("t", f"k{i}", db.now()),
        )
        db.execute_rowcount("UPDATE job_runs SET run_key = run_key WHERE job = ?", ("t",))
    stats = pool.get_stats()
    assert stats["pool_size"] <= pool.max_size
    # nothing is still checked out once the helpers have returned
    assert stats.get("pool_available", 0) >= 1


def test_numeric_aggregates_load_as_float_not_decimal(fresh_db):
    """SUM, ROUND(::numeric) and EXTRACT(epoch) must not reach a caller as
    Decimal.

    Decimal is not JSON-serializable, and the services are written for the
    float SQLite returned. The break lands only on the json.dumps callers:
    FastAPI routes survive on jsonable_encoder, so the REST surface stays
    green while every agent tool returning the same numbers raises
    TypeError. Without the numeric loader in db.py the findings job dies on
    its own budget receipt and get_flow_metrics raises whenever a task is
    complete."""
    import json

    from app import db

    row = db.query_one(
        "SELECT SUM(n)::bigint AS total, ROUND(AVG(n)::numeric, 1) AS mean,"
        " EXTRACT(epoch FROM now()) AS epoch"
        " FROM (SELECT 1 AS n UNION ALL SELECT 2) t"
    )
    assert row is not None
    for key, value in row.items():
        assert not isinstance(value, Decimal), f"{key} loaded as Decimal"
    json.dumps(row)  # the sink every agent tool goes through
