"""Durable first wake for work that a human delegated to an agent."""

from __future__ import annotations

import contextlib
import logging
import threading
from typing import TYPE_CHECKING

from .. import config, db
from . import leases

if TYPE_CHECKING:
    from ..extensions import ExtensionRegistry

log = logging.getLogger("skein")

# The composed root, installed once by the lifespan. A wake worker is a bare
# thread: without this, current_policy_engine() raises and every wake fails
# before the provider is reached.
_extensions: ExtensionRegistry | None = None


def configure(extensions: ExtensionRegistry) -> None:
    global _extensions
    _extensions = extensions
    _shutdown.clear()


WAKE_TOOLS = frozenset(
    {
        "read_artifact",
        "list_milestones",
        "list_tasks",
        "list_questions",
        "list_decisions",
        "list_standups",
        "search_notes",
        "list_events",
        "list_blockers",
        "list_intake_requests",
        "list_engagements",
        "team_capacity",
        "list_playbooks",
        "search_workspace",
        "get_portfolio_health",
        "get_flow_metrics",
        "list_promises",
        "get_context_pack",
        "my_agent_inbox",
        "recall_memories",
        "claim_delegated_task",
        "report_progress",
        "read_worklog",
        "submit_for_acceptance",
        "get_attention",
        "get_findings",
        "list_absences",
        "field_guide",
        "raise_blocker",
    }
)

_worker_lock = threading.Lock()
_worker_running = False
_worker_thread: threading.Thread | None = None
_shutdown = threading.Event()
_SAFE_STOP_REASONS = frozenset({"cancelled", "limit_turns", "limit_total_tokens"})


def _kick_after_commit() -> None:
    kick()


def enqueue(agent: str, task_id: int, *, requested_by: str) -> dict:
    """Queue one agent wake inside the delegation transaction."""
    if not db.in_transaction():
        raise RuntimeError("agent wake enqueue needs the delegation transaction")
    now = db.now()
    row = db.query_row(
        "INSERT INTO agent_wakeups"
        " (agent, status, requested_by, trigger_task_id, requested_at)"
        " VALUES (?, 'pending', ?, ?, ?)"
        " ON CONFLICT (agent) DO UPDATE SET"
        # completion_unknown does NOT coalesce: only automatic retry of an
        # uncertain turn is forbidden, and a new human delegation is a new
        # explicit trigger. Preserving it here left the agent unwakeable
        # forever, because nothing else ever clears that state.
        " status = CASE"
        "   WHEN agent_wakeups.status = 'running' THEN 'running'"
        "   ELSE 'pending'"
        " END,"
        " requested_by = EXCLUDED.requested_by,"
        " trigger_task_id = EXCLUDED.trigger_task_id,"
        " requested_at = CASE"
        "   WHEN agent_wakeups.status IN ('pending', 'running')"
        "     THEN agent_wakeups.requested_at"
        "   ELSE EXCLUDED.requested_at"
        " END,"
        " started_at = CASE"
        "   WHEN agent_wakeups.status = 'running' THEN agent_wakeups.started_at"
        "   ELSE NULL"
        " END,"
        " finished_at = NULL,"
        " rerun_requested = CASE"
        "   WHEN agent_wakeups.status = 'running' THEN 1"
        "   ELSE 0"
        " END,"
        " thread_id = CASE"
        "   WHEN agent_wakeups.status = 'running' THEN agent_wakeups.thread_id"
        "   ELSE ''"
        " END,"
        " reason = CASE"
        "   WHEN agent_wakeups.status = 'running' THEN agent_wakeups.reason"
        "   ELSE ''"
        " END"
        " RETURNING *",
        (agent, requested_by, task_id, now),
    )
    db.on_commit(_kick_after_commit)
    return row


def claim_next() -> dict | None:
    """Claim the oldest pending agent. The row lock serializes workers."""
    with db.transaction():
        row = db.query_one(
            "SELECT * FROM agent_wakeups WHERE status = 'pending'"
            " ORDER BY requested_at, agent FOR UPDATE SKIP LOCKED LIMIT 1"
        )
        if not row:
            return None
        attempt = int(row["attempts"]) + 1
        thread_id = f"wake:{row['agent']}:{attempt}"
        now = db.now()
        return db.query_row(
            "UPDATE agent_wakeups SET status = 'running', started_at = ?,"
            " finished_at = NULL, attempts = ?, thread_id = ?, reason = '',"
            " lease_owner = ?, lease_until = ?, lease_token = ?"
            " WHERE agent = ? AND status = 'pending' RETURNING *",
            (
                now,
                attempt,
                thread_id,
                leases.PROCESS_ID,
                leases.until(),
                leases.new_token(),
                row["agent"],
            ),
        )


def _reason_code(result: dict) -> str:
    reason = str(result.get("reason") or "").lower()
    if "wake cap" in reason:
        return "wake_cap"
    if "already running" in reason:
        return "turn_in_progress"
    if "model provider" in reason or "mock" in reason:
        return "provider_unavailable"
    if "forbidden" in reason:
        return "authority_forbidden"
    if "budget" in reason or "token" in reason:
        return "budget_spent"
    if "build" in reason or "no agent" in reason:
        return "build_failed"
    return "run_failed" if result.get("fault") else "run_refused"


