"""Job registry: the single JOBS tuple drives the cron schedule, the startup
catch-ups, the /health surface, and the job_stale findings rule. Job bodies
resolve their service lazily so this module imports nothing that could cycle
back into it (insights reads the registry for staleness periods)."""

import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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


def _daily_digest():
    from .digest import publish_digest

    return publish_digest(actor="scheduler")


def _notification_flush():
    from .notifications import flush_digest_tier

    return flush_digest_tier(claim=True)


def _daily_backup():
    from .admin import backup_if_stale

    return backup_if_stale()


def _activity_verify():
    from .activity import verify_tail

    return verify_tail()


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
    JobSpec("daily-backup", _daily_backup, {"trigger": "cron", "hour": 3, "minute": 0}, 24, True),
    JobSpec(
        "activity-verify",
        _activity_verify,
        {"trigger": "cron", "hour": 3, "minute": 30},
        24,
        True,
    ),
    JobSpec("context-pack", _context_pack, {"trigger": "cron", "hour": 5, "minute": 0}, 24),
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


def run_job(spec: JobSpec) -> None:
    """Run one registered job: log, time, record the outcome. Never raises —
    a failing job must not take down the scheduler or startup."""
    log.info("job %s: start", spec.name)
    start = time.monotonic()
    try:
        result = spec.fn()
        elapsed = int((time.monotonic() - start) * 1000)
        record_outcome(spec.name, "ok", str(result) if result is not None else "", elapsed)
        log.info("job %s: done %s", spec.name, result if result is not None else "")
    except Exception as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        # outcome table unavailable must not mask the real failure
        with contextlib.suppress(Exception):
            record_outcome(spec.name, "error", f"{type(exc).__name__}: {exc}", elapsed)
        log.exception("job %s: FAILED", spec.name)


def job_health() -> list[dict]:
    """Last success per registered job, with a stale flag at 2x the period.
    Never-succeeded jobs count as stale only once they have any recorded
    attempt older than the threshold — a fresh install isn't an incident."""
    now = datetime.now(timezone.utc)
    last_ok = {
        r["job"]: r["ts"]
        for r in db.query(
            "SELECT job, MAX(created_at) AS ts FROM job_outcomes WHERE status = 'ok' GROUP BY job"
        )
    }
    first_seen = {
        r["job"]: r["ts"]
        for r in db.query("SELECT job, MIN(created_at) AS ts FROM job_outcomes GROUP BY job")
    }
    out = []
    for spec in JOBS:
        threshold = (now - timedelta(hours=2 * spec.period_hours)).isoformat(timespec="seconds")
        ok_ts = last_ok.get(spec.name)
        if ok_ts:
            stale = ok_ts < threshold
        else:
            seen = first_seen.get(spec.name)
            stale = bool(seen and seen < threshold)
        out.append({"job": spec.name, "last_success": ok_ts, "stale": stale})
    return out
