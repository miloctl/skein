"""OAuth 2.1 sign-in for a personal MCP server, on the mcp package's own
client provider. The provider runs the whole grant inside the first HTTP
request of a connect — discovery, dynamic client registration, PKCE, the
redirect, the code exchange, and every later refresh — and expects two
handlers written for a desktop: open a browser, then block for the code.
Skein bridges them to the web: the redirect handler parks the
authorization URL for the settings card, and the callback handler waits
for the code the authorization server sends to /api/mcp/oauth/callback."""

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import AnyUrl

log = logging.getLogger(__name__)

# a sign-in the person never finishes must not hold its thread for good
FLOW_SECONDS = 300.0
_URL_WAIT_SECONDS = 20.0


@dataclass
class _Flow:
    server_id: str
    state: str = ""
    authorization_url: str = ""
    code: str = ""
    error: str = ""
    url_ready: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)


_lock = threading.Lock()
# ponytail: pending flows live in this process, so the callback must reach
# the pod that started the sign-in; store the flow in the database if a
# deployment runs more than one backend replica
_pending: dict[str, _Flow] = {}
# servers whose stored grant no longer works: a chat turn's connect met a
# fresh authorization demand and refused it, the card shows "sign in"
_signin_required: set[str] = set()


def needs_sign_in(server_id: str) -> bool:
    with _lock:
        return server_id in _signin_required


class _SealedStorage:
    """The provider's TokenStorage over the sealed row columns."""

    def __init__(self, row_id: int, server_id: str) -> None:
        self.row_id = row_id
        self.server_id = server_id

    async def get_tokens(self) -> OAuthToken | None:
        from ..services.mcp_servers import load_oauth

        tokens, _ = load_oauth(self.row_id)
        return OAuthToken.model_validate_json(tokens) if tokens else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        from ..services.mcp_servers import store_oauth

        store_oauth(self.row_id, tokens=tokens.model_dump_json())
        with _lock:
            _signin_required.discard(self.server_id)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        from ..services.mcp_servers import load_oauth

        _, client = load_oauth(self.row_id)
        return OAuthClientInformationFull.model_validate_json(client) if client else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        from ..services.mcp_servers import store_oauth

        store_oauth(self.row_id, client=client_info.model_dump_json())


def provider(server: dict) -> OAuthClientProvider:
    """The httpx auth for one connect. `server["flow"]` is set only by
    start(): a connect from a chat turn carries none, and a server that
    then demands a fresh grant fails that connect at once and is marked,
    instead of holding the turn for a sign-in nobody is watching."""
    server_id = str(server["server_id"])
    flow: _Flow | None = server.get("flow")
    metadata = OAuthClientMetadata(
        client_name="Skein",
        redirect_uris=[AnyUrl(server["oauth_redirect_uri"])],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
    )

    async def redirect(url: str) -> None:
        if flow is None:
            with _lock:
                _signin_required.add(server_id)
            raise RuntimeError("sign-in required")
        flow.state = parse_qs(urlsplit(url).query).get("state", [""])[0]
        with _lock:
            _pending[flow.state] = flow
        flow.authorization_url = url
        flow.url_ready.set()

    async def callback() -> tuple[str, str | None]:
        if flow is None:
            raise RuntimeError("sign-in required")
        finished = await asyncio.to_thread(flow.done.wait, FLOW_SECONDS)
        with _lock:
            _pending.pop(flow.state, None)
        if not finished or not flow.code:
            raise RuntimeError(flow.error or "sign-in was not completed")
        return flow.code, flow.state

    return OAuthClientProvider(
        server["url"],
        metadata,
        _SealedStorage(int(server["id"]), server_id),
        redirect,
        callback,
        timeout=FLOW_SECONDS,
    )


def start(server_id: str, server: dict) -> str:
    """Begin a sign-in: open the server in a thread with an interactive
    flow, and return the authorization URL once the provider reaches the
    redirect. The thread finishes the connect after the callback lands."""
    from . import mcp_tools

    flow = _Flow(server_id)
    with _lock:
        if any(pending.server_id == server_id for pending in _pending.values()):
            raise ValueError("A sign-in for this server is already in progress. Finish it first.")
        _signin_required.discard(server_id)
    mcp_tools.forget(server_id)

    def run() -> None:
        try:
            mcp_tools.open_personal(server_id, {**server, "flow": flow})
        finally:
            flow.done.set()
            with _lock:
                _pending.pop(flow.state, None)

    threading.Thread(target=run, daemon=True, name="skein-mcp-oauth").start()
    deadline = time.monotonic() + _URL_WAIT_SECONDS
    # the connect can end before it reaches the redirect (an unreachable
    # host, a server that never answers 401); waiting out the deadline for
    # a thread that already gave up is what the done event prevents
    while not flow.url_ready.wait(0.1):
        if flow.done.is_set() or time.monotonic() > deadline:
            raise ValueError(
                "The server did not ask for a sign-in. Check that the URL is an MCP server"
                " that uses OAuth, then try again."
            )
    return flow.authorization_url


def complete(state: str, code: str, error: str = "") -> bool:
    """The browser came back. True when a flow was waiting for this state."""
    with _lock:
        flow = _pending.get(state)
    if flow is None:
        return False
    flow.code = "" if error else code
    flow.error = error
    flow.done.set()
    return True
