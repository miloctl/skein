"""Backups and the off-box mirror guard."""

import os


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
    """private.db is the one store that exists nowhere else — deliberately
    outside exports — so a backup that skips it loses every 1:1 note on the
    first disk loss. That was the shipped behavior until 2026-08-04."""
    from app import config
    from app.services import admin, private_notes

    private_notes.add_note("manager", "dana", "irreplaceable 1:1 note")
    out = admin.backup()
    assert out["private_path"] and os.path.exists(out["private_path"])
    # a database with no private notes yet has nothing to back up
    os.unlink(config.PRIVATE_DB_PATH)
    assert admin.backup()["private_path"] is None


def test_restore_drill_brings_both_databases_back(fresh_db):
    """The documented restore procedure, executed: an untested backup is a
    hope. Back up both databases, destroy the live ones, copy the backups
    over them, and verify the workspace, the private notes, AND the activity
    chain all survive the ride."""
    import shutil

    from app import config, db
    from app.services import activity, admin, private_notes, work

    task = work.create_task(title="survives the restore", actor="tester")
    private_notes.add_note("manager", "dana", "the private note that must survive")
    for i in range(2):
        db.log_activity("tester", "probe", f"chained row {i}")
    assert activity.verify_chain()["ok"] is True

    out = admin.backup()

    # the disaster: both live databases are gone
    os.unlink(db.DB_PATH)
    os.unlink(config.PRIVATE_DB_PATH)

    # the documented procedure: copy the same-date pair back
    shutil.copy2(out["path"], db.DB_PATH)
    shutil.copy2(out["private_path"], config.PRIVATE_DB_PATH)

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
