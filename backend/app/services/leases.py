"""Live execution acquisitions and the database fence inherited by their work."""

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import Context, ContextVar
from datetime import datetime, timedelta
from uuid import uuid4

from .. import db

log = logging.getLogger("skein")

PROCESS_ID = uuid4().hex
_generation = uuid4().hex
LEASE_SECONDS = 90
HEARTBEAT_SECONDS = 30
LEASED_TABLES = ("agent_wakeups", "chat_agent_runs", "job_runs")

Guard = tuple[str, str]
_guards: ContextVar[tuple[Guard, ...]] = ContextVar("skein_execution_fences", default=())
_live: dict[Guard, dict[threading.Thread, int]] = {}
_live_lock = threading.Lock()
_stop = threading.Event()
_thread: threading.Thread | None = None
_sweeper: threading.Thread | None = None
_wake_kicks = False
_recovery = True


class LeaseLost(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "Execution ownership ended. Check the recorded outcome before requesting more work."
        )


def new_token() -> str:
    return f"{_generation}:{uuid4().hex}"


def _current(token: str) -> bool:
    return token.startswith(f"{_generation}:")


def _check_generation() -> None:
    if any(not _current(token) for _, token in _guards.get()):
        raise LeaseLost()


def until(seconds: int = LEASE_SECONDS) -> str:
    stamp = db.query_row("SELECT clock_timestamp() AS stamp")["stamp"]
    return (stamp + timedelta(seconds=seconds)).isoformat(timespec="microseconds")


def active() -> bool:
    return bool(_guards.get())


@contextmanager
def tracked(token: str, *, table: str) -> Iterator[None]:
    """Track queue metadata without inverting a delegation's task-before-wake locks.

    agent_runner holds the separate agent-turn acquisition around domain writes.
    Holding agent_wakeups there deadlocks a human delegation updating that row.
    """
    if table not in LEASED_TABLES or not isinstance(token, str) or not token:
        raise ValueError("An execution acquisition is required.")
    if not _current(token):
        raise LeaseLost()
    guard = (table, token)
    worker = threading.current_thread()
    with _live_lock:
        holders = _live.setdefault(guard, {})
        holders[worker] = holders.get(worker, 0) + 1
    try:
        yield
    finally:
        with _live_lock:
            holders = _live.get(guard, {})
            count = holders.get(worker, 0)
            if count <= 1:
                holders.pop(worker, None)
            else:
                holders[worker] = count - 1
            if not holders:
                _live.pop(guard, None)


@contextmanager
def held(token: str, *, table: str = "job_runs") -> Iterator[None]:
    """Register the real worker lifetime and inherit every enclosing fence."""
    if db.in_transaction():
        raise RuntimeError("execution ownership must precede the domain transaction")
    with tracked(token, table=table):
        context_token = _guards.set(tuple(sorted({*_guards.get(), (table, token)})))
        try:
            check()
            yield
        finally:
            # ASGI can finalize an abandoned generator in a different context.
            # The original task owns the token, and its guard cannot leak here.
            with suppress(ValueError):
                _guards.reset(context_token)


def lock_fences(conn: db.DictConnection) -> tuple[datetime, ...]:
    """Called by db._txn before any domain lock, never through db helpers."""
    _check_generation()
    deadlines = []
    for table, token in _guards.get():
        row = conn.execute(
            f"SELECT lease_until::timestamptz AS deadline FROM {table}"  # noqa: S608 — table validated by held
            " WHERE lease_token = %s AND lease_owner = %s"
            " AND NULLIF(lease_until, '')::timestamptz > clock_timestamp() FOR UPDATE",
            (token, PROCESS_ID),
        ).fetchone()
        if not row:
            raise LeaseLost()
        deadlines.append(row["deadline"])
    return tuple(deadlines)


def check_deadlines(conn: db.DictConnection, deadlines: tuple[datetime, ...]) -> None:
    _check_generation()
    if deadlines:
        stamp = conn.execute("SELECT clock_timestamp() AS stamp").fetchone()
        if stamp is None or min(deadlines) <= stamp["stamp"]:
            raise LeaseLost()


def check() -> None:
    """Preflight for work outside PostgreSQL. It cannot undo a remote effect."""
    if active():
        with db.transaction():
            pass


def _registered() -> list[Guard]:
    with _live_lock:
        return [guard for guard, holders in _live.items() if any(t.is_alive() for t in holders)]


