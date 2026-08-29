"""Job registry: the single JOBS tuple drives the cron schedule, the startup
catch-ups, the /health surface, and the job_stale findings rule. Job bodies
resolve their service lazily so this module imports nothing that could cycle
back into it (insights reads the registry for staleness periods)."""

import contextlib
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from .. import db

log = logging.getLogger("skein")


@dataclass(frozen=True)
class JobSpec:
    name: str
    fn: Callable[[], Any]
    trigger: dict = field(default_factory=dict)  # APScheduler add_job kwargs
    period_hours: float = 24  # expected cadence — drives the job_stale rule
    catch_up: bool = False  # run at startup to fill in missed firings


def _blocker_sweep():
    from .blockers import sweep_escalations

    return sweep_escalations()


def _chase_received():
    from .promises import chase_received

    return chase_received()


def _daily_digest():
    from .digest import publish_digest

    return publish_digest(actor="scheduler")


def _notification_flush():
    from .notifications import flush_digest_tier

    return flush_digest_tier(claim=True)


def _daily_backup():
    from .admin import backup_if_stale

    return backup_if_stale()


def _embed_reconcile():
    from .. import config
    from .search import embed_missing

    if not config.EMBED_READY:
        return "embeddings off"
    # bounded batch: a huge backlog (first enable, model change) must not hold
    # a job slot for hours — the next hourly run continues where this stopped
    done, failed = embed_missing(limit=200)
    return {"embedded": done, "failed": failed}


def _activity_verify():
    from .activity import nightly_verify

    return nightly_verify()


def _weekly_plan():
    from .weekly import propose_weekly_plan

    return propose_weekly_plan(actor="scheduler")


def _stale_wip_nudge():
    from .portfolio import nudge_stale_wip

    return nudge_stale_wip()


def _stale_decisions():
    from .collab import sweep_stale_decisions

    return sweep_stale_decisions()


def _context_pack():
    from .context_pack import publish_pack

    return publish_pack(actor="scheduler")


def _agent_run():
    from .agent_runner import run

    return run()


def _health_snapshot():
    from .adoption import snapshot_health

    return snapshot_health()


def _forecast_snapshot():
    from .adoption import snapshot_forecasts

    return snapshot_forecasts()


def _findings():
    from .insights import run_findings

    return run_findings(actor="scheduler")


def _week_open():
    from .rituals import week_open

    return week_open(actor="scheduler")


def _week_close():
    from .rituals import week_close

    return week_close(actor="scheduler")


def _authority_review():
    from .delegation import review_authority

    return review_authority(actor="scheduler")


def _retention_prune():
    from .retention import prune

    return prune(actor="scheduler")


JOBS: tuple[JobSpec, ...] = (
    JobSpec("blocker-sweep", _blocker_sweep, {"trigger": "interval", "hours": 1}, 1, True),
    # hourly like the blocker sweep, and for the same reason: the job runs
    # often so a due date is noticed the day it passes, while the nudge itself
    # is once per cycle (services/promises.py::NUDGE_CYCLE_HOURS)
    JobSpec("promise-chase", _chase_received, {"trigger": "interval", "hours": 1}, 1, True),
    JobSpec("daily-backup", _daily_backup, {"trigger": "cron", "hour": 3, "minute": 0}, 24, True),
    # hourly heal for _maybe_embed's best-effort gaps: a provider outage
    # leaves indexed rows without vectors, and nothing else retries them
    JobSpec("embed-reconcile", _embed_reconcile, {"trigger": "interval", "hours": 1}, 1, True),
    JobSpec(
        "activity-verify",
        _activity_verify,
        {"trigger": "cron", "hour": 3, "minute": 30},
        24,
        True,
    ),
    JobSpec("context-pack", _context_pack, {"trigger": "cron", "hour": 5, "minute": 0}, 24),
    JobSpec(
        "health-snapshot",
        _health_snapshot,
        {"trigger": "cron", "hour": 5, "minute": 10},
        24,
        True,
    ),
    JobSpec(
        "forecast-snapshot",
        _forecast_snapshot,
        {"trigger": "cron", "hour": 5, "minute": 15},
        24,
        True,
    ),
    JobSpec(
        "weekly-plan",
        _weekly_plan,
        {"trigger": "cron", "day_of_week": "mon", "hour": 6, "minute": 0},
        168,
        True,
    ),
    JobSpec(
        "week-open",
        _week_open,
        {"trigger": "cron", "day_of_week": "mon", "hour": 6, "minute": 30},
        168,
        True,
    ),
    JobSpec(
        "week-close",
        _week_close,
        {"trigger": "cron", "day_of_week": "fri", "hour": 15, "minute": 0},
        168,
    ),
    JobSpec(
        "authority-review",
        _authority_review,
        {"trigger": "cron", "day_of_week": "mon", "hour": 6, "minute": 45},
        168,
    ),
    JobSpec(
        "stale-wip-nudge",
        _stale_wip_nudge,
        {"trigger": "cron", "day_of_week": "mon", "hour": 6, "minute": 15},
        168,
        True,
    ),
    JobSpec(
        "agent-run",
        _agent_run,
        # after the context pack (05:00) so a woken agent reads a fresh one,
        # and well before the 07:00 digest so its proposals are in the inbox
        # the digest reports on. catch_up=False on purpose: this SPENDS, and a
        # restart must not buy a turn nobody scheduled — the per-agent claim
        # key would stop a second run, but only after the decision to run.
        {"trigger": "cron", "hour": 5, "minute": 30},
        24,
    ),
    JobSpec("stale-decisions", _stale_decisions, {"trigger": "cron", "hour": 6, "minute": 30}, 24),
    JobSpec("findings", _findings, {"trigger": "cron", "hour": 6, "minute": 50}, 24, True),
    JobSpec("daily-digest", _daily_digest, {"trigger": "cron", "hour": 7, "minute": 0}, 24),
    JobSpec(
        "notification-flush",
        _notification_flush,
        {"trigger": "cron", "hour": "7,15", "minute": 5},
        12,
    ),
    JobSpec(
        "retention-prune",
        _retention_prune,
        {"trigger": "cron", "day": 1, "hour": 4, "minute": 0},
        744,
        True,
    ),
)


