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
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from . import config
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


def today() -> date:
    """The team's current calendar day (config.SKEIN_TZ), for every surface a
    human reads as "today": due-soon, week rituals, digests, month boundaries.

    NOT interchangeable with now()[:10]. That is the UTC day, and east of UTC
    they are a different date for part of every day — a due-today list built
    from one and a digest built from the other disagree, in public.

    Stored timestamps stay UTC (now()), so a row written at 21:00 in New York
    carries the next UTC day. Comparisons that ask "which team-day did this
    happen on" must convert, not slice — see local_day()."""
    return datetime.now(config.TZ).date()


def _local_midnight(d: date) -> datetime:
    """The first instant of team-day d, as an aware UTC datetime.

    "First instant", not "midnight": in zones whose DST transition lands at
    00:00 (America/Havana, America/Santiago, Asia/Beirut) local midnight does
    not exist on the spring date, and the day begins at 01:00. Converting to
    UTC resolves it to that real instant, which is the bound every caller
    wants — a window anchored to a wall time that never happened would either
    start an hour early or drop the hour entirely."""
    return datetime.combine(d, time.min, tzinfo=config.TZ).astimezone(UTC)


def local_midnight_utc(d: date) -> str:
    """The bound for "since the start of team-day d" against a column written
    by now(). A query that filters created_at (UTC) with a bare local date
    compares a timestamp against a 10-character string: it works
    lexicographically but anchors the window to UTC midnight, which is a
    different moment than local midnight everywhere except UTC. Use today()
    only against date columns, which carry no zone at all.

    Matches now()'s shape — offset-aware, seconds. Never use it against
    events.starts_at, which is stored NAIVE (services/schedule.py::_canon):
    an event at exactly local midnight sorts BEFORE this string, because the
    shorter value is a prefix of it, and drops out of its own day."""
    return _local_midnight(d).isoformat(timespec="seconds")


def local_day(ts: str) -> str:
    """The team-day a stored timestamp falls on, as YYYY-MM-DD. Slicing
    ts[:10] answers a different question — the UTC day — and buckets evening
    work under tomorrow for any zone behind UTC. Use this whenever a bucket
    key is compared against a today()-derived date, or the two key spaces
    disagree and the comparison silently never matches.

    A naive value is UTC by the storage contract (now(), schedule.py::_canon).
    A date-only value passes through: it was never a moment, so it has no
    zone to convert from."""
    if len(ts) <= 10:
        return ts[:10]
    parsed = datetime.fromisoformat(ts)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(config.TZ).date().isoformat()


def local_moment(ts: str) -> str:
    """A stored timestamp as prose in the TEAM's zone: "09 Aug at 14:30".

    local_day's time-carrying sibling, and it exists for the same reason: a
    naive value is UTC by the storage contract, so printing it unconverted
    tells a reader in Denver that a 09:00 meeting ran at 15:00. A date-only
    value keeps its date and gains no time — it was never a moment, and
    "09 Aug at 00:00" invents one.
    """
    if len(ts) <= 10:
        return _pretty_date(ts[:10])
    parsed = datetime.fromisoformat(ts)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    local = parsed.astimezone(config.TZ)
    return f"{local.strftime('%d %b')} at {local.strftime('%H:%M')}"


def _pretty_date(day: str) -> str:
    try:
        return date.fromisoformat(day).strftime("%d %b")
    except ValueError:
        return day


def local_event_window(d: date) -> tuple[str, str]:
    """[start, end) for team-day d against events.starts_at, in that column's
    own shape — naive UTC "YYYY-MM-DDTHH:MM" (services/schedule.py::_canon
    converts every offset away before storing).

    A separate function from local_midnight_utc rather than a parameter: the
    two shapes are not interchangeable, and the failure of using the wrong one
    is silent — an event missing from the day it belongs to, never an error."""
    fmt = "%Y-%m-%dT%H:%M"
    return (
        _local_midnight(d).strftime(fmt),
        _local_midnight(d + timedelta(days=1)).strftime(fmt),
    )


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
    # WAL takes an exclusive lock to SET, and does NOT honor busy_timeout —
    # SQLite answers SQLITE_BUSY immediately. So several workers booting
    # against a BRAND NEW database race here and all but one die on "database
    # is locked". Once WAL is established the pragma is a no-op read and the
    # race is gone, which is why only a fresh volume or a restore into an
    # empty one ever sees it. The busy_timeout pragma below covers every
    # statement after it, never this one.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


# SQLite checkpoints and DELETES the WAL when the last connection to the
# database closes — and connection-per-operation (query/execute above) makes
# nearly every write the last connection. Measured: one insert costs 11.99 ms
# with no other connection open and 0.28 ms with one idle connection held, so
# the app was 42x slower per write when NEARLY IDLE than under load. This one
# connection exists only to keep the WAL alive between operations; it never
# runs a statement (sqlite3's check_same_thread raises if a worker thread
# tries), holds no transaction, and so never blocks a checkpoint — the
# 1000-page auto-checkpoint still bounds WAL size through the writers.
_keepalive: sqlite3.Connection | None = None


