from typing import Annotated

from fastapi import Depends, Header

from ..services.api_keys import PREFIX, verify_key
from ..services.users import ensure_user


def current_user(
    x_user: Annotated[str, Header()] = "",
    authorization: Annotated[str, Header()] = "",
) -> str:
    """Identity resolution:
    1. A per-teammate API key (Authorization: Bearer sk-strands-…) wins —
       attributed automation (CLI, MCP, hooks, scripts).
    2. Otherwise the trusted-LAN X-User header from the frontend name picker.
    """
    if authorization.startswith("Bearer "):
        token = authorization[7:]
        if token.startswith(PREFIX):
            owner = verify_key(token)
            if owner:
                return owner
    return ensure_user(x_user or "anonymous")["name"]


CurrentUser = Annotated[str, Depends(current_user)]
