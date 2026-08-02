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