def open_keepalive() -> None:
    global _keepalive
    if _keepalive is None:
        _keepalive = connect()


def close_keepalive() -> None:
    global _keepalive
    if _keepalive is not None:
        _keepalive.close()
        _keepalive = None


def _statements(sql: str) -> list[str]:
    """Split a migration into statements. Convention: migrations contain no
    semicolons inside string literals, trigger bodies, OR COMMENTS — a
    semicolon in comment prose splits mid-comment and the tail half is a
    syntax error at startup (bit a pre-squash migration)."""
    return [s.strip() for s in sql.split(";") if s.strip()]


def init_db() -> None:
    """Apply pending migrations in filename order; track in schema_version.

    Each migration runs inside ONE transaction (BEGIN IMMEDIATE) together with
    its schema_version insert, so a crash mid-migration rolls back cleanly and
    concurrent workers serialize on the write lock instead of double-applying.

    Migrations run with foreign_keys OFF and a foreign_key_check before every
    commit. With enforcement ON, the 12-step table rebuild (the only way to
    widen a CHECK in SQLite) destroys data: DROP TABLE on a parent performs an
    implicit DELETE that fires ON DELETE actions, nulling or cascading every
    child row — and PRAGMA foreign_keys is a silent no-op inside a
    transaction, so a migration cannot opt itself out. OFF makes rebuilds
    safe; the check keeps a buggy migration from committing orphans.
    """
    conn = connect()
    conn.isolation_level = None  # sqlite3's implicit BEGIN breaks BEGIN IMMEDIATE below
    try:
        conn.execute("PRAGMA foreign_keys = OFF")  # before BEGIN, where it still works
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
                broken = conn.execute("PRAGMA foreign_key_check").fetchmany(5)
                if broken:
                    raise sqlite3.IntegrityError(
                        f"{path.name} leaves broken foreign keys: "
                        + ", ".join(f"{r[0]} row {r[1]} -> {r[2]}" for r in broken)
                    )
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
        # A BEGIN IMMEDIATE that times out on the write lock opened no
        # transaction, so an unguarded ROLLBACK raises "cannot rollback - no
        # transaction is active" and REPLACES the real "database is locked".
        # sqlite3.OperationalError has no handler in main.py, so the caller
        # gets a 500 and the operator gets the wrong diagnosis. Same guard
        # log_activity already uses on the identical statement.
        with contextlib.suppress(sqlite3.DatabaseError):
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
    # closing(), not `with connect()`: sqlite3's connection context manager
    # scopes the TRANSACTION and never closes. Written that way, these three
    # helpers leaked every connection into a reference cycle (Connection ↔
    # cursors) that refcounting cannot free — measured at 84k open fds over
    # 30k queries between gc runs, each one holding a WAL reader mark that
    # stalls checkpoints and starves writers of the lock.
    with contextlib.closing(connect()) as conn:
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
    with contextlib.closing(connect()) as conn:  # closing: see query()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid or 0


def execute_rowcount(sql: str, params: tuple = ()) -> int:
    """Run a write statement, return the number of affected rows (for
    compare-and-swap guards like `... WHERE status = 'pending'`)."""
    ambient = _ambient.get()
    if ambient is not None:
        return ambient.execute(sql, params).rowcount
    with contextlib.closing(connect()) as conn:  # closing: see query()
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
# How many times log_activity took the unchained fallback since the last
# adoption. Read and reset by services/activity.py::adopt_unchained, which
# reports it beside the number of rows it chained — an adoption larger than
# this count is a row nothing in this process wrote.
UNCHAINED_FALLBACKS = "activity_unchained_fallbacks"


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
        # Count it where a MACHINE can read it, not only in the server log.
        # services/activity.py::adopt_unchained reports adopted-vs-recorded,
        # and an operator can only tell a busy ledger from a smuggled row by
        # comparing those two numbers. Left to the log alone, ONE genuine
        # warning exonerated every row adopted the same night, because one
        # receipt covers them all. Best-effort on purpose: a counter that
        # cannot be written must not lose the ledger row underneath it.
        with contextlib.suppress(sqlite3.DatabaseError):
            execute(
                "INSERT INTO app_settings (key, value, updated_at) VALUES (?, '1', ?)"
                " ON CONFLICT(key) DO UPDATE SET value ="
                " CAST(CAST(value AS INTEGER) + 1 AS TEXT), updated_at = excluded.updated_at",
                (UNCHAINED_FALLBACKS, now()),
            )
    except sqlite3.DatabaseError as exc:
        log.error("activity row LOST (%s: %s) — the write it describes did commit", action, exc)
