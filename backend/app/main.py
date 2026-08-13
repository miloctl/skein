import logging
import sqlite3
from collections.abc import Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from functools import partial
from inspect import isawaitable
from typing import Any, cast

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from . import config, db, ratelimit
from .extensions import AppSettings, ExtensionRegistry, SkeinModule
from .extensions.contracts import JobContribution, JobExecutionContext
from .extensions.core import core_module
from .extensions.fastapi import contributed_route_policy, enforce_mutation_policy
from .extensions.registry import validate_core_tool_names
from .identity_names import (
    activate_runtime_machine_subjects,
    deactivate_runtime_machine_subjects,
)
from .public.errors import PublicError
from .services import handoff
from .services.activity import chain_health
from .services.jobs import JOBS, JobSpec, job_health, run_job
from .services.personas import unlisted_model_warnings
from .services.settings import effective_context_strategy, model_pick_state
from .telemetry import setup_telemetry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("skein")


def _job_specs(registry: ExtensionRegistry, settings: AppSettings) -> tuple[JobSpec, ...]:
    from .public.work import WorkItems

    policy = registry.policy_engine
    work_items = WorkItems(policy)

    def invoke(contribution: JobContribution) -> Any:
        from .extensions.policy import PolicyEffect, PolicyInput, PolicyResource

        subject = registry.service_subject(contribution.service_identity)
        decision = policy.decide(
            PolicyInput(
                subject,
                contribution.policy_action,
                PolicyResource("job", contribution.name),
                "background",
                agent=subject.name,
                tool=contribution.name,
                tool_effect=contribution.effect,
                tool_risk=contribution.risk,
            )
        )
        if decision.effect != PolicyEffect.PERMIT:
            return {
                "status": "error",
                "error_code": (
                    "POLICY_REVIEW_UNSUPPORTED"
                    if decision.effect == PolicyEffect.REVIEW
                    else "POLICY_DENIED"
                ),
            }
        seconds = max(int(contribution.period_hours * 3600), 60)
        run_id = f"{contribution.name}:{int(datetime.now(UTC).timestamp()) // seconds}"
        if not contribution.name.startswith("skein.core.") and not db.claim_job(
            f"extension:{contribution.name}", run_id
        ):
            return {"skipped": "this job run is already claimed", "run_id": run_id}
        from .public.work import _bind_execution_context

        if contribution.name == "skein.core.agent-run":
            # This core adapter needs the full trusted composition root. The
            # public JobExecutionContext stays narrow for private jobs, while
            # every model-facing tool in an unattended turn receives the same
            # policy engine and extension registry as an interactive turn.
            from .services.agent_runner import run as run_agents

            return run_agents(actor=subject.name, extensions=registry, policy=policy)
        if contribution.name.startswith("skein.core."):
            return contribution.handler(
                _bind_execution_context(
                    work_items,
                    JobExecutionContext(policy, work_items, subject, run_id, contribution.name),
                    subject=subject,
                    namespace=contribution.name,
                    receipt_namespace=f"job:{contribution.name}",
                    correlation_id=run_id,
                )
            )
        from .public._owner_work import run_bounded_work_handler

        # The owner-dispatch facade revokes the job's WorkItems authority at
        # the deadline. A bare executor only stopped WAITING: the worker
        # thread kept a live context and wrote core rows AFTER the run was
        # recorded COMPLETION_UNKNOWN.
        def invoke_handler(services: JobExecutionContext, _request: Any) -> Any:
            return contribution.handler(services)

        try:
            bounded = run_bounded_work_handler(
                policy,
                lambda bound_work_items: _bind_execution_context(
                    bound_work_items,
                    JobExecutionContext(
                        policy,
                        bound_work_items,
                        subject,
                        run_id,
                        contribution.name,
                    ),
                    subject=subject,
                    namespace=contribution.name,
                    receipt_namespace=f"job:{contribution.name}",
                    correlation_id=run_id,
                ),
                invoke_handler,
                None,
                contribution.timeout_seconds,
                thread_name="skein-extension-job",
            )
            if bounded.timed_out:
                return {
                    "status": "error",
                    "error_code": (
                        "COMPLETION_UNKNOWN"
                        if contribution.effect in ("write", "unknown")
                        else "JOB_TIMEOUT"
                    ),
                }
            result = bounded.value
            if isawaitable(result):
                close = getattr(result, "close", None)
                if close is not None:
                    close()
                return {"status": "error", "error_code": "ASYNC_HANDLER_UNSUPPORTED"}
            if not isinstance(result, dict):
                return {
                    "status": "error",
                    "error_code": (
                        "COMPLETION_UNKNOWN"
                        if contribution.effect in ("write", "unknown")
                        else "INVALID_JOB_RESULT"
                    ),
                }
            return result
        except Exception:
            log.exception("extension job failed", extra={"job": contribution.name})
            return {
                "status": "error",
                "error_code": (
                    "COMPLETION_UNKNOWN"
                    if contribution.effect in ("write", "unknown")
                    else "JOB_FAILED"
                ),
            }

    specs = []
    for contribution in registry.jobs:
        name = contribution.name
        if name.startswith("skein.core."):
            name = name.removeprefix("skein.core.")
        specs.append(
            JobSpec(
                name=name,
                fn=partial(invoke, contribution),
                trigger=dict(contribution.trigger),
                period_hours=contribution.period_hours,
                catch_up=contribution.catch_up,
            )
        )
    from .extensions.contracts import EventExecutionContext
    from .public.events import dispatch_events

    event_context = EventExecutionContext(policy, work_items, registry.service_subject)

    def dispatch_extension_events() -> dict[str, Any]:
        # One dispatcher per minute WINDOW, not true single-flight: a batch
        # that outlives its minute can still overlap the next window's
        # winner, which the at-least-once contract (handlers key on
        # event_id) is what survives. The claim removes the common two-worker
        # same-minute duplication; a per-delivery lease is the ROADMAP item.
        # status "noop" on the lost claim: run_job records nothing for it, so
        # the loser's every-minute skip cannot keep last-success fresh while
        # the winner fails.
        window = f"events:{int(datetime.now(UTC).timestamp()) // 60}"
        if not db.claim_job("extension-events", window):
            return {"skipped": "this dispatch window is already claimed", "status": "noop"}
        counts = dict(dispatch_events(registry.events, event_context))
        if counts.get("failed") or counts.get("dead"):
            # `partial` is what run_job records as an error — without it a
            # night of dead deliveries left /health green.
            return {**counts, "status": "partial"}
        return counts

    specs.append(
        JobSpec(
            name="extension-events",
            fn=dispatch_extension_events,
            trigger={"trigger": "interval", "minutes": 1},
            period_hours=1 / 60,
            # catch_up drained the whole backlog synchronously before the
            # lifespan yielded — a slow subscriber delayed readiness by its
            # full timeout per event. The one-minute interval covers boot.
            catch_up=False,
        )
    )
    return tuple(specs)


