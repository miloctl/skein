from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from ..services.adoption import record_use
from ..services.api_keys import PREFIX, verify_key
from ..services.users import ensure_user, is_agent


def _resolve(x_user: str, authorization: str, method: str = "POST") -> tuple[str, bool]:
    """Identity resolution → (user, strong).

    1. A per-teammate API key (Authorization: Bearer sk-strands-…) wins —
       attributed automation (CLI, MCP, hooks, scripts). A PRESENTED key that
       is invalid or revoked is a hard 401 — never a silent fallback, or
       revocation would be a no-op for callers that also send X-User.
    2. Otherwise the trusted-LAN X-User header from the frontend name picker
       — a weak, self-asserted identity (strong=False). A weak header may
       never claim an agent identity: agent rows carry trust scores and gate
       levels, and writes as them would sidestep the review gate entirely.
       Reads don't mint roster rows — a typo'd or scripted GET must not
       grow the roster.
    """
    if authorization.startswith("Bearer ") and authorization[7:].startswith(PREFIX):
        owner = verify_key(authorization[7:])
        if not owner:
            raise HTTPException(status_code=401, detail="invalid or revoked API key")
        return owner, True
    name = (x_user or "anonymous").strip()[:64] or "anonymous"
    if is_agent(name):
        raise HTTPException(
            status_code=403,
            detail=f"'{name}' is an agent identity — agents authenticate with"
            " their API key, not the name picker",
        )
    if method in ("GET", "HEAD", "OPTIONS"):
        return name, False
    return ensure_user(name)["name"], False


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
    user, strong = _resolve(x_user, authorization, request.method)
    request.state.strong_auth = strong
    record_use(user, _surface(request, x_client))
    return user


def strong_user(
    request: Request,
    x_user: Annotated[str, Header()] = "",
    x_client: Annotated[str, Header()] = "",
    authorization: Annotated[str, Header()] = "",
) -> str:
    """Strong identity ONLY — private records and admin surfaces. The
    self-asserted X-User header is never sufficient here.

    Today a strong credential is a personal API key. The deployed target is
    OIDC + PKCE (ported from an existing setup); when it lands, a validated
    OIDC session satisfies this dependency and nothing downstream changes —
    this function is the single swap point.
    """
    user, strong = _resolve(x_user, authorization, request.method)
    if not strong:
        raise HTTPException(
            status_code=403,
            detail="this surface requires a personal API key. Get your first"
            " one from whoever runs the box (python -m app.bootstrap_key"
            " <you>), then set it via the 🔑 button or send"
            " Authorization: Bearer sk-strands-...",
        )
    request.state.strong_auth = True
    record_use(user, _surface(request, x_client))
    return user


CurrentUser = Annotated[str, Depends(current_user)]
StrongUser = Annotated[str, Depends(strong_user)]
