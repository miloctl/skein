"""Backups and export. SQLite's .backup API gives consistent copies even
mid-write; exports are JSON snapshots for portability.

Ops note: backups default to the same volume as the live DB. Copy them off-box
(host cron: `docker cp` / rsync of /data/backups) or set SKEIN_BACKUP_DIR to
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

# everything except: search_index/schema_version (derived), api_keys (secret
# hashes must not travel in portable exports — recreate keys after a restore),
# and chat_threads/chat_messages (owner-scoped transcripts stay out of
# portable exports on purpose; sqlite backups still carry them)
TABLES = (
    "users",
    "milestones",
    "tasks",
    "questions",
    "decisions",
    "standups",
    "events",
    "notes",
    "activity",
    "blockers",
    "intake_requests",
    "pending_changes",
    "usage_log",
    "engagements",
    "allocations",
    "lessons",
    "artifacts",
    "memories",
    "notifications",
    "commitments",
    "agent_authority",
    "feedback",
    "findings",
    "context_packs",
    "tool_usage",
    "forecast_snapshots",
    "job_runs",
    "job_outcomes",
    "finding_dispositions",
    "app_settings",
    "absences",
    "task_worklog",
)


def _backups_dir() -> Path:
    d = Path(os.getenv("SKEIN_BACKUP_DIR", "") or Path(config.DATA_DIR) / "backups")
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
    mirrored = _mirror(dest)
    return {"path": str(dest), "kept": min(len(existing), keep), "mirrored": mirrored}


def mirror_dir() -> Path | None:
    """SKEIN_BACKUP_MIRROR as a Path — or None when unset, or when this is not
    a production data dir. Only a production instance may touch the mirror: a
    test/dev run with a sandboxed SKEIN_DATA_DIR must never overwrite the
    off-box copy. Production shapes: the repo default (backend/data) or the
    container canonical /data (set by the Dockerfile)."""
    mirror = os.getenv("SKEIN_BACKUP_MIRROR", "")
    if not mirror:
        return None
    data_dir = Path(config.DATA_DIR).resolve()
    if data_dir not in ((Path(config.BASE_DIR) / "data").resolve(), Path("/data")):
        log.info("backup mirror skipped: non-default data dir (%s)", config.DATA_DIR)
        return None
    return Path(mirror)


def _mirror(dest: Path) -> str | None:
    """Copy the fresh backup to SKEIN_BACKUP_MIRROR (a mounted NAS/remote
    path). Off-box durability without extra tooling; rclone/rsync in a host
    cron remains the alternative for true remote targets."""
    mdir = mirror_dir()
    if mdir is None:
        return None
    try:
        import shutil

        mdir.mkdir(parents=True, exist_ok=True)
        tmp = mdir / (dest.name + ".tmp")
        shutil.copy2(dest, tmp)
        os.replace(tmp, mdir / dest.name)
        for old in sorted(mdir.glob("platform-*.db"))[:-30]:
            old.unlink()
        return str(mdir / dest.name)
    except Exception as exc:
        log.warning("backup mirror to %s failed: %s", mdir, exc)
        return None


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
            dump[table] = db.query(f"SELECT * FROM {table}")  # noqa: S608 — TABLES constant
        except Exception:
            dump[table] = []
    exports_dir = Path(config.DATA_DIR) / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    path = exports_dir / f"export-{db.now().replace(':', '')}.json"
    path.write_text(json.dumps(dump, indent=1))
    for old in sorted(exports_dir.glob("export-*.json"))[:-keep]:
        old.unlink()
    return {"path": str(path), "tables": {t: len(rows) for t, rows in dump.items()}}