def _start_scheduler(
    specs: Sequence[JobSpec] = JOBS,
    timezone: str | None = None,
):
    """Background jobs in the team's zone, from the composed registry.

    Jobs are once-only via db.claim_job or CAS status flips, so an accidental
    multi-worker deployment cannot double-run them.

    The hours in JOBS are the hours a person experiences: the 07:00 digest is
    07:00 where the team works. APScheduler resolves the DST edges — a job at
    an hour that a spring-forward skips runs once, not zero times."""
    from apscheduler.schedulers.background import (
        BackgroundScheduler,
    )

    scheduler = BackgroundScheduler(daemon=True, timezone=timezone or config.TZ_NAME)
    for spec in specs:
        scheduler.add_job(lambda spec=spec: run_job(spec), id=spec.name, **spec.trigger)
    scheduler.start()
    return scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: AppSettings = app.state.skein_settings
    registry: ExtensionRegistry = app.state.skein_registry
    specs = _job_specs(registry, settings)
    db.init_db()  # a failed migration MUST abort startup — everything else must not
    from .services.users import identity_ownership_conflicts

    for conflict in identity_ownership_conflicts():
        log.error(
            "conflicting identity ownership (%s): %s. Run python -m app.identity_audit"
            " before these identities authenticate or run as machines.",
            conflict["kind"],
            ", ".join(conflict["names"]),
        )
    for contribution in registry.migrations:
        contribution.store.migrate(contribution.migrations)
    # same rule for the field-guide registry: malformed knots.yaml aborts boot
    # here, instead of 500ing the first /field-guide request at 3pm
    from .services import fieldguide, playbooks

    fieldguide.registry()
    content_errors = playbooks.validate_startup(
        {contribution.name for contribution in registry.workflow_actions}
    )
    if content_errors:
        raise RuntimeError("invalid playbook content: " + "; ".join(content_errors))
    from .extensions.registry import validate_machine_identity_ownership

    # Invalid contributed machine ownership is an application composition
    # error. An invalid operator-supplied MCP actor disables only MCP; REST
    # stays available, as it did before the extension composition work.
    validate_machine_identity_ownership(registry)
    mcp_identity_available = True
    try:
        validate_machine_identity_ownership(
            registry,
            (("MCP actor", settings.mcp_user),),
        )
    except RuntimeError as exc:
        mcp_identity_available = False
        log.error(
            "SKEIN_MCP_USER=%r cannot be reserved: %s. The MCP identity is"
            " unavailable until this is changed. The REST API is unaffected.",
            settings.mcp_user,
            exc,
        )
    # reserve the built-in agent identities as kind=agent BEFORE any request
    # can claim them: a weak X-User minting "agent" as a human row would
    # permanently shadow the chat identity's writes
    from .services.activity import SYSTEM_ACTORS
    from .services.users import _reserve_core_agent_identity, ensure_agent_identity

    try:
        _reserve_core_agent_identity("agent")
    except ValueError as exc:
        # a legacy human row named `Agent` was legal before the collision
        # guard; it must not brick a boot nobody can reach the rename route on
        log.error("the built-in 'agent' identity is unavailable: %s", exc)
    for specialist in registry.specialists:
        try:
            ensure_agent_identity(specialist.name, owner=f"specialist:{specialist.name}")
        except ValueError as exc:
            raise RuntimeError(
                f"specialist identity {specialist.name!r} is already owned: {exc}"
            ) from exc
    for identity in registry.service_identities:
        # Core machine actors are reserved independently of the users table.
        # `ensure_user` intentionally refuses those names, and no human entry
        # point can claim them. Private service names still get an agent row so
        # they cannot be claimed later through a legacy or direct user path.
        if identity.subject not in SYSTEM_ACTORS:
            try:
                ensure_agent_identity(identity.subject, owner=f"service:{identity.name}")
            except ValueError as exc:
                raise RuntimeError(
                    f"service identity {identity.subject!r} is already owned: {exc}"
                ) from exc
    # SKEIN_MCP_USER is operator-supplied, and the obvious thing to type is
    # your own name — which reserves it as an AGENT identity, and agent
    # identities are refused on REST and on every private surface. An existing
    # human row is unsafe because it would merge human and machine ownership.
    # Disable MCP and keep REST available when that collision exists.
    mcp_user = settings.mcp_user
    minted = False
    if mcp_identity_available:
        minted = db.query_one("SELECT 1 FROM users WHERE name = ?", (mcp_user,)) is None
        try:
            ensure_agent_identity(mcp_user, owner="mcp")
        except ValueError as exc:
            # Operator-supplied config never takes down REST. The same rule
            # lets a bad model provider degrade to deterministic mode.
            minted = False
            mcp_identity_available = False
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
    if config.TZ_ERROR:
        # the rejected value, for whoever runs the server. TZ_ERROR itself is
        # served on /health and never carries it (config.py::TZ_REJECTED) —
        # the same split the auth fault below makes, for the same reason.
        log.warning(
            "time zone is misconfigured (SKEIN_TZ=%r): %s",
            config.TZ_REJECTED,
            config.TZ_ERROR,
        )
    if settings.auth_error:
        # the rejected value goes to the LOG, never to the 503 body: that
        # response is served to unauthenticated callers, and an operator who
        # pastes a secret into the wrong variable must not broadcast it
        log.error(
            "auth is misconfigured (SKEIN_AUTH_MODE=%r): %s — every /api request"
            " is refused until this is fixed",
            settings.auth_mode,
            settings.auth_error,
        )
    elif settings.auth_mode == "trusted-header":
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

    if settings.api_token and settings.auth_mode != "trusted-header":
        log.warning(
            "SKEIN_API_TOKEN has no effect with SKEIN_AUTH_MODE=%s — that mode"
            " already demands a per-caller credential on every request",
            settings.auth_mode,
        )
    # one-time import of pre-045 file sessions; flagged, so it never
    # resurrects a deleted chat. Per-session failures log and skip.
    from .agents.session_store import import_file_sessions

    import_file_sessions()
    scheduler = None
    keepalive_open = False
    runtime_subjects = {
        *(identity.subject for identity in registry.service_identities),
        *(specialist.name for specialist in registry.specialists),
    }
    if mcp_identity_available:
        runtime_subjects.add(mcp_user)
    from .services import flocks as flocks_svc
    from .services import personas as personas_svc

    # Persona filenames reserve identity even when their prompt is malformed;
    # the strict validator reports that fault. A malformed flock is not an
    # identity until it parses, so fixing or adding one requires restart.
    configured_content_owners = personas_svc.bench_slugs() | {
        item["slug"] for item in flocks_svc.list_flocks()
    }
    from .services.users import reserve_content_identities

    runtime_content_owners, content_identity_conflicts = reserve_content_identities(
        configured_content_owners
    )
    for conflict in content_identity_conflicts:
        log.error(
            "content identity %r is unavailable because roster ownership belongs to: %s",
            conflict["claim"],
            ", ".join(conflict["names"]),
        )
    runtime_subject_token = activate_runtime_machine_subjects(
        runtime_subjects, runtime_content_owners
    )
    try:
        # claim-guarded catch-up runs fill in for cron firings missed while the
        # process was down (no misfire replay); run_job never raises
        for spec in specs:
            if spec.catch_up:
                run_job(spec)
        setup_telemetry()
        from .agents.narrator import register_narrator

        register_narrator()  # composition root: agents plug into services here
        # Two SEPARATE thread pools, sized here so the numbers are chosen rather
        # than inherited (config.py documents the measurement). anyio's limiter
        # carries every sync route handler and run_in_threadpool call; the loop's
        # default executor carries every sync @tool via asyncio.to_thread — left
        # unset it sizes itself min(32, cpu + 4), invisible and host-dependent.
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        import anyio.to_thread

        # THE only place either pool size is applied. services/tuning.py exposes
        # both as knobs marked live=False, and this line is why that mark is
        # honest: an administrator's override reaches the process here and
        # nowhere else, so it takes effect at the next boot and not before.
        # db.init_db() ran at the top of this function, so the read is safe.
        pools = {"thread_pool": settings.thread_pool, "tool_threads": settings.tool_threads}
        try:
            from .services.tuning import effective

            pools = {name: effective(name) for name in pools}
        except Exception:
            # operator config never takes down the API — the env values are a
            # correct sizing, just not the stored one
            log.exception("could not read the stored pool sizes — using the environment values")
        anyio.to_thread.current_default_thread_limiter().total_tokens = pools["thread_pool"]
        asyncio.get_running_loop().set_default_executor(
            ThreadPoolExecutor(max_workers=pools["tool_threads"], thread_name_prefix="skein-tool")
        )
        # held for the process lifetime so writes stop paying WAL
        # checkpoint-on-close — 42x per write when idle, measured (db.py)
        db.open_keepalive()
        keepalive_open = True
        scheduler = (
            _start_scheduler(specs, settings.timezone) if settings.scheduler_enabled else None
        )
        yield
    finally:
        # Keep each cleanup independent so one failure cannot skip the
        # database close or the machine-subject release.
        if scheduler:
            try:
                scheduler.shutdown(wait=False)
            except Exception:
                log.exception("scheduler shutdown failed")
        try:
            from .agents.mcp_tools import shutdown_mcp

            shutdown_mcp()
        except Exception:
            log.exception("MCP shutdown failed")
        try:
            from .services import adoption

            adoption.flush()
        except Exception:
            log.exception("adoption flush failed")
        try:
            if keepalive_open:
                db.close_keepalive()
        finally:
            deactivate_runtime_machine_subjects(runtime_subject_token)


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
    settings: AppSettings = request.app.state.skein_settings
    if not request.app.state.skein_explicit_settings:
        settings = AppSettings.from_config()
    if settings.auth_error:
        # fail CLOSED: a typo'd mode must not silently open the deployment
        return JSONResponse(status_code=503, content={"detail": settings.auth_error})
    if settings.auth_mode == "trusted-header" and not settings.api_token:
        return await call_next(request)
    from .routes.deps import (
        INACTIVE,
        INVALID_KEY,
        NEED_KEY,
        NEED_LOGIN,
        agent_on_rest,
        agent_on_signin,
        content_on_signin,
        is_shared_token,
    )
    from .services.api_keys import PREFIX, verify_key
    from .services.users import (
        ensure_human_identity,
        identity_collision_refusal,
        is_active,
        is_agent,
        is_content_identity,
        reserved_refusal,
    )

    auth = request.headers.get("Authorization", "")
    # The shared token is checked BEFORE the key prefix: an operator whose
    # SKEIN_API_TOKEN happens to begin with the key prefix would otherwise be
    # routed into verify_key and locked out of their own deployment. Here the
    # order is safe either way — this door only decides pass-or-refuse, and a
    # key that is also the token passes on both branches. routes/deps.py has
    # to check it AFTER verify_key, because it decides identity as well.
    if is_shared_token(auth, request):
        return await call_next(request)
    if auth.startswith(f"Bearer {PREFIX}"):
        # verify_key and is_agent hit SQLite; oidc.validate does network I/O
        # and RSA work. This middleware is async, so running any of it inline
        # blocks the event loop for EVERY concurrent request.
        owner = await run_in_threadpool(verify_key, auth[7:])
        if owner is None:
            return JSONResponse(status_code=401, content={"detail": INVALID_KEY})
        # the same agent wall routes/deps.py applies. It belongs here too:
        # this runs before ANY handler, so it is the one refusal that covers
        # the catalog reads which resolve no caller and never reach deps
        # (tests/test_route_identity.py::OPEN_READS).
        if await run_in_threadpool(is_agent, owner):
            return JSONResponse(status_code=403, content={"detail": agent_on_rest(owner)})
        collision = await run_in_threadpool(identity_collision_refusal, owner)
        if collision:
            return JSONResponse(status_code=403, content={"detail": collision})
        # and the reserved-name wall, for exactly the same reason: deps.py
        # refuses this credential, but the catalog reads never reach deps.
        reserved = await run_in_threadpool(reserved_refusal, owner)
        if reserved:
            return JSONResponse(status_code=403, content={"detail": reserved})
        # deactivation wall, for the same reason the agent wall is here.
        # It is also the EARLY refusal: deps.py checks the same thing, and
        # doing it at the door keeps an offboarded key out of the handler
        # rather than one dependency deep.
        if not await run_in_threadpool(is_active, owner):
            return JSONResponse(status_code=403, content={"detail": INACTIVE})
        request.state.auth_key_owner = owner
        return await call_next(request)
    if settings.auth_mode == "trusted-header":
        return JSONResponse(status_code=401, content={"detail": "invalid API token"})
    if settings.auth_mode == "oidc" and auth.startswith("Bearer "):
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
        # the agent wall again, for the same reason it is above: a sign-in
        # naming an agent row must be refused before any handler, catalog
        # reads included.
        if await run_in_threadpool(is_agent, name):
            if await run_in_threadpool(is_content_identity, name):
                return JSONResponse(
                    status_code=403,
                    content={"detail": content_on_signin()},
                )
            return JSONResponse(status_code=403, content={"detail": agent_on_signin(name)})
        reserved = await run_in_threadpool(reserved_refusal, name)
        if reserved:
            return JSONResponse(status_code=403, content={"detail": reserved})
        if not await run_in_threadpool(is_active, name):
            return JSONResponse(status_code=403, content={"detail": INACTIVE})
        try:
            human = await run_in_threadpool(ensure_human_identity, name)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc):
                raise
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "The database is busy. Wait 5 seconds, then send the request again."
                },
                headers={"Retry-After": "5"},
            )
        except ValueError as exc:
            return JSONResponse(
                status_code=403,
                content={
                    "detail": f"{exc} Set SKEIN_OIDC_USERNAME_CLAIM to a claim"
                    " that gives each person one name."
                },
            )
        request.state.auth_claims = claims
        request.state.auth_human_owner = human["name"]
        return await call_next(request)
    detail = NEED_KEY if settings.auth_mode == "api-key" else NEED_LOGIN
    return JSONResponse(status_code=401, content={"detail": detail})


