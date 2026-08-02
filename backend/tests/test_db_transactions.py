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
