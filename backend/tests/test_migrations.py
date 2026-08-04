"""The migration runner: idempotence, one transaction per file, the ledger
rewrite guard, the rename trap, and the guard that stops a long-lived side
process applying schema."""

import re
import shutil
import sqlite3
from pathlib import Path

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


MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
# once 036 chains the ledger, a later migration that rewrites activity breaks
# verification permanently at its earliest touched row (CLAUDE.md)
CHAIN_MIGRATION = "036_activity_chain.sql"
REWRITES_ACTIVITY = re.compile(r"\b(?:UPDATE\s+activity\b|DELETE\s+FROM\s+activity\b)", re.I)


def _sql_only(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))


def test_no_migration_after_the_chain_rewrites_the_ledger():
    """CI databases are born empty, so a destructive migration hits 0 chained
    rows here and every row in production — the suite alone can never catch
    one. This scan can. Ordering, not an allowlist, is what makes 020 safe:
    on any database it runs before 036 exists, so no row it touches carries
    a seq."""
    # positive control: 020 really does rewrite activity, so a broken pattern
    # fails loudly instead of passing forever
    assert REWRITES_ACTIVITY.search(_sql_only((MIGRATIONS / "020_pulse_anonymize.sql").read_text()))
    offenders = [
        p.name
        for p in sorted(MIGRATIONS.glob("*.sql"))
        if p.name > CHAIN_MIGRATION and REWRITES_ACTIVITY.search(_sql_only(p.read_text()))
    ]
    assert offenders == []


def _staged(tmp_path, monkeypatch):
    """The real corpus in a tmp dir, so tests can add or rename migrations."""
    from app import db

    staged = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS, staged)
    monkeypatch.setattr(db, "MIGRATIONS_DIR", staged)
    return staged


def test_a_pending_migration_faces_a_chained_ledger(fresh_db, tmp_path, monkeypatch):
    """The blind spot behind the scan above, closed behaviorally: every other
    test database is freshly migrated BEFORE any activity lands, so no
    migration in the suite ever applied over chained rows until this one."""
    from app.services import activity

    for i in range(3):
        fresh_db.log_activity("tester", "probe", f"row {i}")
    assert activity.verify_chain()["ok"] is True

    staged = _staged(tmp_path, monkeypatch)
    (staged / "998_harmless.sql").write_text("CREATE TABLE IF NOT EXISTS probe_table (id INTEGER)")
    fresh_db.init_db()
    assert activity.verify_chain()["ok"] is True

    # and the harness has teeth: a destructive migration is caught, not absorbed
    (staged / "999_destructive.sql").write_text("UPDATE activity SET detail = 'x' WHERE seq = 1")
    fresh_db.init_db()
    assert activity.verify_chain()["ok"] is False


def test_a_failing_migration_leaves_no_trace(fresh_db, tmp_path, monkeypatch):
    """One transaction per file is init_db's contract. Without it the good
    half of a failed migration persists, and the retry after the fix hits
    'already exists' on a database stuck between versions."""
    staged = _staged(tmp_path, monkeypatch)
    bad = staged / "999_bad.sql"
    bad.write_text("CREATE TABLE half_applied (id INTEGER);\nINSERT INTO no_such_table VALUES (1)")
    with pytest.raises(sqlite3.OperationalError):
        fresh_db.init_db()
    assert (
        fresh_db.query_one("SELECT 1 AS x FROM sqlite_master WHERE name = 'half_applied'") is None
    )
    assert (
        fresh_db.query_one("SELECT 1 AS x FROM schema_version WHERE version = '999_bad.sql'")
        is None
    )
    # recovery: the fixed file applies cleanly on the next boot
    bad.write_text("CREATE TABLE half_applied (id INTEGER)")
    fresh_db.init_db()
    assert fresh_db.query_one("SELECT 1 AS x FROM sqlite_master WHERE name = 'half_applied'")


def test_renaming_a_migration_needs_the_recovery_row_ordered_first(fresh_db, tmp_path, monkeypatch):
    """schema_version records migrations by FILENAME, so a renamed file
    re-runs on every existing database. The in-place rename CLAUDE.md warns
    about bricks the boot — and so does the recovery UPDATE if it sorts after
    the new name, because init_db reaches the renamed file first. The working
    procedure: the renamed file takes a number at the END of the order, and
    the schema_version UPDATE takes the number BEFORE it."""
    staged = _staged(tmp_path, monkeypatch)

    # the trap, in-place: 043's CREATE TABLE is not idempotent, so the rerun
    # under the new name dies on 'already exists' at boot
    (staged / "043_search_ids.sql").rename(staged / "043_search_id_map.sql")
    with pytest.raises(sqlite3.OperationalError):
        fresh_db.init_db()

    # the working procedure
    (staged / "043_search_id_map.sql").rename(staged / "999_search_id_map.sql")
    (staged / "998_rename_search_ids.sql").write_text(
        "UPDATE schema_version SET version = '999_search_id_map.sql'"
        " WHERE version = '043_search_ids.sql'"
    )
    fresh_db.init_db()
    assert fresh_db.pending_migrations() == []
    names = {r["version"] for r in fresh_db.query("SELECT version FROM schema_version")}
    assert "999_search_id_map.sql" in names and "043_search_ids.sql" not in names


def test_seed_builds_its_demo_team_on_a_fresh_database(fresh_db, capsys):
    """seed.py calls the same services the API does; a signature change that
    breaks the demo path used to have no CI signal at all."""
    import importlib.util

    # backend/ is not a package on the test path; load the script by file
    spec = importlib.util.spec_from_file_location("seed", MIGRATIONS.parent / "seed.py")
    seed = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(seed)
    seed.main()
    assert "Seeded:" in capsys.readouterr().out
    assert fresh_db.query_row("SELECT COUNT(*) AS n FROM tasks")["n"] > 0
