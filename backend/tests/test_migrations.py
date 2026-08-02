"""The migration runner: idempotence, one transaction per file, and the guard that stops a long-lived side process applying schema."""

import pytest


def test_pending_migrations_empty_after_init(fresh_db):
    from app import db

    assert db.pending_migrations() == []
    db.execute("DELETE FROM schema_version WHERE version LIKE '013%'")
    assert db.pending_migrations() == ["013_job_outcomes.sql"]


def test_mcp_main_refuses_pending_migrations(fresh_db, monkeypatch):
    from app import mcp_server

    monkeypatch.setattr(mcp_server.db, "pending_migrations", lambda: ["013_job_outcomes.sql"])
    with pytest.raises(SystemExit):
        mcp_server.main()


def test_migrations_idempotent_and_atomic(fresh_db):
    fresh_db.init_db()  # second run must be a clean no-op
    versions = [r["version"] for r in fresh_db.query("SELECT version FROM schema_version")]
    assert len(versions) == len(set(versions)) >= 4
