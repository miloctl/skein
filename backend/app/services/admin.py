"""Backups and export. pg_dump writes a consistent snapshot from a single
transaction even mid-write; exports are JSON snapshots for portability.

Ops note: backups default to the same volume as the live databases, so
losing that volume loses both together. Put them elsewhere: on OpenShift,
mount a second PVC and point SKEIN_BACKUP_DIR (or SKEIN_BACKUP_MIRROR) at
it, or run the Litestream sidecar (TODO.md, deploy entry) for streaming
off-cluster copies. On a host deployment, a cron rsync of the backups
directory does the same job.

Restore procedure (drilled in tests/test_admin_backup.py::
test_restore_drill_brings_both_databases_back):
1. Scale the deployment to zero replicas, so nothing writes during the load.
2. pg_restore --clean --if-exists both dumps of the SAME date:
   platform-<date>.dump and private-<date>.dump. Both, and matching: they
   reference each other's people.
3. Scale back to one replica. Boot applies any migrations newer than the
   backup; the activity chain verifies from its anchor on /health.
4. The anchor-log check (services/activity.py::check_anchor_log) now fires
   daily: lines anchored after the backup date point at rows the restore
   removed. That is correct signal — a restore IS a loss of history. Once
   the restore is confirmed as the cause, trim both anchor logs to the
   backup date (deploy/k8s/README.md, restore section). Never trim them
   for any other reason: trimming is exactly what an attacker would do.
"""

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict

from .. import config, db

log = logging.getLogger(__name__)