def finish(claim: dict, result: dict) -> None:
    """Settle one claimed row. A stale finisher, or one whose lease another
    process reclaimed, writes nothing."""
    agent = str(claim["agent"])
    attempt = int(claim["attempts"])
    with db.transaction():
        row = db.query_one(
            "SELECT * FROM agent_wakeups WHERE agent = ? FOR UPDATE",
            (agent,),
        )
        if (
            not row
            or row["status"] != "running"
            or int(row["attempts"]) != attempt
            or row["lease_owner"] != leases.PROCESS_ID
            or row["lease_token"] != claim["lease_token"]
            or not db.query_one(
                "SELECT 1 WHERE NULLIF(?, '')::timestamptz > clock_timestamp()",
                (row["lease_until"],),
            )
        ):
            return
        now = db.now()
        db.execute(
            "UPDATE agent_wakeups SET lease_owner = '', lease_until = '', lease_token = '' WHERE agent = ?",
            (agent,),
        )
        if result.get("ran"):
            if row["rerun_requested"]:
                db.execute(
                    "UPDATE agent_wakeups SET status = 'pending', requested_at = ?,"
                    " started_at = NULL, finished_at = NULL, rerun_requested = 0,"
                    " thread_id = '', reason = '' WHERE agent = ?",
                    (now, agent),
                )
            else:
                stopped = str(result.get("stopped") or "")
                reason = stopped if stopped in _SAFE_STOP_REASONS else ""
                db.execute(
                    "UPDATE agent_wakeups SET status = 'completed', finished_at = ?,"
                    " rerun_requested = 0, reason = ? WHERE agent = ?",
                    (now, reason, agent),
                )
            return
        if result.get("paused"):
            # The pause raced the worker after it claimed this row but before
            # any model call. Put the explicit delegation back for resume.
            db.execute(
                "UPDATE agent_wakeups SET status = 'pending', started_at = NULL,"
                " finished_at = NULL, rerun_requested = 0, thread_id = '', reason = ''"
                " WHERE agent = ?",
                (agent,),
            )
            return
        if result.get("completion_unknown"):
            # a queued rerun dies here on purpose: the abandoned turn's thread
            # can still be alive, and only a NEW human delegation may re-pend
            # into that risk
            status_value = "completion_unknown"
            reason = "completion_unknown"
        elif row["rerun_requested"]:
            # the rerun is a distinct human delegation that never got its
            # turn — a failure or refusal of THIS turn does not answer it
            db.execute(
                "UPDATE agent_wakeups SET status = 'pending', requested_at = ?,"
                " started_at = NULL, finished_at = NULL, rerun_requested = 0,"
                " thread_id = '', reason = '' WHERE agent = ?",
                (now, agent),
            )
            return
        else:
            status_value = "failed" if result.get("fault") else "refused"
            reason = _reason_code(result)
        db.execute(
            "UPDATE agent_wakeups SET status = ?, finished_at = ?,"
            " rerun_requested = 0, reason = ? WHERE agent = ?",
            (status_value, now, reason, agent),
        )


def reclaim_expired() -> int:
    """A running row whose lease lapsed has no live holder.

    A row with rerun_requested carries a delegation that arrived DURING the
    lost turn — a distinct explicit human request that never got its turn.
    Re-pending it is not a retry of the uncertain turn, so it goes back to
    pending instead of dying with the lease."""
    with db.transaction():
        now = db.now()
        recovered = db.execute_rowcount(
            "UPDATE agent_wakeups SET status = 'pending', requested_at = ?,"
            " started_at = NULL, finished_at = NULL, rerun_requested = 0,"
            " thread_id = '', reason = '', lease_owner = '', lease_until = '', lease_token = ''"
            " WHERE status = 'running' AND rerun_requested = 1"
            " AND COALESCE(NULLIF(lease_until, '')::timestamptz, '-infinity') <= clock_timestamp()",
            (now,),
        )
        return recovered + db.execute_rowcount(
            "UPDATE agent_wakeups SET status = 'completion_unknown',"
            " finished_at = ?, rerun_requested = 0, reason = 'lease_expired',"
            " lease_owner = '', lease_until = '', lease_token = ''"
            " WHERE status = 'running'"
            " AND COALESCE(NULLIF(lease_until, '')::timestamptz, '-infinity') <= clock_timestamp()",
            (now,),
        )


