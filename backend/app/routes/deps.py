from typing import Annotated

from fastapi import Depends, Header

from ..services.users import ensure_user


def current_user(x_user: Annotated[str, Header()] = "anonymous") -> str:
    """Trusted-LAN identity: the frontend name picker sets X-User."""
    return ensure_user(x_user)["name"]


CurrentUser = Annotated[str, Depends(current_user)]