# Every real table is either exported (TABLES) or excluded here with its
# reason — test_admin_export.py::test_export_accounts_for_every_table walks
# the catalog and fails on a table in neither set, so a new migration
# cannot silently fall out of the export.
EXCLUDED = frozenset(
    {
        # derived: rebuilt from the exported rows on each record's next write
        "search_index",
        "embeddings",
        "schema_version",
        # durable operational delivery state; events carry public identifiers,
        # and a JSON export must not replay integrations on restore
        "extension_outbox",
        "extension_event_deliveries",
        "extension_event_attempts",
        "extension_command_receipts",
        "extension_review_invocations",
        # secret hashes must not travel in portable exports — recreate keys
        # after a restore
        "api_keys",
        # owner-scoped conversation state stays out of portable exports on
        # purpose; the pg_dump backups still carry it
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
    # who has dismissed which team announcement (009). Exported WITH
    # notifications, not excluded: a restore that carried the announcements but
    # not the dismissals would resurface every team notification the roster had
    # already read, on everybody's My Day, at once.
    "notification_reads",
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
    "health_snapshots",
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


_DATED_BACKUP = re.compile(r"\d{4}-\d{2}-\d{2}\.dump")


def _today() -> str:
    return db.today().isoformat()


_EXTENSION_STORES: dict[str, str] = {}


def set_extension_stores(stores: dict[str, str]) -> None:
    """Register the extension-owned schemas one composed app must back up.

    The composition root calls this so the service layer never imports the
    extension layer, the way agents/narrator.py registers into digest.
    """
    _EXTENSION_STORES.clear()
    _EXTENSION_STORES.update(stores)


def _pg_env() -> dict[str, str]:
    """PG* variables for pg_dump, parsed from SKEIN_DATABASE_URL.

    The URL is NOT passed on the command line: it carries the password, and
    argv is world-readable in `ps` for every process on the node.
    """
    info = conninfo_to_dict(config.DATABASE_URL)
    env = dict(os.environ)
    for key, var in (
        ("user", "PGUSER"),
        ("password", "PGPASSWORD"),
        ("host", "PGHOST"),
        ("port", "PGPORT"),
        ("dbname", "PGDATABASE"),
    ):
        value = info.get(key)
        if value is not None:
            env[var] = str(value)
    return env


def _backup_one(args: list[str], dest: Path, keep: int, prefix: str = "") -> None:
    """One pg_dump into dest, then prune that prefix's older copies.

    Custom format (-Fc), not plain SQL: pg_restore can then load it selectively
    and in dependency order, which a flat script cannot do.
    """
    binary = shutil.which("pg_dump")
    if binary is None:
        raise RuntimeError(
            "pg_dump is not installed. Install the PostgreSQL client tools"
            " that match the server major version."
        )
    tmp = dest.with_suffix(".dump.tmp")
    try:
        # check=True: a failed dump must not be renamed over a good backup.
        # absolute path from which(), never a bare name: the argv is fixed and
        # there is no shell, so the resolved binary is the only variable left.
        subprocess.run(  # noqa: S603 — fixed argv, no shell, values are not caller input
            [binary, "--format=custom", "--file", str(tmp), *args],
            env=_pg_env(),
            check=True,
            capture_output=True,
        )
        os.replace(tmp, dest)  # atomic: no truncated file can masquerade as a backup
    finally:
        tmp.unlink(missing_ok=True)
    # An explicit prefix keeps one store's retention off another's files: every
    # extension backup starts with "extension-", so the derived prefix would
    # prune all of them together and keep only the newest store's copies.
    prefix = prefix or dest.name.split("-", 1)[0]
    # The date suffix must match too. A bare "{prefix}-*" also selects the
    # files of a store whose name merely STARTS with this one, and the date
    # sorts before the longer name, so this store's own copies are the ones
    # that fall off the end of the list. Store names admit both "acme.data"
    # and "acme.data-archive" (extensions/registry.py::_IDENTIFIER).
    kept = sorted(
        path
        for path in dest.parent.glob(f"{prefix}-*.dump")
        if _DATED_BACKUP.fullmatch(path.name.removeprefix(f"{prefix}-"))
    )
    for old in kept[:-keep]:
        old.unlink()
    log.info("backup written: %s", dest)


def backup(*, keep: int = 14, actor: str | None = None) -> dict:
    """Every schema, or the backup is not one: the core schema holds the
    workspace, `private` holds the 1:1 notes — the one store that exists
    nowhere else (deliberately outside exports), so a backup that skips it
    silently loses the most personal data on the first disk loss. The private
    dump stays out of the off-box mirror — see the note below.

    actor is the person behind a MANUAL backup (the route passes it); the
    scheduled run passes none. The distinction is load-bearing twice: the
    ledger row is the provenance of a deliberate pre-change copy, and the
    field-guide `backup` predicate reads it — a scheduler run must not tie
    the card for anybody."""
    backups_dir = _backups_dir()
    dest = backups_dir / f"platform-{_today()}.dump"
    # Every schema this function dumps SEPARATELY must be excluded here, or it
    # travels in the core file too — and the core file is the one that goes
    # off-box, which is exactly what the private exclusion exists to prevent.
    excluded = [f"--exclude-schema={config.PRIVATE_SCHEMA}"]
    excluded += [f"--exclude-schema={schema}" for schema in sorted(_EXTENSION_STORES.values())]
    _backup_one(excluded, dest, keep)
    mirrored = _mirror(dest)

    private_path = None
    if _schema_has_rows(config.PRIVATE_SCHEMA):
        private_dest = backups_dir / f"private-{_today()}.dump"
        _backup_one([f"--schema={config.PRIVATE_SCHEMA}"], private_dest, keep)
        # deliberately NOT mirrored: SKEIN_BACKUP_MIRROR is an off-box copy,
        # and 1:1 notes stay on the box — the local backup adds no reader
        # (whoever runs the server can read the schema itself), the mirror
        # would. tests/test_privacy.py pins both halves.
        private_path = str(private_dest)

    # Extension stores are deliberately NOT mirrored: SKEIN_BACKUP_MIRROR is an
    # off-box copy, and core cannot know what a private package keeps in its
    # own schema. The deployment owns any off-box copy of it.
    extension_paths = []
    for name, schema in sorted(_EXTENSION_STORES.items()):
        if not _schema_has_rows(schema):
            continue
        prefix = f"extension-{name}"
        store_dest = backups_dir / f"{prefix}-{_today()}.dump"
        _backup_one([f"--schema={schema}"], store_dest, keep, prefix)
        extension_paths.append(str(store_dest))

    kept = len(sorted(backups_dir.glob("platform-*.dump")))
    if actor:
        db.log_activity(actor, "backup", dest.name)
    return {
        "path": str(dest),
        "private_path": private_path,
        "extension_paths": extension_paths,
        "kept": min(kept, keep),
        "mirrored": mirrored,
    }


def _schema_has_rows(schema: str) -> bool:
    """Whether a schema exists with at least one table.

    A schema is created ahead of its first row, so its EXISTENCE says
    nothing. A dump of an empty schema is a valid but pointless file, and it
    would make the first real backup look like a repeat."""
    row = db.query_one(
        "SELECT 1 AS present FROM information_schema.tables WHERE table_schema = ? LIMIT 1",
        (schema,),
    )
    return row is not None


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
        for old in sorted(mdir.glob(f"{prefix}-*.dump"))[:-30]:
            old.unlink()
        return str(mdir / dest.name)
    except Exception as exc:
        log.warning("backup mirror to %s failed: %s", mdir, exc)
        return None


def backup_if_stale() -> dict | None:
    """Daily hook, multi-process safe via the job_runs claim."""
    backups_dir = _backups_dir()
    done = (backups_dir / f"platform-{_today()}.dump").exists() and (
        # the private schema gets its first table with the first 1:1 note —
        # the day it does, the platform file existing must not skip the first
        # private backup
        not _schema_has_rows(config.PRIVATE_SCHEMA)
        or (backups_dir / f"private-{_today()}.dump").exists()
    )
    if done:
        return None
    if not db.claim_job("backup", _today()):
        # the claim commits before the copy, so a process killed between the
        # two burns the day's claim with no file to show — and job_health only
        # flags the backup stale at 48h, so without this line the lost day is
        # invisible. (A copy still in flight on another process logs this
        # once, then its file appears.)
        log.error(
            "backup claim for %s is taken but no backup file exists."
            " A previous run was interrupted. POST /api/admin/backup runs one now.",
            _today(),
        )
        return None
    return backup()


def export(*, keep: int = 14) -> dict:
    from .scope import CLASSIFIED, PRIVATE

    dump = {}
    for table in TABLES:
        try:
            # a private row leaves the box in this file otherwise. The export
            # is plaintext JSON on disk under DATA_DIR, kept `keep` deep, and
            # nothing downstream re-checks a column — NOT because _mirror
            # copies it: _mirror has one caller, backup(), on the .db file.
            # The cost of this line is that a restore from an export loses
            # every private row with no signal. Only `private` is dropped;
            # crew rows export in full, because an export is an operator
            # artifact and a crew is not a secret from the operator.
            where = f" WHERE visibility != '{PRIVATE}'" if table in CLASSIFIED else ""
            dump[table] = db.query(f"SELECT * FROM {table}{where}")  # noqa: S608 — TABLES constant, and `where` interpolates only scope.PRIVATE
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