# JSON payloads compress well at any level; added before CORS so CORS stays
# outermost. compresslevel=1, not the default 9: gzip runs ON THE EVENT LOOP,
# and a 251 KB /api/tasks response measured 1.37 ms at level 9 against
# 0.17 ms at level 1, for 5.7 KB instead of 4.2 KB on the wire — on an
# internal deployment the loop time is the scarce resource, not the bytes.
# added AFTER perimeter_auth so CORS is the OUTERMOST layer — a 401 short-circuit
# must still carry Access-Control-Allow-Origin, or the browser reports an
# opaque CORS failure instead of a readable auth error
# Malformed input is the caller's error. The rule is the classification, not
# this list of handlers: if a request body, path, or query can produce the
# exception, it maps to a 4xx here. If only our own state can produce it, it
# stays a 500. Load is the third class: if the identical request succeeds on
# a retry with nothing changed (a rate cap, a held write lock), it maps to
# 429 or 503 with Retry-After. Add a handler when a 500 traces back to
# something a caller sent. Add it for that reason, never because an exception
# class looked familiar. A handler never echoes the rejected value back. The
# caller already has it, and rendering it turned a 50 MB body into a 50 MB
# response.
async def not_found_handler(request: Request, exc: db.NotFound):
    # one rule for the surface: entity-lookup failures are 404, everywhere
    # an owner-scoped miss is a 404 too, because any other status confirms the row exists
    return JSONResponse(status_code=404, content={"detail": str(exc)})


