from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import config, db
from .routes import api, chat, slack
from .services import admin, blockers, digest, notifications
from .telemetry import setup_telemetry


def _start_scheduler():
    """Programmatic background jobs (UTC): hourly blocker escalation sweep,
    daily digest (07:00), twice-daily notification flush (07:05 / 15:00),
    daily backup (03:00)."""
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler(daemon=True, timezone="UTC")
    scheduler.add_job(blockers.sweep_escalations, "interval", hours=1,
                      id="blocker-sweep")
    scheduler.add_job(lambda: digest.publish_digest(actor="scheduler"),
                      "cron", hour=7, minute=0, id="daily-digest")
    scheduler.add_job(notifications.flush_digest_tier, "cron",
                      hour="7,15", minute=5, id="notification-flush")
    scheduler.add_job(admin.backup_if_stale, "cron", hour=3, minute=0,
                      id="daily-backup")
    scheduler.start()
    return scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    admin.backup_if_stale()
    blockers.sweep_escalations()
    setup_telemetry()
    scheduler = _start_scheduler() if config.SCHEDULER_ENABLED else None
    yield
    if scheduler:
        scheduler.shutdown(wait=False)
    from .agents.mcp_tools import shutdown_mcp

    shutdown_mcp()


app = FastAPI(title="Strands Team Platform", lifespan=lifespan)

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
    if config.API_TOKEN and request.url.path.startswith("/api") \
            and not request.url.path.startswith(open_paths):
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {config.API_TOKEN}":
            return JSONResponse(status_code=401, content={"detail": "invalid API token"})
    return await call_next(request)


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


app.include_router(api.router)
app.include_router(chat.router)
app.include_router(slack.router)


@app.get("/health")
def health():
    return {"ok": True, "provider": config.MODEL_PROVIDER, "model": config.MODEL_ID}
