"""Backups and the off-box mirror guard."""

import os
import shutil


def _pg_restore() -> str:
    """Absolute path, so the argv carries no bare executable name."""
    found = shutil.which("pg_restore")
    assert found, "pg_restore must be installed to drill a restore"
    return found


def test_backup_mirror_guarded_to_production_data_dir(fresh_db, tmp_path, monkeypatch):
    from pathlib import Path

    from app import config
    from app.services import admin

    mirror = tmp_path / "offbox"
    monkeypatch.setenv("SKEIN_BACKUP_MIRROR", str(mirror))

    # non-default data dir (tests, sandboxes): mirror must be skipped —
    # a throwaway instance overwrote the real NAS mirror before this guard
    assert admin.backup()["mirrored"] is None
    assert not mirror.exists()

    # "production": DATA_DIR == BASE_DIR/data → mirror happens
    prod_data = tmp_path / "data"
    prod_data.mkdir()
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", prod_data)
    out = admin.backup()
    assert out["mirrored"] and os.path.exists(out["mirrored"])
    assert Path(out["mirrored"]).parent == mirror

    monkeypatch.delenv("SKEIN_BACKUP_MIRROR")
    assert admin.backup()["mirrored"] is None


def test_backup_carries_private_notes(fresh_db):
    """The private schema is the one store that exists nowhere else —
    deliberately outside exports — so a backup that skips it loses every 1:1
    note on the first disk loss."""
    from app import config, db
    from app.services import admin, private_notes

    private_notes.add_note("manager", "dana", "irreplaceable 1:1 note")
    out = admin.backup()
    assert out["private_path"] and os.path.exists(out["private_path"])
    # a deployment with no private notes yet has nothing to back up
    db.execute(f"DROP SCHEMA {config.PRIVATE_SCHEMA} CASCADE")
    private_notes._schema_ready = False
    assert admin.backup()["private_path"] is None


def test_restore_drill_brings_both_schemas_back(scratch_db):
    """The documented restore procedure, executed: an untested backup is a
    hope. Back up both schemas, destroy the live ones, pg_restore the same
    dated pair over them, and verify the workspace, the private notes, AND
    the activity chain all survive the ride.

    scratch_db, never fresh_db: this DROPs the public schema, and fresh_db is
    shared by every test in the xdist worker — the drop would take all of them
    with it.

    The restore is spelled out here rather than wrapped in a helper because
    deploy/k8s/README.md is what an operator follows at 3am; if the two ever
    disagree, this test is the one that runs. Its argv matches that runbook:
    no --dbname (the URL carries the password and argv is world-readable),
    and --no-owner so a restore into a different role still loads."""
    import subprocess

    from app import config, db
    from app.services import activity, admin, private_notes, work

    restore = _pg_restore()  # before anything is destroyed, so a missing
    # client tool fails the test instead of leaving a wrecked database behind
    task = work.create_task(title="survives the restore", actor="tester")
    private_notes.add_note("manager", "dana", "the private note that must survive")
    for i in range(2):
        db.log_activity("tester", "probe", f"chained row {i}")
    assert activity.verify_chain()["ok"] is True

    out = admin.backup()

    # the disaster: both live schemas are gone
    db.execute("DROP SCHEMA public CASCADE")
    db.execute(f"DROP SCHEMA {config.PRIVATE_SCHEMA} CASCADE")
    db.execute("CREATE SCHEMA public")
    private_notes._schema_ready = False

    # the documented procedure: pg_restore the same-date pair
    env = admin._pg_env()
    for dump in (out["path"], out["private_path"]):
        subprocess.run(  # noqa: S603 — fixed argv, no shell
            # --dbname takes the NAME, never the URL: pg_restore must connect
            # to restore at all (without -d it just prints SQL), and the name
            # carries no password into argv. _pg_env supplies the rest.
            [restore, "--dbname", env["PGDATABASE"], "--clean", "--if-exists", "--no-owner", dump],
            env=env,
            check=True,
            capture_output=True,
        )
    db.close_pool()  # the restore replaced the objects the pool's plans referred to

    restored = db.query_one("SELECT title FROM tasks WHERE id = ?", (task["id"],))
    assert restored and restored["title"] == "survives the restore"
    notes = private_notes.list_notes("manager", "dana")
    assert [n["body"] for n in notes] == ["the private note that must survive"]
    assert activity.verify_chain()["ok"] is True


def test_interrupted_backup_claim_is_an_error_not_a_silent_skip(fresh_db, caplog):
    """claim_job commits before the copy, so a process killed between the two
    leaves the day claimed with no file — and the next backup_if_stale used to
    return None silently, losing the day with /health still green for 48h."""
    import logging

    from app import db
    from app.services import admin

    assert db.claim_job("backup", admin._today())  # the claimer that "died"
    with caplog.at_level(logging.ERROR, logger="app.services.admin"):
        assert admin.backup_if_stale() is None
    assert any("backup claim" in r.message for r in caplog.records)
    assert not (admin._backups_dir() / f"platform-{admin._today()}.db").exists()


def test_manual_backup_ties_the_knot_and_scheduled_does_not(fresh_db):
    """Only the route passes an actor (routes/api.py::post_backup), so the
    ledger row and the field-guide tie belong to a deliberate manual backup.
    The 03:00 scheduler run must tie nothing for anybody."""
    from app.services import admin, fieldguide

    fresh_db.execute(
        "INSERT INTO users (name, kind, active, created_at) VALUES (?, 'human', 1, ?)",
        ("ava", fresh_db.now()),
    )
    admin.backup()  # the scheduled shape: no actor
    assert not fieldguide.PREDICATES["backup"]("ava")

    admin.backup(actor="ava")
    assert fieldguide.PREDICATES["backup"]("ava")
    row = fresh_db.query_one(
        "SELECT detail FROM activity WHERE actor = 'ava' AND action = 'backup'"
    )
    assert row and row["detail"].startswith("platform-")
