"""OAuth sign-in for a personal MCP server: the grant is bridged from the
provider's desktop handlers to the browser, tokens are sealed, a chat
turn never waits on a sign-in, and the callback is open on the perimeter
but keyed on the provider's state alone."""

import asyncio
import threading
import time
from functools import partial
from typing import ClassVar

import pytest
from cryptography.fernet import Fernet
from mcp.shared.auth import AuthorizationCodeResult

from app import config


@pytest.mark.parametrize("destination", ["127.0.0.1", "169.254.169.254"])
def test_oauth_discovery_validates_every_request_destination(monkeypatch, destination):
    import socket
    from unittest.mock import AsyncMock

    import httpx2 as httpx

    from app.agents import mcp_oauth, mcp_tools

    monkeypatch.setattr(mcp_oauth._SealedStorage, "get_tokens", AsyncMock(return_value=None))
    monkeypatch.setattr(mcp_oauth._SealedStorage, "get_client_info", AsyncMock(return_value=None))
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, *args, **kwargs: [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (
                    host if host == destination else "10.0.0.5",
                    443,
                ),
            )
        ],
    )
    sent = []

    def respond(request):
        sent.append(str(request.url))
        if request.url.host == destination:
            raise AssertionError("OAuth discovery reached a forbidden destination")
        return httpx.Response(
            401,
            headers={
                "WWW-Authenticate": f'Bearer resource_metadata="http://{destination}/metadata"',
            },
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(respond),
            trust_env=False,
            **kwargs,
        ),
    )

    async def connect():
        auth = mcp_oauth.provider(
            {
                "server_id": "personal:tester:probe",
                "id": 1,
                "url": "https://mcp.example/mcp",
                "oauth_redirect_uri": "https://skein.example/api/mcp/oauth/callback",
            }
        )
        async with mcp_tools._no_redirect_client(auth=auth) as client:
            with pytest.raises(ValueError, match="remote MCP server"):
                await client.post("https://mcp.example/mcp")

    asyncio.run(connect())
    assert sent == ["https://mcp.example/mcp"]


@pytest.mark.parametrize("iss", ["https://idp.example", "https://other.example", None, ""])
@pytest.mark.parametrize("required", [False, True])
def test_callback_issuer_is_validated_before_code_exchange(client, sealed, iss, required):
    from urllib.parse import parse_qs, urlsplit

    from mcp.client.auth.exceptions import OAuthFlowError
    from mcp.shared.auth import OAuthClientInformationFull, OAuthMetadata

    from app.agents import mcp_oauth
    from app.services import mcp_servers

    row, flow, provider = _registered_flow("issuer")
    mcp_servers.release_oauth(flow.claim)
    flow.claim = mcp_servers.claim_oauth(
        row["id"], "ava", mcp_oauth.FLOW_SECONDS, browser="browser-1"
    )
    cookie = {"Cookie": f"{mcp_oauth.BROWSER_COOKIE}{row['id']}=browser-1"}
    provider.context.client_info = OAuthClientInformationFull(client_id="skein")
    provider.context.oauth_metadata = OAuthMetadata(
        issuer="https://idp.example",
        authorization_endpoint="https://idp.example/authorize",
        token_endpoint="https://idp.example/token",
        response_types_supported=["code"],
        authorization_response_iss_parameter_supported=required,
    )
    original_redirect = provider.context.redirect_handler

    async def browser(url):
        await original_redirect(url)
        state = parse_qs(urlsplit(url).query)["state"][0]
        params = {"state": state, "code": "issuer-code"}
        if iss is not None:
            params["iss"] = iss
        response = client.get("/api/mcp/oauth/callback", params=params, headers=cookie)
        assert response.status_code == 200
        assert "issuer-code" not in response.text
        assert not mcp_oauth.complete(state, "replayed", iss=iss)

    provider.context.redirect_handler = browser
    assert provider.context.client_metadata.application_type == "web"
    if iss == "https://idp.example" or (iss is None and not required):
        code, verifier = asyncio.run(provider._perform_authorization_code_grant())
        assert code == "issuer-code" and verifier
    else:
        with pytest.raises(OAuthFlowError):
            asyncio.run(provider._perform_authorization_code_grant())


@pytest.mark.parametrize("location", ["https://mcp.example/next", "https://other.example/next"])
def test_personal_client_refuses_redirects(monkeypatch, location):
    import httpx2

    from app.agents import mcp_tools
    from app.services import mcp_servers

    sent = []
    monkeypatch.setattr(mcp_servers, "check_url", lambda url: None)
    real_client = httpx2.AsyncClient

    def respond(request):
        sent.append(str(request.url))
        return httpx2.Response(307, headers={"Location": location})

    monkeypatch.setattr(
        httpx2,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx2.MockTransport(respond), trust_env=False, **kwargs
        ),
    )

    async def connect():
        async with mcp_tools._no_redirect_client(
            headers={"Authorization": "Bearer secret"}
        ) as client:
            assert not client.follow_redirects
            with pytest.raises(ValueError, match="redirect"):
                await client.post("https://mcp.example/mcp")

    asyncio.run(connect())
    assert sent == ["https://mcp.example/mcp"]


