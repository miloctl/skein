"""Every read route resolves a caller, or is listed here with a reason.

A read with no caller cannot be given a visibility filter later without
changing its signature, and 45 of the 76 GET routes had none — the whole
team workspace answered an unidentified request. Both facts are why this
inventory exists rather than a convention: the export allowlist
(services/admin.py::TABLES), the rename map (services/users.py::_ATTRIBUTION)
and the ungated-writer list (tests/test_gate_coverage.py::UNGATED_WRITERS)
are the three other enumerate-everything structures here, and each is
enforced by a test for the same reason.
"""

from fastapi.routing import APIRoute

from app.main import app
from app.routes.deps import admin_user, current_user, strong_user

# path -> why this one answers a caller it cannot name
OPEN_READS = {
    "/health": "the liveness probe, outside /api and outside the perimeter",
    "/api/auth/config": (
        "the public OIDC client parameters. Read BEFORE a sign-in exists, so"
        " requiring identity here would make signing in impossible"
    ),
    "/api/calendar.ics": (
        "its own door: a shared feed token compared with hmac. Calendar"
        " clients cannot send X-User or a bearer, so identity is the URL"
    ),
    "/api/chat/commands": "the composer's static command catalog, no database read",
    "/api/personas": "backend/personas/*.md, checked into the repository",
    "/api/personas/{slug}": "backend/personas/*.md, checked into the repository",
    "/api/flocks": "backend/flocks/*.yaml, checked into the repository",
    "/api/playbooks": "backend/playbooks/*.yaml, checked into the repository",
}

_RESOLVERS = {current_user, strong_user, admin_user}


def _api_routes(routes=None) -> list[APIRoute]:
    """Every APIRoute the app serves.

    app.routes is NOT flat: include_router leaves a wrapper object per
    router, and in fastapi 0.141 that wrapper (_IncludedRouter) reaches its
    APIRouter through `original_router` rather than exposing `.routes`. A
    plain isinstance sweep over app.routes therefore finds ONE route
    (/health) and silently reports every other route as compliant, which is
    the failure test_the_traversal_reaches_every_router exists to catch.
    """
    found = []
    for route in app.routes if routes is None else routes:
        if isinstance(route, APIRoute):
            found.append(route)
            continue
        nested = getattr(route, "original_router", None) or route
        found.extend(_api_routes(getattr(nested, "routes", [])))
    return found


def _resolves_identity(dependant) -> bool:
    """Walk the dependency tree, not the signature: a router-level or nested
    dependency resolves identity just as well as a parameter annotation."""
    if dependant.call in _RESOLVERS:
        return True
    return any(_resolves_identity(sub) for sub in dependant.dependencies)


def _open_reads() -> set[str]:
    return {
        route.path
        for route in _api_routes()
        if "GET" in route.methods and not _resolves_identity(route.dependant)
    }


def test_the_traversal_reaches_every_router():
    """Ground truth for the three tests below, and the one thing they cannot
    check themselves: a walker that under-collects reports the routes it
    never saw as compliant. A FastAPI upgrade that renames the wrapper
    attribute fails HERE, where the message says so, rather than turning the
    other tests green for the wrong reason."""
    paths = {route.path for route in _api_routes()}
    for router in ("/api/tasks", "/api/auth/config", "/api/chat", "/api/private/notes"):
        assert router in paths, f"{router} is missing — _api_routes is not reaching every router"
    assert len(_api_routes()) > 100


def test_every_read_route_resolves_a_caller():
    unlisted = _open_reads() - set(OPEN_READS)
    assert not unlisted, (
        f"these GET routes answer a caller they cannot name: {sorted(unlisted)}."
        " Add CurrentUser, or add a line to OPEN_READS saying why not."
    )


def test_the_open_read_list_has_no_stale_entries():
    """A path that gained a user dependency must leave the list, or the next
    reader trusts an exemption that is no longer load-bearing."""
    stale = set(OPEN_READS) - _open_reads()
    assert not stale, f"these now resolve a caller. Delete them from OPEN_READS: {sorted(stale)}"


def test_every_mutating_route_resolves_a_caller():
    """Provenance is a hard constraint: services record origin and created_by
    on every write. A write with no caller has no name to record."""
    open_writes = {
        (sorted(route.methods - {"HEAD", "OPTIONS"})[0], route.path)
        for route in _api_routes()
        if route.methods - {"GET", "HEAD", "OPTIONS"} and not _resolves_identity(route.dependant)
    }
    # the two signed doors: identity is a shared secret over the raw body,
    # verified in the handler (routes/deps.py::verify_forge_signature and
    # routes/slack.py), and the token exchange that runs before a sign-in
    assert open_writes == {
        ("POST", "/api/auth/token"),
        ("POST", "/api/slack/command"),
        ("POST", "/api/webhooks/forge"),
    }
