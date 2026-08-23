"""Database recovery dumps and portable JSON exports.

One local database dump contains the public, private, and opted-in extension
schemas in one PostgreSQL snapshot. A configured mirror receives a separate
core public-schema dump that needs database-grade protection. Artifact bytes
stay on the data volume.

The restore drill pins the archive and artifact mechanics:
1. Scale the backend to zero.
2. Restore the matching artifact-volume copy at the same SKEIN_DATA_DIR.
3. Restore `database-<date>-<backup-id>.dump` into a clean database with `--no-owner`.
4. Start the backend so newer migrations apply.
5. Require the restored verified anchor in one retained log. Then trim only
   later anchor lines.
"""

import fcntl
import json
import logging
import os
import re
import shutil
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from uuid import uuid4

from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.errors import LockNotAvailable

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
        # proposal payloads can contain private target bodies and extension
        # previews. The portable export cannot reconstruct their governing tier.
        "pending_changes",
        # rendered delivery rows can quote private source content but carry no
        # visibility column of their own
        "notifications",
        "notification_reads",
        # arbitrary feedback can contain chat input, model output, and a human
        # correction. The database backup keeps this evaluation corpus.
        "feedback",
        # deployment state and scheduler diagnostics are not portable work. They
        # can also carry model names, integrity marks, exception text, and paths.
        "app_settings",
        "job_runs",
        "job_outcomes",
        # the immutable ledger carries historical settings and operational
        # details. A projection would no longer be a verifiable chain.
        "activity",
        # owner-scoped telemetry and discovery state do not become an
        # administrator surveillance export
        "usage_log",
        "tool_usage",
        "flock_traces",
        "feature_unlocks",
        # materialized context bodies can retain source text after its row changes
        "context_packs",
        # findings can copy excluded proposal, chat, scheduler, and budget data
        # into their message and receipt. Dispositions identify those rows.
        "findings",
        "finding_dispositions",
        # mention delivery/dedupe rows can point at private entities but have no
        # visibility column of their own
        "mention_log",
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
    "blockers",
    "intake_requests",
    "engagements",
    "allocations",
    "lessons",
    "artifacts",
    "memories",
    "promises",
    "agent_authority",
    "forecast_snapshots",
    "health_snapshots",
    "absences",
    "task_worklog",
    "crews",
    "crew_members",
)

_BACKUP_LOCK = Lock()
_BACKUP_LOCK_WAIT_SECONDS = 5
_BACKUP_RUN_TIMEOUT_SECONDS = 300
# ponytail: export needs only a process lock in the supported one-worker shape.
# Backup also takes a filesystem lock because its files are recovery state.
_EXPORT_LOCK = Lock()
_EXPORT_LOCK_WAIT_SECONDS = 5
_EXPORT_ID_BATCH = 10_000
MAX_EXPORT_DOWNLOAD_BYTES = 256 * 1024 * 1024


class ExportTooLarge(RuntimeError):
    pass


class _ExportWriter:
    """Text writer that refuses before one UTF-8 fragment crosses the cap."""

    def __init__(self, file, max_bytes: int) -> None:
        self.file = file
        self.max_bytes = max(0, int(max_bytes))
        self.written = 0

    def write(self, text: str) -> int:
        size = len(text.encode("utf-8"))
        if self.max_bytes and self.written + size > self.max_bytes:
            raise ExportTooLarge("portable export exceeds the browser download limit")
        result = self.file.write(text)
        self.written += size
        return result


@contextmanager
def _held_export_lock():
    if not _EXPORT_LOCK.acquire(timeout=_EXPORT_LOCK_WAIT_SECONDS):
        raise LockNotAvailable("Another portable export is still running.")
    try:
        yield
    finally:
        _EXPORT_LOCK.release()


@contextmanager
def _export_writer(path: Path, max_bytes: int):
    with path.open("w", encoding="utf-8") as file:
        yield _ExportWriter(file, max_bytes)


def _backups_dir() -> Path:
    directory = Path(os.getenv("SKEIN_BACKUP_DIR", "") or Path(config.DATA_DIR) / "backups")
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    return directory