def workers_active() -> bool:
    return bool(_registered())


def renew() -> int:
    """Only a live scope can renew, and an expired acquisition stays expired."""
    guards = [guard for guard in _registered() if _current(guard[1])]

    def independent() -> int:
        renewed = 0
        for table in LEASED_TABLES:
            tokens = [token for name, token in guards if name == table]
            if not tokens:
                continue
            with db.transaction():
                renewed += db.execute_rowcount(
                    f"WITH renewable AS (SELECT lease_token FROM {table}"  # noqa: S608 — closed tuple
                    " WHERE lease_owner = ? AND lease_token = ANY(?)"
                    " AND NULLIF(lease_until, '')::timestamptz > clock_timestamp()"
                    " FOR UPDATE SKIP LOCKED)"
                    f" UPDATE {table} AS held SET lease_until = ? FROM renewable"
                    " WHERE held.lease_token = renewable.lease_token",
                    (PROCESS_ID, tokens, until()),
                )
        return renewed

    # Bookkeeping cannot join the worker's domain transaction or inherit a
    # revoked parent fence. The heartbeat uses this same isolated path.
    return Context().run(independent)


def sweep() -> dict:
    from .. import ratelimit
    from . import agent_wakeups, mcp_servers, shared_chat_agents

    renewed = renew()
    for cleanup in (ratelimit.prune, mcp_servers.prune_oauth_flows):
        try:
            cleanup()
        except Exception:
            log.exception("execution cleanup failed")
    if not _recovery:
        return {"renewed": renewed, "wakes": {}, "chats": {}}
    wakes = (
        agent_wakeups.recover_and_kick()
        if _wake_kicks
        else {"recovered": agent_wakeups.reclaim_expired(), "started": False}
    )
    chats = shared_chat_agents.recover_and_kick()
    return {"renewed": renewed, "wakes": wakes, "chats": chats}


def _loop(stop: threading.Event) -> None:
    while not stop.wait(HEARTBEAT_SECONDS):
        try:
            renew()
        except Exception:
            log.exception("execution lease renewal failed")


def _maintenance_loop(stop: threading.Event) -> None:
    # Queue recovery and pruning can wait on unrelated domain locks. They must
    # never prevent the separate heartbeat from renewing live acquisitions.
    while not stop.wait(HEARTBEAT_SECONDS):
        try:
            sweep()
        except Exception:
            log.exception("execution lease sweep failed")


def disable_recovery() -> None:
    global _recovery
    _recovery = False


def enable_recovery(*, wake_kicks: bool) -> dict:
    global _wake_kicks, _recovery
    _wake_kicks = wake_kicks
    _recovery = True
    return sweep()


def start(*, wake_kicks: bool, recover: bool = True) -> dict:
    """Renew before startup catch-ups. Enable queue drains after composition."""
    global _thread, _sweeper, _stop, _wake_kicks, _recovery
    _wake_kicks = wake_kicks
    _recovery = recover
    _stop = threading.Event()
    _thread = threading.Thread(target=_loop, args=(_stop,), daemon=True, name="execution-leases")
    _sweeper = threading.Thread(
        target=_maintenance_loop, args=(_stop,), daemon=True, name="execution-recovery"
    )
    _thread.start()
    _sweeper.start()
    return sweep() if recover else {"renewed": 0, "wakes": {}, "chats": {}}


def stop(timeout: float = 5.0) -> None:
    """Call after drains stop. Revoke any daemon that outlives the lifespan."""
    global _thread, _sweeper, _recovery, _generation
    # Revoke copied contexts even if PostgreSQL is unavailable or an old tool
    # still holds its acquisition row. Its transaction must not commit later.
    _generation = uuid4().hex
    _recovery = False
    _stop.set()
    for thread in (_thread, _sweeper):
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                log.warning("execution maintenance did not stop before the deadline")
    if _thread is not None and not _thread.is_alive():
        _thread = None
    if _sweeper is not None and not _sweeper.is_alive():
        _sweeper = None

    def revoke() -> None:
        for table in LEASED_TABLES:
            with db.transaction():
                db.execute(
                    f"UPDATE {table} SET lease_until = '2000-01-01T00:00:00+00:00'"  # noqa: S608 — closed tuple
                    " WHERE lease_owner = ? AND lease_token != '' AND lease_until != ''",
                    (PROCESS_ID,),
                )

    Context().run(revoke)
