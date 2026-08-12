import hmac
import sqlite3
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from .. import config
from ..services import scope
from ..services.adoption import record_use
from ..services.api_keys import PREFIX, verify_key
from ..services.users import ensure_human_identity, is_agent, is_content_identity

# One condition, one wording: main.py's perimeter middleware refuses the same
# conditions before a route dependency ever runs, so it imports these strings
# instead of drafting near-duplicates.
# States the fix, like NEED_KEY below. Without it this reads as a dead end:
# it is the page-level error on EVERY surface once a stored key goes bad, and
# the one screen that can clear the key shows the same sentence. A reader with
# a revoked key asked "how am I supposed to log in" — which is the question a
# refusal with no remedy always produces.
INVALID_KEY = (
    "invalid or revoked API key. Open Settings, step 2, and delete the stored"
    " key or paste a new one. Get a new key from whoever runs the server"
    " (python -m app.bootstrap_key <you>)."
)
NEED_KEY = (
    "SKEIN_AUTH_MODE=api-key: every request needs a personal API key. Get"
    " your first one from whoever runs the server (python -m"
    " app.bootstrap_key <you>). Then paste it in Settings, step 2, or send"
    " Authorization: Bearer sk-skein-..."
)
NEED_LOGIN = (
    "SKEIN_AUTH_MODE=oidc: every request needs a sign-in token or a personal"
    " API key. Sign in, or send Authorization: Bearer sk-skein-..."
)
# names no name on purpose: this refuses caller-supplied identity in
# trusted-header mode, and an error never echoes the rejected value back
INACTIVE = "This roster entry is not active. Ask whoever runs the server to reactivate it."


def agent_on_rest(owner: str) -> str:
    return (
        f"'{owner}' is an agent identity — agents work through the gated"
        " tool surface (chat tools / MCP), not the REST API"
    )


def agent_on_signin(name: str) -> str:
    return f"'{name}' is an agent identity — agents authenticate with their API key, not a sign-in"


def content_on_signin() -> str:
    return (
        "This name is reserved for agent content. Set SKEIN_OIDC_USERNAME_CLAIM"
        " to a claim that gives each person one name."
    )


def _refuse_reserved(name: str) -> None:
    from ..services.users import refuse_authenticated_name

    try:
        refuse_authenticated_name(name)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _refuse_inactive(name: str) -> None:
    """services/users.py::set_active calls itself the offboarding switch and
    revokes every API key the person owns. It revoked keys and nothing else,
    so the oidc and header doors stayed open and an offboarded teammate kept
    strong read AND write — including their own private notes — until someone
    separately disabled the IdP account. No roster row is not inactive: a
    first-ever sign-in has none, and a read must not refuse it.

    The detail names no name: this runs on caller-supplied identity in
    trusted-header mode, and an error never echoes the rejected value back.
    """
    from ..services.users import is_active

    if not is_active(name):
        raise HTTPException(status_code=403, detail=INACTIVE)


def _refuse_ambiguous(name: str) -> None:
    from ..services.users import identity_collision_refusal

    if refusal := identity_collision_refusal(name):
        raise HTTPException(status_code=403, detail=refusal)


def _app_setting(request: Request | None, name: str, fallback):
    settings = getattr(getattr(request, "app", None), "state", None)
    if not getattr(settings, "skein_explicit_settings", False):
        return fallback
    snapshot = getattr(settings, "skein_settings", None)
    return getattr(snapshot, name, fallback)


def is_shared_token(authorization: str, request: Request | None = None) -> bool:
    """The deployment-wide SKEIN_API_TOKEN. It proves membership in the
    deployment, never identity, so a caller holding it stays weak.

    Compared as BYTES: starlette decodes headers as latin-1 and
    compare_digest raises TypeError on a non-ASCII str, so one 0xFF byte in
    an Authorization header turned a caller's mistake into a 500 — the same
    hazard verify_forge_signature below documents. main.py's perimeter calls
    this rather than restating the comparison, so the two doors cannot
    disagree about the same header.
    """
    auth_mode = _app_setting(request, "auth_mode", config.AUTH_MODE)
    api_token = _app_setting(request, "api_token", config.API_TOKEN)
    if auth_mode != "trusted-header" or not api_token:
        return False
    return hmac.compare_digest(
        authorization.encode("utf-8", "replace"), f"Bearer {api_token}".encode()
    )


