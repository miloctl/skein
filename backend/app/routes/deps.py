from typing import Annotated

from fastapi import Depends, Header, HTTPException

from ..services.api_keys import PREFIX, verify_key
from ..services.users import ensure_user


def current_user(
    x_user: Annotated[str, Header()] = "",
    authorization: Annotated[str, Header()] = "",
) -> str:
    """Identity resolution:
    1. A per-teammate API key (Authorization: Bearer sk-strands-…) wins —
       attributed automation (CLI, MCP, hooks, scripts). A PRESENTED key that
       is invalid or revoked is a hard 401 — never a silent fallback, or
       revocation would be a no-op for callers that also send X-User.
    2. Otherwise the trusted-LAN X-User header from the frontend name picker.
    """
    if authorization.startswith("Bearer ") and authorization[7:].startswith(PREFIX):
        owner = verify_key(authorization[7:])
        if not owner:
            raise HTTPException(status_code=401, detail="invalid or revoked API key")
        return owner
    return ensure_user(x_user or "anonymous")["name"]


CurrentUser = Annotated[str, Depends(current_user)]
