from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import config, db
from .routes import api, chat
from .services import admin, blockers, digest


def _start_scheduler():
    """Programmatic background jobs: blocker escalation sweep (hourly),
    daily digest (07:00 UTC), daily backup (03:00 UTC)."""
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(blockers.sweep_escalations, "interval", hours=1,
                      id="blocker-sweep")
    scheduler.add_job(lambda: digest.publish_digest(actor="scheduler"),
                      "cron", hour=7, minute=0, id="daily-digest")
    scheduler.add_job(admin.backup_if_stale, "cron", hour=3, minute=0,
                      id="daily-backup")
    scheduler.start()
    return scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    admin.backup_if_stale()
    blockers.sweep_escalations()
    scheduler = _start_scheduler() if config.SCHEDULER_ENABLED else None
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Strands Team Platform", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


app.include_router(api.router)
app.include_router(chat.router)


@app.get("/health")
def health():
    return {"ok": True, "provider": config.MODEL_PROVIDER, "model": config.MODEL_ID}