async def permission_error_handler(request: Request, exc: PermissionError):
    # 403, not 404: a crew is not a secret. GET /api/crews lists every crew to
    # every caller (scope.UNSCOPED classifies `crews` that way), so refusing a
    # steward-only change with "no such crew" would hide nothing and lie about
    # what happened. The scoped ROWS are what 404 protects.
    return JSONResponse(status_code=403, content={"detail": str(exc)})


async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


async def public_error_handler(request: Request, exc: PublicError):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "code": exc.code,
            "retryable": exc.retryable,
            "obligations": list(exc.obligations),
        },
    )


async def rate_limited_handler(request: Request, exc: ratelimit.RateLimited):
    # starlette matches handlers by walking the exception's MRO, so this wins
    # over the ValueError handler above even though RateLimited subclasses it
    return JSONResponse(
        status_code=429,
        content={"detail": str(exc)},
        headers={"Retry-After": str(exc.retry_after)},
    )


async def database_busy_handler(request: Request, exc: sqlite3.OperationalError):
    # A held write lock past busy_timeout (db.py) is LOAD, not fault: the same
    # request succeeds on a retry with nothing changed, which is the 503 +
    # Retry-After contract. A 500 here told the operator "bug" and the client
    # "do not retry" — both wrong. Every OTHER OperationalError (bad SQL) IS
    # our own fault: re-raising hands it to the default 500 path unchanged.
    if "locked" not in str(exc):
        raise exc
    return JSONResponse(
        status_code=503,
        content={"detail": "The database is busy. Wait 5 seconds, then send the request again."},
        headers={"Retry-After": "5"},
    )


