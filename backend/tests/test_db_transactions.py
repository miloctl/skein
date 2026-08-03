"""The ambient-connection transaction context manager: rollback, nesting, and the compound services that depend on it being atomic."""

import pytest


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
