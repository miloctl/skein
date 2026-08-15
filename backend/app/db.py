"""Thin PostgreSQL layer. Connections come from one pool and run in
autocommit, so a single query costs one round trip; db.transaction() gives
compound writes one shared connection and a real BEGIN/COMMIT instead.
Schema lives in app/core_migrations/*.sql.

Service SQL is written with `?` placeholders and translated here (see
_prepare). Keep writing `?`: the style only has to be consistent, and the
translation is the one place that knows the driver.
"""

import contextlib
import hashlib
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import psycopg
from psycopg import IsolationLevel
from psycopg import sql as pgsql
from psycopg.rows import dict_row
from psycopg.types.numeric import FloatLoader
from psycopg_pool import ConnectionPool, PoolTimeout

from . import config

MIGRATIONS_DIR = Path(__file__).resolve().parent / "core_migrations"

log = logging.getLogger("skein")

# Services catch this instead of importing psycopg — the driver stays behind
# this module. Re-exported, not redefined: a subclass would not match what
# the driver actually raises.
IntegrityError = psycopg.errors.IntegrityError
UniqueViolation = psycopg.errors.UniqueViolation

# LOAD, not fault: every one of these means the identical request succeeds on a
# retry with nothing changed, which is the 503 + Retry-After contract in
# CLAUDE.md. main.py maps each to that; a 500 would tell the client "bug, do
# not retry", the opposite of the truth.
#
# The TYPE carries the classification, so nothing here parses a message.
# TransactionRollback is the 40xxx family (serialization failure, deadlock
# detected); PoolTimeout is the same condition seen from the pool — every
# connection in use. Ordinary faults (bad SQL, a missing column) are
# ProgrammingError and stay 500s.
BUSY_ERRORS: tuple[type[Exception], ...] = (
    psycopg.errors.TransactionRollback,
    psycopg.errors.LockNotAvailable,
    PoolTimeout,
)

# Parameterized with the row factory, so mypy knows a row is a dict and not
# the driver default tuple — see pool().
DictConnection = psycopg.Connection[dict[str, Any]]

_ambient: ContextVar[DictConnection | None] = ContextVar("skein_txn", default=None)
_on_commit: ContextVar[list[Callable[[], None]] | None] = ContextVar(
    "skein_txn_commits", default=None
)
# Ledger rows queued by log_activity inside a transaction, flushed as that
# transaction's LAST statements — see _flush_activity.
_pending_activity: ContextVar[list[tuple[str, str, str, str]] | None] = ContextVar(
    "skein_txn_activity", default=None
)