def status(agent: str, *, task_id: int = 0) -> dict | None:
    """The safe projection of one agent's wake row.

    task_id scopes it to the delegation that wrote the row: the row is keyed
    per agent, so without the comparison a viewer of task A reads the timing
    and outcome of a wake triggered by task B they cannot read. An earlier
    coalesced task falls back to the legacy runner guidance instead.
    """
    row = db.query_one(
        "SELECT status, trigger_task_id, requested_at, started_at, finished_at, reason"
        " FROM agent_wakeups WHERE agent = ?",
        (agent,),
    )
    if not row or (task_id and int(row["trigger_task_id"]) != task_id):
        return None
    enabled = _automation_enabled()
    reason = row["reason"]
    if config.SCHEDULER_ENABLED and not enabled and row["status"] in {"pending", "running"}:
        reason = "automation_paused"
    return {
        "status": row["status"],
        "requested_at": row["requested_at"],
        "started_at": row["started_at"] or "",
        "finished_at": row["finished_at"] or "",
        "reason": reason,
        # one deployment-shape bit, on purpose: Task Peek cannot phrase
        # "queued" honestly without knowing whether anything will drain it
        "automation_enabled": enabled,
    }


def _has_pending() -> bool:
    return db.query_one("SELECT 1 FROM agent_wakeups WHERE status = 'pending' LIMIT 1") is not None


def _wake_cap_reached() -> bool:
    """Workspace-wide daily bound on delegation-triggered turns.

    Counted from usage_log wake threads: the per-agent token ceiling defaults
    to 0 and a strong caller can mint fresh agent names, so only an aggregate
    cap bounds the bill. A turn that spends but fails to record its usage row
    slips the count.
    """
    # ponytail: undercounts unrecorded spend, move to a dedicated counter if it matters
    if not config.AGENT_WAKES_PER_DAY:
        return False
    row = db.query_one(
        # 'wake:%', never 'wake-%': ':' is outside the chat thread-id charset,
        # so only the runner writes matching rows — a user-named 'wake-...'
        # chat thread must not spend the workspace's daily wake allowance
        "SELECT COUNT(*) AS n FROM usage_log WHERE thread_id LIKE 'wake:%' AND created_at >= ?",
        (db.local_midnight_utc(db.today()),),
    )
    return row is not None and int(row["n"]) >= config.AGENT_WAKES_PER_DAY


def _drain() -> None:
    global _worker_running
    try:
        from .agent_runner import run_one
        from .settings import agent_automation_enabled

        # checked BEFORE claiming: a claim made while paused would finish as
        # refused and need a fresh delegation to re-arm — left pending, the
        # resume switch drains it with no data lost
        while not _shutdown.is_set() and agent_automation_enabled() and (claim := claim_next()):
            with leases.tracked(str(claim["lease_token"]), table="agent_wakeups"):
                try:
                    # its own guard, not the run_one try below: a database blip
                    # in this read must not mark a turn that never started as
                    # completion_unknown. Not capped on error — a failing
                    # database refuses the turn itself a moment later.
                    capped = _wake_cap_reached()
                except Exception:
                    log.exception("wake cap check failed for %s", claim["agent"])
                    capped = False
                if capped:
                    finish(claim, {"ran": False, "fault": False, "reason": "wake cap"})
                    continue
                try:
                    result = run_one(
                        str(claim["agent"]),
                        actor="scheduler",
                        extensions=_extensions,
                        policy=_extensions.policy_engine if _extensions else None,
                        explicit_key=str(claim["attempts"]),
                        allowed_tools=WAKE_TOOLS,
                    )
                except Exception:
                    log.exception("agent wake worker failed for %s", claim["agent"])
                    result = {
                        "agent": claim["agent"],
                        "ran": False,
                        "fault": True,
                        "completion_unknown": True,
                        "reason": "worker failed",
                    }
                try:
                    finish(claim, result)
                except Exception:
                    # the row stays running until the next restart's recovery; a
                    # database that refuses this write refuses claim_next too, so
                    # looping on would spin
                    log.exception("wake settle failed for %s", claim["agent"])
                    break
    finally:
        with _worker_lock:
            _worker_running = False
        # suppressed so a database fault here cannot mask the loop's own
        # exception; a row this misses recovers at the next enqueue's kick
        with contextlib.suppress(Exception):
            if _has_pending():
                kick()


def _automation_enabled() -> bool:
    if not config.SCHEDULER_ENABLED:
        return False
    from .settings import agent_automation_enabled

    return agent_automation_enabled()


def kick() -> bool:
    """Start one process-local queue drain after a committed delegation."""
    global _worker_running, _worker_thread
    if _shutdown.is_set() or not _automation_enabled():
        return False
    with _worker_lock:
        if _worker_running:
            return False
        _worker_running = True
    try:
        _worker_thread = threading.Thread(target=_drain, daemon=True, name="agent-wake-worker")
        _worker_thread.start()
    except Exception:
        # thread-resource exhaustion: a wedged True here would refuse every
        # future kick until restart with pending rows visible and undrained
        with _worker_lock:
            _worker_running = False
        raise
    return True


def recover_and_kick() -> dict:
    recovered = reclaim_expired()
    started = kick() if _has_pending() else False
    return {"recovered": recovered, "started": started}


def shutdown(timeout: float = 5.0) -> bool:
    _shutdown.set()
    if _worker_thread is not None:
        _worker_thread.join(timeout)
        return not _worker_thread.is_alive()
    return True
