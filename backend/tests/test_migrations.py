"""The migration runner: idempotence, one transaction per file, the ledger
rewrite guard, the rename trap, and the guard that stops a long-lived side
process applying schema.

Every test that stages a migration or edits schema_version takes scratch_db,
not fresh_db: fresh_db shares one database per xdist worker, so a schema
mutation here would follow every later test in that worker.
"""

import re
import shutil
from pathlib import Path

import psycopg
import pytest

MIGRATIONS = Path(__file__).resolve().parent.parent / "app" / "core_migrations"
BASELINE = "001_baseline.sql"


def test_pending_migrations_empty_after_init(scratch_db):
    assert scratch_db.pending_migrations() == []
    scratch_db.execute("DELETE FROM schema_version WHERE version = ?", (BASELINE,))
    assert scratch_db.pending_migrations() == [BASELINE]


def test_mcp_main_refuses_pending_migrations(fresh_db, monkeypatch):
    from app import mcp_server

    monkeypatch.setattr(mcp_server.db, "pending_migrations", lambda: ["002_example.sql"])
    with pytest.raises(SystemExit):
        mcp_server.main()


def test_migrations_idempotent_and_atomic(scratch_db):
    scratch_db.init_db()  # second run must be a clean no-op
    versions = [r["version"] for r in scratch_db.query("SELECT version FROM schema_version")]
    assert len(versions) == len(set(versions)) >= 1


# the activity chain is born in the baseline, so NO migration may ever
# rewrite a chained row — verification breaks permanently at the earliest
# touched row (CLAUDE.md).
REWRITES_ACTIVITY = re.compile(r"\b(?:UPDATE\s+activity\b|DELETE\s+FROM\s+activity\b)", re.I)


def _sql_only(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))


def test_no_migration_rewrites_the_ledger():
    """CI databases are born empty, so a destructive migration hits 0 chained
    rows here and every row in production — the suite alone can never catch
    one. This scan can."""
    # positive control: a broken pattern fails loudly instead of passing forever
    assert REWRITES_ACTIVITY.search("UPDATE activity SET detail = 'x'")
    assert REWRITES_ACTIVITY.search("DELETE FROM activity WHERE seq = 1")
    offenders = [
        p.name
        for p in sorted(MIGRATIONS.glob("*.sql"))
        if REWRITES_ACTIVITY.search(_sql_only(p.read_text()))
    ]
    assert offenders == []


def _staged(tmp_path, monkeypatch):
    """The real corpus in a tmp dir, so tests can add or rename migrations."""
    from app import db

    staged = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS, staged)
    monkeypatch.setattr(db, "MIGRATIONS_DIR", staged)
    return staged


def test_a_pending_migration_faces_a_chained_ledger(scratch_db, tmp_path, monkeypatch):
    """The blind spot behind the scan above, closed behaviorally: every other
    test database is freshly migrated BEFORE any activity lands, so no
    migration in the suite ever applied over chained rows until this one."""
    from app.services import activity

    for i in range(3):
        scratch_db.log_activity("tester", "probe", f"row {i}")
    assert activity.verify_chain()["ok"] is True

    staged = _staged(tmp_path, monkeypatch)
    (staged / "998_harmless.sql").write_text("CREATE TABLE IF NOT EXISTS probe_table (id bigint)")
    scratch_db.init_db()
    assert activity.verify_chain()["ok"] is True

    # and the harness has teeth: a destructive migration is caught, not absorbed
    (staged / "999_destructive.sql").write_text("UPDATE activity SET detail = 'x' WHERE seq = 1")
    scratch_db.init_db()
    assert activity.verify_chain()["ok"] is False


