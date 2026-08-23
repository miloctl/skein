"""Artifact bytes follow database commit and rollback boundaries."""

from pathlib import Path

import pytest


def test_publish_requires_a_transaction(fresh_db):
    from app import config
    from app.services import artifact_files

    target = Path(config.DATA_DIR) / "artifacts" / "probe.md"
    with pytest.raises(RuntimeError, match="active transaction"):
        artifact_files.publish(target, b"body")


def test_rollback_removes_new_publication(fresh_db):
    from app import config, db
    from app.services import artifact_files

    target = Path(config.DATA_DIR) / "artifacts" / "probe.md"
    with pytest.raises(RuntimeError), db.transaction():
        artifact_files.publish(target, b"body")
        assert target.read_bytes() == b"body"
        raise RuntimeError("rollback")
    assert not target.exists()


def test_commit_keeps_new_file_and_deletes_old_revision(fresh_db):
    from app import config, db
    from app.services import artifact_files

    old = Path(config.DATA_DIR) / "artifacts" / "document.md"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old")
    new = artifact_files.unique_revision(old)
    with db.transaction():
        artifact_files.publish(new, b"new", old=old)
        assert old.exists()
        assert new.read_bytes() == b"new"
    assert not old.exists()
    assert new.read_bytes() == b"new"
    assert new.stat().st_mode & 0o077 == 0


def test_outer_rollback_keeps_old_revision(fresh_db):
    from app import config, db
    from app.services import artifact_files

    old = Path(config.DATA_DIR) / "artifacts" / "document.md"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old")
    new = artifact_files.unique_revision(old)
    with pytest.raises(RuntimeError), db.transaction():
        artifact_files.publish(new, b"new", old=old)
        raise RuntimeError("rollback")
    assert old.read_bytes() == b"old"
    assert not new.exists()


def test_savepoint_rollback_cleans_only_its_file(fresh_db):
    from app import config, db
    from app.services import artifact_files

    root = Path(config.DATA_DIR) / "artifacts"
    kept = root / "kept.md"
    removed = root / "removed.md"
    with db.transaction():
        artifact_files.publish(kept, b"kept")
        with pytest.raises(ValueError), db.savepoint():
            artifact_files.publish(removed, b"removed")
            raise ValueError("savepoint")
        assert kept.exists()
        assert not removed.exists()
    assert kept.exists()


def test_released_savepoint_keeps_cleanup_for_outer_rollback(fresh_db):
    from app import config, db
    from app.services import artifact_files

    target = Path(config.DATA_DIR) / "artifacts" / "released.md"
    with pytest.raises(RuntimeError), db.transaction():
        with db.savepoint():
            artifact_files.publish(target, b"published")
        assert target.exists()
        raise RuntimeError("outer rollback")
    assert not target.exists()


def test_publish_syncs_before_and_after_replace(fresh_db, monkeypatch):
    from app import config, db
    from app.services import artifact_files

    events = []
    real_replace = artifact_files.os.replace

    def sync(_descriptor):
        events.append("sync")

    def replace(source, target):
        events.append("replace")
        real_replace(source, target)

    monkeypatch.setattr(artifact_files.os, "fsync", sync)
    monkeypatch.setattr(artifact_files.os, "replace", replace)
    target = Path(config.DATA_DIR) / "artifacts" / "probe.md"
    with db.transaction():
        artifact_files.publish(target, b"body")
    assert events[0] == "sync"
    assert "replace" in events
    assert events.index("replace") < len(events) - 1
    assert not list(target.parent.glob("*.tmp"))


def test_publish_refuses_a_symlink_escape(fresh_db, tmp_path):
    from app import config, db
    from app.services import artifact_files

    root = Path(config.DATA_DIR) / "artifacts"
    root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeError, match="must stay"), db.transaction():
        artifact_files.publish(root / "escape" / "probe.md", b"body")
