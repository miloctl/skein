import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from . import config, db
from .routes import api, auth, chat, private, slack, webhooks
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

    try:
        ensure_user("agent", kind="agent")
    except ValueError as exc:
        # a legacy human row named `Agent` was legal before the collision
        # guard; it must not brick a boot nobody can reach the rename route on
        log.error("the built-in 'agent' identity is unavailable: %s", exc)
    # SKEIN_MCP_USER is operator-supplied, and the obvious thing to type is
    # your own name — which reserves it as an AGENT identity, and agent
    # identities are refused on REST and on every private surface. An existing
    # human row is safe (INSERT OR IGNORE leaves it alone), so the trap is a
    # fresh install. Say so at boot instead of letting the operator find out
    # by being locked out; the recovery is a rename of the agent row.
    mcp_user = os.getenv("SKEIN_MCP_USER", "mcp-agent")
    minted = db.query_one("SELECT 1 FROM users WHERE name = ?", (mcp_user,)) is None
    try:
        ensure_user(mcp_user, kind="agent")
    except ValueError as exc:
        # operator-supplied config NEVER takes down the REST API — the same
        # rule the model provider follows when it degrades to mock. Without
        # this, one typo in SKEIN_MCP_USER refuses every request in the
        # deployment, which is worse than the lockout the warning below
        # exists to prevent.
        minted = False
        log.error(
            "SKEIN_MCP_USER=%r cannot be reserved: %s. The MCP identity is"
            " unavailable until this is changed. The REST API is unaffected.",
            mcp_user,
            exc,
        )
    if minted and mcp_user != "mcp-agent":
        log.warning(
            "reserved %r as an AGENT identity (SKEIN_MCP_USER). Agent identities cannot"
            " use REST or the private surfaces. If that is your own name, free it with"
            " POST /api/users/%s/rename before picking it in the UI.",
            mcp_user,
            mcp_user,
        )
    if config.AUTH_ERROR:
        # the rejected value goes to the LOG, never to the 503 body: that
        # response is served to unauthenticated callers, and an operator who
        # pastes a secret into the wrong variable must not broadcast it
        log.error(
            "auth is misconfigured (SKEIN_AUTH_MODE=%r): %s — every /api request"
            " is refused until this is fixed",
            config.AUTH_MODE,
            config.AUTH_ERROR,
        )
    elif config.AUTH_MODE == "trusted-header":
        log.warning(
            "SKEIN_AUTH_MODE=trusted-header: identity is the self-asserted X-User"
            " header. This mode is for a trusted network or local development. If the"
            " deployment is shared, set SKEIN_AUTH_MODE=api-key or oidc."
        )
    # a row holding a system-actor name predates the wall that now refuses it.
    # The doors refuse the identity, so nothing writes under it — but the
    # person cannot work until it moves, and moving a person is rename_user's
    # job, not a migration's: it knows all 47 attribution columns and the
    # private notes DB that SQL cannot reach.
    from .services.users import reserved_name_rows

    for stuck in reserved_name_rows():
        log.warning(
            "roster row '%s' holds a name reserved for the system, so every"
            " request as that identity is refused. An administrator moves it"
            " with POST /api/users/%s/rename",
            stuck,
            stuck,
        )

    if config.API_TOKEN and config.AUTH_MODE != "trusted-header":
        log.warning(
            "SKEIN_API_TOKEN has no effect with SKEIN_AUTH_MODE=%s — that mode"
            " already demands a per-caller credential on every request",
            config.AUTH_MODE,
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


# /docs, /redoc and /openapi.json sit OUTSIDE /api, so the perimeter
# middleware never sees them. In the locked modes the endpoint map is
# credentialed surface like everything else — an unauthenticated caller must
# not be able to read the whole admin route list.
_open_docs = config.AUTH_MODE == "trusted-header"
app = FastAPI(
    title="Skein",
    description="Many strands. One formation.",
    lifespan=lifespan,
    docs_url="/docs" if _open_docs else None,
    redoc_url="/redoc" if _open_docs else None,
    openapi_url="/openapi.json" if _open_docs else None,
)


@app.middleware("http")
async def perimeter_auth(request: Request, call_next):
    """Perimeter gate, by SKEIN_AUTH_MODE. Route dependencies (routes/deps.py)
    resolve WHO the caller is; this layer only refuses requests that carry no
    valid credential at all — so a future route that forgets a user dependency
    is still not open in api-key/oidc mode. /health stays open for container
    checks; Slack verifies its own signature.

    trusted-header mode keeps the historical behavior: open unless
    SKEIN_API_TOKEN sets a shared perimeter token.
    """
    # calendar.ics: calendar clients can't send headers — the route checks
    # its own dedicated ?token= secret.
    # /api/auth/: the sign-in flow itself, which by definition runs before the
    # caller has a credential. Both routes there are written for that (public
    # client parameters, and a relay of a code the browser already holds).
    # /api/webhooks/forge: a git forge cannot hold a personal key or sign in,
    # so it proves itself with an HMAC over the body (deps.verify_forge_
    # signature), the way Slack does. Without this the endpoint answers every
    # delivery with "get a personal API key" in api-key, oidc, and token mode.
    open_paths = (
        "/health",
        "/api/slack/",
        "/api/calendar.ics",
        "/api/auth/",
        "/api/webhooks/forge",
    )
    # OPTIONS must pass through so CORS preflights (which carry no Authorization
    # header) reach CORSMiddleware instead of 401ing here.
    if (
        request.method == "OPTIONS"
        or not request.url.path.startswith("/api")
        or request.url.path.startswith(open_paths)
    ):
        return await call_next(request)
    if config.AUTH_ERROR:
        # fail CLOSED: a typo'd mode must not silently open the deployment
        return JSONResponse(status_code=503, content={"detail": config.AUTH_ERROR})
    if config.AUTH_MODE == "trusted-header" and not config.API_TOKEN:
        return await call_next(request)
    from .routes.deps import (
        INVALID_KEY,
        NEED_KEY,
        NEED_LOGIN,
        agent_on_rest,
        agent_on_signin,
    )
    from .services.api_keys import PREFIX, verify_key
    from .services.users import is_agent, reserved_refusal

    auth = request.headers.get("Authorization", "")
    # The shared token is checked BEFORE the key prefix: an operator whose
    # SKEIN_API_TOKEN happens to begin with the key prefix would otherwise be
    # routed into verify_key and locked out of their own deployment.
    if config.AUTH_MODE == "trusted-header":
        import hmac

        if hmac.compare_digest(auth, f"Bearer {config.API_TOKEN}"):
            return await call_next(request)
    if auth.startswith(f"Bearer {PREFIX}"):
        # verify_key and is_agent hit SQLite; oidc.validate does network I/O
        # and RSA work. This middleware is async, so running any of it inline
        # blocks the event loop for EVERY concurrent request.
        owner = await run_in_threadpool(verify_key, auth[7:])
        if owner is None:
            return JSONResponse(status_code=401, content={"detail": INVALID_KEY})
        # the same agent wall routes/deps.py applies. It belongs here too:
        # the read routes that carry no user dependency never reach deps, so
        # in api-key/oidc mode this is their only gate.
        if await run_in_threadpool(is_agent, owner):
            return JSONResponse(status_code=403, content={"detail": agent_on_rest(owner)})
        # and the reserved-name wall, for exactly the same reason: deps.py
        # refuses this credential, but the read routes never reach deps.
        reserved = await run_in_threadpool(reserved_refusal, owner)
        if reserved:
            return JSONResponse(status_code=403, content={"detail": reserved})
        request.state.auth_key_owner = owner
        return await call_next(request)
    if config.AUTH_MODE == "trusted-header":
        return JSONResponse(status_code=401, content={"detail": "invalid API token"})
    if config.AUTH_MODE == "oidc" and auth.startswith("Bearer "):
        from . import oidc

        try:
            claims = await run_in_threadpool(oidc.validate, auth[7:])
            name, _ = oidc.principal(claims)
        except oidc.OIDCUnavailable as exc:
            # the token was never judged, so this is our fault to report, not
            # the caller's to fix by signing in again
            return JSONResponse(status_code=503, content={"detail": str(exc)})
        except oidc.OIDCError as exc:
            return JSONResponse(status_code=401, content={"detail": str(exc)})
        # the agent wall again, for the same reason it is above: the read
        # routes that carry no user dependency never reach deps, so a sign-in
        # naming an agent row would otherwise read them all.
        if await run_in_threadpool(is_agent, name):
            return JSONResponse(status_code=403, content={"detail": agent_on_signin(name)})
        reserved = await run_in_threadpool(reserved_refusal, name)
        if reserved:
            return JSONResponse(status_code=403, content={"detail": reserved})
        request.state.auth_claims = claims
        return await call_next(request)
    detail = NEED_KEY if config.AUTH_MODE == "api-key" else NEED_LOGIN
    return JSONResponse(status_code=401, content={"detail": detail})


# JSON payloads compress ~77%; added before CORS so CORS stays outermost
app.add_middleware(GZipMiddleware, minimum_size=1000)


# added AFTER perimeter_auth so CORS is the OUTERMOST layer — a 401 short-circuit
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
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(private.router)
app.include_router(slack.router)
app.include_router(webhooks.router)


@app.get("/health")
def health():
    return {
        "ok": True,
        "auth_mode": config.AUTH_MODE,
        "auth_error": config.AUTH_ERROR,
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