def test_a_failing_migration_leaves_no_trace(scratch_db, tmp_path, monkeypatch):
    """One transaction per file is init_db's contract. Without it the good
    half of a failed migration persists, and the retry after the fix hits
    'already exists' on a database stuck between versions."""
    staged = _staged(tmp_path, monkeypatch)
    bad = staged / "999_bad.sql"
    bad.write_text("CREATE TABLE half_applied (id bigint);\nINSERT INTO no_such_table VALUES (1)")
    with pytest.raises(psycopg.errors.UndefinedTable, match="999_bad"):
        scratch_db.init_db()
    assert _table(scratch_db, "half_applied") is None
    assert (
        scratch_db.query_one("SELECT 1 AS x FROM schema_version WHERE version = '999_bad.sql'")
        is None
    )
    # recovery: the fixed file applies cleanly on the next boot
    bad.write_text("CREATE TABLE half_applied (id bigint)")
    scratch_db.init_db()
    assert _table(scratch_db, "half_applied")


def _table(db, name):
    return db.query_one(
        "SELECT 1 AS x FROM information_schema.tables WHERE table_name = ?", (name,)
    )


def test_a_renamed_migration_reruns_and_bricks_the_boot(scratch_db, tmp_path, monkeypatch):
    """schema_version records migrations by FILENAME, so a renamed file
    re-runs on every existing database — the baseline's CREATE TABLE is not
    idempotent, and the boot dies on 'already exists'. This is why CLAUDE.md
    says a migration keeps its name after first deploy: no recovery
    MIGRATION can fix it. One numbered after the renamed file runs too late
    (the runner walks in filename order and hits the rerun first), and
    moving the renamed file to the end reorders fresh builds, so a later
    migration that depends on it sees a different world. A pre-deploy
    rename hand-updates schema_version in every live database instead."""
    staged = _staged(tmp_path, monkeypatch)
    (staged / BASELINE).rename(staged / "001_v1_schema.sql")
    with pytest.raises(psycopg.errors.DuplicateTable):
        scratch_db.init_db()

    # the hand-update IS the recovery: with the record renamed too, the
    # runner sees the file as applied and the boot is a no-op again
    scratch_db.execute(
        "UPDATE schema_version SET version = '001_v1_schema.sql' WHERE version = ?",
        (BASELINE,),
    )
    scratch_db.init_db()
    assert scratch_db.pending_migrations() == []


def test_a_migration_that_breaks_a_foreign_key_is_refused(scratch_db, tmp_path, monkeypatch):
    """PostgreSQL enforces foreign keys inside the migration's own
    transaction, so a buggy migration rolls itself back instead of committing
    orphans. SQLite had to run migrations with enforcement OFF (the 12-step
    table rebuild fires ON DELETE actions otherwise) and check afterwards."""
    staged = _staged(tmp_path, monkeypatch)
    (staged / "999_orphan.sql").write_text(
        "INSERT INTO tasks (title, milestone_id, created_at, updated_at)"
        " VALUES ('orphan', 4242, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    with pytest.raises(psycopg.errors.ForeignKeyViolation, match="999_orphan"):
        scratch_db.init_db()
    assert scratch_db.query_one("SELECT 1 AS x FROM tasks WHERE title = 'orphan'") is None
    assert (
        scratch_db.query_one("SELECT 1 AS x FROM schema_version WHERE version = '999_orphan.sql'")
        is None
    )


def test_the_baseline_contains_no_data_statements():
    """The baseline is DDL only: on an empty database every backfill in the
    pre-squash corpus was a no-op, so nothing carried forward. A data
    statement appearing here means someone edited the baseline instead of
    adding a numbered migration."""
    sql = _sql_only((MIGRATIONS / BASELINE).read_text())
    # per-statement leading verb: `ON DELETE SET NULL` inside a foreign key
    # is DDL, not a data statement
    verbs = [s.split(None, 1)[0].upper() for s in sql.split(";") if s.strip()]
    assert not {"INSERT", "UPDATE", "DELETE"} & set(verbs)


def test_seed_builds_its_demo_team_on_a_fresh_database(fresh_db, capsys):
    """seed.py calls the same services the API does; a signature change that
    breaks the demo path used to have no CI signal at all."""
    import importlib.util

    # backend/ is not a package on the test path; load the script by file
    spec = importlib.util.spec_from_file_location("seed", MIGRATIONS.parent.parent / "seed.py")
    seed = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(seed)
    seed.main()
    assert "Seeded:" in capsys.readouterr().out
    assert fresh_db.query_row("SELECT COUNT(*) AS n FROM tasks")["n"] > 0
