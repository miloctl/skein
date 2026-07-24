import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import config, db
from .routes import api, chat, slack, webhooks
from .services import (
    admin,
    blockers,
    collab,
    context_pack,
    digest,
    notifications,
    portfolio,
    weekly,
)
from .telemetry import setup_telemetry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("strands")


def _job(name, fn):
    """Wrap a scheduled job with start/finish/error logging."""

    def run():
        log.info("job %s: start", name)
        try:
            result = fn()
            log.info("job %s: done %s", name, result if result is not None else "")
        except Exception:
            log.exception("job %s: FAILED", name)

    return run


def _start_scheduler():
    """Programmatic background jobs (UTC): hourly blocker escalation sweep,
    daily digest (07:00), twice-daily notification flush (07:05 / 15:05),
    daily backup (03:00), Monday weekly-plan draft (06:00) + stale-WIP nudge
    (06:15), daily stale-decision sweep (06:30), daily context-pack refresh
    (05:00), daily forecast snapshot (05:15), daily findings run (06:50). Jobs are once-only via db.claim_job or CAS status flips, so an
    accidental multi-worker deployment can't double-run them."""
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler(daemon=True, timezone="UTC")
    scheduler.add_job(
        _job("blocker-sweep", blockers.sweep_escalations), "interval", hours=1, id="blocker-sweep"
    )
    scheduler.add_job(
        _job("daily-digest", lambda: digest.publish_digest(actor="scheduler")),
        "cron",
        hour=7,
        minute=0,
        id="daily-digest",
    )
    scheduler.add_job(
        _job("notification-flush", lambda: notifications.flush_digest_tier(claim=True)),
        "cron",
        hour="7,15",
        minute=5,
        id="notification-flush",
    )
    scheduler.add_job(
        _job("daily-backup", admin.backup_if_stale), "cron", hour=3, minute=0, id="daily-backup"
    )
    scheduler.add_job(
        _job("weekly-plan", lambda: weekly.propose_weekly_plan(actor="scheduler")),
        "cron",
        day_of_week="mon",
        hour=6,
        minute=0,
        id="weekly-plan",
    )
    scheduler.add_job(
        _job("stale-wip-nudge", portfolio.nudge_stale_wip),
        "cron",
        day_of_week="mon",
        hour=6,
        minute=15,
        id="stale-wip-nudge",
    )
    scheduler.add_job(
        _job("stale-decisions", collab.sweep_stale_decisions),
        "cron",
        hour=6,
        minute=30,
        id="stale-decisions",
    )
    scheduler.add_job(
        _job("context-pack", lambda: context_pack.publish_pack(actor="scheduler")),
        "cron",
        hour=5,
        minute=0,
        id="context-pack",
    )
    from .services.adoption import snapshot_forecasts

    scheduler.add_job(
        _job("forecast-snapshot", snapshot_forecasts),
        "cron",
        hour=5,
        minute=15,
        id="forecast-snapshot",
    )
    from .services.insights import run_findings

    scheduler.add_job(
        _job("findings", lambda: run_findings(actor="scheduler")),
        "cron",
        hour=6,
        minute=50,
        id="findings",
    )
    scheduler.start()
    return scheduler


def _startup_forecast_snapshot():
    from .services.adoption import snapshot_forecasts

    return snapshot_forecasts()


def _startup_findings():
    from .services.insights import run_findings

    return run_findings(actor="scheduler")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()  # a failed migration SHOULD abort startup — everything else must not
    # weekly-plan/nudge claims make the catch-up calls idempotent — they fill
    # in for cron firings missed while the process was down (no misfire replay)
    for name, fn in (
        ("startup-backup", admin.backup_if_stale),
        ("startup-sweep", blockers.sweep_escalations),
        ("startup-weekly-plan", lambda: weekly.propose_weekly_plan(actor="scheduler")),
        ("startup-wip-nudge", portfolio.nudge_stale_wip),
        ("startup-forecast-snapshot", _startup_forecast_snapshot),
        ("startup-findings", _startup_findings),
    ):
        try:
            fn()
        except Exception:
            log.exception("%s failed (continuing startup)", name)
    setup_telemetry()
    scheduler = _start_scheduler() if config.SCHEDULER_ENABLED else None
    yield
    if scheduler:
        scheduler.shutdown(wait=False)
    from .agents.mcp_tools import shutdown_mcp

    shutdown_mcp()


app = FastAPI(title="Skein", description="Many strands. One formation.", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def bearer_auth(request: Request, call_next):
    """Optional shared-token auth: enforced only when STRANDS_API_TOKEN is set.
    /health stays open for container checks; Slack verifies its own signature."""
    open_paths = ("/health", "/api/slack/")
    # OPTIONS must pass through so CORS preflights (which carry no Authorization
    # header) reach CORSMiddleware instead of 401ing here.
    if (
        config.API_TOKEN
        and request.method != "OPTIONS"
        and request.url.path.startswith("/api")
        and not request.url.path.startswith(open_paths)
    ):
        import hmac

        from .services.api_keys import PREFIX, verify_key

        auth = request.headers.get("Authorization", "")
        shared_ok = hmac.compare_digest(auth, f"Bearer {config.API_TOKEN}")
        key_ok = auth.startswith(f"Bearer {PREFIX}") and verify_key(auth[7:]) is not None
        if not (shared_ok or key_ok):
            return JSONResponse(status_code=401, content={"detail": "invalid API token"})
    return await call_next(request)


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(OverflowError)
async def overflow_error_handler(request: Request, exc: OverflowError):
    # absurd ints (ids > 2^63, weeks=1e18) must be a 400, never a 500
    return JSONResponse(status_code=400, content={"detail": "value out of range"})


app.include_router(api.router)
app.include_router(chat.router)
app.include_router(slack.router)
app.include_router(webhooks.router)


@app.get("/health")
def health():
    return {"ok": True, "provider": config.MODEL_PROVIDER, "model": config.MODEL_ID}