def _cached(request: Request | None, attr: str):
    """What the perimeter middleware already proved about this request.

    The middleware verifies the credential before any route runs, and in
    api-key/oidc mode it is the ONLY gate for the read routes that carry no
    user dependency. Re-verifying here would charge every request twice:
    verify_key costs a hash and a lookup (plus a periodic last_used_at
    stamp), and an OIDC token costs a full signature check. None means "not
    proved yet" — a direct call, or trusted-header mode, where the
    middleware steps aside entirely."""
    return getattr(request.state, attr, None) if request is not None else None


def _resolve(
    x_user: str,
    authorization: str,
    method: str = "POST",
    request: Request | None = None,
) -> tuple[str, bool, list[str]]:
    """Identity resolution → (user, strong, groups). SKEIN_AUTH_MODE picks
    which doors exist; this function is the single swap point.

    1. A per-teammate API key (Authorization: Bearer sk-skein-…) wins in
       EVERY mode — attributed automation (CLI, MCP, hooks, scripts). A
       PRESENTED key that is invalid or revoked is a hard 401 — never a
       silent fallback, or revocation would be a no-op for callers that also
       send X-User.
    2. oidc mode: any other bearer token is an IdP-issued JWT, validated
       in-process (app/oidc.py). A validated sign-in is strong identity and
       carries the IdP's group claims. Like a key, it may never claim an
       agent identity: agent rows carry trust scores and gate levels, and
       writes as them would sidestep the review gate entirely.
    3. trusted-header mode only: the X-User header from the frontend name
       picker — weak, self-asserted (strong=False), same agent wall. Reads
       don't mint roster rows — a typo'd or scripted GET must not grow the
       roster. api-key and oidc modes never reach this door: those modes
       exist exactly because the header is self-asserted.

    A broken auth config (config.AUTH_ERROR) refuses everything with a 503 —
    fail closed, unlike the model-provider faults that degrade to mock,
    because "degrade" for auth means "open".
    """
    auth_error = _app_setting(request, "auth_error", config.AUTH_ERROR)
    auth_mode = _app_setting(request, "auth_mode", config.AUTH_MODE)
    if auth_error:
        raise HTTPException(status_code=503, detail=auth_error)
    if authorization.startswith("Bearer ") and authorization[7:].startswith(PREFIX):
        owner = _cached(request, "auth_key_owner") or verify_key(authorization[7:])
        # A SKEIN_API_TOKEN that begins with sk-skein- reaches this door and is
        # not a key, so refusing every unverified prefix locks the operator out
        # of their own deployment. Checked AFTER verify_key, never before: an
        # operator who set the token TO a real personal key would otherwise
        # have that key silently demoted from strong identity to a weak shared
        # door. The token proves membership in the deployment, never identity,
        # so it falls through to the name-picker door below.
        if not owner and not is_shared_token(authorization, request):
            raise HTTPException(status_code=401, detail=INVALID_KEY)
        if owner:
            # two write paths, one service layer: humans use REST, agents use
            # the gated tools/MCP. An agent-owned key on REST would reach every
            # ungated human surface with origin=human — refuse the door entirely
            if is_agent(owner):
                raise HTTPException(status_code=403, detail=agent_on_rest(owner))
            _refuse_ambiguous(owner)
            # the key door never calls ensure_user, so a row that predates the
            # reserved-name wall (or was renamed into one) would keep writing as
            # a system actor and leak every row to every viewer. Refuse the
            # CREDENTIAL, so a broken identity fails at the door rather than
            # half-working.
            _refuse_reserved(owner)
            _refuse_inactive(owner)
            return owner, True, []
    if auth_mode == "oidc":
        if authorization.startswith("Bearer "):
            from .. import oidc

            try:
                claims = _cached(request, "auth_claims")
                if claims is None:
                    claims = oidc.validate(authorization[7:])
                name, groups = oidc.principal(claims)
            except oidc.OIDCUnavailable as exc:
                # 503, not 401: the token was never judged. Answering 401 tells
                # a whole team of signed-in people to sign in again, at an
                # identity provider that is the very thing that is down.
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except oidc.OIDCError as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc
            if is_agent(name):
                if is_content_identity(name):
                    raise HTTPException(
                        status_code=403,
                        detail=content_on_signin(),
                    )
                raise HTTPException(status_code=403, detail=agent_on_signin(name))
            # Apply the same walls for direct dependency calls that do not pass
            # through the perimeter middleware.
            _refuse_reserved(name)
            _refuse_inactive(name)
            try:
                # The perimeter reserves every validated OIDC principal before
                # any handler runs. Direct calls and tests do the same here.
                # Durable ownership prevents a new service, specialist, or MCP
                # identity from taking the name during this request.
                if _cached(request, "auth_human_owner") == name:
                    return name, True, groups
                return ensure_human_identity(name)["name"], True, groups
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc):
                    raise
                raise HTTPException(
                    status_code=503,
                    detail="The database is busy. Wait 5 seconds, then send the request again.",
                    headers={"Retry-After": "5"},
                ) from exc
            except ValueError as exc:
                # a reserved name (bench-persona slug) or a fold collision
                # would otherwise refuse EVERY request, and an OIDC caller
                # cannot pick another name the way the name picker can. Say
                # what the operator must change.
                raise HTTPException(
                    status_code=403,
                    detail=f"{exc} Set SKEIN_OIDC_USERNAME_CLAIM to a claim"
                    " that gives each person one name.",
                ) from exc
        raise HTTPException(status_code=401, detail=NEED_LOGIN)
    if auth_mode == "api-key":
        raise HTTPException(status_code=401, detail=NEED_KEY)
    supplied_name = (x_user or "").strip()[:64]
    name = supplied_name or "anonymous"
    # Weak reads do not reserve a roster row. Apply the stable reserved,
    # inactive, and agent walls before that early return.
    if supplied_name:
        _refuse_reserved(name)
    _refuse_inactive(name)
    if supplied_name:
        _refuse_ambiguous(name)
    if is_agent(name):
        raise HTTPException(
            status_code=403,
            detail=f"'{name}' is an agent identity — agents authenticate with"
            " their API key, not the name picker",
        )
    if method in ("GET", "HEAD", "OPTIONS"):
        return name, False, []
    if not supplied_name:
        # Preserve the historic unnamed weak write identity. It is synthetic,
        # cannot become strong, and never owns a private surface.
        from ..services.users import ensure_user

        return ensure_user("anonymous")["name"], False, []
    try:
        return ensure_human_identity(name)["name"], False, []
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def forge_webhook_off() -> HTTPException:
    """The refusal for an unconfigured forge webhook. Returned rather than
    raised so the route can refuse BEFORE it reads a body, and raised again
    here so no caller can reach the door with the feature switched off."""
    return HTTPException(
        status_code=503,
        detail="the forge webhook is off. Set SKEIN_FORGE_WEBHOOK_SECRET,"
        " then use the same secret in the repository webhook settings.",
    )