@pytest.mark.parametrize(
    "code",
    [
        "opaque-code",
        '{"code":"nested","iss":"https://fake.example"}',
        "skein-oauth-callback-v1:plain-code",
    ],
)
def test_callback_encoding_preserves_opaque_legacy_codes(monkeypatch, code):
    from app.services import credentials, mcp_servers

    monkeypatch.setattr(config, "CREDENTIAL_KEY", Fernet.generate_key().decode())
    legacy = credentials.seal(code)
    assert mcp_servers._unseal_callback(legacy) == {"code": code, "iss": None}
    encoded = mcp_servers._seal_callback(code, "https://idp.example")
    assert code.encode() not in encoded
    assert b"https://idp.example" not in encoded
    assert mcp_servers._unseal_callback(encoded) == {"code": code, "iss": "https://idp.example"}


@pytest.mark.parametrize("payload", ['{"code":"c"}', '{"code":"c","iss":12}', "[]", "not-json"])
def test_malformed_callback_payload_is_not_an_authorization_code(monkeypatch, payload):
    from app.services import credentials, mcp_servers

    monkeypatch.setattr(config, "CREDENTIAL_KEY", Fernet.generate_key().decode())
    for prefix in (mcp_servers._CALLBACK_PREFIX, b"skein-oauth-callback-v2:"):
        encoded = prefix + credentials.seal(payload)
        assert mcp_servers._unseal_callback(encoded) == {"code": "", "iss": None}


def test_http_transport_owns_its_client_and_yields_the_sdk_pair(monkeypatch):
    from contextlib import asynccontextmanager

    import httpx2
    import mcp.client.streamable_http

    from app.agents import mcp_tools
    from app.services import mcp_servers

    events = []
    monkeypatch.setattr(mcp_servers, "check_url", lambda url: None)
    real_client = httpx2.AsyncClient
    clients = []

    def make_client(**kwargs):
        client = real_client(
            transport=httpx2.MockTransport(lambda request: httpx2.Response(200)),
            trust_env=False,
            **kwargs,
        )
        clients.append(client)
        return client

    @asynccontextmanager
    async def transport(url, *, http_client):
        assert not http_client.is_closed
        assert http_client.headers["Authorization"] == "Bearer secret"
        events.append("enter")
        try:
            yield "read", "write"
        finally:
            assert not http_client.is_closed
            events.append("exit")

    monkeypatch.setattr(httpx2, "AsyncClient", make_client)
    monkeypatch.setattr(mcp.client.streamable_http, "streamable_http_client", transport)

    async def connect():
        assert clients == []
        async with mcp_tools._http_transport(
            "https://mcp.example/mcp", headers={"Authorization": "Bearer secret"}, personal=True
        ) as streams:
            assert streams == ("read", "write")
            assert not clients[0].is_closed
        assert clients[0].is_closed

    asyncio.run(connect())
    assert events == ["enter", "exit"]


@pytest.mark.parametrize("personal", [False, True])
@pytest.mark.parametrize("trust", ["default", "file", "directory", "both"])
def test_mcp_transport_preserves_certificate_trust(monkeypatch, personal, trust):
    import ssl
    from contextlib import asynccontextmanager

    import certifi
    import httpx2
    import mcp.client.streamable_http

    from app.agents import mcp_tools

    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    if trust in ("file", "both"):
        monkeypatch.setenv("SSL_CERT_FILE", "/configured/ca.pem")
    if trust in ("directory", "both"):
        monkeypatch.setenv("SSL_CERT_DIR", "/configured/certs")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    seen = []
    monkeypatch.setattr(
        ssl, "create_default_context", lambda **kwargs: seen.append(kwargs) or context
    )

    @asynccontextmanager
    async def client(**kwargs):
        assert kwargs["verify"] is context
        assert kwargs.get("trust_env", True)
        assert kwargs["follow_redirects"] is (not personal)
        assert kwargs["timeout"].connect == 30.0
        assert kwargs["timeout"].read == 300.0
        yield object()

    @asynccontextmanager
    async def transport(url, *, http_client):
        yield "read", "write"

    monkeypatch.setattr(httpx2, "AsyncClient", client)
    monkeypatch.setattr(mcp.client.streamable_http, "streamable_http_client", transport)

    async def connect():
        async with mcp_tools._http_transport("https://mcp.example", personal=personal):
            pass

    asyncio.run(connect())
    expected = (
        {"cafile": "/configured/ca.pem"}
        if trust in ("file", "both")
        else (
            {"capath": "/configured/certs"} if trust == "directory" else {"cafile": certifi.where()}
        )
    )
    assert seen == [expected]