_DATED_BACKUP = re.compile(r"\d{4}-\d{2}-\d{2}(?:-[0-9]{6}-[0-9a-f]{8})?\.dump")
_BACKUP_FILE = re.compile(r"(.+)-(\d{4}-\d{2}-\d{2}(?:-[0-9]{6}-[0-9a-f]{8})?)\.(?:dump|db)")


def _harden_retained_backups(directory: Path, keep: int, *, current: str = "") -> int:
    """Harden and retain logical recovery units across old and new formats."""
    for pattern in ("*.dump.tmp", "*.dump.*.tmp"):
        for stale in directory.glob(pattern):
            stale.unlink(missing_ok=True)
    groups: dict[str, list[Path]] = {}
    for path in directory.iterdir():
        if not path.is_file() or path.suffix not in (".dump", ".db"):
            continue
        path.chmod(0o600)
        match = _BACKUP_FILE.fullmatch(path.name)
        if match:
            # Unknown extension prefixes are deployment-owned archives. Never
            # prune them after an extension was removed from the registry. The
            # core database/platform/private companions share one recovery id.
            if match.group(1).startswith("extension-"):
                continue
            groups.setdefault(match.group(2), []).append(path)
    keep = max(1, int(keep))
    retained = set(sorted(groups)[-keep:])
    if current:
        retained.add(current)
        for older in sorted(retained - {current})[: max(0, len(retained) - keep)]:
            retained.remove(older)
    for key, paths in groups.items():
        if key not in retained:
            for old in paths:
                old.unlink()
    return len(retained)


def _today() -> str:
    return db.today().isoformat()


# EVERY extension schema, opted out ones included — see set_extension_stores.
_EXTENSION_STORES: dict[str, str] = {}
# The subset included in the local database recovery unit.
_BACKED_UP_STORES: set[str] = set()


def set_extension_stores(stores: dict[str, str], dumped: set[str]) -> None:
    """Register the extension-owned schemas, and which of them to dump.

    `stores` is EVERY store, so the public mirror excludes each extension
    schema. `dumped` is the subset with include_in_backup=True, which joins the
    local database recovery unit. Registering only that subset can copy an
    unknown extension schema into the public mirror.

    The composition root calls this so the service layer never imports the
    extension layer, the way agents/narrator.py registers into digest.
    """
    _EXTENSION_STORES.clear()
    _EXTENSION_STORES.update(stores)
    _BACKED_UP_STORES.clear()
    _BACKED_UP_STORES.update(dumped)


def _pg_conninfo() -> str:
    """The complete libpq contract without credentials in process argv."""
    info = conninfo_to_dict(config.DATABASE_URL)
    info.pop("password", None)
    info.pop("sslpassword", None)
    return make_conninfo(**{key: str(value) for key, value in info.items() if value is not None})


def _pg_env() -> dict[str, str]:
    """Credential environment for PostgreSQL client processes."""
    info = conninfo_to_dict(config.DATABASE_URL)
    env = dict(os.environ)
    for key, var in (
        ("user", "PGUSER"),
        ("password", "PGPASSWORD"),
        ("sslpassword", "PGSSLPASSWORD"),
        ("host", "PGHOST"),
        ("port", "PGPORT"),
        ("dbname", "PGDATABASE"),
    ):
        value = info.get(key)
        if value is not None:
            env[var] = str(value)
    return env


