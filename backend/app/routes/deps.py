from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from .. import config
from ..services.adoption import record_use
from ..services.api_keys import PREFIX, verify_key
from ..services.users import ensure_user, is_agent

# One condition, one wording: main.py's perimeter middleware refuses the same
# conditions before a route dependency ever runs, so it imports these strings
# instead of drafting near-duplicates.
INVALID_KEY = "invalid or revoked API key"
NEED_KEY = (
    "SKEIN_AUTH_MODE=api-key: every request needs a personal API key. Get"
    " your first one from whoever runs the server (python -m"
    " app.bootstrap_key <you>). Then set it with the 🔑 button, or send"
    " Authorization: Bearer sk-skein-..."
)
NEED_LOGIN = (
    "SKEIN_AUTH_MODE=oidc: every request needs a sign-in token or a personal"
    " API key. Sign in, or send Authorization: Bearer sk-skein-..."
)


def agent_on_rest(owner: str) -> str:
    return (
        f"'{owner}' is an agent identity — agents work through the gated"
        " tool surface (chat tools / MCP), not the REST API"
    )


def agent_on_signin(name: str) -> str:
    return f"'{name}' is an agent identity — agents authenticate with their API key, not a sign-in"


def _cached(request: Request | None, attr: str):
    """What the perimeter middleware already proved about this request.

    The middleware verifies the credential before any route runs, and in
    api-key/oidc mode it is the ONLY gate for the read routes that carry no
    user dependency. Re-verifying here would charge every request twice:
    verify_key writes last_used_at on each call, and an OIDC token costs a
    full signature check. None means "not proved yet" — a direct call, or
    trusted-header mode, where the middleware steps aside entirely."""
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
    if config.AUTH_ERROR:
        raise HTTPException(status_code=503, detail=config.AUTH_ERROR)
    if authorization.startswith("Bearer ") and authorization[7:].startswith(PREFIX):
        owner = _cached(request, "auth_key_owner") or verify_key(authorization[7:])
        if not owner:
            raise HTTPException(status_code=401, detail=INVALID_KEY)
        # two write paths, one service layer: humans use REST, agents use the
        # gated tools/MCP. An agent-owned key on REST would reach every
        # ungated human surface with origin=human — refuse the door entirely
        if is_agent(owner):
            raise HTTPException(status_code=403, detail=agent_on_rest(owner))
        return owner, True, []
    if config.AUTH_MODE == "oidc":
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
                raise HTTPException(status_code=403, detail=agent_on_signin(name))
            # same rule as the header door: a read never grows the roster, so
            # a polling service account does not accumulate rows
            if method in ("GET", "HEAD", "OPTIONS"):
                return name, True, groups
            try:
                return ensure_user(name)["name"], True, groups
            except ValueError as exc:
                # a reserved name (bench-persona slug) would otherwise 400 on
                # EVERY request, and an OIDC caller cannot pick another name
                # the way the name picker can. Say what the operator must change.
                raise HTTPException(
                    status_code=403,
                    detail=f"{exc} Set SKEIN_OIDC_USERNAME_CLAIM to a claim"
                    " that does not collide with a reserved name.",
                ) from exc
        raise HTTPException(status_code=401, detail=NEED_LOGIN)
    if config.AUTH_MODE == "api-key":
        raise HTTPException(status_code=401, detail=NEED_KEY)
    name = (x_user or "anonymous").strip()[:64] or "anonymous"
    if is_agent(name):
        raise HTTPException(
            status_code=403,
            detail=f"'{name}' is an agent identity — agents authenticate with"
            " their API key, not the name picker",
        )
    if method in ("GET", "HEAD", "OPTIONS"):
        return name, False, []
    return ensure_user(name)["name"], False, []


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
        raise HTTPException(
            status_code=503,
            detail="the forge webhook is off. Set SKEIN_FORGE_WEBHOOK_SECRET,"
            " then use the same secret in the repository webhook settings.",
        )
    expected = hmac.new(config.FORGE_WEBHOOK_SECRET.encode(), body, sha256).hexdigest()
    # Gitea sends the bare hex digest, GitHub prefixes it with "sha256="
    if not hmac.compare_digest(expected, signature.strip().removeprefix("sha256=")):
        raise HTTPException(status_code=401, detail="the webhook signature does not match")


def _is_admin(user: str, groups: list[str]) -> bool:
    """SKEIN_ADMINS names administrators; in oidc mode an IdP group
    (SKEIN_OIDC_ADMIN_GROUP) grants it too. With NEITHER configured,
    trusted-header mode lets every key holder administer — the historical
    scarcity model, right where the operator mints each key by hand. api-key
    and oidc modes hand out credentials freely, so there the fallback stays
    closed until SKEIN_ADMINS is set.

    Names match case-insensitively, the way resolve_teammate matches the
    roster: SKEIN_ADMINS=Casey must not lock out the roster's `casey`. Group
    names stay exact — those come from the IdP, not from a person typing."""
    if any(user.casefold() == admin.casefold() for admin in config.ADMINS):
        return True
    if config.OIDC_ADMIN_GROUP and config.OIDC_ADMIN_GROUP in groups:
        return True
    return (
        not config.ADMINS and not config.OIDC_ADMIN_GROUP and config.AUTH_MODE == "trusted-header"
    )


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
    user, strong, _ = _resolve(x_user, authorization, request.method, request)
    request.state.strong_auth = strong
    record_use(user, _surface(request, x_client))
    return user


def _require_strong(strong: bool) -> None:
    if not strong:
        raise HTTPException(
            status_code=403,
            detail="this surface requires a personal API key. Get your first"
            " one from whoever runs the server (python -m app.bootstrap_key"
            " <you>). Then set it with the 🔑 button, or send"
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
    user, strong, _ = _resolve(x_user, authorization, request.method, request)
    _require_strong(strong)
    request.state.strong_auth = True
    record_use(user, _surface(request, x_client))
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
    if not _is_admin(user, groups):
        raise HTTPException(
            status_code=403,
            detail=f"'{user}' is not an administrator. Ask whoever runs the"
            " server to add the name to SKEIN_ADMINS.",
        )
    request.state.strong_auth = True
    record_use(user, _surface(request, x_client))
    return user


CurrentUser = Annotated[str, Depends(current_user)]
StrongUser = Annotated[str, Depends(strong_user)]
AdminUser = Annotated[str, Depends(admin_user)]