def on_commit(fn: Callable[[], None]) -> bool:
    """Queue fn to run after the ambient transaction commits; a rollback
    drops it. Returns False when no transaction is active — the caller runs
    the work inline. For side effects that must not hold a write open
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


# ---- connection pool -------------------------------------------------------

# PostgreSQL returns `numeric` for SUM over an integer column, for
# ROUND(x::numeric, n), and for EXTRACT(epoch ...); psycopg loads that as
# Decimal, which json.dumps refuses. Every service here is written for the
# float SQLite returned, and the breakage lands on the json.dumps callers
# only — FastAPI routes survive on jsonable_encoder, so a route stays green
# while the agent tool beside it raises. Without this the findings job dies
# on its own budget receipt (services/insights.py) and get_flow_metrics
# raises whenever any task is complete (tools/portfolio.py).
# Registered on the module adapters, so it covers every connection the pool
# hands out.
psycopg.adapters.register_loader("numeric", FloatLoader)

_pool: ConnectionPool[DictConnection] | None = None


def pool() -> ConnectionPool[DictConnection]:
    """The process-wide pool, opened on first use.

    autocommit is ON for every pooled connection, so a bare query() costs one
    round trip instead of BEGIN + query + COMMIT. transaction() opens a real
    block on top of it, which is the only place a multi-statement unit exists.
    """
    global _pool
    if _pool is None:
        if config.DATABASE_ERROR:
            raise RuntimeError(config.DATABASE_ERROR)
        _pool = ConnectionPool[DictConnection](
            config.DATABASE_URL,
            min_size=1,
            # Every request thread and every sync @tool can hold one
            # connection at once. Sized from the two knobs that already bound
            # that number, so it cannot drift below them: a pool smaller than
            # the thread pool turns a burst into a queue nobody configured.
            max_size=config.THREAD_POOL + config.TOOL_THREADS + 4,
            kwargs={"autocommit": True, "row_factory": dict_row},
            open=True,
        )
    return _pool


def privilege_warnings() -> list[str]:
    """A warning when the application connects as a database SUPERUSER.

    A superuser can COPY ... FROM PROGRAM, which runs shell commands on the
    database host, and pg_read_file, which reads its filesystem. That turns
    every SQL bug — and every extension, which supplies raw SQL — into command
    execution. The deployment creates a NOSUPERUSER role for this
    (deploy/k8s/base/postgres.yaml); a deployment that skipped it has no other
    signal, so /health carries this the way it carries a bad model provider.

    A warning, never a refusal: an existing deployment must not fail to boot
    over its own historical credentials, and the operator needs the running
    app to read the message on.
    """
    try:
        row = query_one("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
    except psycopg.Error:  # pragma: no cover — reported by the connection check
        return []
    if row and row["rolsuper"]:
        return [
            "Skein connects to PostgreSQL as a superuser. A superuser can run"
            " shell commands on the database host through SQL. Create a role"
            " with NOSUPERUSER and point SKEIN_DATABASE_URL at it."
        ]
    return []


def close_pool() -> None:
    """Drop the pool. Shutdown, and the test suite between databases."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@lru_cache(maxsize=4096)
def _translate(sql: str) -> str:
    """`?` placeholders to psycopg's `%s`, and literal `%` doubled.

    ORDER IS LOAD-BEARING: doubling after the placeholder rewrite would turn
    each fresh `%s` into `%%s`, which psycopg emits literally instead of
    binding — every parameter would silently go unsent. Only reached when the
    caller passes parameters; see _prepare."""
    return sql.replace("%", "%%").replace("?", "%s")


def _prepare(sql: str, params: tuple) -> tuple[str, tuple | None]:
    """The (query, params) pair psycopg is given.

    A parameterless query is passed through VERBATIM with params=None,
    because psycopg only unescapes `%%` when parameters are present — doubling
    it here would leave `LIKE 'x%%'` matching a literal percent sign in the
    data."""
    if not params:
        return sql, None
    return _translate(sql), params


@contextmanager
def _conn() -> Iterator[DictConnection]:
    """The ambient transaction's connection, or a pooled one for a single
    autocommit statement."""
    ambient = _ambient.get()
    if ambient is not None:
        yield ambient
        return
    with pool().connection() as conn:
        yield conn


@contextmanager
def _txn(isolation: IsolationLevel | None = None) -> Iterator[list[Callable[[], None]]]:
    """One connection held for a real BEGIN/COMMIT block, published as the
    ambient transaction. Yields the on_commit queue, which the caller runs
    only after the block commits."""
    callbacks: list[Callable[[], None]] = []
    queued: list[tuple[str, str, str, str]] = []
    with pool().connection() as conn:
        previous = conn.isolation_level
        if isolation is not None:
            conn.isolation_level = isolation
        token = _ambient.set(conn)
        cb_token = _on_commit.set(callbacks)
        act_token = _pending_activity.set(queued)
        try:
            # Rolls back and re-raises on an exception, so the callback loop
            # in the caller is skipped for a failed block.
            with conn.transaction():
                yield callbacks
                # INSIDE the transaction, and LAST. A rollback still drops
                # these rows with the write they describe.
                _flush_activity(conn, queued)
        finally:
            _pending_activity.reset(act_token)
            _on_commit.reset(cb_token)
            _ambient.reset(token)
            # The pool resets the transaction on return, never this attribute,
            # so a leftover REPEATABLE READ would silently follow this
            # connection into whatever checks it out next.
            conn.isolation_level = previous


