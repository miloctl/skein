"""Thin SQLite layer. One connection per operation keeps this thread-safe
under uvicorn without a pool; db.transaction() gives compound writes one
shared connection instead. Schema lives in ../migrations/*.sql."""

import contextlib
import hashlib
import logging
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

from .config import DB_PATH

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

log = logging.getLogger("skein")

_ambient: ContextVar[sqlite3.Connection | None] = ContextVar("skein_txn", default=None)
_on_commit: ContextVar[list[Callable[[], None]] | None] = ContextVar(
    "skein_txn_commits", default=None
)


def on_commit(fn: Callable[[], None]) -> bool:
    """Queue fn to run after the ambient transaction commits; a rollback
    drops it. Returns False when no transaction is active — the caller runs
    the work inline. For side effects that must not hold the write lock
    (search's embedding HTTP call) and must not survive a rolled-back write.
    A raising callback is logged and swallowed — the write it followed
    committed, so the caller must never see its failure."""
    callbacks = _on_commit.get()
    if callbacks is None:
        return False
    callbacks.append(fn)
    return True


class NotFound(ValueError):
    """Entity-lookup failure. Subclasses ValueError so every existing catch
    still works; the API layer maps it to 404 instead of 400 — one rule for
    the whole surface instead of per-route guesswork."""


class TerminalReject(ValueError):
    """A service refusal that re-approval can never satisfy — a permanent
    policy block, not a transient failure. Subclasses ValueError so the direct
    write path is unchanged (still a 400); review.approve_change catches it
    and settles the proposal as rejected, instead of resetting it to pending
    where it would boomerang forever."""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def validate_date(label: str, value: str, allow_clear: bool = True) -> None:
    """Shared YYYY-MM-DD guard for every service that stores a date. Empty
    passes; '-' (the clear sentinel) passes only where an update path maps it
    to NULL (allow_clear) — on creates it would be STORED and sort before
    every real date. Dates are compared as strings and fed to the ICS feed,
    so a malformed one corrupts every due-soon surface downstream."""
    if not value:
        return
    if value == "-":
        if allow_clear:
            return
        raise ValueError(f"{label} must be YYYY-MM-DD ('-' only clears an existing value)")
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        raise ValueError(f"{label} must be YYYY-MM-DD")
    from datetime import date

    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a real date (YYYY-MM-DD)") from exc


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _statements(sql: str) -> list[str]:
    """Split a migration into statements. Convention: migrations contain no
    semicolons inside string literals, trigger bodies, OR COMMENTS — a
    semicolon in comment prose splits mid-comment and the tail half is a
    syntax error at startup (bit migration 034 during review)."""
    return [s.strip() for s in sql.split(";") if s.strip()]


def init_db() -> None:
    """Apply pending migrations in filename order; track in schema_version.

    Each migration runs inside ONE transaction (BEGIN IMMEDIATE) together with
    its schema_version insert, so a crash mid-migration rolls back cleanly and
    concurrent workers serialize on the write lock instead of double-applying.
    """
    conn = connect()
    conn.isolation_level = None  # sqlite3's implicit BEGIN breaks BEGIN IMMEDIATE below
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version"
            " (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.execute("BEGIN IMMEDIATE")
            try:
                already = conn.execute(
                    "SELECT 1 FROM schema_version WHERE version = ?", (path.name,)
                ).fetchone()
                if already:
                    conn.execute("COMMIT")
                    continue
                for stmt in _statements(path.read_text()):
                    conn.execute(stmt)
                conn.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                    (path.name, now()),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
    finally:
        conn.close()


@contextmanager
def transaction() -> Iterator[None]:
    """Every db.* call inside the block shares one connection and commits
    atomically at exit; any exception rolls the whole block back. Nested
    blocks join the outer transaction. Context-local, so concurrent requests
    (threads or tasks) never share a transaction."""
    if _ambient.get() is not None:
        yield
        return
    conn = connect()
    conn.isolation_level = None  # sqlite3's implicit BEGIN breaks BEGIN IMMEDIATE below
    token = _ambient.set(conn)
    callbacks: list[Callable[[], None]] = []
    cb_token = _on_commit.set(callbacks)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        _on_commit.reset(cb_token)
        _ambient.reset(token)
        conn.close()
    # reached only after a successful COMMIT (an exception propagates past
    # here), with the connection closed — a callback never holds the lock.
    # Isolated per callback: the write already committed, so a raising
    # callback must not turn a successful write into a 500, and must not
    # starve the callbacks queued after it.
    for cb in callbacks:
        try:
            cb()
        except Exception:
            log.exception("on_commit callback failed")


def pending_migrations() -> list[str]:
    """Migration files not yet recorded in schema_version (all of them when
    the database doesn't exist yet). Lets long-lived side processes (MCP)
    refuse to start instead of racing the API server to apply schema."""
    names = [p.name for p in sorted(MIGRATIONS_DIR.glob("*.sql"))]
    if not Path(DB_PATH).exists():
        return names
    conn = connect()
    try:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
        ).fetchone()
        if not has_table:
            return names
        applied = {
            r["version"] for r in conn.execute("SELECT version FROM schema_version").fetchall()
        }
    finally:
        conn.close()
    return [n for n in names if n not in applied]


