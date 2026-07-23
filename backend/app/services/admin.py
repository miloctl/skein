"""Backups and export. SQLite's .backup API gives consistent copies even
mid-write; exports are JSON snapshots for portability.

Ops note: backups default to the same volume as the live DB. Copy them off-box
(host cron: `docker cp` / rsync of /data/backups) or set STRANDS_BACKUP_DIR to
a bind mount — losing the volume otherwise loses DB and backups together.
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .. import config, db

log = logging.getLogger(__name__)

TABLES = (
    "users", "milestones", "tasks", "questions", "decisions", "standups",
    "events", "notes", "activity", "blockers", "intake_requests",
    "pending_changes", "usage_log", "engagements", "allocations", "lessons",
    "artifacts", "memories", "notifications",
)


def _backups_dir() -> Path:
    d = Path(os.getenv("STRANDS_BACKUP_DIR", "") or Path(config.DATA_DIR) / "backups")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def backup(*, keep: int = 14) -> dict:
    backups_dir = _backups_dir()
    dest = backups_dir / f"platform-{_today()}.db"
    tmp = dest.with_suffix(".db.tmp")

    src = db.connect()
    try:
        target = sqlite3.connect(tmp)
        with target:
            src.backup(target)
        target.close()
        os.replace(tmp, dest)  # atomic: no truncated file can masquerade as a backup
    finally:
        src.close()
        tmp.unlink(missing_ok=True)

    existing = sorted(backups_dir.glob("platform-*.db"))
    for old in existing[:-keep]:
        old.unlink()
    log.info("backup written: %s", dest)
    return {"path": str(dest), "kept": min(len(existing), keep)}


def backup_if_stale() -> dict | None:
    """Daily hook, multi-process safe via the job_runs claim."""
    dest = _backups_dir() / f"platform-{_today()}.db"
    if dest.exists():
        return None
    if not db.claim_job("backup", _today()):
        return None
    return backup()


def export(*, keep: int = 14) -> dict:
    dump = {}
    for table in TABLES:
        try:
            dump[table] = db.query(f"SELECT * FROM {table}")  # noqa: S608 — fixed list
        except Exception:
            dump[table] = []
    exports_dir = Path(config.DATA_DIR) / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    path = exports_dir / f"export-{db.now().replace(':', '')}.json"
    path.write_text(json.dumps(dump, indent=1))
    for old in sorted(exports_dir.glob("export-*.json"))[:-keep]:
        old.unlink()
    return {"path": str(path), "tables": {t: len(rows) for t, rows in dump.items()}}
