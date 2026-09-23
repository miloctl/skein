"""OAuth 2.1 sign-in for personal MCP servers using the MCP client's grant.

Discovery and registration run in the connect's first request. A database
claim owns that grant through token storage, and its callback can land on
any process. The local flow only carries the waiting thread and browser URL.
"""

import asyncio
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

import httpx2
from mcp.client.auth import OAuthClientProvider
from mcp.client.auth.exceptions import OAuthFlowError
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)
from pydantic import AnyUrl

from ..services import mcp_servers

# A sign-in the person never finishes must not hold its thread for good.
FLOW_SECONDS = 300.0
# One cookie per server, so two sign-ins open in one browser do not
# overwrite each other's binding (routes/api.py sets and reads it).
BROWSER_COOKIE = "skein_mcp_oauth_"
_URL_WAIT_SECONDS = 20.0


@dataclass
class _Flow:
    server_id: str
    claim: str
    state: str = ""
    authorization_url: str = ""
    code: str = ""
    iss: str | None = None
    error: str = ""
    url_ready: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)


def _await_code(flow: _Flow) -> None:
    deadline = time.monotonic() + FLOW_SECONDS
    while not flow.done.wait(0.25):
        row = mcp_servers.oauth_result(flow.claim)
        if row is None:
            break
        if row["done"]:
            flow.code = row["code"]
            flow.iss = row["iss"]
            flow.error = "sign-in was refused" if row["refused"] else ""
            break
        if time.monotonic() >= deadline:
            break


class _SealedStorage:
    """A grant owns registration; later refreshes compare the stored grant."""

    def __init__(self, row_id: int, server_id: str, flow: _Flow | None = None) -> None:
        self.row_id = row_id
        self.owner = server_id.removeprefix("personal:").rsplit(":", 1)[0]
        self.flow = flow
        self._expected: tuple[str, str] | None = None

    def _load(self) -> tuple[str, str]:
        if self._expected is None:
            self._expected = mcp_servers.load_oauth(self.row_id)
        return self._expected

    async def get_tokens(self) -> OAuthToken | None:
        # SDK initialization reads tokens before client info. Refresh the pair
        # here, not in both getters: another grant can commit between them.
        self._expected = mcp_servers.load_oauth(self.row_id)
        tokens, _ = self._expected
        return OAuthToken.model_validate_json(tokens) if tokens else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        encoded = tokens.model_dump_json(by_alias=True)
        expected = self._load()
        mcp_servers.store_oauth(
            self.row_id,
            self.owner,
            expected,
            claim=self.flow.claim if self.flow else "",
            tokens=encoded,
        )
        self._expected = (encoded, expected[1])

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        _, client = self._load()
        if not client and not (self.flow and self.flow.claim):
            mcp_servers.mark_oauth_sign_in(self.row_id, True)
            raise RuntimeError("sign-in required")
        return OAuthClientInformationFull.model_validate_json(client) if client else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        encoded = client_info.model_dump_json(by_alias=True)
        expected = self._load()
        mcp_servers.store_oauth(
            self.row_id,
            self.owner,
            expected,
            claim=self.flow.claim if self.flow else "",
            client=encoded,
        )
        self._expected = (expected[0], encoded)


class _Provider(OAuthClientProvider):
    async def _perform_authorization_code_grant(self) -> tuple[str, str]:
        try:
            return await super()._perform_authorization_code_grant()
        except OAuthFlowError:
            # The SDK's outer auth flow logs tracebacks. Its validation errors
            # include callback state and issuer, so redact before that logger.
            raise OAuthFlowError(
                "The sign-in response is invalid. Start it again from Settings."
            ) from None

    async def _handle_refresh_response(self, response: httpx2.Response) -> bool:
        try:
            return await super()._handle_refresh_response(response)
        except mcp_servers.OAuthGrantChanged:
            # The SDK installs the response in memory before storage.set_tokens.
            # After a refused CAS, the next request must reload the stored pair
            # (tests/test_mcp_oauth.py), not reuse that rejected refresh response.
            self.context.clear_tokens()
            self._initialized = False
            raise


def provider(server: dict) -> OAuthClientProvider:
    """A noninteractive connect refuses a fresh authorization demand at once."""
    server_id = str(server["server_id"])
    flow: _Flow | None = server.get("flow")
    metadata = OAuthClientMetadata(
        client_name="Skein",
        application_type="web",
        redirect_uris=[AnyUrl(server["oauth_redirect_uri"])],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
    )

    async def redirect(url: str) -> None:
        if flow is None or not flow.claim:
            mcp_servers.mark_oauth_sign_in(int(server["id"]), True)
            raise RuntimeError("sign-in required")
        state = parse_qs(urlsplit(url).query).get("state", [""])[0]
        mcp_servers.register_oauth_state(flow.claim, state)
        flow.state = state
        flow.authorization_url = url
        flow.url_ready.set()

    async def callback() -> AuthorizationCodeResult:
        if flow is None:
            raise RuntimeError("sign-in required")
        await asyncio.to_thread(_await_code, flow)
        if not flow.code:
            raise RuntimeError(flow.error or "sign-in was not completed")
        return AuthorizationCodeResult(code=flow.code, state=flow.state, iss=flow.iss)

    return _Provider(
        server["url"],
        metadata,
        _SealedStorage(int(server["id"]), server_id, flow),
        redirect,
        callback,
    )


def start(server_id: str, server: dict) -> str:
    """Claim before discovery and keep ownership until the connect finishes."""
    from . import mcp_tools

    claim = mcp_servers.claim_oauth(
        int(server["id"]),
        server["owner"],
        FLOW_SECONDS,
        redirect_uri=server["oauth_redirect_uri"],
        browser=server.get("oauth_browser", ""),
    )
    flow = _Flow(server_id, claim)

    def run() -> None:
        try:
            mcp_tools.open_personal(server_id, {**server, "flow": flow})
        finally:
            flow.done.set()
            try:
                mcp_servers.release_oauth(claim)
            finally:
                # The connected provider later refreshes with snapshot CAS,
                # not the interactive claim that ended with this connect.
                flow.claim = ""

    try:
        mcp_servers.mark_oauth_sign_in(int(server["id"]), False)
        mcp_tools.forget(server_id)
        threading.Thread(target=run, daemon=True, name="skein-mcp-oauth").start()
    except BaseException:
        mcp_servers.release_oauth(claim)
        raise
    deadline = time.monotonic() + _URL_WAIT_SECONDS
    while not flow.url_ready.wait(0.1):
        if flow.done.is_set() or time.monotonic() > deadline:
            raise ValueError(
                "The server did not ask for a sign-in. Check that the URL is an MCP server"
                " that uses OAuth, then try again."
            )
    return flow.authorization_url


def complete(state: str, code: str, error: str = "", *, iss: str | None = None) -> bool:
    return mcp_servers.complete_oauth(state, code, bool(error), iss=iss)