def query(sql: str, params: tuple = ()) -> list[dict]:
    ambient = _ambient.get()
    if ambient is not None:
        return [dict(r) for r in ambient.execute(sql, params).fetchall()]
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def query_one(sql: str, params: tuple = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def query_row(sql: str, params: tuple = ()) -> dict:
    """query_one for queries guaranteed a row (aggregates, just-written ids)."""
    row = query_one(sql, params)
    if row is None:
        raise LookupError(f"expected a row from: {sql}")
    return row


def execute(sql: str, params: tuple = ()) -> int:
    """Run a write statement, return lastrowid."""
    ambient = _ambient.get()
    if ambient is not None:
        return ambient.execute(sql, params).lastrowid or 0
    with connect() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid or 0


def execute_rowcount(sql: str, params: tuple = ()) -> int:
    """Run a write statement, return the number of affected rows (for
    compare-and-swap guards like `... WHERE status = 'pending'`)."""
    ambient = _ambient.get()
    if ambient is not None:
        return ambient.execute(sql, params).rowcount
    with connect() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount


def claim_job(job: str, run_key: str) -> bool:
    """CAS-style once-only claim for scheduled jobs (digest, flush, backup) so
    accidental multi-worker deployments can't double-run them."""
    return (
        execute_rowcount(
            "INSERT OR IGNORE INTO job_runs (job, run_key, created_at) VALUES (?, ?, ?)",
            (job, run_key, now()),
        )
        == 1
    )


ACTIVITY_DOMAIN = b"skein-activity/v1"
GENESIS_PREV = "0" * 64


def activity_hash(
    seq: int, created_at: str, actor: str, action: str, detail: str, prev_hex: str
) -> str:
    """SHA-256 over one ledger row and its predecessor's digest.

    Field ORDER IS FIXED — changing it, or the domain string, invalidates
    every chain already written. Each part is length-prefixed rather than
    separated, so no field's content can imitate a boundary.

    created_at is hashed exactly as stored. now() emits second precision. A
    "harmless" bump to microseconds would hash one preimage and verify
    another, breaking every chain from that deploy onward.
    """
    h = hashlib.sha256()
    for part in (
        ACTIVITY_DOMAIN,
        str(seq).encode(),
        created_at.encode(),
        actor.encode(),
        action.encode(),
        detail.encode(),
        prev_hex.encode(),
    ):
        h.update(len(part).to_bytes(4, "big"))
        h.update(part)
    return h.hexdigest()


def _append_activity(
    conn: sqlite3.Connection, actor: str, action: str, detail: str, created_at: str
) -> None:
    """Read the chain tail, link to it, insert. Caller holds the write lock."""
    tail = conn.execute(
        "SELECT seq, hash FROM activity WHERE seq IS NOT NULL ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    seq = (tail["seq"] if tail else 0) + 1
    prev = tail["hash"] if tail else None
    conn.execute(
        "INSERT INTO activity (actor, action, detail, created_at, seq, hash, prev_hash)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            actor,
            action,
            detail,
            created_at,
            seq,
            activity_hash(seq, created_at, actor, action, detail, prev or GENESIS_PREV),
            prev,
        ),
    )


def log_activity(actor: str, action: str, detail: str = "") -> None:
    """Append to the provenance ledger, chained to the row before it.

    Inside an ambient transaction the enclosing BEGIN IMMEDIATE already
    serializes the read-tail-then-insert, and a failure rolls back the caller's
    whole write — correct, and loud. Standalone the append takes its own
    immediate transaction; if that fails, the row is recorded UNCHAINED rather
    than raising into a caller's write.

    ONE attempt, not several. BEGIN IMMEDIATE already waits out busy_timeout,
    so every extra retry adds another full timeout to a request that is already
    stuck — a retry budget here buys nothing and multiplies the worst-case
    hang. The fallback is logged: an unchained row is a hole in the ledger, and
    a hole nobody was told about is the version that matters.
    """
    created_at = now()
    ambient = _ambient.get()
    if ambient is not None:
        _append_activity(ambient, actor, action, detail, created_at)
        return
    conn = connect()
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        _append_activity(conn, actor, action, detail, created_at)
        conn.execute("COMMIT")
        return
    except sqlite3.DatabaseError as exc:
        with contextlib.suppress(sqlite3.DatabaseError):
            conn.execute("ROLLBACK")
        log.warning("activity chain append failed (%s: %s) — recording unchained", action, exc)
    finally:
        conn.close()
    # The fallback opens a NEW connection with the same busy timeout, so a
    # lock held past it raises here too — straight into a caller that had
    # already committed its business write, losing the ledger row AND
    # 500ing a write that actually happened. The docstring promised this
    # path never raises; now it does not. A lost row still shows up: the
    # unchained count and the chain marks are what report it.
    try:
        execute(
            "INSERT INTO activity (actor, action, detail, created_at) VALUES (?, ?, ?, ?)",
            (actor, action, detail, created_at),
        )
    except sqlite3.DatabaseError as exc:
        log.error("activity row LOST (%s: %s) — the write it describes did commit", action, exc)
