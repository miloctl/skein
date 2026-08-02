import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from . import config, db
from .routes import api, chat, private, slack, webhooks
from .services.activity import chain_health
from .services.jobs import JOBS, job_health, run_job
from .services.settings import effective_context_strategy
from .telemetry import setup_telemetry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("skein")


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
    # same rule for the field-guide registry: malformed knots.yaml aborts boot
    # here, instead of 500ing the first /field-guide request at 3pm
    from .services import fieldguide

    fieldguide.registry()
    # reserve the built-in agent identities as kind=agent BEFORE any request
    # can claim them: a weak X-User minting "agent" as a human row would
    # permanently shadow the chat identity's writes
    from .services.users import ensure_user

    ensure_user("agent", kind="agent")
    # SKEIN_MCP_USER is operator-supplied, and the obvious thing to type is
    # your own name — which reserves it as an AGENT identity, and agent
    # identities are refused on REST and on every private surface. An existing
    # human row is safe (INSERT OR IGNORE leaves it alone), so the trap is a
    # fresh install. Say so at boot instead of letting the operator find out
    # by being locked out; the recovery is a rename of the agent row.
    mcp_user = os.getenv("SKEIN_MCP_USER", "mcp-agent")
    minted = db.query_one("SELECT 1 FROM users WHERE name = ?", (mcp_user,)) is None
    ensure_user(mcp_user, kind="agent")
    if minted and mcp_user != "mcp-agent":
        log.warning(
            "reserved %r as an AGENT identity (SKEIN_MCP_USER). Agent identities cannot"
            " use REST or the private surfaces. If that is your own name, free it with"
            " POST /api/users/%s/rename before picking it in the UI.",
            mcp_user,
            mcp_user,
        )
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


@app.middleware("http")
async def bearer_auth(request: Request, call_next):
    """Optional shared-token auth: enforced only when SKEIN_API_TOKEN is set.
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


# JSON payloads compress ~77%; added before CORS so CORS stays outermost
app.add_middleware(GZipMiddleware, minimum_size=1000)


# added AFTER bearer_auth so CORS is the OUTERMOST layer — a 401 short-circuit
# must still carry Access-Control-Allow-Origin, or the browser reports an
# opaque CORS failure instead of a readable auth error
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    # X-User/X-Client make every call non-simple, so each one preflights;
    # a 10-minute cache meant a phone re-preflighted constantly
    max_age=7200,
)


# Malformed input is the caller's error. The rule is the classification, not
# this list of handlers: if a request body, path, or query can produce the
# exception, it maps to a 4xx here. If only our own state can produce it, it
# stays a 500. Add a handler when a 500 traces back to something a caller
# sent. Add it for that reason, never because an exception class looked
# familiar. A handler never echoes the rejected value back. The caller already
# has it, and rendering it turned a 50 MB body into a 50 MB response.
@app.exception_handler(db.NotFound)
async def not_found_handler(request: Request, exc: db.NotFound):
    # one rule for the surface: entity-lookup failures are 404, everywhere
    # an owner-scoped miss is a 404 too, because any other status confirms the row exists
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(OverflowError)
async def overflow_error_handler(request: Request, exc: OverflowError):
    # absurd ints (ids > 2^63, weeks=1e18) must be a 400, never a 500
    return JSONResponse(status_code=400, content={"detail": "value out of range"})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """FastAPI's default handler renders the rejected value back into the body
    with jsonable_encoder. That recurses on a deeply nested body (2000 nested
    arrays hit RecursionError and returned a plain-text 500 to an unauthorized
    caller), and it echoed a 50 MB string back verbatim, turning every write
    endpoint into a 1:1 bandwidth amplifier. The value the caller already sent
    is worth nothing back to them, so this drops it and keeps the part that
    helps: where the error is and what was wrong.
    """
    errors = []
    for err in exc.errors()[:20]:
        loc = ".".join(str(p) for p in err.get("loc", ()))
        errors.append({"loc": loc, "msg": str(err.get("msg", ""))[:300], "type": err.get("type")})
    first = errors[0] if errors else {}
    detail = f"{first.get('loc', 'request body')}: {first.get('msg', 'is not valid')}"
    return JSONResponse(status_code=422, content={"detail": detail, "errors": errors})


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
        "model": config.MODEL_ID if config.EFFECTIVE_PROVIDER != "mock" else "",
        "provider_error": config.MODEL_PROVIDER_ERROR,
        "embeddings_error": config.EMBEDDINGS_ERROR,
        "overlay_errors": config.overlay_errors(),
        # the EFFECTIVE strategy, not the env default — the toggle overrides it,
        # and two surfaces disagreeing about one fact is the bug this avoids
        "context_strategy": effective_context_strategy(),
        "context_error": config.CONTEXT_STRATEGY_ERROR,
        "jobs": job_health(),
        "activity_chain": chain_health(),
    }
