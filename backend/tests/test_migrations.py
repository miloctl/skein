"""The migration runner: idempotence, one transaction per file, the ledger
rewrite guard, the rename trap, and the guard that stops a long-lived side
process applying schema."""

import re
import shutil
import sqlite3
from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
BASELINE = "001_baseline.sql"


def test_pending_migrations_empty_after_init(fresh_db):
    from app import db

    assert db.pending_migrations() == []
    db.execute("DELETE FROM schema_version WHERE version = ?", (BASELINE,))
    assert db.pending_migrations() == [BASELINE]


def test_mcp_main_refuses_pending_migrations(fresh_db, monkeypatch):
    from app import mcp_server

    monkeypatch.setattr(mcp_server.db, "pending_migrations", lambda: ["002_example.sql"])
    with pytest.raises(SystemExit):
        mcp_server.main()


def test_migrations_idempotent_and_atomic(fresh_db):
    fresh_db.init_db()  # second run must be a clean no-op
    versions = [r["version"] for r in fresh_db.query("SELECT version FROM schema_version")]
    assert len(versions) == len(set(versions)) >= 1


# the activity chain is born in the baseline, so NO migration may ever
# rewrite a chained row — verification breaks permanently at the earliest
# touched row (CLAUDE.md). Before the 2026-08-04 squash this rule was
# "nothing after 036"; the baseline swallowed 036, so it is now absolute.
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


def test_a_renamed_migration_reruns_and_bricks_the_boot(fresh_db, tmp_path, monkeypatch):
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
    with pytest.raises(sqlite3.OperationalError):
        fresh_db.init_db()

    # the hand-update IS the recovery: with the record renamed too, the
    # runner sees the file as applied and the boot is a no-op again
    fresh_db.execute(
        "UPDATE schema_version SET version = '001_v1_schema.sql' WHERE version = ?",
        (BASELINE,),
    )
    fresh_db.init_db()
    assert fresh_db.pending_migrations() == []


def test_a_rebuild_migration_keeps_child_foreign_keys(fresh_db, tmp_path, monkeypatch):
    """The 12-step table rebuild is the only way to widen a CHECK, and with
    foreign_keys ON the DROP fires ON DELETE actions: rebuilding milestones
    nulled every task's milestone_id. The runner turns enforcement off (a
    migration cannot — the pragma is a silent no-op inside a transaction)
    and relies on foreign_key_check instead."""
    from app.services import work

    m = work.create_milestone(title="anchor")
    t = work.create_task(title="linked")
    fresh_db.execute("UPDATE tasks SET milestone_id = ? WHERE id = ?", (m["id"], t["id"]))

    staged = _staged(tmp_path, monkeypatch)
    # a real rebuild recreates the DDL — CREATE ... AS SELECT would drop the
    # primary key, and foreign_key_check refuses the missing parent key
    ddl = fresh_db.query_one(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'milestones'"
    )["sql"].replace("CREATE TABLE milestones", "CREATE TABLE milestones_new", 1)
    (staged / "998_rebuild.sql").write_text(
        f"{ddl};\n"  # noqa: S608 — DDL from sqlite_master, not user input
        "INSERT INTO milestones_new SELECT * FROM milestones;\n"
        "DROP TABLE milestones;\n"
        "ALTER TABLE milestones_new RENAME TO milestones"
    )
    fresh_db.init_db()
    row = fresh_db.query_one("SELECT milestone_id FROM tasks WHERE id = ?", (t["id"],))
    assert row["milestone_id"] == m["id"], "the rebuild fired ON DELETE SET NULL"


def test_a_migration_that_breaks_a_foreign_key_is_refused(fresh_db, tmp_path, monkeypatch):
    """Enforcement is off during migrations (see above), so foreign_key_check
    before commit is the only thing standing between a buggy migration and
    committed orphans."""
    staged = _staged(tmp_path, monkeypatch)
    (staged / "999_orphan.sql").write_text(
        "INSERT INTO tasks (title, milestone_id, created_at, updated_at)"
        " VALUES ('orphan', 4242, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    with pytest.raises(sqlite3.IntegrityError, match="999_orphan"):
        fresh_db.init_db()
    assert fresh_db.query_one("SELECT 1 AS x FROM tasks WHERE title = 'orphan'") is None
    assert (
        fresh_db.query_one("SELECT 1 AS x FROM schema_version WHERE version = '999_orphan.sql'")
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
    spec = importlib.util.spec_from_file_location("seed", MIGRATIONS.parent / "seed.py")
    seed = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(seed)
    seed.main()
    assert "Seeded:" in capsys.readouterr().out
    assert fresh_db.query_row("SELECT COUNT(*) AS n FROM tasks")["n"] > 0
