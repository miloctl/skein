import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import config, db
from .routes import api, chat, private, slack, webhooks
from .services.jobs import JOBS, job_health, run_job
from .telemetry import setup_telemetry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("strands")


def _start_scheduler():
    """Background jobs (UTC), one per services.jobs.JOBS entry. Jobs are
    once-only via db.claim_job or CAS status flips, so an accidental
    multi-worker deployment can't double-run them."""
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler(daemon=True, timezone="UTC")
    for spec in JOBS:
        scheduler.add_job(lambda spec=spec: run_job(spec), id=spec.name, **spec.trigger)
    scheduler.start()
    return scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()  # a failed migration SHOULD abort startup — everything else must not
    # reserve the built-in agent identities as kind=agent BEFORE any request
    # can claim them: a weak X-User minting "agent" as a human row would
    # permanently shadow the chat identity's writes
    from .services.users import ensure_user

    ensure_user("agent", kind="agent")
    ensure_user(os.getenv("STRANDS_MCP_USER", "mcp-agent"), kind="agent")
    # claim-guarded catch-up runs fill in for cron firings missed while the
    # process was down (no misfire replay); run_job never raises
    for spec in JOBS:
        if spec.catch_up:
            run_job(spec)
    setup_telemetry()
    from .agents.narrator import register_narrator

    register_narrator()  # composition root: agents plug into services here
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
    # calendar.ics: calendar clients can't send headers — the route checks
    # ?token= itself when a shared token is configured
    open_paths = ("/health", "/api/slack/", "/api/calendar.ics")
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
app.include_router(private.router)
app.include_router(slack.router)
app.include_router(webhooks.router)


@app.get("/health")
def health():
    return {
        "ok": True,
        "provider": config.MODEL_PROVIDER,
        "model": config.MODEL_ID,
        "jobs": job_health(),
    }
