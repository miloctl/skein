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

from app import config


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
def sealed(monkeypatch):
    from app.agents import mcp_oauth, mcp_tools

    monkeypatch.setattr(config, "CREDENTIAL_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr("strands.tools.mcp.MCPClient", FakeClient)
    monkeypatch.setattr(mcp_oauth, "_URL_WAIT_SECONDS", 5.0)
    FakeClient.seen.clear()
    mcp_tools.shutdown_mcp()
    mcp_oauth._pending.clear()
    mcp_oauth._signin_required.clear()
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

    done = client.get("/api/mcp/oauth/callback?state=nonce-1&code=code-9")
    assert done.status_code == 200 and "code-9" not in done.text
    deadline = time.monotonic() + 5
    while "personal:ava:jira" not in mcp_tools._connections and time.monotonic() < deadline:
        time.sleep(0.05)
    assert "personal:ava:jira" in mcp_tools._connections
    assert mcp_tools._connections["personal:ava:jira"].client.granted == ("code-9", "nonce-1")
    assert mcp_oauth._pending == {}


def test_tokens_are_sealed_and_never_shown(client, sealed):
    from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

    from app import db
    from app.agents.mcp_oauth import _SealedStorage
    from app.services import mcp_servers

    row = mcp_servers.add("ava", "jira", "https://jira.example/mcp", auth="oauth", actor="ava")
    storage = _SealedStorage(row["id"], row["server_id"])
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

    server = {
        "id": 1,
        "server_id": "personal:ava:jira",
        "url": "https://jira.example/mcp",
        "oauth_redirect_uri": "https://skein.example/api/mcp/oauth/callback",
    }
    provider = mcp_oauth.provider(server)
    started = time.monotonic()
    with pytest.raises(RuntimeError):
        asyncio.run(provider.context.redirect_handler("https://idp.example/a?state=s"))
    assert time.monotonic() - started < 1
    assert mcp_oauth.needs_sign_in("personal:ava:jira") is True


def test_an_abandoned_sign_in_times_out_and_is_forgotten(fresh_db, sealed, monkeypatch):
    from app.agents import mcp_oauth

    monkeypatch.setattr(mcp_oauth, "FLOW_SECONDS", 0.2)
    flow = mcp_oauth._Flow("personal:ava:jira")
    provider = mcp_oauth.provider(
        {
            "id": 1,
            "server_id": "personal:ava:jira",
            "url": "https://jira.example/mcp",
            "oauth_redirect_uri": "https://skein.example/cb",
            "flow": flow,
        }
    )
    asyncio.run(provider.context.redirect_handler("https://idp.example/a?state=gone"))
    assert "gone" in mcp_oauth._pending
    with pytest.raises(RuntimeError):
        asyncio.run(provider.context.callback_handler())
    assert "gone" not in mcp_oauth._pending
    assert mcp_oauth.complete("gone", "late") is False


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
    mcp_servers.set_redirect_uri(row["id"], "https://skein.example/api/mcp/oauth/callback")
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
    from app.agents import mcp_oauth
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
    with pytest.raises(ValueError):
        mcp_oauth.start(row["server_id"], server)
    assert time.monotonic() - started < 3