def _sync_file(path: Path) -> None:
    with path.open("rb") as file:
        os.fsync(file.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _backup_one(args: list[str], dest: Path, prefix: str = "") -> None:
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
    prefix = prefix or dest.name.split("-", 1)[0]
    for stale in dest.parent.glob(f"{prefix}-*.dump.*.tmp"):
        stale.unlink(missing_ok=True)
    tmp = dest.with_name(f"{dest.name}.{uuid4().hex}.tmp")
    descriptor = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        # The sanitized conninfo keeps TLS and routing options without putting a
        # password in argv. A timeout kills the client before it can hold every
        # later backup behind the workflow lock.
        try:
            subprocess.run(  # noqa: S603 — fixed argv, no shell, values are not caller input
                [
                    binary,
                    "--dbname",
                    _pg_conninfo(),
                    "--format=custom",
                    "--file",
                    str(tmp),
                    *args,
                ],
                env=_pg_env(),
                check=True,
                capture_output=True,
                timeout=_BACKUP_RUN_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise LockNotAvailable("The database backup command timed out.") from exc
        tmp.chmod(0o600)
        _sync_file(tmp)
        os.replace(tmp, dest)
        dest.chmod(0o600)
        _sync_file(dest)
    finally:
        tmp.unlink(missing_ok=True)
    log.info("backup written: %s", dest)


def backup(*, keep: int = 14, actor: str | None = None) -> dict:
    """Serialize the complete dated backup workflow across threads and workers."""
    if not _BACKUP_LOCK.acquire(timeout=_BACKUP_LOCK_WAIT_SECONDS):
        raise LockNotAvailable("Another database backup is still running.")
    try:
        lock_path = _backups_dir() / ".backup.lock"
        with lock_path.open("a+") as lock:
            deadline = time.monotonic() + _BACKUP_LOCK_WAIT_SECONDS
            while True:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise LockNotAvailable("Another database backup is still running.") from exc
                    time.sleep(0.1)
            try:
                return _backup(keep=keep, actor=actor)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    finally:
        _BACKUP_LOCK.release()


def _backup(*, keep: int, actor: str | None) -> dict:
    """Write one local database recovery unit, plus an optional public mirror.

    The local dump contains public, private, and each opted-in extension schema
    in one PostgreSQL snapshot. The mirror receives a separate core-schema dump.
    Artifact bytes remain on the data volume and need their own storage backup.

    actor names a manual backup. Scheduled runs pass none, so their routine copy
    does not tie a person's field-guide card."""
    from . import private_notes

    keep = max(1, int(keep))
    backups_dir = _backups_dir()
    _harden_retained_backups(backups_dir, keep)
    # Include the schema unconditionally. Creating it before pg_dump closes the
    # race where the first private note appears after schema selection but before
    # the dump snapshot.
    private_notes._ready()
    schemas = ["public", config.PRIVATE_SCHEMA]
    schemas.extend(
        schema
        for name, schema in sorted(_EXTENSION_STORES.items())
        if name in _BACKED_UP_STORES and _schema_has_rows(schema)
    )
    backup_id = f"{_today()}-{db.now()[11:19].replace(':', '')}-{uuid4().hex[:8]}"
    database_dest = backups_dir / f"database-{backup_id}.dump"
    _backup_one([f"--schema={schema}" for schema in schemas], database_dest, "database")

    mirror_status, mirror = _mirror_target()
    mirrored = None
    if mirror is not None:
        platform_dest = backups_dir / f"platform-{backup_id}.dump"
        try:
            # Positive selection: an unregistered or removed extension schema
            # must not ride the public mirror because core does not know its name.
            _backup_one(["--schema=public"], platform_dest, "platform")
            mirrored = _mirror(platform_dest, mirror)
        except Exception:
            log.exception("public platform mirror dump failed")
        mirror_status = "written" if mirrored else "unavailable"

    kept = _harden_retained_backups(backups_dir, keep, current=backup_id)
    if actor:
        db.log_activity(actor, "backup", database_dest.name)
    status = "partial" if mirror_status == "unavailable" else "ok"
    return {
        "status": status,
        "backup_id": backup_id,
        "database_path": str(database_dest),
        # Legacy aliases remain until callers move to the explicit fields.
        "path": str(database_dest),
        "private_path": None,
        "extension_paths": [],
        "kept": kept,
        "mirror_status": mirror_status,
        "mirror_scope": "public_schema",
        "mirrored_platform_path": mirrored,
        "mirrored": mirrored,
        "artifacts_included": False,
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


def _separate_mirror_filesystem(target: Path) -> bool:
    return target.stat().st_dev != _backups_dir().stat().st_dev


def _mirror_target() -> tuple[str, Path | None]:
    configured = os.getenv("SKEIN_BACKUP_MIRROR", "").strip()
    if not configured:
        return "not_configured", None
    target = Path(configured)
    if not target.is_dir():
        log.warning("backup mirror is unavailable: %s", target)
        return "unavailable", None
    try:
        if target.samefile(_backups_dir()) or not _separate_mirror_filesystem(target):
            log.warning("backup mirror is not on a separate filesystem: %s", target)
            return "unavailable", None
    except OSError:
        log.warning("backup mirror cannot be inspected: %s", target)
        return "unavailable", None
    return "available", target


def mirror_dir() -> Path | None:
    """The existing configured mirror directory, or None."""
    _, target = _mirror_target()
    return target


def _mirror(dest: Path, mdir: Path) -> str | None:
    """Copy one core public-schema dump to an existing configured mirror."""
    _harden_retained_backups(mdir, 30)
    prefix = dest.name.split("-", 1)[0]
    for stale in mdir.glob(f"{prefix}-*.dump.*.tmp"):
        stale.unlink(missing_ok=True)
    tmp = mdir / f"{dest.name}.{uuid4().hex}.tmp"
    mirrored = mdir / dest.name
    try:
        copy = shutil.which("cp")
        if copy is None:
            raise RuntimeError("cp is not installed. Install the core file utilities.")
        subprocess.run(  # noqa: S603 — fixed argv, no shell
            [copy, "-p", "--", str(dest), str(tmp)],
            check=True,
            capture_output=True,
            timeout=_BACKUP_RUN_TIMEOUT_SECONDS,
        )
        tmp.chmod(0o600)
        _sync_file(tmp)
        os.replace(tmp, mirrored)
        mirrored.chmod(0o600)
        _sync_file(mirrored)
        match = _BACKUP_FILE.fullmatch(dest.name)
        _harden_retained_backups(mdir, 30, current=match.group(2) if match else "")
        return str(mirrored)
    except Exception as exc:
        log.warning("backup mirror to %s failed: %s", mdir, exc)
        return None
    finally:
        tmp.unlink(missing_ok=True)


def backup_if_stale() -> dict:
    """Daily hook, multi-process safe via the job_runs claim."""
    backups_dir = _backups_dir()
    database_done = any(backups_dir.glob(f"database-{_today()}*.dump"))
    mirror_status, mirror = _mirror_target()
    mirror_done = mirror_status == "not_configured" or bool(
        mirror and any(mirror.glob(f"platform-{_today()}*.dump"))
    )
    if database_done and mirror_done:
        return {"status": "noop"}
    if not db.claim_job("backup", _today()):
        reason = (
            "The daily backup claim is taken, but the database or configured"
            " mirror copy is incomplete. Run POST /api/admin/backup."
        )
        log.error(reason)
        return {"status": "error", "reason": reason}
    return backup()


def _make_export(*, keep: int, actor: str, open_file: bool, max_bytes: int = 0):
    from . import scope, work

    with _held_export_lock():
        exports_dir = Path(config.DATA_DIR) / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        exports_dir.chmod(0o700)
        for retained in exports_dir.glob("export-*.json"):
            retained.chmod(0o600)
        for stale in exports_dir.glob("export-*.json.tmp"):
            stale.unlink(missing_ok=True)
        stamp = db.now().replace(":", "")
        path = exports_dir / f"export-{stamp}-{uuid4().hex[:8]}.json"
        tmp = path.with_suffix(".json.tmp")
        descriptor = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        counts = {}
        try:
            with _export_writer(tmp, max_bytes) as fh, db.read_transaction():
                fh.write("{\n")
                portable_viewer = scope.Viewer("", False)
                portable_viewer.crew_ids = [row["id"] for row in db.query("SELECT id FROM crews")]

                def visible_ids(table: str, ids: set[int]) -> set[int]:
                    found: set[int] = set()
                    ordered = sorted(ids)
                    visible, params = scope.visible_filter(portable_viewer, table)
                    for offset in range(0, len(ordered), _EXPORT_ID_BATCH):
                        batch = ordered[offset : offset + _EXPORT_ID_BATCH]
                        marks = ",".join("?" for _ in batch)
                        found.update(
                            int(row["id"])
                            for row in db.query(
                                f"SELECT id FROM {table} WHERE id IN ({marks}) AND {visible}",  # noqa: S608 — table and marks are closed, scope emits bound SQL
                                (*batch, *params),
                            )
                        )
                    return found

                for index, table in enumerate(TABLES):
                    params: tuple
                    if table == "allocations":
                        sql = (
                            "SELECT allocation.* FROM allocations allocation"
                            " JOIN engagements engagement"
                            " ON engagement.id = allocation.engagement_id"
                            " WHERE engagement.visibility != ?"
                        )
                        params = (scope.PRIVATE,)
                    else:
                        where = (
                            f" WHERE visibility != '{scope.PRIVATE}'"
                            if table in scope.CLASSIFIED
                            else ""
                        )
                        sql = f"SELECT * FROM {table}{where}"  # noqa: S608 — closed table set and private literal
                        params = ()
                    if index:
                        fh.write(",\n")
                    json.dump(table, fh)
                    fh.write(": [")
                    count = 0
                    try:
                        for rows in db.query_batches(sql, params, batch_size=_EXPORT_ID_BATCH):
                            if table == "tasks":
                                rows = work.redact_task_relationships(rows, portable_viewer)
                            if table in ("tasks", "questions"):
                                for row in rows:
                                    row["source_finding_id"] = None
                            if table != "allocations" and rows and "engagement_id" in rows[0]:
                                parents = visible_ids(
                                    "engagements",
                                    {
                                        int(row["engagement_id"])
                                        for row in rows
                                        if row.get("engagement_id")
                                    },
                                )
                                for row in rows:
                                    if int(row.get("engagement_id") or 0) not in parents:
                                        row["engagement_id"] = None
                            if table in ("blockers", "task_worklog"):
                                visible_tasks = visible_ids(
                                    "tasks",
                                    {int(row["task_id"]) for row in rows if row.get("task_id")},
                                )
                                for row in rows:
                                    if int(row.get("task_id") or 0) not in visible_tasks:
                                        row["task_id"] = None
                            if table == "memories":
                                for row in rows:
                                    row["thread_id"] = ""
                                    row["source_kind"] = ""
                                    row["source_id"] = ""
                            if table == "artifacts":
                                rows = [
                                    {key: value for key, value in row.items() if key != "path"}
                                    for row in rows
                                ]
                            for row in rows:
                                if count:
                                    fh.write(",")
                                json.dump(row, fh, ensure_ascii=False, separators=(",", ":"))
                                count += 1
                    except Exception:
                        # An unreadable table is not an empty table. Re-raise the
                        # driver error so retryable load keeps its classification.
                        log.exception("export failed while reading table %s", table)
                        raise
                    fh.write("]")
                    counts[table] = count
                fh.write("\n}\n")
            tmp.chmod(0o600)
            _sync_file(tmp)
            os.replace(tmp, path)
            path.chmod(0o600)
            _sync_file(path)
        finally:
            tmp.unlink(missing_ok=True)
        opened = path.open("rb") if open_file else None
        if opened is not None:
            # Linux keeps the open inode readable while removing transient disk
            # use. Remove it before retention counts the files it must keep.
            path.unlink(missing_ok=True)
        keep = max(1, int(keep))
        files = sorted(exports_dir.glob("export-*.json"))
        excess = max(0, len(files) - keep)
        for old in [item for item in files if item != path][:excess]:
            old.unlink(missing_ok=True)
        if actor:
            db.log_activity(actor, "export", path.name)
        return path, counts, opened


def export(*, keep: int = 1, actor: str = "") -> dict:
    """Create an export and keep the legacy metadata response contract."""
    path, counts, _ = _make_export(keep=keep, actor=actor, open_file=False)
    return {"path": str(path), "tables": counts}


def export_download(*, keep: int = 1, actor: str = ""):
    """Create a bounded export and pin it before retention can unlink its path."""
    path, _, opened = _make_export(
        keep=keep,
        actor=actor,
        open_file=True,
        max_bytes=MAX_EXPORT_DOWNLOAD_BYTES,
    )
    return opened, path.name