async def artifact_unreadable_handler(request: Request, exc: RuntimeError):
    # The 500 CLASS is right — the row is readable and the FILE is not, which
    # is our own state and belongs in the error rate. What was wrong is the
    # SHAPE: with no handler, Starlette answers a bare `Internal Server Error`
    # in text/plain, so the operator instruction inside the message reached
    # nobody and lib/api.ts fell back to the status line. An error response is
    # always JSON (CLAUDE.md).
    #
    # Handled on ITS OWN CLASS, not on RuntimeError: that would catch every
    # RuntimeError in the process — Starlette's, anyio's, the SDK's — and put
    # a raw message from one of them into a response body. The four raises
    # this covers are written for a reader; nothing else is.
    logging.getLogger("skein").exception("artifact unreadable", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": str(exc)})


async def overflow_error_handler(request: Request, exc: OverflowError):
    # absurd ints (ids > 2^63, weeks=1e18) must be a 400, never a 500
    return JSONResponse(status_code=400, content={"detail": "value out of range"})


async def unhandled_error_handler(request: Request, exc: Exception):
    """Anything with no handler above, as JSON with NOTHING from the exception.

    "An error response is always JSON" (CLAUDE.md) was true for the classes
    named above and false for every other one — a KeyError or a bad-SQL
    OperationalError answered `Internal Server Error` in text/plain, and
    lib/api.ts fell back to the status line. The body carries no message on
    purpose: these are unclassified, so the text is as likely to be a
    filesystem path or a library's internals as anything a reader can act on.
    The log is where the detail belongs, and the 500 puts it in the error
    rate. A handler that echoed str(exc) here would publish every stub's
    NotImplementedError message to whoever tripped it.
    """
    logging.getLogger("skein").exception("unhandled error", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Something failed on the server. Read the server log for the cause."},
    )


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


def health(specs: Sequence[JobSpec] = JOBS, settings: AppSettings | None = None):
    from .services.users import identity_ownership_error

    selected = settings or AppSettings.from_config()
    return {
        "ok": True,
        "auth_mode": selected.auth_mode,
        "auth_error": selected.auth_error,
        "provider": config.MODEL_PROVIDER,
        # the EFFECTIVE model, through the service: with a pick in force,
        # config.MODEL_ID names a model the deployment is not running
        "model": model_pick_state()["model"],
        "provider_error": config.MODEL_PROVIDER_ERROR,
        "models_error": config.MODELS_ERROR,
        # personas whose model override the menu does not list, and the env
        # default itself when the menu omits it — runtime, not lint, because
        # SKEIN_MODELS is env and CI shares no env
        "model_warnings": unlisted_model_warnings() + config.menu_warnings(),
        "embeddings_error": config.EMBEDDINGS_ERROR,
        "overlay_errors": config.overlay_errors(),
        "identity_ownership_error": identity_ownership_error(),
        # the EFFECTIVE strategy, not the env default — the toggle overrides it,
        # and two surfaces disagreeing about one fact is the bug this avoids
        "context_strategy": effective_context_strategy(),
        "context_error": config.CONTEXT_STRATEGY_ERROR,
        # the zone the scheduler and every "today" run in, and the fault when
        # the configured name degraded to UTC — an operator whose rituals fire
        # at the wrong hour reads it here first
        "timezone": selected.timezone,
        "timezone_error": config.TZ_ERROR,
        "jobs": job_health(specs),
        "activity_chain": chain_health(),
    }


def create_app(
    settings: AppSettings | None = None,
    modules: Sequence[SkeinModule] = (),
) -> FastAPI:
    """Compose one immutable Skein application from trusted modules.

    The caller supplies modules explicitly. Installed packages are never
    scanned or executed automatically.
    """
    explicit_settings = settings is not None
    selected_settings = settings or AppSettings.from_config()
    registry = ExtensionRegistry.build((core_module(), *tuple(modules)))
    from .tools import ALL_TOOLS

    validate_core_tool_names(
        registry,
        {
            str(getattr(item, "tool_name", ""))
            for item in ALL_TOOLS
            if getattr(item, "tool_name", "")
        }
        | {"plan_project"},
    )
    specs = _job_specs(registry, selected_settings)

    # /docs, /redoc and /openapi.json sit outside /api, so the perimeter
    # middleware cannot protect them. Locked modes do not expose the map.
    application = FastAPI(
        title="Skein",
        description="Many strands. One formation.",
        lifespan=lifespan,
        docs_url="/docs" if selected_settings.docs_enabled else None,
        redoc_url="/redoc" if selected_settings.docs_enabled else None,
        openapi_url="/openapi.json" if selected_settings.docs_enabled else None,
    )
    application.state.skein_settings = selected_settings
    application.state.skein_explicit_settings = explicit_settings
    application.state.skein_registry = registry

    application.middleware("http")(perimeter_auth)
    # JSON payloads compress well at any level. Add gzip before CORS so CORS
    # stays outermost and also decorates perimeter-auth refusals.
    application.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=1)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(selected_settings.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
        max_age=7200,
    )

    application.add_exception_handler(db.NotFound, cast(Any, not_found_handler))
    application.add_exception_handler(PermissionError, cast(Any, permission_error_handler))
    application.add_exception_handler(ValueError, cast(Any, value_error_handler))
    application.add_exception_handler(PublicError, cast(Any, public_error_handler))
    application.add_exception_handler(ratelimit.RateLimited, cast(Any, rate_limited_handler))
    application.add_exception_handler(sqlite3.OperationalError, cast(Any, database_busy_handler))
    application.add_exception_handler(
        handoff.ArtifactUnreadable, cast(Any, artifact_unreadable_handler)
    )
    application.add_exception_handler(OverflowError, cast(Any, overflow_error_handler))
    application.add_exception_handler(Exception, unhandled_error_handler)
    application.add_exception_handler(RequestValidationError, cast(Any, validation_error_handler))

    for contribution in registry.routes:
        dependencies = None
        if not contribution.name.startswith("skein.core."):
            dependencies = [
                Depends(enforce_mutation_policy),
                Depends(contributed_route_policy(contribution)),
            ]
        application.include_router(contribution.router, dependencies=dependencies)
    health_settings = selected_settings if explicit_settings else None
    application.add_api_route("/health", lambda: health(specs, health_settings), methods=["GET"])
    return application


# Backward-compatible ASGI entry point. Private deployments can expose their
# own module that calls create_app(settings, modules) without editing this file.
app = create_app()