def verify_forge_signature(body: bytes, signature: str) -> None:
    """The forge webhook's whole identity: HMAC-SHA256 over the raw body with
    a shared secret. It lives here because every other door in Skein is
    decided in this file, and a caller that proves possession of a secret is
    a door — the ICS feed token is the same shape.

    No secret configured means the endpoint is CLOSED, not open: it moves
    tasks, so an unsigned caller must never reach it. compare_digest, not
    ==, or the reject time leaks the expected prefix byte by byte."""
    import hmac
    from hashlib import sha256

    if not config.FORGE_WEBHOOK_SECRET:
        raise forge_webhook_off()
    expected = hmac.new(config.FORGE_WEBHOOK_SECRET.encode(), body, sha256).hexdigest()
    # Gitea sends the bare hex digest, and also GitHub's "sha256=" form.
    # Compare BYTES: starlette decodes headers as latin-1, and compare_digest
    # raises TypeError on a non-ASCII str — an unsigned caller must not be
    # able to turn one 0xFF byte into a 500.
    sent = signature.strip().removeprefix("sha256=").encode("utf-8", "replace")
    if not hmac.compare_digest(expected.encode(), sent):
        raise HTTPException(status_code=401, detail="the webhook signature does not match")


def _is_admin(user: str, groups: list[str], request: Request | None = None) -> bool:
    """SKEIN_ADMINS names administrators; in oidc mode an IdP group
    (SKEIN_OIDC_ADMIN_GROUP) grants it too. With NEITHER configured,
    trusted-header mode lets every key holder administer — the historical
    scarcity model, right where the operator mints each key by hand. api-key
    and oidc modes hand out credentials freely, so there the fallback stays
    closed until SKEIN_ADMINS is set.

    Names match case-insensitively, the way resolve_teammate matches the
    roster: SKEIN_ADMINS=Casey must not lock out the roster's `casey`. Group
    names stay exact — those come from the IdP, not from a person typing."""
    if is_named_admin(user, groups):
        return True
    return (
        not config.ADMINS
        and not config.OIDC_ADMIN_GROUP
        and _app_setting(request, "auth_mode", config.AUTH_MODE) == "trusted-header"
    )