@pytest.mark.parametrize(
    "failure", ["metadata", "registration", "token", "refresh", "issuer", "state"]
)
def test_oauth_rejects_unsafe_requests_without_logging_callback_secrets(
    monkeypatch, caplog, failure
):
    import socket
    from unittest.mock import AsyncMock

    import httpx2
    from mcp.shared.auth import OAuthClientInformationFull, OAuthMetadata, OAuthToken

    from app.agents import mcp_oauth, mcp_tools
    from app.services import mcp_servers

    sentinel = "private-callback-sentinel"
    forbidden = "http://169.254.169.254/" + sentinel
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, *args, **kwargs: [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (host if host == "169.254.169.254" else "10.0.0.5", 443),
            )
        ],
    )
    monkeypatch.setattr(mcp_oauth._SealedStorage, "get_tokens", AsyncMock(return_value=None))
    monkeypatch.setattr(mcp_oauth._SealedStorage, "get_client_info", AsyncMock(return_value=None))
    monkeypatch.setattr(mcp_oauth._SealedStorage, "set_client_info", AsyncMock())
    monkeypatch.setattr(mcp_servers, "register_oauth_state", lambda claim, state: None)
    flow = mcp_oauth._Flow("personal:ava:test", "claim")

    def callback(flow):
        flow.code = sentinel
        flow.iss = sentinel if failure == "issuer" else "https://idp.example"
        if failure == "state":
            flow.state = sentinel

    monkeypatch.setattr(mcp_oauth, "_await_code", callback)
    auth = mcp_oauth.provider(
        {
            "server_id": flow.server_id,
            "id": 1,
            "url": "https://mcp.example/mcp",
            "oauth_redirect_uri": "https://skein.example/cb",
            "flow": flow,
        }
    )
    metadata = {
        "issuer": "https://idp.example",
        "authorization_endpoint": "https://idp.example/authorize",
        "token_endpoint": forbidden
        if failure in ("token", "refresh")
        else "https://idp.example/token",
        "registration_endpoint": forbidden
        if failure == "registration"
        else "https://idp.example/register",
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
        "authorization_response_iss_parameter_supported": True,
    }
    if failure == "refresh":
        auth._initialized = True
        auth.context.current_tokens = OAuthToken(access_token="old", refresh_token=sentinel)
        auth.context.client_info = OAuthClientInformationFull(
            client_id="skein", issuer="https://idp.example"
        )
        auth.context.token_expiry_time = 1
        auth.context.oauth_metadata = OAuthMetadata.model_validate(metadata)
    sent = []

    def respond(request):
        url = str(request.url)
        sent.append(url)
        assert request.url.host != "169.254.169.254"
        if url == "https://mcp.example/mcp":
            return httpx2.Response(
                401,
                headers={
                    "WWW-Authenticate": 'Bearer resource_metadata="https://mcp.example/metadata"'
                },
            )
        if url == "https://mcp.example/metadata":
            return httpx2.Response(
                200,
                json={
                    "resource": "https://mcp.example/mcp",
                    "authorization_servers": [
                        forbidden if failure == "metadata" else "https://idp.example"
                    ],
                },
            )
        if ".well-known/" in url:
            return httpx2.Response(200, json=metadata)
        if url == "https://idp.example/register":
            return httpx2.Response(
                201,
                json={
                    "client_id": "skein",
                    "redirect_uris": ["https://skein.example/cb"],
                    "token_endpoint_auth_method": "none",
                },
            )
        raise AssertionError("The refused OAuth flow reached token exchange")

    real_client = httpx2.AsyncClient
    monkeypatch.setattr(
        httpx2,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx2.MockTransport(respond), trust_env=False, **kwargs
        ),
    )

    async def connect():
        async with mcp_tools._no_redirect_client(auth=auth) as client:
            from mcp.client.auth.exceptions import OAuthFlowError

            error = OAuthFlowError if failure in ("issuer", "state") else ValueError
            with pytest.raises(error) as caught:
                await client.post("https://mcp.example/mcp")
            assert sentinel not in str(caught.value)

    asyncio.run(connect())
    assert (
        len(sent)
        == {"metadata": 2, "registration": 3, "token": 4, "refresh": 0, "issuer": 4, "state": 4}[
            failure
        ]
    )
    assert not any(url.startswith(forbidden) for url in sent)
    assert sentinel not in caplog.text


def _bootstrap(owner: str) -> dict:
    from app.services.api_keys import create_key

    return {"Authorization": f"Bearer {create_key(owner, 'test')['key']}"}


class _RemoteTool:
    def __init__(self, prefix):
        self.tool_name = f"{prefix}_ping"
        self.tool_spec = {"name": "ping", "inputSchema": {}}
        self.mcp_tool = None


class FakeClient:
    """Drives the provider's handlers the way its auth flow would on a 401:
    the redirect handler with a state, then the callback handler."""

    seen: ClassVar[list[dict]] = []

    def __init__(self, factory: partial, prefix=None, startup_timeout=30, **_kwargs):
        self.auth = factory.keywords.get("auth")
        self.prefix = prefix
        FakeClient.seen.append({"startup_timeout": startup_timeout, "auth": self.auth})

    def __enter__(self):
        context = self.auth.context

        async def grant():
            await context.redirect_handler("https://idp.example/authorize?state=nonce-1&x=y")
            return await context.callback_handler()

        self.granted = asyncio.run(grant())
        return self

    def __exit__(self, *_args):
        return None

    def list_tools_sync(self):
        return [_RemoteTool(self.prefix)]


