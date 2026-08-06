"""Backups and export. SQLite's .backup API gives consistent copies even
mid-write; exports are JSON snapshots for portability.

Ops note: backups default to the same volume as the live databases, so
losing that volume loses both together. Put them elsewhere: on OpenShift,
mount a second PVC and point SKEIN_BACKUP_DIR (or SKEIN_BACKUP_MIRROR) at
it, or run the Litestream sidecar (TODO.md, deploy entry) for streaming
off-cluster copies. On a host deployment, a cron rsync of the backups
directory does the same job.

Restore procedure (drilled in tests/test_admin_backup.py::
test_restore_drill_brings_both_databases_back):
1. Scale the deployment to zero replicas — SQLite must have no writer.
2. Copy platform-<date>.db over <data>/platform.db and private-<date>.db
   over <data>/private.db (oc cp into the PVC via a debug pod, or plain cp
   on a host). BOTH files, from the SAME date: they reference each other's
   people.
3. Scale back to one replica. Boot applies any migrations newer than the
   backup; the activity chain verifies from its anchor on /health.
"""

import json
import logging
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .. import config, db

log = logging.getLogger(__name__)

# Every real table is either exported (TABLES) or excluded here with its
# reason — test_admin_export.py::test_export_accounts_for_every_table walks
# sqlite_master and fails on a table in neither set, so a new migration
# cannot silently fall out of the export.
EXCLUDED = frozenset(
    {
        # derived: rebuilt from the exported rows on each record's next write
        # (search_index_* FTS shadow tables ride with search_index)
        "search_index",
        "search_ids",
        "embeddings",
        "schema_version",
        # secret hashes must not travel in portable exports — recreate keys
        # after a restore
        "api_keys",
        # owner-scoped conversation state stays out of portable exports on
        # purpose; sqlite backups still carry it
        "chat_threads",
        "chat_messages",
        "chat_folders",
        "sessions",
        "session_agents",
        "session_messages",
        "session_multi_agents",
    }
)
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
    "promises",
    "agent_authority",
    "feedback",
    "findings",
    "context_packs",
    "tool_usage",
    # exported with the other spend tables, not excluded with chat_messages:
    # a trace carries slugs, timings and token counts, never message text
    "flock_traces",
    "forecast_snapshots",
    "job_runs",
    "job_outcomes",
    "finding_dispositions",
    "app_settings",
    "absences",
    "task_worklog",
    "feature_unlocks",
    "mention_log",
    "crews",
    "crew_members",
)


def _backups_dir() -> Path:
    d = Path(os.getenv("SKEIN_BACKUP_DIR", "") or Path(config.DATA_DIR) / "backups")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _backup_one(src: sqlite3.Connection, dest: Path, keep: int) -> None:
    tmp = dest.with_suffix(".db.tmp")
    try:
        target = sqlite3.connect(tmp)
        with target:
            src.backup(target)
        target.close()
        os.replace(tmp, dest)  # atomic: no truncated file can masquerade as a backup
    finally:
        src.close()
        tmp.unlink(missing_ok=True)
    prefix = dest.name.split("-", 1)[0]
    for old in sorted(dest.parent.glob(f"{prefix}-*.db"))[:-keep]:
        old.unlink()
    log.info("backup written: %s", dest)


def backup(*, keep: int = 14) -> dict:
    """Both databases, or the backup is not one: platform.db holds the
    workspace, private.db holds the 1:1 notes — the one store that exists
    nowhere else (deliberately outside exports), so a backup that skips it
    silently loses the most personal data on the first disk loss. The
    private backup stays out of the off-box mirror — see the note below."""
    backups_dir = _backups_dir()
    dest = backups_dir / f"platform-{_today()}.db"
    _backup_one(db.connect(), dest, keep)
    mirrored = _mirror(dest)

    private_path = None
    if Path(config.PRIVATE_DB_PATH).exists():
        private_dest = backups_dir / f"private-{_today()}.db"
        _backup_one(sqlite3.connect(config.PRIVATE_DB_PATH), private_dest, keep)
        # deliberately NOT mirrored: SKEIN_BACKUP_MIRROR is an off-box copy,
        # and 1:1 notes stay on the box — the local backup adds no reader
        # (whoever runs the server can read private.db itself), the mirror
        # would. tests/test_privacy.py pins both halves.
        private_path = str(private_dest)

    kept = len(sorted(backups_dir.glob("platform-*.db")))
    return {
        "path": str(dest),
        "private_path": private_path,
        "kept": min(kept, keep),
        "mirrored": mirrored,
    }


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
        prefix = dest.name.split("-", 1)[0]
        for old in sorted(mdir.glob(f"{prefix}-*.db"))[:-30]:
            old.unlink()
        return str(mdir / dest.name)
    except Exception as exc:
        log.warning("backup mirror to %s failed: %s", mdir, exc)
        return None


def backup_if_stale() -> dict | None:
    """Daily hook, multi-process safe via the job_runs claim."""
    backups_dir = _backups_dir()
    done = (backups_dir / f"platform-{_today()}.db").exists() and (
        # private.db appears with the first 1:1 note — the day it does, the
        # platform file existing must not skip the first private backup
        not Path(config.PRIVATE_DB_PATH).exists()
        or (backups_dir / f"private-{_today()}.db").exists()
    )
    if done:
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
            # LOGGED, not swallowed. One table left empty is indistinguishable
            # from one table that is empty, and the completeness test only
            # checks the key exists — so an export missing crew_members
            # restores a workspace where nobody can read anything crew-scoped,
            # and nothing anywhere said so.
            log.exception("export skipped table %s — the dump is INCOMPLETE", table)
            dump[table] = []
    exports_dir = Path(config.DATA_DIR) / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    path = exports_dir / f"export-{db.now().replace(':', '')}.json"
    path.write_text(json.dumps(dump, indent=1))
    for old in sorted(exports_dir.glob("export-*.json"))[:-keep]:
        old.unlink()
    return {"path": str(path), "tables": {t: len(rows) for t, rows in dump.items()}}