def _run_callbacks(callbacks: list[Callable[[], None]]) -> None:
    # Reached only after a successful COMMIT, with the connection returned to
    # the pool — a callback never holds a write open. Isolated per callback:
    # the write already committed, so a raising callback must not turn a
    # successful write into a 500, and must not starve the ones queued after.
    for cb in callbacks:
        try:
            cb()
        except Exception:
            log.exception("on_commit callback failed")


@contextmanager
def transaction() -> Iterator[None]:
    """Every db.* call inside the block shares one connection and commits
    atomically at exit; any exception rolls the whole block back. Nested
    blocks join the outer transaction. Context-local, so concurrent requests
    (threads or tasks) never share a transaction."""
    if _ambient.get() is not None:
        yield
        return
    with _txn() as callbacks:
        yield
    _run_callbacks(callbacks)


@contextmanager
def read_transaction() -> Iterator[None]:
    """Keep one read snapshot across several queries.

    REPEATABLE READ, not the default: under READ COMMITTED each statement
    takes a fresh snapshot, so a concurrent write lands mid-block and two
    counts that must agree do not. Readers block nothing either way."""
    if _ambient.get() is not None:
        yield
        return
    with _txn(IsolationLevel.REPEATABLE_READ) as callbacks:
        yield
    _run_callbacks(callbacks)


# Namespaces for name_lock, so two unrelated subsystems locking the same string
# never contend. Add a constant here rather than passing a literal.
LOCK_IDENTITY = 1
LOCK_SESSION = 2
LOCK_RECEIPT = 3
LOCK_CREW = 4

# EVERY advisory lock is scoped to the current database by this expression.
# PostgreSQL advisory locks are CLUSTER-global: the key space is shared by
# every database on the server, so without this a dev database beside a
# production one — or the test suite's per-worker databases — serialize on
# each other's ledger and identity locks, and can deadlock across databases
# that share nothing. A stable 32-bit hash of the database name is the first
# key of every two-key acquisition below.
_DB_KEY = "('x' || substr(md5(current_database()), 1, 8))::bit(32)::int"


def _advisory(conn: DictConnection, key: int, *, xact: bool = True) -> None:
    """Take one advisory lock, scoped to this database — see _DB_KEY."""
    fn = "pg_advisory_xact_lock" if xact else "pg_advisory_lock"
    # ::int on both keys: the two-key form is (int4, int4), and an unadorned
    # Python int binds as bigint, which matches no overload.
    conn.execute(f"SELECT {fn}({_DB_KEY}, %s::int)", (key,))


def in_transaction() -> bool:
    """Whether the caller is inside db.transaction().

    Read by code that must behave differently when its statement is part of a
    larger unit — a row lock only means something while a transaction holds
    it (services/policy_context.py::resource_row).
    """
    return _ambient.get() is not None


def name_lock(namespace: int, name: str) -> None:
    """Serialize the transactions that claim one `name` inside `namespace`.

    A read takes no lock, so a read that decides whether to insert protects
    nothing on its own: two claimants both read "absent" and both proceed.
    This is the lock that makes the pair atomic.

    Transaction-scoped, so it releases at commit or rollback with no unlock
    call to forget. hashtext() maps the name into the lock's integer key
    space; a collision between two different names only means they serialize
    with each other, which costs a wait and never correctness.

    MUST be called inside a transaction: outside one the lock would be taken
    and released around the single statement, protecting nothing.
    """
    conn = _ambient.get()
    if conn is None:
        raise RuntimeError("name_lock needs an active transaction")
    # Scoped to this database like every other advisory lock (_DB_KEY). The
    # namespace and the folded name are combined into the second key, because
    # the first is spent on the database identity.
    conn.execute(
        f"SELECT pg_advisory_xact_lock({_DB_KEY}, hashtext(%s))",
        (f"{namespace}:{name}",),
    )