@pytest.fixture
def sealed(monkeypatch, fresh_db):
    from app.agents import mcp_oauth, mcp_tools
    from app.services import users

    users.ensure_user("ava")

    monkeypatch.setattr(config, "CREDENTIAL_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr("strands.tools.mcp.MCPClient", FakeClient)
    monkeypatch.setattr(mcp_oauth, "_URL_WAIT_SECONDS", 5.0)
    FakeClient.seen.clear()
    mcp_tools.shutdown_mcp()
    yield
    mcp_tools.shutdown_mcp()


def test_the_grant_is_bridged_to_the_browser_and_the_connect_completes(client, sealed):
    from app import db
    from app.agents import mcp_oauth, mcp_tools

    ava = _bootstrap("ava")
    added = client.post(
        "/api/mcp/servers",
        json={"name": "jira", "url": "https://jira.example/mcp", "auth": "oauth"},
        headers=ava,
    )
    assert added.status_code == 200, added.text
    row = added.json()
    assert row["auth"] == "oauth" and row["signed_in"] is False and row["status"] is None
    listing = client.get("/api/mcp/servers", headers=ava).json()["personal"][0]
    assert listing["sign_in_required"] is True
    assert FakeClient.seen == [], "an unsigned OAuth server was opened by a turn"

    started = client.post(f"/api/mcp/servers/{row['id']}/sign-in", headers=ava)
    assert started.status_code == 200, started.text
    assert started.json()["authorization_url"].startswith("https://idp.example/authorize?")
    assert FakeClient.seen[-1]["startup_timeout"] == int(mcp_oauth.FLOW_SECONDS)
    stored = db.query_one("SELECT oauth_redirect_uri FROM mcp_servers WHERE id = ?", (row["id"],))
    assert stored is not None and stored["oauth_redirect_uri"].endswith("/api/mcp/oauth/callback")

    # a second start while one waits is refused, and the wrong state learns nothing
    assert client.post(f"/api/mcp/servers/{row['id']}/sign-in", headers=ava).status_code == 400
    assert client.get("/api/mcp/oauth/callback?state=other&code=c").status_code == 404
    # The state travels in the authorization URL. A colleague's browser that
    # opens it holds no binding cookie, and its grant must not reach ava's
    # server (mcp_servers.oauth_browser_matches).
    name = f"{mcp_oauth.BROWSER_COOKIE}{row['id']}"
    binding = started.cookies[name]
    for other in ({}, {"Cookie": f"{name}=someone-else"}):
        stolen = client.get("/api/mcp/oauth/callback?state=nonce-1&code=stolen", headers=other)
        assert stolen.status_code == 404
    assert "stolen" not in stolen.text

    done = client.get(
        "/api/mcp/oauth/callback?state=nonce-1&code=code-9&iss=https://idp.example",
        headers={"Cookie": f"{name}={binding}"},
    )
    assert done.status_code == 200 and "code-9" not in done.text
    deadline = time.monotonic() + 5
    while "personal:ava:jira" not in mcp_tools._connections and time.monotonic() < deadline:
        time.sleep(0.05)
    assert "personal:ava:jira" in mcp_tools._connections
    assert mcp_tools._connections["personal:ava:jira"].client.granted == AuthorizationCodeResult(
        code="code-9", state="nonce-1", iss="https://idp.example"
    )
    deadline = time.monotonic() + 3
    while db.query("SELECT * FROM mcp_oauth_flows") and time.monotonic() < deadline:
        time.sleep(0.01)
    assert db.query("SELECT * FROM mcp_oauth_flows") == []


def test_a_sign_in_moves_the_stamp_and_a_refresh_does_not(fresh_db, sealed):
    """A sign-in kept the row's stamp, so a connection in another process
    kept its old grant, refreshed with it, failed, and marked the server
    signed out again over the fresh sign-in."""
    from mcp.shared.auth import OAuthToken

    from app import db
    from app.agents.mcp_oauth import FLOW_SECONDS, _Flow, _SealedStorage
    from app.services import mcp_servers

    row = mcp_servers.add("ava", "jira", "https://jira.example/mcp", auth="oauth", actor="ava")
    old = "2000-01-01T00:00:00+00:00"
    db.execute("UPDATE mcp_servers SET updated_at = ? WHERE id = ?", (old, row["id"]))
    claim = mcp_servers.claim_oauth(row["id"], "ava", FLOW_SECONDS)
    connecting = {"stamp": old}
    grant = _SealedStorage(row["id"], row["server_id"], _Flow(row["server_id"], claim), connecting)
    asyncio.run(grant.set_tokens(OAuthToken(access_token="a1", refresh_token="r1")))
    stamp = db.query_one("SELECT updated_at FROM mcp_servers WHERE id = ?", (row["id"],))
    assert stamp["updated_at"] != old
    # the sign-in's own connection keeps the new stamp, or it is discarded
    entry = dict(mcp_servers.entries_for("ava"))[row["server_id"]]
    assert connecting["stamp"] == entry["stamp"]

    mcp_servers.release_oauth(claim)
    refresh = _SealedStorage(row["id"], row["server_id"], None, {"stamp": entry["stamp"]})
    asyncio.run(refresh.get_tokens())
    asyncio.run(refresh.set_tokens(OAuthToken(access_token="a2", refresh_token="r1")))
    after = db.query_one("SELECT updated_at FROM mcp_servers WHERE id = ?", (row["id"],))
    assert after == stamp, "a refresh must not make every process reconnect"


def test_tokens_are_sealed_and_never_shown(client, sealed):
    from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

    from app import db
    from app.agents.mcp_oauth import FLOW_SECONDS, _Flow, _SealedStorage
    from app.services import mcp_servers

    row = mcp_servers.add("ava", "jira", "https://jira.example/mcp", auth="oauth", actor="ava")
    flow = _Flow(row["server_id"], mcp_servers.claim_oauth(row["id"], "ava", FLOW_SECONDS))
    storage = _SealedStorage(row["id"], row["server_id"], flow)
    assert asyncio.run(storage.get_tokens()) is None
    asyncio.run(storage.set_tokens(OAuthToken(access_token="at-secret", refresh_token="rt-secret")))
    asyncio.run(
        storage.set_client_info(
            OAuthClientInformationFull(
                client_id="cid", client_secret="cs-secret", redirect_uris=["https://s/cb"]
            )
        )
    )
    raw = db.query_one("SELECT * FROM mcp_servers WHERE id = ?", (row["id"],))
    assert raw is not None
    for secret in ("at-secret", "rt-secret", "cs-secret"):
        assert secret.encode() not in bytes(raw["oauth_tokens_sealed"])
        assert secret.encode() not in bytes(raw["oauth_client_sealed"])
    assert asyncio.run(storage.get_tokens()).access_token == "at-secret"
    assert asyncio.run(storage.get_client_info()).client_secret == "cs-secret"
    ava = _bootstrap("ava")
    listing = client.get("/api/mcp/servers", headers=ava)
    assert listing.json()["personal"][0]["signed_in"] is True
    for secret in ("at-secret", "rt-secret", "cs-secret"):
        assert secret not in listing.text


def test_a_turn_never_waits_on_a_sign_in(fresh_db, sealed):
    """A stored grant the server no longer accepts: the provider asks for a
    redirect, and a connect with no flow refuses at once and marks the row."""
    from app.agents import mcp_oauth
    from app.services import mcp_servers

    row = mcp_servers.add("ava", "jira", "https://jira.example/mcp", auth="oauth", actor="ava")
    server = {
        "id": row["id"],
        "server_id": "personal:ava:jira",
        "url": "https://jira.example/mcp",
        "oauth_redirect_uri": "https://skein.example/api/mcp/oauth/callback",
    }
    provider = mcp_oauth.provider(server)
    started = time.monotonic()
    with pytest.raises(RuntimeError):
        asyncio.run(provider.context.redirect_handler("https://idp.example/a?state=s"))
    assert time.monotonic() - started < 1
    assert mcp_servers.list_for("ava")[0]["oauth_signin_required"] is True


def test_the_callback_can_land_on_another_process(fresh_db, sealed, monkeypatch):
    """The code returns to whichever process serves the callback. The waiting
    connect reads it from the flow row, not from this process's memory."""
    import threading

    from app import db
    from app.agents import mcp_oauth

    _, flow, provider = _registered_flow("afar")
    assert db.query_one("SELECT 1 FROM mcp_oauth_flows WHERE state = 'afar'")
    box: dict = {}
    waiter = threading.Thread(
        target=lambda: box.update(got=asyncio.run(provider.context.callback_handler()))
    )
    waiter.start()
    assert mcp_oauth.complete("afar", "code-7", iss="https://idp.example") is True
    stored = db.query_row("SELECT code_sealed FROM mcp_oauth_flows WHERE state = 'afar'")
    assert b"code-7" not in bytes(stored["code_sealed"])
    assert b"https://idp.example" not in bytes(stored["code_sealed"])
    waiter.join(5)
    assert box["got"] == AuthorizationCodeResult(
        code="code-7", state="afar", iss="https://idp.example"
    )
    assert mcp_oauth.complete("afar", "late") is False
    # Ownership survives the callback until the provider stores its tokens.
    assert db.query_one("SELECT 1 FROM mcp_oauth_flows WHERE state = 'afar'")
    from app.services import mcp_servers

    mcp_servers.release_oauth(flow.claim)
    assert db.query_one("SELECT 1 FROM mcp_oauth_flows WHERE state = 'afar'") is None


def test_an_abandoned_sign_in_times_out_and_is_forgotten(fresh_db, sealed, monkeypatch):
    from app.agents import mcp_oauth

    monkeypatch.setattr(mcp_oauth, "FLOW_SECONDS", 0.2)
    from app.services import mcp_servers

    _, flow, provider = _registered_flow("gone")
    with pytest.raises(RuntimeError):
        asyncio.run(provider.context.callback_handler())
    mcp_servers.release_oauth(flow.claim)
    assert mcp_oauth.complete("gone", "late") is False


def _registered_flow(name="jira"):
    from app.agents import mcp_oauth
    from app.services import mcp_servers, users

    users.ensure_user("ava")
    row = mcp_servers.add("ava", name, "https://jira.example/mcp", auth="oauth", actor="ava")
    sid, server = mcp_servers.entry_for(row["id"], "ava")
    server["oauth_redirect_uri"] = "https://skein.example/cb"
    flow = mcp_oauth._Flow(sid, mcp_servers.claim_oauth(row["id"], "ava", mcp_oauth.FLOW_SECONDS))
    provider = mcp_oauth.provider({**server, "flow": flow})
    asyncio.run(provider.context.redirect_handler(f"https://idp.example/a?state={name}"))
    return row, flow, provider


def test_local_callback_is_one_shot(fresh_db, sealed):
    from app.agents import mcp_oauth

    _, flow, provider = _registered_flow()
    assert mcp_oauth.complete(flow.state, "first")
    assert not mcp_oauth.complete(flow.state, "second")
    assert asyncio.run(provider.context.callback_handler()) == AuthorizationCodeResult(
        code="first", state=flow.state
    )


def test_local_callback_refuses_expired_flow(fresh_db, sealed, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.agents import mcp_oauth

    _, flow, _ = _registered_flow()
    future = (datetime.now(UTC) + timedelta(minutes=6)).isoformat(timespec="seconds")
    monkeypatch.setattr(fresh_db, "now", lambda: future)
    assert not mcp_oauth.complete(flow.state, "late")


def test_deactivation_invalidates_an_oauth_callback(fresh_db, sealed):
    from app.agents import mcp_oauth
    from app.services import users

    _, flow, _ = _registered_flow()
    users.set_active("ava", False)
    assert not mcp_oauth.complete(flow.state, "offboarded")
    assert fresh_db.query("SELECT * FROM mcp_oauth_flows") == []


def test_sign_in_claim_precedes_discovery_and_registration(fresh_db, sealed, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from app.agents import mcp_oauth
    from app.services import mcp_servers

    entered, release = threading.Event(), threading.Event()

    class Registering(FakeClient):
        def __enter__(self):
            entered.set()
            assert release.wait(5)
            return super().__enter__()

    monkeypatch.setattr("strands.tools.mcp.MCPClient", Registering)
    row = mcp_servers.add("ava", "jira", "https://jira.example/mcp", auth="oauth", actor="ava")
    sid, server = mcp_servers.entry_for(row["id"], "ava")
    server["oauth_redirect_uri"] = "https://skein.example/cb"
    with ThreadPoolExecutor(1) as callers:
        first = callers.submit(mcp_oauth.start, sid, server)
        try:
            assert entered.wait(3)
            with pytest.raises(ValueError, match="already in progress"):
                mcp_oauth.start(sid, server)
            assert len(FakeClient.seen) == 1
        finally:
            release.set()
            first.result(5)
            mcp_oauth.complete("nonce-1", "accepted")
    deadline = time.monotonic() + 5
    while fresh_db.query("SELECT * FROM mcp_oauth_flows") and time.monotonic() < deadline:
        time.sleep(0.01)
    assert fresh_db.query("SELECT * FROM mcp_oauth_flows") == []


def test_a_state_collision_cannot_attach_a_second_server(fresh_db, sealed):
    from psycopg.errors import UniqueViolation

    from app.agents import mcp_oauth
    from app.services import mcp_servers

    _, first, provider = _registered_flow()
    row = mcp_servers.add("ava", "second", "https://second.example/mcp", auth="oauth", actor="ava")
    claim = mcp_servers.claim_oauth(row["id"], "ava", mcp_oauth.FLOW_SECONDS)
    with pytest.raises(UniqueViolation):
        mcp_servers.register_oauth_state(claim, first.state)
    assert mcp_oauth.complete(first.state, "first-only")
    assert asyncio.run(provider.context.callback_handler()) == AuthorizationCodeResult(
        code="first-only", state=first.state
    )
    assert mcp_servers.oauth_result(claim) == {
        "refused": False,
        "done": False,
        "code": "",
        "iss": None,
    }
    mcp_servers.release_oauth(claim)
    assert mcp_servers.oauth_result(first.claim)["done"]


@pytest.mark.parametrize("merge", [False, True])
def test_rename_invalidates_the_old_grants_owner(fresh_db, sealed, merge):
    from mcp.shared.auth import OAuthToken

    from app.agents import mcp_oauth
    from app.services import mcp_servers, users

    row, flow, provider = _registered_flow()
    if merge:
        # a merge would hand ava's MCP server to dana, a person ava never
        # chose, so it is refused while she holds one (users._holds_personal_data)
        users.ensure_user("dana")
        with pytest.raises(ValueError, match="only its owner can read"):
            users.rename_user("ava", "dana", actor="ops")
        return
    users.rename_user("ava", "dana", actor="ops")
    assert not mcp_oauth.complete(flow.state, "renamed")
    assert fresh_db.query("SELECT * FROM mcp_oauth_flows") == []
    successor = mcp_servers.claim_oauth(row["id"], "dana", mcp_oauth.FLOW_SECONDS)
    mcp_servers.release_oauth(flow.claim)
    assert fresh_db.query_row("SELECT claim_id, owner FROM mcp_oauth_flows") == {
        "claim_id": successor,
        "owner": "dana",
    }
    with pytest.raises(ValueError, match="not available"):
        asyncio.run(provider.context.storage.set_tokens(OAuthToken(access_token="late")))


def test_expired_grant_cannot_store_tokens_or_release_its_successor(fresh_db, sealed, monkeypatch):
    from mcp.shared.auth import OAuthToken

    from app.agents import mcp_oauth
    from app.services import mcp_servers

    row, old_flow, provider = _registered_flow()
    old_storage = provider.context.storage
    assert asyncio.run(old_storage.get_tokens()) is None
    with monkeypatch.context() as expired:
        expires_at = fresh_db.query_row("SELECT expires_at FROM mcp_oauth_flows")["expires_at"]
        expired.setattr(fresh_db, "now", lambda: expires_at)
        assert mcp_servers.prune_oauth_flows() == 1
        with pytest.raises(ValueError, match="sign-in changed"):
            asyncio.run(old_storage.set_tokens(OAuthToken(access_token="expired")))
    successor = mcp_servers.claim_oauth(row["id"], "ava", mcp_oauth.FLOW_SECONDS)
    mcp_servers.release_oauth(old_flow.claim)
    assert fresh_db.query_row("SELECT claim_id FROM mcp_oauth_flows")["claim_id"] == successor
    with pytest.raises(ValueError, match="sign-in changed"):
        asyncio.run(old_storage.set_tokens(OAuthToken(access_token="stale")))
    assert mcp_servers.load_oauth(row["id"]) == ("", "")


@pytest.mark.parametrize("rotate", [False, True])
def test_refresh_preserves_or_rotates_the_sealed_grant(fresh_db, sealed, rotate):
    import httpx2
    from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

    from app.agents import mcp_oauth
    from app.services import mcp_servers

    row, flow, provider = _registered_flow()
    storage = provider.context.storage
    asyncio.run(
        storage.set_client_info(
            OAuthClientInformationFull(
                client_id="first",
                issuer="https://idp.example",
                redirect_uris=["https://skein.example/cb"],
            )
        )
    )
    asyncio.run(
        storage.set_tokens(
            OAuthToken(access_token="first", refresh_token="first-refresh", scope="tools:read")
        )
    )
    mcp_servers.release_oauth(flow.claim)
    flow.claim = ""
    asyncio.run(provider._initialize())
    response = {"access_token": "next", "expires_in": 300}
    if rotate:
        response["refresh_token"] = "next-refresh"
    assert asyncio.run(provider._handle_refresh_response(httpx2.Response(200, json=response)))
    restored = mcp_oauth._SealedStorage(row["id"], row["server_id"])
    tokens = asyncio.run(restored.get_tokens())
    assert tokens.access_token == "next"
    assert tokens.refresh_token == ("next-refresh" if rotate else "first-refresh")
    assert tokens.scope == "tools:read"
    assert asyncio.run(restored.get_client_info()).issuer == "https://idp.example"


def test_refresh_cannot_overwrite_a_new_interactive_grant(fresh_db, sealed):
    import httpx2 as httpx
    from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

    from app.agents import mcp_oauth
    from app.services import mcp_servers

    row, flow, provider = _registered_flow()
    storage = provider.context.storage
    asyncio.run(
        storage.set_client_info(
            OAuthClientInformationFull(
                client_id="first", redirect_uris=["https://skein.example/cb"]
            )
        )
    )
    asyncio.run(storage.set_tokens(OAuthToken(access_token="first", refresh_token="first-refresh")))
    mcp_servers.release_oauth(flow.claim)
    flow.claim = ""
    asyncio.run(provider._initialize())
    flow.claim = mcp_servers.claim_oauth(row["id"], "ava", mcp_oauth.FLOW_SECONDS)
    with pytest.raises(ValueError, match="sign-in changed"):
        mcp_servers.store_oauth(
            row["id"],
            "ava",
            mcp_servers.load_oauth(row["id"]),
            tokens='{"access_token":"old-refresh"}',
        )
    new_storage = mcp_oauth._SealedStorage(row["id"], row["server_id"], flow)
    asyncio.run(
        new_storage.set_client_info(
            OAuthClientInformationFull(
                client_id="second", redirect_uris=["https://skein.example/cb"]
            )
        )
    )
    asyncio.run(
        new_storage.set_tokens(OAuthToken(access_token="second", refresh_token="second-refresh"))
    )
    mcp_servers.release_oauth(flow.claim)
    flow.claim = ""

    async def refresh_and_retry():
        provider.context.token_expiry_time = 1
        refresh = provider.async_auth_flow(httpx.Request("POST", "https://jira.example/mcp"))
        request = await anext(refresh)
        assert b"refresh_token=first-refresh" in request.content
        with pytest.raises(ValueError, match="sign-in changed"):
            await refresh.asend(
                httpx.Response(
                    200,
                    json={
                        "access_token": "late-first",
                        "refresh_token": "late-first-refresh",
                        "expires_in": 300,
                    },
                )
            )
        retry = provider.async_auth_flow(httpx.Request("POST", "https://jira.example/mcp"))
        try:
            request = await anext(retry)
            assert request.headers["Authorization"] == "Bearer second"
            assert provider.context.client_info.client_id == "second"
        finally:
            await retry.aclose()

    asyncio.run(refresh_and_retry())
    tokens, client_info = mcp_servers.load_oauth(row["id"])
    assert '"second-refresh"' in tokens and '"second"' in client_info


def test_oauth_needs_the_credential_key_and_takes_no_token(client, sealed, monkeypatch):
    ava = _bootstrap("ava")
    refused = client.post(
        "/api/mcp/servers",
        json={"name": "j", "url": "https://j.example/", "auth": "oauth", "auth_token": "t"},
        headers=ava,
    )
    assert refused.status_code == 400
    monkeypatch.setattr(config, "CREDENTIAL_KEY", "")
    refused = client.post(
        "/api/mcp/servers",
        json={"name": "j", "url": "https://j.example/", "auth": "oauth"},
        headers=ava,
    )
    assert refused.status_code == 400
    assert "SKEIN_CREDENTIAL_KEY" in refused.json()["detail"]


def test_unknown_callback_does_not_require_a_credential_key(client, monkeypatch):
    monkeypatch.setattr(config, "CREDENTIAL_KEY", "")
    assert client.get("/api/mcp/oauth/callback?state=unknown&code=untrusted").status_code == 404


def test_the_callback_is_open_on_the_perimeter_in_api_key_mode(client, sealed, monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "api-key")
    assert client.get("/api/mcp/oauth/callback?state=nope&code=c").status_code == 404
    assert client.get("/api/mcp/servers").status_code in (401, 403)


def test_a_sign_in_thread_is_the_only_opener(fresh_db, sealed):
    from app.agents import mcp_tools

    opened = threading.Event()

    class Slow(FakeClient):
        def __enter__(self):
            opened.set()
            time.sleep(0.3)
            return super().__enter__()

    import strands.tools.mcp as strands_mcp

    strands_mcp.MCPClient = Slow  # type: ignore[attr-defined]
    from app.services import mcp_servers

    row = mcp_servers.add("ava", "jira", "https://jira.example/mcp", auth="oauth", actor="ava")
    _, server = mcp_servers.entry_for(row["id"], "ava")
    server["oauth_redirect_uri"] = "https://skein.example/api/mcp/oauth/callback"
    from app.agents import mcp_oauth

    url = mcp_oauth.start(row["server_id"], server)
    assert url.startswith("https://idp.example/")
    assert opened.wait(2)
    assert row["server_id"] in mcp_tools._opening
    assert mcp_tools.personal_mcp_tools("ava") == []
    assert len(FakeClient.seen) == 1, "a turn opened the server a sign-in was opening"
    mcp_oauth.complete("nonce-1", "code-1")


def test_a_start_against_a_dead_server_returns_when_the_connect_gives_up(
    fresh_db, sealed, monkeypatch
):
    from app.agents import mcp_oauth, mcp_tools
    from app.services import mcp_servers

    class Dead(FakeClient):
        def __enter__(self):
            raise RuntimeError("unreachable")

    import strands.tools.mcp as strands_mcp

    strands_mcp.MCPClient = Dead  # type: ignore[attr-defined]
    monkeypatch.setattr(mcp_oauth, "_URL_WAIT_SECONDS", 10.0)
    row = mcp_servers.add("ava", "dead", "https://dead.example/mcp", auth="oauth", actor="ava")
    _, server = mcp_servers.entry_for(row["id"], "ava")
    server["oauth_redirect_uri"] = "https://skein.example/api/mcp/oauth/callback"
    started = time.monotonic()
    # the server is down, not the URL wrong: a retry fixes it, so 503 with
    # the retry wait, never the 400 that sends the person to edit the URL
    with pytest.raises(mcp_tools.MCPServerNotReady) as down:
        mcp_oauth.start(row["server_id"], server)
    assert time.monotonic() - started < 3
    assert (down.value.code, down.value.status_code) == ("MCP_SERVER_UNAVAILABLE", 503)
    assert down.value.retry_after > 0


def test_a_sign_in_with_no_free_connect_slot_says_busy(fresh_db, sealed, monkeypatch):
    """With every connect slot held, the sign-in said "The server did not ask
    for a sign-in. Check that the URL is an MCP server that uses OAuth", sending
    the person to check a URL that was fine."""
    from app.agents import mcp_oauth, mcp_tools
    from app.services import mcp_servers

    row = mcp_servers.add("ava", "jira", "https://jira.example/mcp", auth="oauth", actor="ava")
    sid, server = mcp_servers.entry_for(row["id"], "ava")
    server["oauth_redirect_uri"] = "https://skein.example/cb"
    monkeypatch.setitem(mcp_tools._owner_connects, "ava", mcp_tools._PER_OWNER_CONNECTS)
    with pytest.raises(mcp_tools.MCPServerNotReady) as busy:
        mcp_oauth.start(sid, server)
    assert (busy.value.code, busy.value.status_code) == ("MCP_CONNECT_BUSY", 503)
    # the claim is released, so the person can sign in once a slot frees
    deadline = time.monotonic() + 3
    while fresh_db.query("SELECT * FROM mcp_oauth_flows") and time.monotonic() < deadline:
        time.sleep(0.05)
    assert fresh_db.query("SELECT * FROM mcp_oauth_flows") == []


def test_a_slow_sign_in_start_lets_the_person_try_again(fresh_db, sealed, monkeypatch):
    """Discovery slower than the wait for the authorization URL answered "the
    server did not ask for a sign-in", and kept the claim, so the retry said
    "already in progress, finish it first" for up to five minutes."""
    from app.agents import mcp_oauth, mcp_tools
    from app.services import mcp_servers

    release = threading.Event()

    class Slow(FakeClient):
        def __enter__(self):
            release.wait(10)
            return super().__enter__()

    monkeypatch.setattr("strands.tools.mcp.MCPClient", Slow)
    monkeypatch.setattr(mcp_oauth, "_URL_WAIT_SECONDS", 0.3)
    row = mcp_servers.add("ava", "jira", "https://jira.example/mcp", auth="oauth", actor="ava")
    sid, server = mcp_servers.entry_for(row["id"], "ava")
    server["oauth_redirect_uri"] = "https://skein.example/cb"
    try:
        with pytest.raises(mcp_tools.MCPServerNotReady) as slow:
            mcp_oauth.start(sid, server)
        assert (slow.value.code, slow.value.status_code) == ("MCP_SIGN_IN_SLOW", 503)
        assert fresh_db.query("SELECT * FROM mcp_oauth_flows") == []
        # the person can start again at once, not "already in progress"
        with pytest.raises(mcp_tools.MCPServerNotReady):
            mcp_oauth.start(sid, server)
    finally:
        release.set()
        # the released sign-ins still write their claim release; fresh_db
        # drops the database under them otherwise ("being accessed by other
        # users")
        for thread in threading.enumerate():
            if thread.name == "skein-mcp-oauth":
                thread.join(10)
