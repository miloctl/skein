"""Durable files that follow an ambient database transaction."""

import hashlib
import os
from pathlib import Path
from uuid import uuid4

from .. import config, db


def _root() -> Path:
    root = Path(config.DATA_DIR) / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _contained(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(_root()):
        # Every caller passes a server-generated or stored path. Escaping the
        # root is storage corruption, not malformed caller input (ValueError
        # maps to 400), so let the generic JSON 500 handler report it.
        raise RuntimeError("artifact path must stay under SKEIN_DATA_DIR/artifacts")
    return resolved


def _sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _delete(path: Path) -> None:
    contained = _contained(path)
    if contained.exists():
        contained.unlink()
        _sync_directory(contained.parent)


def unique_revision(logical: Path) -> Path:
    """A physical revision beside its logical predecessor."""
    contained = _contained(logical)
    return contained.with_name(f"{contained.stem}-{uuid4().hex}{contained.suffix}")


def content_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_matches(data: bytes, expected: str | None) -> bool:
    return expected is None or content_sha256(data) == expected


def publish(final: Path, data: bytes, *, old: Path | None = None) -> str:
    """Publish durable bytes before the ambient database transaction commits."""
    if not db.in_transaction():
        raise RuntimeError("artifact publication needs an active transaction")
    target = _contained(final)
    previous = _contained(old) if old is not None else None
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    descriptor = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        if not db.on_rollback(lambda: _delete(target)):
            raise RuntimeError("artifact publication lost its rollback transaction")
        os.replace(temp, target)
        target.chmod(0o600)
        _sync_directory(target.parent)
        if previous is not None and previous != target:
            delete_after_commit(previous)
        return content_sha256(data)
    finally:
        temp.unlink(missing_ok=True)


def delete_after_commit(path: Path) -> None:
    """Delete one contained file only after its database row commits."""
    contained = _contained(path)
    if not db.on_commit(lambda: _delete(contained)):
        _delete(contained)
