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
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import AnyUrl

from .. import db

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
# The thread that started a sign-in waits here for its code. The code itself
# arrives through mcp_oauth_flows, because the callback can land on any
# process; this map only holds the local events and the parked URL.
_pending: dict[str, _Flow] = {}


def _mark_sign_in(sid: int, required: bool) -> None:
    """A stored grant the server no longer accepts: a chat turn's connect met
    a fresh authorization demand and refused it, so the card shows sign in
    (mcp_servers.oauth_signin_required, read by the server listing)."""
    db.execute("UPDATE mcp_servers SET oauth_signin_required = ? WHERE id = ?", (required, sid))


def _flow_expiry() -> str:
    return (datetime.now(UTC) + timedelta(seconds=FLOW_SECONDS)).isoformat(timespec="seconds")


def _await_code(flow: _Flow) -> None:
    """Block until the callback lands on any process, or the flow lapses."""
    deadline = time.monotonic() + FLOW_SECONDS
    while not flow.done.wait(0.25):
        row = db.query_one(
            "SELECT code, error, done FROM mcp_oauth_flows WHERE state = ?", (flow.state,)
        )
        if row and row["done"]:
            flow.code, flow.error = row["code"], row["error"]
            break
        if time.monotonic() >= deadline:
            break


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
        _mark_sign_in(self.row_id, False)

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
            _mark_sign_in(int(server["id"]), True)
            raise RuntimeError("sign-in required")
        flow.state = parse_qs(urlsplit(url).query).get("state", [""])[0]
        with _lock:
            _pending[flow.state] = flow
        db.execute(
            "INSERT INTO mcp_oauth_flows (state, server_id, created_at, expires_at)"
            " VALUES (?, ?, ?, ?) ON CONFLICT (state) DO NOTHING",
            (flow.state, server_id, db.now(), _flow_expiry()),
        )
        flow.authorization_url = url
        flow.url_ready.set()

    async def callback() -> tuple[str, str | None]:
        if flow is None:
            raise RuntimeError("sign-in required")
        await asyncio.to_thread(_await_code, flow)
        with _lock:
            _pending.pop(flow.state, None)
        db.execute("DELETE FROM mcp_oauth_flows WHERE state = ?", (flow.state,))
        if not flow.code:
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
    db.execute("DELETE FROM mcp_oauth_flows WHERE expires_at <= ?", (db.now(),))
    with _lock:
        if any(pending.server_id == server_id for pending in _pending.values()) or db.query_one(
            "SELECT 1 FROM mcp_oauth_flows WHERE server_id = ? AND NOT done", (server_id,)
        ):
            raise ValueError("A sign-in for this server is already in progress. Finish it first.")
    _mark_sign_in(int(server["id"]), False)
    mcp_tools.forget(server_id)

    def run() -> None:
        try:
            mcp_tools.open_personal(server_id, {**server, "flow": flow})
        finally:
            flow.done.set()
            with _lock:
                _pending.pop(flow.state, None)
            if flow.state:
                db.execute("DELETE FROM mcp_oauth_flows WHERE state = ?", (flow.state,))

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
    """The browser came back. True when a flow was waiting for this state,
    on this process or another."""
    landed = db.execute_rowcount(
        "UPDATE mcp_oauth_flows SET code = ?, error = ?, done = TRUE"
        " WHERE state = ? AND NOT done AND expires_at > ?",
        ("" if error else code, error, state, db.now()),
    )
    with _lock:
        flow = _pending.get(state)
    if flow is not None:
        flow.code = "" if error else code
        flow.error = error
        flow.done.set()
    return bool(landed) or flow is not None
