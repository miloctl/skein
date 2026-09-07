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

from .. import config, db
from . import leases

log = logging.getLogger("skein")


@dataclass(frozen=True)
class JobSpec:
    name: str
    fn: Callable[[], Any]
    trigger: dict = field(default_factory=dict)  # APScheduler add_job kwargs
    period_hours: float = 24  # expected cadence — drives the job_stale rule
    catch_up: bool = False  # run at startup to fill in missed firings
    retry_safe: bool = False
    timezone: str | None = None


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
    status = "error" if failed and not done else "partial" if failed else "ok"
    return {"embedded": done, "failed": failed, "status": status}


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
    # Retry opt-ins belong beside the body. Transactional sweeps, unique
    # snapshots, and backup_if_stale can repeat without another effect.
    # Model calls, outbound sends, and unclassified bodies stay conservative.
    JobSpec(
        "blocker-sweep",
        _blocker_sweep,
        {"trigger": "interval", "hours": 1},
        1,
        True,
        retry_safe=True,
    ),
    # hourly like the blocker sweep, and for the same reason: the job runs
    # often so a due date is noticed the day it passes, while the nudge itself
    # is once per cycle (services/promises.py::NUDGE_CYCLE_HOURS)
    JobSpec(
        "promise-chase",
        _chase_received,
        {"trigger": "interval", "hours": 1},
        1,
        True,
        retry_safe=True,
    ),
    JobSpec(
        "daily-backup",
        _daily_backup,
        {"trigger": "cron", "hour": 3, "minute": 0},
        24,
        True,
        retry_safe=True,
    ),
    # Hourly heal for _maybe_embed's best-effort gaps. No startup catch-up:
    # 200 external calls at the five-second timeout can delay readiness by
    # roughly 1000 seconds, and semantic repair is not a pre-serve dependency.
    JobSpec("embed-reconcile", _embed_reconcile, {"trigger": "interval", "hours": 1}, 1),
    JobSpec(
        "activity-verify",
        _activity_verify,
        {"trigger": "cron", "hour": 3, "minute": 30},
        24,
        True,
    ),
    JobSpec(
        "context-pack",
        _context_pack,
        {"trigger": "cron", "hour": 5, "minute": 0},
        24,
        retry_safe=True,
    ),
    JobSpec(
        "health-snapshot",
        _health_snapshot,
        {"trigger": "cron", "hour": 5, "minute": 10},
        24,
        True,
        retry_safe=True,
    ),
    JobSpec(
        "forecast-snapshot",
        _forecast_snapshot,
        {"trigger": "cron", "hour": 5, "minute": 15},
        24,
        True,
        retry_safe=True,
    ),
    JobSpec(
        "weekly-plan",
        _weekly_plan,
        {"trigger": "cron", "day_of_week": "mon", "hour": 6, "minute": 0},
        168,
        True,
        retry_safe=True,
    ),
    JobSpec(
        "week-open",
        _week_open,
        {"trigger": "cron", "day_of_week": "mon", "hour": 6, "minute": 30},
        168,
        True,
        retry_safe=True,
    ),
    JobSpec(
        "week-close",
        _week_close,
        {"trigger": "cron", "day_of_week": "fri", "hour": 15, "minute": 0},
        168,
        retry_safe=True,
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
        retry_safe=True,
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
    JobSpec(
        "stale-decisions",
        _stale_decisions,
        {"trigger": "cron", "hour": 6, "minute": 30},
        24,
        retry_safe=True,
    ),
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


def record_outcome(
    job: str,
    status: str,
    detail: str = "",
    duration_ms: int = 0,
    *,
    outcome_id: int | None = None,
) -> None:
    if outcome_id is not None:
        db.execute(
            "UPDATE job_outcomes SET status = ?, detail = ?, duration_ms = ?, created_at = ?"
            " WHERE id = ? AND job = ?",
            (status, detail[:500], duration_ms, db.now(), outcome_id, job),
        )
    else:
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


def fire_key(spec: JobSpec, now: datetime | None = None) -> str:
    """The firing this run stands for, agreed by every process.

    A cron job keys on its latest scheduled time, so two processes firing
    together and a boot catch-up hours later all name the same run. An
    interval job fires relative to each process's own start, so it keys on
    the period window instead."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    if spec.trigger.get("trigger") == "cron":
        from apscheduler.triggers.cron import CronTrigger

        fields = {k: v for k, v in spec.trigger.items() if k != "trigger"}
        fields.setdefault("timezone", spec.timezone or config.TZ_NAME)
        trigger = CronTrigger(**fields)
        # Two periods back: at 06:00 a 07:00-and-15:00 job last fired 15 hours
        # ago, one period back finds nothing and the key falls to the window.
        cursor = now - timedelta(hours=2 * spec.period_hours)
        latest = None
        while (fire := trigger.get_next_fire_time(None, cursor)) and fire <= now:
            latest = fire
            # Local arithmetic resets fold=1 and repeats the same DST firing
            # forever. UTC progress is strict across both copies of that hour.
            cursor = fire.astimezone(UTC) + timedelta(seconds=1)
        if latest is not None:
            return latest.isoformat(timespec="minutes")
    seconds = max(int(spec.period_hours * 3600), 60)
    return str(int(now.timestamp()) // seconds)


def run_job(spec: JobSpec) -> None:
    """Fence the whole job independently of its firing receipt.

    Only retry_safe bodies can replay a failed or interrupted firing. Other
    bodies commit a receipt and unknown-completion evidence before invocation,
    because an exception cannot undo an external effect. Acquisition failures
    never escape into startup or the scheduler."""
    active = None
    try:
        key = fire_key(spec)
        active = db.claim_job(f"active:{spec.name}", "running", lease_seconds=leases.LEASE_SECONDS)
        if not active:
            log.info("job %s: skipped, another firing is running", spec.name)
            return
        with leases.held(active):
            if spec.retry_safe:
                _run_retryable(spec, key)
            else:
                _run_once(spec, key)
    except Exception as exc:
        with contextlib.suppress(Exception):
            record_outcome(spec.name, "error", f"{type(exc).__name__}: {exc}")
        log.exception("job %s: acquisition or settlement failed", spec.name)
    finally:
        if active:
            try:
                db.release_job(f"active:{spec.name}", "running", active)
            except Exception:
                log.exception("job %s: could not release its active claim", spec.name)


def _run_retryable(spec: JobSpec, key: str) -> None:
    token = db.claim_job(f"fire:{spec.name}", key, lease_seconds=leases.LEASE_SECONDS)
    if not token:
        log.info("job %s: skipped, firing %s is claimed", spec.name, key)
        return
    succeeded = False
    try:
        with leases.held(token):
            succeeded = _run_claimed(spec)
    finally:
        if succeeded:
            db.settle_job(f"fire:{spec.name}", key, token)
        else:
            db.release_job(f"fire:{spec.name}", key, token)


def _run_once(spec: JobSpec, key: str) -> None:
    with db.transaction():
        if not db.claim_job(f"fire:{spec.name}", key):
            log.info("job %s: skipped, firing %s is claimed", spec.name, key)
            return
        # The receipt and evidence commit together before a socket or file
        # effect. A killed process then leaves a visible reconciliation need,
        # not a lapsed receipt that silently repeats an uncertain operation.
        outcome_id = db.execute(
            "INSERT INTO job_outcomes (job, status, detail, duration_ms, created_at)"
            " VALUES (?, 'error', ?, 0, ?) RETURNING id",
            (
                spec.name,
                f"Completion unknown for firing {key}. Check its effects before a manual retry.",
                db.now(),
            ),
        )
    _run_claimed(spec, outcome_id=outcome_id)


def _run_claimed(spec: JobSpec, *, outcome_id: int | None = None) -> bool:
    log.info("job %s: start", spec.name)
    start = time.monotonic()
    try:
        result = spec.fn()
    except Exception as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        unknown = "Completion unknown. " if not spec.retry_safe else ""
        with contextlib.suppress(Exception):
            record_outcome(
                spec.name,
                "error",
                f"{unknown}{type(exc).__name__}: {exc}",
                elapsed,
                outcome_id=outcome_id,
            )
        log.exception("job %s: FAILED", spec.name)
        return False

    elapsed = int((time.monotonic() - start) * 1000)
    declared = result.get("status") if isinstance(result, dict) else None
    # Only these explicit outcomes affect health. A domain row's ordinary
    # status value cannot turn a successful job into a failure.
    status = "error" if declared in ("partial", "error") else "ok"
    try:
        if declared == "noop":
            # A no-op must not replace the winner's last outcome. Unsafe jobs
            # already wrote provisional evidence, so remove only that row.
            if outcome_id is not None:
                db.execute("DELETE FROM job_outcomes WHERE id = ?", (outcome_id,))
            log.info("job %s: done (noop)", spec.name)
            return True
        detail = _outcome_detail(result)
        record_outcome(spec.name, status, detail, elapsed, outcome_id=outcome_id)
        log.info("job %s: done (%s) %s", spec.name, declared or status, detail)
    except Exception:
        # The body has finished. Losing its outcome write cannot turn its
        # successful effects into a retry of the same firing.
        log.exception("job %s: could not record its outcome", spec.name)
    return status == "ok"


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