@contextmanager
def schema_scope(schema: str) -> Iterator[None]:
    """Run the block's db.* calls against `schema` instead of public.

    SET LOCAL, so the search path reverts when the transaction ends and can
    never follow a pooled connection to its next borrower. The explicit reset
    covers the nested case, where the outer transaction commits later and the
    statements after this block would otherwise still resolve to `schema`.

    The identifier is composed with psycopg's quoting rather than an f-string:
    a schema name reaches this from an extension's own manifest.
    """
    with transaction():
        conn = _ambient.get()
        if conn is None:  # pragma: no cover — transaction() just set it
            raise RuntimeError("schema_scope needs an active transaction")
        set_path = pgsql.SQL("SET LOCAL search_path TO {}")
        conn.execute(set_path.format(pgsql.Identifier(schema)))
        try:
            yield
        finally:
            # Suppressed, because the block may have left the transaction
            # ABORTED — and then this reset raises InFailedSqlTransaction and
            # REPLACES the real error on the way out, so the caller is told
            # "transaction is aborted" instead of which statement aborted it.
            # The reset is only an optimisation anyway: SET LOCAL dies with
            # the transaction regardless.
            with contextlib.suppress(psycopg.Error):
                conn.execute(set_path.format(pgsql.Identifier("public")))


@contextmanager
def savepoint() -> Iterator[None]:
    """Roll back one nested unit while keeping its outer transaction alive."""
    connection = _ambient.get()
    if connection is None:
        with transaction():
            yield
        return
    # This fixed internal name prevents caller-controlled SQL. Savepoints DO
    # nest now (the agent gate wraps direct(), and a service under it can
    # open its own), and one name still works: PostgreSQL shadows a
    # re-declared savepoint name, so ROLLBACK TO and RELEASE bind to the most
    # recent declaration and each level unwinds its own. Pinned by
    # tests/test_db_transactions.py::test_nested_savepoints_unwind_their_own_level.
    callbacks = _on_commit.get()
    callback_count = len(callbacks) if callbacks is not None else 0
    queued = _pending_activity.get()
    activity_count = len(queued) if queued is not None else 0
    connection.execute("SAVEPOINT skein_review_apply")
    try:
        yield
        connection.execute("RELEASE SAVEPOINT skein_review_apply")
    except BaseException:
        connection.execute("ROLLBACK TO SAVEPOINT skein_review_apply")
        connection.execute("RELEASE SAVEPOINT skein_review_apply")
        # SQL created after the savepoint no longer exists. Its deferred
        # effects must not run when the outer review-settlement transaction
        # commits — and the LEDGER rows queued inside the rolled-back unit
        # must go with them, or the chain records writes that never happened.
        if callbacks is not None:
            del callbacks[callback_count:]
        if queued is not None:
            del queued[activity_count:]
        raise


# ---- migrations ------------------------------------------------------------

# Two workers booting together must not both apply a migration. A session
# advisory lock serializes them without a table to contend on — the arbitrary
# constant only has to be stable and unique to this use.
_MIGRATION_LOCK = 4_216_017  # int4, see _advisory


@contextmanager
def _admin_conn() -> Iterator[DictConnection]:
    """A connection of its own for schema work, outside the pool.

    Migrations run once at boot and hold a session lock across several
    transactions. Borrowing a pooled connection for that would leave the lock
    tied to whatever else checks it out next."""
    with psycopg.connect(config.DATABASE_URL, autocommit=True, row_factory=dict_row) as conn:
        yield conn


def init_db() -> None:
    """Apply pending migrations in filename order; track in schema_version.

    Each migration runs inside ONE transaction together with its
    schema_version insert, so a crash mid-migration rolls back cleanly — DDL
    is transactional here, so a half-applied file cannot exist.

    A migration file is executed WHOLE rather than split on semicolons, so it
    may contain them freely: in prose comments, in string literals, and inside
    dollar-quoted function bodies.
    """
    if config.DATABASE_ERROR:
        raise RuntimeError(config.DATABASE_ERROR)
    with _admin_conn() as conn:
        _advisory(conn, _MIGRATION_LOCK, xact=False)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version"
                " (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                already = conn.execute(
                    "SELECT 1 FROM schema_version WHERE version = %s", (path.name,)
                ).fetchone()
                if already:
                    continue
                try:
                    with conn.transaction():
                        conn.execute(path.read_text())
                        conn.execute(
                            "INSERT INTO schema_version (version, applied_at) VALUES (%s, %s)",
                            (path.name, now()),
                        )
                except psycopg.Error as exc:
                    # NAME THE FILE. The driver reports the failing statement
                    # and nothing about which migration it came from, and a
                    # boot that dies on "relation ... does not exist" with a
                    # directory of files tells an operator nothing.
                    raise type(exc)(f"{path.name}: {exc}") from exc
        finally:
            conn.execute(
                f"SELECT pg_advisory_unlock({_DB_KEY}, %s::int)",
                (_MIGRATION_LOCK,),
            )