def is_named_admin(user: str, groups: list[str]) -> bool:
    """Administrator by CONFIGURATION, without the scarcity fallback above.

    The fallback exists because a deployment that mints every key by hand has
    already decided who it trusts. That reasoning does not carry to a boundary
    that decides what a person READS: with it, any key holder could make
    themselves the steward of any crew and evict the one who was there
    (routes/api.py::_crew_steward). Membership is that boundary, so it takes
    the strict test — and an operator who wants an administrator to repair a
    crew names one in SKEIN_ADMINS.
    """
    if any(user.casefold() == admin.casefold() for admin in config.ADMINS):
        return True
    return bool(config.OIDC_ADMIN_GROUP and config.OIDC_ADMIN_GROUP in groups)


# Methods that read. adoption.record_use takes counts=False for these, so a
# page load registers the person without inflating the action tally — see the
# docstring there for what that protects.
_READS = frozenset({"GET", "HEAD", "OPTIONS"})


def _surface(request: Request, x_client: str) -> str:
    if request.url.path.startswith("/api/chat"):
        return "chat"
    return x_client if x_client in ("web", "cli") else "api"


def current_user(
    request: Request,
    x_user: Annotated[str, Header()] = "",
    x_client: Annotated[str, Header()] = "",
    authorization: Annotated[str, Header()] = "",
) -> str:
    """Every resolved identity also counts toward adoption telemetry (day/
    user/surface tallies — reach of the tool, never content or output)."""
    user, strong, groups = _resolve(x_user, authorization, request.method, request)
    _stash(request, user, strong, groups, authentication_source(request, authorization, strong))
    record_use(user, _surface(request, x_client), counts=request.method not in _READS)
    return user


def authentication_source(request: Request, authorization: str, strong: bool) -> str:
    """Return the stable source that proved one request identity."""
    if strong and authorization.startswith("Bearer "):
        if authorization[7:].startswith(PREFIX):
            return "api-key"
        if _app_setting(request, "auth_mode", config.AUTH_MODE) == "oidc":
            return "oidc"
    return "trusted-header"


