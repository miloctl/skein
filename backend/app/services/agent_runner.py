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
from typing import TYPE_CHECKING

from .. import config, db
from . import delegation, usage

if TYPE_CHECKING:
    from ..extensions import ExtensionRegistry, PolicyEngine

log = logging.getLogger("skein")

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
    " anything you were not delegated. If a task is blocked, say so in"
    " report_progress and move on."
)


def _release(agent: str) -> None:
    """Hand back today's claim. Same shape as rituals.py::_release_claim, and
    the same reason: a failure that spent nothing must not eat the slot."""
    db.execute(
        "DELETE FROM job_runs WHERE job = ? AND run_key = ?",
        (f"agent-run:{agent}", db.today().isoformat()),
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
        for task in _due(agent, policy):
            if not task["sponsor"]:
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
                (f" Last note: {note[:120]}\u2026" if len(note) > 120 else f" Last note: {note}")
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
            notify(
                task["sponsor"],
                f"{agent} holds task #{task['id']} '{task['title']}'"
                f" with no progress note for {QUIET_DAYS}"
                f" day{'' if QUIET_DAYS == 1 else 's'}.{said}",
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


def _failed(agent: str, reason: str) -> dict:
    """A run that was SUPPOSED to happen and did not.

    A build that raised, a turn that raised, a turn abandoned at the wall
    clock. `run` reports these to the scheduler as a partial or failed job:
    without the distinction every allowlisted agent could fail all week while
    `job_outcomes` recorded `ok` and /health showed the fleet green, because
    the job itself returned a value rather than raising.
    """
    return {"agent": agent, "ran": False, "fault": True, "reason": reason}


def run_one(
    agent: str,
    *,
    actor: str = "scheduler",
    extensions: ExtensionRegistry | None = None,
    policy: PolicyEngine | None = None,
) -> dict:
    """One bounded, unattended turn for one agent.

    Returns a reason rather than raising for every refusal an operator can
    act on — the caller runs a fleet, and one agent over budget must not stop
    the others.
    """
    if agent not in config.AGENT_RUNNER:
        return _refused(agent, "not in SKEIN_AGENT_RUNNER")
    if config.EFFECTIVE_PROVIDER == "mock":
        # not an error: mock is a supported deployment, and sweep() above is
        # the whole feature there. Saying so keeps /health honest.
        return _refused(agent, "no model provider — sweep only")
    if delegation.authority_level(agent, "task") == "forbidden":
        return _refused(agent, "forbidden on tasks")
    if not _due(agent, policy):
        return _refused(agent, "nothing delegated")
    try:
        usage.assert_within_budget(agent)
    except usage.BudgetSpent as exc:
        # a spent budget is a CEILING working, not a fault — the operator set it
        return _refused(agent, str(exc))

    # once per (agent, team day): a restart must not re-spend the allowance
    if not db.claim_job(f"agent-run:{agent}", db.today().isoformat()):
        return _refused(agent, "already ran today")

    from ..agents.identity import reset_agent_identity, set_agent_identity
    from ..agents.team_agent import build_agent
    from ..extensions.policy import (
        PolicySubject,
        current_policy_engine,
        reset_policy_engine,
        reset_policy_subject,
        set_policy_engine,
        set_policy_subject,
    )

    # A thread id per agent per day, so the run has somewhere to keep its
    # session and a human can read the transcript afterwards on /chat.
    thread = f"run-{agent}-{db.today().isoformat()}"
    agent_subject = PolicySubject(
        agent,
        kind="agent",
        strong=True,
        source="agent-runner",
    )
    policy_token = set_policy_engine(policy or current_policy_engine())
    subject_token = set_policy_subject(agent_subject)
    token = set_agent_identity(agent)
    try:
        try:
            if extensions is not None:
                built = build_agent(
                    thread,
                    user=agent,
                    extensions=extensions,
                    policy_subject=agent_subject,
                )
            else:
                built = build_agent(thread, user=agent)
        except Exception as exc:
            # the claim is RELEASED here: nothing reached the provider, so
            # nothing was spent, and a 30-second blip at 05:30 must not cost
            # the whole day on a job that runs once and does not catch up
            _release(agent)
            log.warning("agent build failed for %s: %s", agent, exc)
            return _failed(agent, f"could not build: {type(exc).__name__}")
        if built is None:
            _release(agent)
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

        def _turn() -> None:
            try:
                box["reply"] = built(_WAKE)
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
                row = usage.row_from_agent(built, thread, agent_name=agent)
                if row:
                    with contextlib.suppress(Exception):
                        usage.record_chat_usage(**row)

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
        worker.start()
        worker.join(timeout=config.AGENT_RUN_SECONDS)
        if worker.is_alive():
            # the claim key stays taken on purpose: a turn that ran long
            # enough to time out has already spent tokens, and retrying it
            # today would spend them again
            log.warning("agent run for %s exceeded %ss", agent, config.AGENT_RUN_SECONDS)
            return _failed(agent, f"run exceeded {config.AGENT_RUN_SECONDS}s and was abandoned")
        if "error" in box:
            raise box["error"]
        reply = box.get("reply", "")
        text = str(reply)[:2000]
        db.log_activity(actor, "agent_run", f"{agent}: unattended run")
        return {"agent": agent, "ran": True, "fault": False, "thread": thread, "reply": text}
    except Exception as exc:
        # Logged and reported, never raised: run() below is a scheduled job,
        # and a raise there marks the whole sweep failed on /health when the
        # other agents ran fine.
        log.warning("agent run failed for %s: %s", agent, exc)
        return _failed(agent, f"run failed: {type(exc).__name__}")
    finally:
        # in a finally, not after the call: an exception mid-turn would
        # otherwise leave this thread's identity set to the agent, and the
        # next write on it would carry the wrong actor
        reset_agent_identity(token)
        reset_policy_subject(subject_token)
        reset_policy_engine(policy_token)


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
    }
