"""Execution leases: what separates a live process from a dead one.

A running row names the process that holds it and the moment that claim
lapses. The holder renews on a heartbeat, and whichever process sweeps next
reclaims what has lapsed. Without the owner, a second process booting reset
every running row, and the first process's live turn and a fresh claim then
wrote the same model session. The lease also fences the finish: a process
that lost its lease cannot publish a result over a reclaimed row."""

import logging
import threading
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from .. import db

log = logging.getLogger("skein")

PROCESS_ID = uuid4().hex
LEASE_SECONDS = 90
HEARTBEAT_SECONDS = 30
# Every table with lease_owner/lease_until. Renewal touches all of them at
# once, so a table left out here loses its rows to the sweep mid-turn.
LEASED_TABLES = ("agent_wakeups", "chat_agent_runs", "job_runs")

_stop = threading.Event()
_thread: threading.Thread | None = None
_wake_kicks = False


def until(seconds: int = LEASE_SECONDS) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def renew() -> int:
    """Extend every lease this process holds. A finished row clears its lease."""
    fresh = until()
    return sum(
        db.execute_rowcount(
            f"UPDATE {table} SET lease_until = ? WHERE lease_owner = ? AND lease_until != ''",  # noqa: S608 — table from the closed tuple above
            (fresh, PROCESS_ID),
        )
        for table in LEASED_TABLES
    )


def sweep() -> dict:
    """Renew this process's leases, then reclaim what any process let lapse."""
    from . import agent_wakeups, shared_chat_agents

    renewed = renew()
    wakes = (
        agent_wakeups.recover_and_kick()
        if _wake_kicks
        else {"recovered": agent_wakeups.reclaim_expired(), "started": False}
    )
    chats = shared_chat_agents.recover_and_kick()
    return {"renewed": renewed, "wakes": wakes, "chats": chats}


def _loop() -> None:
    while not _stop.wait(HEARTBEAT_SECONDS):
        try:
            sweep()
        except Exception:
            # A database blip must not end the heartbeat: the next tick renews
            # before LEASE_SECONDS lapses, and a missed renewal only costs a
            # reclaim that the finish guard already tolerates.
            log.exception("execution lease sweep failed")


def start(*, wake_kicks: bool) -> dict:
    """One synchronous sweep, then the heartbeat. Boot recovery is that sweep."""
    global _thread, _wake_kicks
    _wake_kicks = wake_kicks
    _stop.clear()
    result = sweep()
    _thread = threading.Thread(target=_loop, daemon=True, name="execution-leases")
    _thread.start()
    return result


def stop(timeout: float = 5.0) -> None:
    global _thread
    _stop.set()
    if _thread is not None:
        _thread.join(timeout)
        _thread = None
