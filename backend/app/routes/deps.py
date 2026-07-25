from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from ..services.adoption import record_use
from ..services.api_keys import PREFIX, verify_key
from ..services.users import ensure_user


def _resolve(x_user: str, authorization: str) -> tuple[str, bool]:
    """Identity resolution → (user, strong).

    1. A per-teammate API key (Authorization: Bearer sk-strands-…) wins —
       attributed automation (CLI, MCP, hooks, scripts). A PRESENTED key that
       is invalid or revoked is a hard 401 — never a silent fallback, or
       revocation would be a no-op for callers that also send X-User.
    2. Otherwise the trusted-LAN X-User header from the frontend name picker
       — a weak, self-asserted identity (strong=False).
    """
    if authorization.startswith("Bearer ") and authorization[7:].startswith(PREFIX):
        owner = verify_key(authorization[7:])
        if not owner:
            raise HTTPException(status_code=401, detail="invalid or revoked API key")
        return owner, True
    return ensure_user(x_user or "anonymous")["name"], False


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
    user, strong = _resolve(x_user, authorization)
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
    user, strong = _resolve(x_user, authorization)
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