def _stash(
    request: Request,
    user: str,
    strong: bool,
    groups: list[str],
    source: str = "trusted-header",
) -> None:
    """What a handler can read back off the request.

    All three dependencies stash, not only current_user: a route that
    re-derives admin-ness in its own body (routes/api.py::_crew_steward) reads
    these, and an empty group list silently refuses every administrator
    identified only by SKEIN_OIDC_ADMIN_GROUP. Stashing in one door and not
    the others makes that a property of which dependency the next route
    happens to pick.
    """
    request.state.strong_auth = strong
    request.state.auth_groups = groups
    request.state.auth_source = source
    # The viewer every scoped read filters on. Built HERE and nowhere else
    # (services/scope.py::Viewer) so the strong-identity bar is a property of
    # the door rather than a rule every scoped read has to remember.
    # It resolves crew membership once, so a page that fans out to dozens of
    # scoped reads pays for one lookup.
    #
    # Built on EVERY request, including routes that read nothing scoped. It
    # costs a crews_of query only for a STRONG caller — Viewer.__init__ blanks
    # a weak name and then skips the lookup, so in the default trusted-header
    # mode it costs nothing at all. Kept in the single door either way: making
    # it lazy moves construction out of the one place that builds a Viewer,
    # which is the whole reason the strong-identity bar cannot be forgotten.
    request.state.viewer = scope.Viewer(user, strong)


def _require_strong(strong: bool) -> None:
    if not strong:
        raise HTTPException(
            status_code=403,
            detail="this surface requires a personal API key. Get your first"
            " one from whoever runs the server (python -m app.bootstrap_key"
            " <you>). Then paste it in Settings, step 2, or send"
            " Authorization: Bearer sk-skein-...",
        )


def strong_user(
    request: Request,
    x_user: Annotated[str, Header()] = "",
    x_client: Annotated[str, Header()] = "",
    authorization: Annotated[str, Header()] = "",
) -> str:
    """Strong identity ONLY — private records and self-scoped credentials.
    The self-asserted X-User header is never sufficient here. A personal API
    key or a validated OIDC sign-in both qualify: each one proves the caller
    is who the record says."""
    user, strong, groups = _resolve(x_user, authorization, request.method, request)
    _require_strong(strong)
    _stash(request, user, True, groups, authentication_source(request, authorization, True))
    record_use(user, _surface(request, x_client), counts=request.method not in _READS)
    return user


def admin_user(
    request: Request,
    x_user: Annotated[str, Header()] = "",
    x_client: Annotated[str, Header()] = "",
    authorization: Annotated[str, Header()] = "",
) -> str:
    """Administrator identity: strong AND named an administrator. Guards what
    changes OTHER people's rows or the whole team's configuration — roster
    changes, key visibility and the kill switch, agent authority, team theme,
    context strategy, backups, the full export. Self-scoped strong surfaces
    (own keys, private notes) stay on StrongUser: locking a person out of
    their own records is not a privilege boundary, it is a dead end."""
    user, strong, groups = _resolve(x_user, authorization, request.method, request)
    _require_strong(strong)
    _stash(request, user, True, groups, authentication_source(request, authorization, True))
    if not _is_admin(user, groups, request):
        raise HTTPException(
            status_code=403,
            detail=f"'{user}' is not an administrator. Ask whoever runs the"
            " server to add the name to SKEIN_ADMINS.",
        )
    record_use(user, _surface(request, x_client), counts=request.method not in _READS)
    return user


def viewer(request: Request, _user: Annotated[str, Depends(current_user)]) -> "scope.Viewer":
    """What the caller may read.

    `_user` is unused on purpose: it ORDERS the two, so current_user has
    resolved and stashed before this reads request.state. FastAPI caches a
    dependency per request, so a route taking both pays for one resolution.

    The `getattr` default is unreachable while `_user` is here: _stash sets
    request.state.viewer unconditionally. It is the fail-closed landing if
    somebody drops that parameter — which would also drop the ordering, so the
    default going live is the SYMPTOM of that edit, not a case to design for.
    """
    return getattr(request.state, "viewer", scope.NOBODY)


CurrentUser = Annotated[str, Depends(current_user)]
StrongUser = Annotated[str, Depends(strong_user)]
AdminUser = Annotated[str, Depends(admin_user)]
ViewerDep = Annotated["scope.Viewer", Depends(viewer)]