def pending_migrations() -> list[str]:
    """Migration files not yet recorded in schema_version (all of them when
    the schema has never been applied). Lets long-lived side processes (MCP)
    refuse to start instead of racing the API server to apply schema."""
    names = [p.name for p in sorted(MIGRATIONS_DIR.glob("*.sql"))]
    with _admin_conn() as conn:
        has_table = conn.execute("SELECT to_regclass('public.schema_version') AS t").fetchone()
        if not has_table or has_table["t"] is None:
            return names
        applied = {r["version"] for r in conn.execute("SELECT version FROM schema_version")}
    return [n for n in names if n not in applied]


# ---- query helpers ---------------------------------------------------------


def query(sql: str, params: tuple = ()) -> list[dict]:
    with _conn() as conn:
        return conn.execute(*_prepare(sql, params)).fetchall()


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
    """Run a write statement. Returns the first column of the RETURNING row
    when the statement has one, else 0.

    There is no last-inserted-id: an INSERT whose id the caller consumes must
    ask for it with RETURNING id. A caller that forgets gets 0, never a wrong
    id."""
    with _conn() as conn:
        cur = conn.execute(*_prepare(sql, params))
        if cur.description is None:
            return 0
        row = cur.fetchone()
        return int(next(iter(row.values()))) if row else 0


def execute_rowcount(sql: str, params: tuple = ()) -> int:
    """Run a write statement, return the number of affected rows (for
    compare-and-swap guards like `... WHERE status = 'pending'`)."""
    with _conn() as conn:
        return conn.execute(*_prepare(sql, params)).rowcount


def claim_job(job: str, run_key: str) -> bool:
    """CAS-style once-only claim for scheduled jobs (digest, flush, backup) so
    accidental multi-worker deployments can't double-run them."""
    return (
        execute_rowcount(
            "INSERT INTO job_runs (job, run_key, created_at) VALUES (?, ?, ?)"
            " ON CONFLICT DO NOTHING",
            (job, run_key, now()),
        )
        == 1
    )


# ---- provenance ledger -----------------------------------------------------

ACTIVITY_DOMAIN = b"skein-activity/v1"
GENESIS_PREV = "0" * 64
# How many times log_activity took the unchained fallback since the last
# adoption. Read and reset by services/activity.py::adopt_unchained, which
# reports it beside the number of rows it chained — an adoption larger than
# this count is a row nothing in this process wrote.
UNCHAINED_FALLBACKS = "activity_unchained_fallbacks"

