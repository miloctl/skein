"""Backups and export. SQLite's .backup API gives consistent copies even
mid-write; exports are JSON snapshots for portability."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .. import config, db

TABLES = (
    "users", "milestones", "tasks", "questions", "decisions", "standups",
    "events", "notes", "activity", "blockers", "intake_requests",
    "pending_changes", "usage_log", "engagements", "allocations", "lessons",
    "artifacts",
)


def backup(*, keep: int = 14) -> dict:
    backups_dir = Path(config.DATA_DIR) / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    dest = backups_dir / f"platform-{datetime.now(timezone.utc).date().isoformat()}.db"

    src = db.connect()
    try:
        target = sqlite3.connect(dest)
        with target:
            src.backup(target)
        target.close()
    finally:
        src.close()

    existing = sorted(backups_dir.glob("platform-*.db"))
    for old in existing[:-keep]:
        old.unlink()
    return {"path": str(dest), "kept": min(len(existing), keep)}


def backup_if_stale() -> dict | None:
    """Startup/daily hook: back up at most once per day."""
    dest = Path(config.DATA_DIR) / "backups" / f"platform-{datetime.now(timezone.utc).date().isoformat()}.db"
    if dest.exists():
        return None
    return backup()


def export() -> dict:
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
    return {"path": str(path), "tables": {t: len(rows) for t, rows in dump.items()}}
