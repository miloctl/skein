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


def test_core_migration_numbers_are_unique():
    files = sorted(path.name for path in MIGRATIONS.glob("*.sql"))
    numbers = [name.split("_", 1)[0] for name in files]
    duplicates = sorted({number for number in numbers if numbers.count(number) > 1})
    assert duplicates == [], f"duplicate core migration numbers: {duplicates}"


def test_extension_review_status_accepts_unknown_completion(fresh_db):
    from app.services import review

    proposal = review.propose_extension_invocation(
        "core_tool",
        {"tool": "create_task", "agent": "scout"},
        {"tool": "create_task", "agent": "scout", "tool_use": {}},
        summary="run a governed stock tool",
        actor="scout",
        requested_by="mira",
    )
    fresh_db.execute(
        "UPDATE extension_review_invocations SET status = 'completion_unknown' WHERE change_id = ?",
        (proposal["id"],),
    )
    assert (
        fresh_db.query_row(
            "SELECT status FROM extension_review_invocations WHERE change_id = ?",
            (proposal["id"],),
        )["status"]
        == "completion_unknown"
    )


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


def test_artifact_digest_accepts_legacy_rows_and_rejects_malformed_values(fresh_db):
    artifact_id = fresh_db.execute(
        "INSERT INTO artifacts (kind, title, path, created_by, created_at)"
        " VALUES ('document', 'Legacy', '/tmp/legacy.md', 'tester', '2026-08-23T00:00:00+00:00')"
        " RETURNING id"
    )
    assert (
        fresh_db.query_one("SELECT content_sha256 FROM artifacts WHERE id = ?", (artifact_id,))[
            "content_sha256"
        ]
        is None
    )

    valid = "a" * 64
    fresh_db.execute("UPDATE artifacts SET content_sha256 = ? WHERE id = ?", (valid, artifact_id))
    with pytest.raises(psycopg.errors.CheckViolation), fresh_db.transaction():
        fresh_db.execute(
            "UPDATE artifacts SET content_sha256 = ? WHERE id = ?", ("A" * 64, artifact_id)
        )


ACTIVITY_GUARDS = "008_activity_chain_guards.sql"


def _unapply_activity_guards(db):
    db.execute(
        "ALTER TABLE activity DROP CONSTRAINT activity_positive_seq,"
        " DROP CONSTRAINT activity_detail_present,"
        " DROP CONSTRAINT activity_chain_shape"
    )
    db.execute("DELETE FROM schema_version WHERE version = ?", (ACTIVITY_GUARDS,))


def test_activity_guard_upgrade_replaces_a_lagging_old_mark(scratch_db):
    from app.services import activity

    scratch_db.log_activity("tester", "probe", "one")
    scratch_db.log_activity("tester", "probe", "two")
    _unapply_activity_guards(scratch_db)
    activity._put({activity.HIGH_SEQ: "1"})
    scratch_db.execute("DELETE FROM app_settings WHERE key = ?", (activity.HIGH_HASH,))

    scratch_db.init_db()
    tail = scratch_db.query_row("SELECT seq, hash FROM activity ORDER BY seq DESC LIMIT 1")
    assert activity._settings(activity.HIGH_SEQ, activity.HIGH_HASH) == {
        activity.HIGH_SEQ: str(tail["seq"]),
        activity.HIGH_HASH: tail["hash"],
    }
    assert activity.verify_chain()["ok"]


def test_activity_guard_upgrade_preserves_truncation_evidence(scratch_db):
    from app import db
    from app.services import activity

    for i in range(5):
        scratch_db.log_activity("tester", "probe", str(i))
    _unapply_activity_guards(scratch_db)
    scratch_db.execute("DELETE FROM activity WHERE seq >= 4")
    activity._put({activity.HIGH_SEQ: "5"})
    scratch_db.execute("DELETE FROM app_settings WHERE key = ?", (activity.HIGH_HASH,))

    scratch_db.init_db()
    assert activity.verify_chain()["ok"] is False
    with pytest.raises(db.ActivityChainError), scratch_db.transaction():
        scratch_db.log_activity("tester", "probe", "blocked")


def test_activity_guard_upgrade_reports_a_malformed_old_tail(scratch_db):
    from app.services import activity

    scratch_db.log_activity("tester", "probe", "one")
    scratch_db.log_activity("tester", "probe", "two")
    _unapply_activity_guards(scratch_db)
    scratch_db.execute("UPDATE activity SET hash = NULL WHERE seq = 2")

    scratch_db.init_db()
    assert scratch_db.pending_migrations() == []
    result = activity.verify_chain()
    assert not result["ok"]
    assert "invalid chain fields" in result["reason"]


@pytest.mark.parametrize("mark", ["bad", "02", "99999999999999999999", "9" * 5000])
def test_activity_guard_upgrade_does_not_cast_a_malformed_mark(scratch_db, mark):
    from app.services import activity

    scratch_db.log_activity("tester", "probe", "one")
    _unapply_activity_guards(scratch_db)
    activity._put({activity.HIGH_SEQ: mark})
    scratch_db.execute("DELETE FROM app_settings WHERE key = ?", (activity.HIGH_HASH,))

    scratch_db.init_db()
    assert scratch_db.pending_migrations() == []
    assert activity.verify_chain()["ok"] is False


# The activity chain is born in the baseline, so migration DML must never
# bypass the append path. A rewrite breaks verification at the earliest row,
# and a direct insert has no append-owned live-tip update.
MUTATES_ACTIVITY = re.compile(
    r"\b(?:INSERT\s+INTO|MERGE\s+INTO|COPY|UPDATE|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?)\s+"
    r'(?:ONLY\s+)?(?:(?:"?public"?)\s*\.\s*)?"?activity"?\b',
    re.I,
)


def _sql_only(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))


def test_no_migration_bypasses_the_ledger_append_path():
    """CI databases are born empty, so a destructive migration hits 0 chained
    rows here and every row in production — the suite alone can never catch
    one. This scan can."""
    # Positive controls stop a broken pattern from passing forever. Include the
    # qualified and quoted forms that extension or maintenance SQL can use.
    for statement in (
        "UPDATE activity SET detail = 'x'",
        "UPDATE ONLY public.activity SET detail = 'x'",
        "DELETE FROM public.activity WHERE seq = 1",
        'INSERT INTO "public"."activity" (actor) VALUES (\'x\')',
        "MERGE INTO activity USING source ON false WHEN NOT MATCHED THEN INSERT DEFAULT VALUES",
        "COPY public.activity FROM STDIN",
        "TRUNCATE TABLE activity",
    ):
        assert MUTATES_ACTIVITY.search(statement)
    offenders = [
        p.name
        for p in sorted(MIGRATIONS.glob("*.sql"))
        if MUTATES_ACTIVITY.search(_sql_only(p.read_text()))
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