# Two appends may otherwise read the same tail concurrently and write the same
# seq with the same prev_hash, which forks the chain permanently at that row.
# Transaction-scoped, so it releases at commit with no unlock to forget — and
# because a transaction's rows are QUEUED and written at the very end
# (_flush_activity), the ordinary path takes this LAST. That ordering is what
# keeps it out of every deadlock cycle: a lock taken last is never held while
# waiting for another.
#
# hold_activity_chain() is the one exception: it takes this FIRST, because it
# assigns seqs from a tail read it has to serialize. That inverts the order,
# so its transaction may only write rows no chained-append transaction takes
# first. Today it writes the two unchained counters in app_settings, and the
# only other writer of those runs on an autocommit connection below with no
# ambient transaction — it holds no row lock while waiting for this one, so
# there is no cycle. Adding a write to that function means checking this
# again.
_ACTIVITY_LOCK = 4_216_018  # int4, see _advisory


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
    conn: DictConnection, actor: str, action: str, detail: str, created_at: str
) -> None:
    """Read the chain tail, link to it, insert. Caller is inside a
    transaction; the advisory lock makes the read-then-write atomic against
    every other appender."""
    _advisory(conn, _ACTIVITY_LOCK)
    tail = conn.execute(
        "SELECT seq, hash FROM activity WHERE seq IS NOT NULL ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    seq = (tail["seq"] if tail else 0) + 1
    prev = tail["hash"] if tail else None
    conn.execute(
        "INSERT INTO activity (actor, action, detail, created_at, seq, hash, prev_hash)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
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


def hold_activity_chain() -> None:
    """Serialize this transaction against every ledger appender.

    For the one writer that assigns seqs itself — services/activity.py's
    adoption of unchained rows — rather than through _append_activity. It
    reads the chain tail and hands out the seqs that follow it, so without
    this an ordinary append between the read and the writes takes a seq the
    adoption is about to assign, and the loser dies on the seq unique index.

    Take it FIRST in the transaction. The flush at commit acquires the same
    lock last, so a transaction that takes a row lock before this one and an
    appender that takes them the other way round is a deadlock cycle — which
    is exactly what the adoption did while it took no lock at all.
    """
    conn = _ambient.get()
    if conn is None:
        raise RuntimeError("hold_activity_chain needs an active transaction")
    _advisory(conn, _ACTIVITY_LOCK)


def _flush_activity(conn: DictConnection, queued: list[tuple[str, str, str, str]]) -> None:
    """Write a transaction's queued ledger rows, in the order they were made.

    One advisory-lock acquisition for the whole batch rather than one per row,
    and it happens after every other statement in the transaction — which is
    what keeps the ledger lock out of every deadlock cycle.
    """
    for actor, action, detail, created_at in queued:
        _append_activity(conn, actor, action, detail, created_at)
    queued.clear()


def log_activity(actor: str, action: str, detail: str = "") -> None:
    """Append to the provenance ledger, chained to the row before it.

    Inside an ambient transaction the append joins it, so a failure rolls back
    the caller's whole write — correct, and loud. Standalone it takes its own
    transaction; if that fails, the row is recorded UNCHAINED rather than
    raising into a caller's write.

    ONE attempt, not several. A retry budget here buys nothing and multiplies
    the worst-case hang. The fallback is logged: an unchained row is a hole in
    the ledger, and a hole nobody was told about is the version that matters.
    """
    created_at = now()
    queued = _pending_activity.get()
    if queued is not None:
        # QUEUED, not written here. The append takes a global advisory lock
        # (_ACTIVITY_LOCK) that is held until commit, so writing it mid
        # transaction lets a caller hold the ledger lock while it waits for a
        # row — and any other transaction holding that row and then logging
        # closes the cycle. Deferring makes the ledger lock the LAST one every
        # transaction takes, and a lock taken last can never be held while
        # waiting for another. The timestamp is captured NOW, so the row still
        # records when the thing happened, not when the transaction ended.
        queued.append((actor, action, detail, created_at))
        return
    ambient = _ambient.get()
    if ambient is not None:  # pragma: no cover — a transaction always queues
        _append_activity(ambient, actor, action, detail, created_at)
        return
    try:
        with pool().connection() as conn, conn.transaction():
            _append_activity(conn, actor, action, detail, created_at)
        return
    except psycopg.Error as exc:
        log.warning("activity chain append failed (%s: %s) — recording unchained", action, exc)
    # The fallback takes a fresh connection, so a fault that outlives the
    # first attempt raises here too — straight into a caller that had already
    # committed its business write. The catch below is what keeps the
    # docstring's promise that this path never raises: losing the ledger row
    # must not also 500 a write that actually happened. A lost row still
    # shows up — the unchained count and the chain marks report it.
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
        with contextlib.suppress(psycopg.Error):
            execute(
                "INSERT INTO app_settings (key, value, updated_at) VALUES (?, '1', ?)"
                " ON CONFLICT(key) DO UPDATE SET value ="
                " CAST(CAST(app_settings.value AS INTEGER) + 1 AS TEXT),"
                " updated_at = excluded.updated_at",
                (UNCHAINED_FALLBACKS, now()),
            )
    except psycopg.Error as exc:
        log.error("activity row LOST (%s: %s) — the write it describes did commit", action, exc)