def record_outcome(job: str, status: str, detail: str = "", duration_ms: int = 0) -> None:
    db.execute(
        "INSERT INTO job_outcomes (job, status, detail, duration_ms, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (job, status, detail[:500], duration_ms, db.now()),
    )


def _outcome_detail(result: object) -> str:
    """What a job's return value may put in job_outcomes.detail.

    Counts, never rows. `job_outcomes` carries no tier of its own. Two jobs
    break the workspace-only assumption: blockers.sweep_escalations and
    collab.sweep_stale_decisions act on every tier. Storing str(result) would
    copy private titles, detail, and owners into an unscoped operational table.

    Both sweeps already route their ledger line through scope.detail and gate
    their notification. This is the third door out of the same function.

    A scalar stays, and it must be OUR text. Skip reasons and runner faults can
    name agents and causes. A job keeps this field to literals and safe names.
    """
    if result is None:
        return ""
    if isinstance(result, list | tuple | set):
        return f"{len(result)} rows"
    if isinstance(result, dict):
        return ", ".join(
            f"{k}={len(v) if isinstance(v, list | tuple | set | dict) else v}"
            for k, v in result.items()
        )
    return str(result)


def run_job(spec: JobSpec) -> None:
    """Run one registered job: log, time, record the outcome. Never raises —
    a failing job must not take down the scheduler or startup."""
    log.info("job %s: start", spec.name)
    start = time.monotonic()
    try:
        result = spec.fn()
        elapsed = int((time.monotonic() - start) * 1000)
        detail = _outcome_detail(result)
        # A job can declare its own outcome. Without this branch, "did it
        # raise" is the only health signal a job has: a fleet run where every
        # allowlisted agent fails to build returns an ordinary dict, this
        # records `ok`, and /health shows green while nothing has run for a
        # week. Only our own literals are honored — anything else is `ok`, so
        # a job returning a row with a `status` column cannot forge a state.
        declared = result.get("status") if isinstance(result, dict) else None
        # A lost cross-worker claim records NOTHING: the loser's every-minute
        # skip otherwise wrote a fresh 'ok', so job_health's last-success was
        # permanently current on the one deployment shape (two workers) where
        # the winner's failures needed to show.
        if declared == "noop":
            log.info("job %s: done (noop) %s", spec.name, detail)
            return
        # `partial` is STORED as 'error': job_outcomes.status is a two-value
        # CHECK (001_baseline.sql) and job_health counts only 'ok' rows toward
        # last-success, which is the honest answer for a fleet where some
        # agents failed — an operator has to look. Widening the CHECK would
        # mean rebuilding the table for a distinction only this log line makes.
        status = "ok" if declared not in ("partial", "error") else "error"
        record_outcome(spec.name, status, detail, elapsed)
        log.info("job %s: done (%s) %s", spec.name, declared or status, detail)
    except Exception as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        # outcome table unavailable must not mask the real failure
        with contextlib.suppress(Exception):
            record_outcome(spec.name, "error", f"{type(exc).__name__}: {exc}", elapsed)
        log.exception("job %s: FAILED", spec.name)


def job_health(specs: Sequence[JobSpec] = JOBS) -> list[dict]:
    """Last attempt and success per job, with staleness at 2x the period.

    A fresh install is not stale. The latest status stays separate so one failed
    attempt is visible immediately instead of waiting for the stale threshold.
    """
    now = datetime.now(UTC)
    with db.read_transaction():
        last_ok = {
            r["job"]: r["ts"]
            for r in db.query(
                "SELECT job, MAX(created_at) AS ts FROM job_outcomes"
                " WHERE status = 'ok' GROUP BY job"
            )
        }
        first_seen = {
            r["job"]: r["ts"]
            for r in db.query("SELECT job, MIN(created_at) AS ts FROM job_outcomes GROUP BY job")
        }
        latest = {
            r["job"]: r
            for r in db.query(
                "SELECT DISTINCT ON (job) job, status, created_at FROM job_outcomes"
                " ORDER BY job, created_at DESC, id DESC"
            )
        }
    out = []
    for spec in specs:
        threshold = (now - timedelta(hours=2 * spec.period_hours)).isoformat(timespec="seconds")
        ok_ts = last_ok.get(spec.name)
        if ok_ts:
            stale = ok_ts < threshold
        else:
            seen = first_seen.get(spec.name)
            stale = bool(seen and seen < threshold)
        last = latest.get(spec.name)
        out.append(
            {
                "job": spec.name,
                "last_success": ok_ts,
                "last_attempt": last["created_at"] if last else None,
                "last_status": last["status"] if last else None,
                "stale": stale,
            }
        )
    return out
