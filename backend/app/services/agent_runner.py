"""The motor the delegation loop never had.

Everything needed to carry a delegated task end to end was already built —
claim, report_progress, submit_for_acceptance, the sponsor binding, the
authority matrix, the inbox docs/FEATURES.md calls an "ambient wake-up view".
There was no waker. Every job in the JOBS registry was deterministic and none
of them ran an agent turn, so "agents as teammates" meant "agents as very
well-audited chat responses": every action started with a human typing.

Two layers, and the split is the keyless rule:

  sweep()  — deterministic. Notifies the SPONSOR about delegated work with no
             worklog note for QUIET_DAYS, at most once per task per ISO week.
             No model, no tokens, works on SKEIN_MODEL_PROVIDER=mock. This is
             A3's morning sweep and it is the whole feature on a keyless
             deployment.

  run()    — the LLM upgrade on top. One bounded turn per agent per day
             against its own inbox plus the per-engagement context pack,
             under the ceilings in config. Every write it makes still passes
             the same gate a chat turn's writes pass, so an unattended run
             produces proposals, never applied changes.

Bounds, because nobody is watching:
  * SKEIN_AGENT_RUNNER is an ALLOWLIST. A runner that discovered its own
    fleet would grow one every time somebody delegated to a new name.
  * SKEIN_AGENT_DAILY_TOKENS refuses the next run once an agent is over
    (services/usage.py::assert_within_budget).
  * SKEIN_AGENT_RUN_SECONDS bounds the wall clock. The provider socket
    timeout caps a STALL; it never caps a loop that keeps making progress it
    cannot finish, which is the shape of a runaway.
  * claim_job keys the run per (agent, team day), so a restart cannot
    re-spend an agent's allowance.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import threading
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from .. import config, db
from . import delegation, usage

if TYPE_CHECKING:
    from ..extensions import ExtensionRegistry, PolicyEngine

log = logging.getLogger("skein")

# One turn per agent per process at a time — see the acquire site in run_one.
_TURN_LOCKS: dict[str, threading.Lock] = {}

# Days without a worklog note before the sponsor hears about it. The sweep
# runs daily, so without a threshold every open delegation pings every day.
QUIET_DAYS = 2

# What the runner says to an agent it wakes. Deliberately narrow: the turn is
# unattended, so it asks for a worklog note and a submission, never for new
# work. An open-ended "what should we do?" is how an unwatched agent invents
# a project nobody asked for.
_WAKE = (
    "You are resuming work you already hold. Call my_agent_inbox for your"
    " delegated tasks, then read_worklog on each one to see what you last"
    " recorded. Call get_findings once and read it BEFORE you start: a rule may"
    " already have named the reason a task of yours is stuck, and repeating"
    " work the engine has already explained is the most expensive thing you can"
    " do unwatched. For each task, do the next concrete step, then call"
    " report_progress with what you did. Call submit_for_acceptance only when"
    " a task is genuinely finished. Do not create new tasks, and do not start"
    " anything you were not delegated. If a task is blocked, call raise_blocker"
    " with the task id and what you need, THEN note it in report_progress and"
    " move on — a blocker said only in prose reaches nobody: the blocker"
    " register, My Day, and the escalation clock all read the record, not"
    " your reply."
)


def _delegated_at(task_id: int) -> str | None:
    """When this task was last delegated, from the ledger — the one record of
    the hand-off, since tasks carry no delegated_at column. The space after
    the id stops '#4 ->' matching '#40 ->'. None for a delegation that
    predates the chain, which then ages like any old work."""
    row = db.query_one(
        "SELECT MAX(created_at) AS at FROM activity"
        " WHERE action = 'delegate_task' AND detail LIKE ?",
        (f"#{task_id} -> %",),
    )
    return row["at"] if row else None


def _release(agent: str, *, explicit_key: str = "") -> None:
    """Hand back a claim when no model invocation started."""
    db.execute(
        "DELETE FROM job_runs WHERE job = ? AND run_key = ?",
        (
            f"agent-wake:{agent}" if explicit_key else f"agent-run:{agent}",
            explicit_key or db.today().isoformat(),
        ),
    )


def _due(agent: str, policy: PolicyEngine | None = None) -> list[dict]:
    """The agent's open delegated tasks. The inbox's own view, so the runner
    and the agent agree about what is outstanding."""
    from ..extensions.policy import PolicySubject, current_policy_engine
    from . import projection_policy, scope

    task_policy = projection_policy.ProjectionPolicy(
        policy or current_policy_engine(),
        PolicySubject(agent, kind="agent", strong=True, source="agent-runner"),
        "skein.job.agent-run",
        "background",
        scope.NOBODY,
        agent=agent,
        tool="skein.core.agent-run",
    )
    with db.read_transaction():
        return delegation.agent_inbox(
            agent,
            task_filter=lambda task_id, attributes: task_policy.permits(
                "task", task_id, attributes
            ),
        )["delegated_tasks"]


def sweep(policy: PolicyEngine | None = None) -> dict:
    """Deterministic: tell each sponsor about delegated work that has gone
    QUIET — no worklog note for QUIET_DAYS.

    The threshold is the feature. Without it this notifies every sponsor about
    every open delegated task every single day, which is a daily digest entry
    nobody can act on and the fastest way to teach a team to ignore the
    channel.

    No model. This is the half that runs on a keyless deployment, and it is
    also the honest fallback when a real provider is configured but the run
    below refuses (budget spent, agent forbidden, provider down).

    Notifies the SPONSOR, never the team: a delegation has one accountable
    human by construction, and a team-wide ping about somebody else's agent is
    noise nobody can act on.
    """
    from .notifications import notify

    cutoff = db.local_midnight_utc(db.today() - timedelta(days=QUIET_DAYS))
    touched = 0
    for agent in config.AGENT_RUNNER:
        with db.transaction():
            due = _due(agent, policy)
            # Check-in nags FIRST, and the quiet loop skips their tasks: a
            # task both past its check-in and quiet earned the sponsor two
            # notifications in one sweep, and the check-in nag already sends
            # them to look. Once per (task, date): the claim key carries the
            # date, so moving the check-in re-arms the nag and a stale one
            # cannot fire twice.
            today = db.local_day(db.now())
            checked_in: set[int] = set()
            for task in due:
                if not task["sponsor"] or not task.get("check_in_at"):
                    continue
                if str(task["check_in_at"]) >= today:
                    continue
                checked_in.add(int(task["id"]))
                if not db.claim_job(f"sweep-checkin:{task['id']}", str(task["check_in_at"])):
                    continue

                def checkin_body(
                    source: dict,
                    current_agent: str = agent,
                    checkin: str = str(task["check_in_at"]),
                ) -> str:
                    return (
                        f"{current_agent} holds task #{source['id']}"
                        f" '{source['title']}' past its check-in date"
                        f" {checkin}. Read the worklog and record a verdict,"
                        " or set a new check-in date."
                    )

                notify(
                    task["sponsor"],
                    checkin_body,
                    source_entity="task",
                    source_id=int(task["id"]),
                    tier="digest",
                    link="/agents",
                )
                touched += 1
            for task in due:
                if not task["sponsor"]:
                    continue
                if int(task["id"]) in checked_in:
                    continue
                # A delegation younger than the window is not quiet — it is
                # new. "No progress note for 2 days" on a task delegated an
                # hour ago is false as stated, and the first thing a sponsor
                # reads about their own fresh delegation must not be a nag.
                delegated_at = _delegated_at(int(task["id"]))
                if delegated_at and delegated_at >= cutoff:
                    continue
                notes = delegation.list_worklog(task["id"], limit=1, actor=agent)
                last = notes[0] if notes else None
                # a note INSIDE the window means the work is not quiet. No note at
                # all is the loudest case, not the quietest — the agent has held
                # the task since it was delegated and recorded nothing.
                if last and last["created_at"] >= cutoff:
                    continue
                note = str(last["note"]) if last else ""
                # truncation carries its marker: a cut note read as a whole one
                said = (
                    (
                        f" Last note: {note[:120]}\u2026"
                        if len(note) > 120
                        else f" Last note: {note}"
                    )
                    if note
                    else " No progress note yet."
                )
                # Once per (task, ISO week), the bound services/portfolio.py::
                # nudge_stale_wip already uses for the same shape of nudge. The
                # QUIET_DAYS threshold alone only decides WHETHER a task is quiet;
                # without this the sweep runs daily and a task quiet for a month
                # sends the same sponsor the same sentence twenty-eight times,
                # which is how a team learns to filter the channel. The claim is
                # taken only when there is something to send, so a quiet week does
                # not burn it.
                iso = db.today().isocalendar()
                if not db.claim_job(f"sweep-quiet:{task['id']}", f"{iso.year}-W{iso.week:02d}"):
                    continue

                def quiet_body(
                    source: dict,
                    current_agent: str = agent,
                    last_note: str = said,
                ) -> str:
                    return (
                        f"{current_agent} holds task #{source['id']} '{source['title']}'"
                        f" with no progress note for {QUIET_DAYS}"
                        f" day{'' if QUIET_DAYS == 1 else 's'}.{last_note}"
                    )

                notify(
                    task["sponsor"],
                    quiet_body,
                    source_entity="task",
                    source_id=int(task["id"]),
                    tier="digest",
                    link="/agents",
                )
                touched += 1
    return {"swept": touched, "agents": len(config.AGENT_RUNNER)}


def _refused(agent: str, reason: str) -> dict:
    """A run that did not happen because nothing asked for one.

    Nothing delegated, already ran today, mock provider, a budget ceiling
    doing its job, an authority level set to forbidden. The fleet is healthy
    and the scheduler must say so, or every quiet night reads as an incident.
    """
    return {"agent": agent, "ran": False, "fault": False, "reason": reason}


# Set while the operator pause is on. automation_paused bridges it to each
# active Strands Agent through Agent.cancel(); the wall clock remains the
# backstop for a provider call that cannot reach a cancellation-safe point.
_automation_stop = threading.Event()
_active_lock = threading.Lock()
_active_agents: dict[int, Any] = {}


def _cancel_agent(agent: Any) -> None:
    cancel = getattr(agent, "cancel", None)
    if not callable(cancel):
        return
    try:
        cancel()
    except Exception as exc:
        log.warning("active agent cancellation failed (%s)", type(exc).__name__)


def _start_active(agent: Any, worker: threading.Thread) -> bool:
    """Publish and start one invocation without crossing an existing pause."""
    with _active_lock:
        if _automation_stop.is_set():
            return False
        _active_agents[id(agent)] = agent
        try:
            worker.start()
        except Exception:
            _active_agents.pop(id(agent), None)
            raise
    return True


def _untrack_active(agent: Any) -> None:
    with _active_lock:
        _active_agents.pop(id(agent), None)


def automation_paused() -> None:
    _automation_stop.set()
    with _active_lock:
        active = tuple(_active_agents.values())
    for agent in active:
        _cancel_agent(agent)


def automation_resumed() -> None:
    _automation_stop.clear()


def _paused(agent: str) -> dict:
    return {
        "agent": agent,
        "ran": False,
        "fault": False,
        "paused": True,
        "reason": "agent automation is paused",
    }


def _failed(agent: str, reason: str) -> dict:
    """A required turn failed before its completion became uncertain."""
    return {"agent": agent, "ran": False, "fault": True, "reason": reason}


def _unknown(agent: str, reason: str) -> dict:
    """A model invocation started and can already have written records."""
    return {
        "agent": agent,
        "ran": False,
        "fault": True,
        "completion_unknown": True,
        "reason": reason,
    }


def run_one(
    agent: str,
    *,
    actor: str = "scheduler",
    extensions: ExtensionRegistry | None = None,
    policy: PolicyEngine | None = None,
    explicit_key: str = "",
    allowed_tools: set[str] | frozenset[str] | None = None,
) -> dict:
    """One bounded, unattended turn for one agent.

    Returns a reason rather than raising for every refusal an operator can
    act on — the caller runs a fleet, and one agent over budget must not stop
    the others.
    """
    from .settings import agent_automation_enabled

    if not agent_automation_enabled():
        # the operator switch (Settings → AI runtime): stops every unattended
        # turn without a redeploy. It stops AUTOMATION only — authority,
        # policy, and review gates are not reachable through it either way
        return _paused(agent)
    if not explicit_key and agent not in config.AGENT_RUNNER:
        return _refused(agent, "not in SKEIN_AGENT_RUNNER")
    if config.EFFECTIVE_PROVIDER == "mock":
        # not an error: mock is a supported deployment, and sweep() above is
        # the whole feature there. Saying so keeps /health honest.
        return _refused(agent, "no model provider — sweep only")
    if delegation.authority_level(agent, "task") == "forbidden":
        return _refused(agent, "forbidden on tasks")
    try:
        usage.assert_within_budget(agent)
    except usage.BudgetSpent as exc:
        # a spent budget is a CEILING working, not a fault — the operator set it
        return _refused(agent, str(exc))

    # Evaluate the current delegated work and claim this run in one write
    # transaction, so a restart cannot re-spend the day's allowance.
    #
    # It does NOT pin the project: _due reads tasks with no lock, claim_job
    # writes a different table, and a relink committing between them changes
    # the project after the policy decision. Deliberate — the delegated set
    # is variable and this runs once per agent per day, so holding every task
    # in it costs more than the staleness it prevents. The consequence is
    # bounded: one run proceeds under the previous project's policy, and the
    # next day re-decides.
    with db.transaction():
        if not _due(agent, policy):
            return _refused(agent, "nothing delegated")
        claim_job = f"agent-wake:{agent}" if explicit_key else f"agent-run:{agent}"
        claim_key = explicit_key or db.today().isoformat()
        if not db.claim_job(claim_job, claim_key):
            return _refused(agent, "already ran this wake" if explicit_key else "already ran today")

    # The wake worker and the daily 05:30 job share one process and disjoint
    # claim namespaces, so without this an agent runs two concurrent turns
    # over the same inbox: double spend, duplicate proposals and notes.
    turn_lock = _TURN_LOCKS.setdefault(agent, threading.Lock())
    if not turn_lock.acquire(blocking=False):
        # the claim goes back: this entrant spent nothing, and the turn that
        # holds the lock owns the day's (or this wake's) allowance
        _release(agent, explicit_key=explicit_key)
        return _refused(agent, "a turn for this agent is already running")

    from ..agents.identity import reset_agent_identity, set_agent_identity
    from ..agents.team_agent import (
        build_agent,
        model_in_force,
        reset_team_model_snapshot,
        set_team_model_snapshot,
    )
    from ..extensions.policy import (
        PolicySubject,
        current_policy_engine,
        reset_policy_engine,
        reset_policy_subject,
        set_policy_engine,
        set_policy_subject,
    )

    # The ":" separator is outside chat_threads._THREAD_ID's charset, the same
    # guarantee persona_session_id relies on: a runner session id cannot be
    # typed, so no chat caller can claim it, restore the agent's unattended
    # conversation, or mint usage rows that count against the wake cap.
    thread = (
        f"wake:{agent}:{explicit_key}" if explicit_key else f"run:{agent}:{db.today().isoformat()}"
    )
    agent_subject = PolicySubject(
        agent,
        kind="agent",
        strong=True,
        source="agent-runner",
    )
    policy_token = set_policy_engine(policy or current_policy_engine())
    subject_token = set_policy_subject(agent_subject)
    token = set_agent_identity(agent)
    # A planner or specialist can be built after the outer agent starts. Freeze
    # the team pick so an admin change cannot split one unattended turn.
    model_token = set_team_model_snapshot(model_in_force())
    invoked = False
    try:
        try:
            build_kwargs: dict[str, Any] = {}
            from .personas import bench_slugs

            if agent in bench_slugs():
                build_kwargs["persona"] = agent
            if allowed_tools is not None:
                build_kwargs["allowed_tools"] = set(allowed_tools)
            if extensions is not None:
                built = build_agent(
                    thread,
                    user=agent,
                    extensions=extensions,
                    policy_subject=agent_subject,
                    **build_kwargs,
                )
            else:
                built = build_agent(thread, user=agent, **build_kwargs)
        except Exception as exc:
            # the claim is RELEASED here: nothing reached the provider, so
            # nothing was spent, and a 30-second blip at 05:30 must not cost
            # the whole day on a job that runs once and does not catch up
            _release(agent, explicit_key=explicit_key)
            log.warning("agent build failed for %s (%s)", agent, type(exc).__name__)
            return _failed(agent, f"could not build: {type(exc).__name__}")
        if built is None:
            _release(agent, explicit_key=explicit_key)
            return _failed(agent, "no agent could be built")
        # A DAEMON thread, not a ThreadPoolExecutor: the executor joins its
        # workers both on context exit AND through an atexit hook, so either
        # one waits out the full hang this bound exists to escape. A daemon
        # thread lets the job return and lets the process exit.
        #
        # The thread is left running on timeout. Nothing in Python can kill
        # it, and the provider call it is blocked on is the orphan
        # agents/team_agent.py::READ_TIMEOUT_S already names. What is bought
        # is that the SCHEDULED JOB returns, so the rest of the fleet still
        # runs tonight.
        box: dict = {}
        wake = _WAKE
        if config.AGENT_DAILY_TOKENS:
            # The ceiling refuses the NEXT run, never this one mid-turn — so
            # the model is told what remains and told to converge near the
            # limit, instead of exploring into a refusal it cannot see coming.
            remaining = max(0, config.AGENT_DAILY_TOKENS - usage.spent_today(agent)["tokens"])
            wake += (
                f"\n\nToken budget for today: {remaining:,} of"
                f" {config.AGENT_DAILY_TOKENS:,}. If less than a quarter"
                " remains, do not explore: finish or record the one most"
                " important step, then stop."
            )

        from . import tuning

        # read before the thread starts: effective() hits the database, and
        # the worker thread must not open a connection just to read two knobs
        limits = {
            "turns": tuning.effective("agent_run_turns"),
            "total_tokens": tuning.effective("agent_run_tokens"),
        }

        def _turn() -> None:
            try:
                # automation_paused calls Agent.cancel() from the setting's
                # request thread. Strands stops at its next cancellation-safe
                # point and returns the literal stop reason `cancelled`.
                box["reply"] = built(wake, limits=limits)
            except Exception as exc:  # carried out, not raised in this thread
                box["error"] = exc
            finally:
                # IN the thread, and in a finally: the abandoned-timeout path
                # is the EXPENSIVE one, and nothing else records this turn.
                # build_agent returns a bare Strands Agent, and every existing
                # record_chat_usage caller is in routes/chat.py — so without
                # this the runner's own spend never reaches usage_log, and
                # both bounds written for it read zero forever: the daily
                # ceiling (usage.assert_within_budget) and the runaway rule
                # (insights.py::_r_turn_runaway).
                try:
                    row = usage.row_from_agent(built, thread, agent_name=agent)
                    if row:
                        with contextlib.suppress(Exception):
                            # a wake turn has no linkable chat thread, so its
                            # spend sat under '(unlinked)' however clearly it
                            # was one engagement's work — attributed only when
                            # every open delegated task resolves to the same
                            # engagement (usage.sole_delegation_engagement),
                            # never guessed
                            usage.record_chat_usage(
                                **row,
                                engagement_id=usage.sole_delegation_engagement(agent),
                            )
                finally:
                    _untrack_active(built)
                    # An invoked turn owns the lock: run_one's finally released
                    # it at the wall-clock timeout while this thread kept
                    # calling tools, and the next wake ran a second concurrent
                    # turn over the same inbox — the exact double spend the
                    # lock exists to prevent.
                    turn_lock.release()

        # copy_context(), because a ContextVar does NOT cross a bare
        # threading.Thread — the worker starts at the var's default. Without
        # this the turn ran as "agent" (the chat identity) rather than the
        # agent we woke: its my_agent_inbox read an empty inbox, every
        # report_progress was refused as "written by its delegate or sponsor
        # only", and the gate evaluated authority against the WRONG row — the
        # one most likely to have been promoted to autonomous. The chat path
        # is safe only because Starlette's run_in_threadpool copies context;
        # this spawn does not get that for free.
        ctx = contextvars.copy_context()
        worker = threading.Thread(
            target=lambda: ctx.run(_turn), daemon=True, name=f"agent-run-{agent}"
        )
        try:
            started = _start_active(built, worker)
        except Exception:
            _release(agent, explicit_key=explicit_key)
            raise
        if not started:
            # The pause landed after the initial guard but before the worker.
            # No provider call started, so return the job claim and leave a
            # delegation wake pending for resume.
            _release(agent, explicit_key=explicit_key)
            return _paused(agent)
        invoked = True
        worker.join(timeout=config.AGENT_RUN_SECONDS)
        if worker.is_alive():
            # the claim key stays taken on purpose: a turn that ran long
            # enough to time out has already spent tokens, and retrying it
            # today would spend them again
            log.warning("agent run for %s exceeded %ss", agent, config.AGENT_RUN_SECONDS)
            return _unknown(agent, f"run exceeded {config.AGENT_RUN_SECONDS}s and was abandoned")
        if "error" in box:
            raise box["error"]
        reply = box.get("reply", "")
        text = str(reply)[:2000]
        db.log_activity(actor, "agent_run", f"{agent}: unattended run")
        out = {"agent": agent, "ran": True, "fault": False, "thread": thread, "reply": text}
        stop = str(getattr(reply, "stop_reason", ""))
        if stop.startswith("limit_") or stop == "cancelled":
            # an SDK literal, never model text — safe for job_outcomes.detail
            out["stopped"] = stop
            log.warning("agent run for %s stopped at %s", agent, stop)
        return out
    except Exception as exc:
        # Logged and reported, never raised: run() below is a scheduled job,
        # and a raise there marks the whole sweep failed on /health when the
        # other agents ran fine.
        log.warning("agent run failed for %s (%s)", agent, type(exc).__name__)
        if invoked:
            return _unknown(agent, f"run failed: {type(exc).__name__}")
        return _failed(agent, f"run failed: {type(exc).__name__}")
    finally:
        # in a finally, not after the call: an exception mid-turn would
        # otherwise leave this thread's identity set to the agent, and the
        # next write on it would carry the wrong actor
        reset_team_model_snapshot(model_token)
        reset_agent_identity(token)
        reset_policy_subject(subject_token)
        reset_policy_engine(policy_token)
        # only when no turn thread started: once invoked, _turn's finally is
        # the sole releaser, so an abandoned turn keeps the agent locked until
        # its thread actually ends
        if not invoked:
            turn_lock.release()


def run(
    *,
    actor: str = "scheduler",
    extensions: ExtensionRegistry | None = None,
    policy: PolicyEngine | None = None,
) -> dict:
    """The scheduled entry point: the deterministic sweep, then one bounded
    turn per allowlisted agent. The sweep runs FIRST and unconditionally, so
    a sponsor still hears about quiet work on a day every run refuses."""
    swept = sweep(policy)
    runs = [
        run_one(a, actor=actor, extensions=extensions, policy=policy) for a in config.AGENT_RUNNER
    ]
    ran = sum(1 for r in runs if r["ran"])
    faults = [r for r in runs if r.get("fault")]
    stopped = [r for r in runs if r.get("stopped")]
    # `status` is read by jobs.run_job, which otherwise records `ok` for any
    # return value at all. Drop this key and a fleet where every agent fails to
    # build returns an ordinary dict: /health shows the job green and the
    # reasons reach nothing but the process log.
    status = "ok" if not faults else ("partial" if ran else "error")
    return {
        "sweep": swept,
        "runs": runs,
        "ran": ran,
        "status": status,
        # named, not counted: an operator fixing this needs to know WHICH
        # agent and WHY, and `_outcome_detail` reduces a list to its length
        "faults": " | ".join(f"{r['agent']}: {r['reason']}" for r in faults),
        # `_outcome_detail` reduces lists to counts. Keep SDK stop literals in
        # one safe scalar so the durable job row says why a bounded turn ended.
        "stops": " | ".join(f"{r['agent']}: {r['stopped']}" for r in stopped),
    }
