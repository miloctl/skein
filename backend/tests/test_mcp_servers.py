"""Personal MCP servers: the token is sealed and never echoed, rows are
owner-scoped, the URL check refuses this host, and the offboarding and rename
paths carry the rows along."""

import json
from functools import partial

import pytest
from cryptography.fernet import Fernet

from app import config


def _bootstrap(owner: str) -> dict:
    from app.services.api_keys import create_key

    return {"Authorization": f"Bearer {create_key(owner, 'test')['key']}"}


class _Annotations:
    def __init__(self, read_only: bool):
        self.readOnlyHint = read_only
        self.destructiveHint = not read_only


class _Raw:
    def __init__(self, read_only: bool):
        self.annotations = _Annotations(read_only)


class _RemoteTool:
    def __init__(self, name: str, prefix: str | None, read_only: bool):
        self.tool_name = f"{prefix}_{name}" if prefix else name
        self.tool_spec = {"name": name, "inputSchema": {"json": {"type": "object"}}}
        self.mcp_tool = _Raw(read_only)


SEEN: list[dict] = []


class FakeClient:
    """Records what the real MCPClient would be given: the URL, the bearer
    header, and the prefix the personal tier must apply."""

    def __init__(self, factory: partial, prefix=None, **_kwargs):
        self.url = factory.args[0]
        self.headers = factory.keywords.get("headers")
        self.prefix = prefix
        SEEN.append({"url": self.url, "headers": self.headers, "prefix": prefix})

    def __enter__(self):
        if "down.example" in self.url:
            raise RuntimeError("unreachable")
        return self

    def __exit__(self, *_args):
        return None

    def list_tools_sync(self):
        return [
            _RemoteTool("search", self.prefix, True),
            _RemoteTool("update", self.prefix, False),
        ]


@pytest.fixture
def sealed(monkeypatch):
    from app.agents import mcp_tools

    monkeypatch.setattr(config, "CREDENTIAL_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr("strands.tools.mcp.MCPClient", FakeClient)
    SEEN.clear()
    mcp_tools.shutdown_mcp()
    yield
    mcp_tools.shutdown_mcp()


def test_a_personal_server_is_owner_scoped_and_its_token_never_leaves_sealed(client, sealed):
    assert (
        client.post("/api/mcp/servers", json={"name": "jira", "url": "https://x/"}).status_code
        == 403
    )
    ava, bo = _bootstrap("ava"), _bootstrap("bo")

    added = client.post(
        "/api/mcp/servers",
        json={"name": "jira", "url": "https://jira.example/mcp", "auth_token": "tok-secret"},
        headers=ava,
    )
    assert added.status_code == 200, added.text
    row = added.json()
    assert "tok-secret" not in added.text
    assert row["has_token"] is True and row["server_id"] == "personal:ava:jira"
    # the connection got the unsealed token, and the personal prefix
    assert SEEN[-1] == {
        "url": "https://jira.example/mcp",
        "headers": {"Authorization": "Bearer tok-secret"},
        "prefix": "jira",
    }
    assert row["status"]["connected"] is True
    assert {(t["name"], t["effect"], t["risk"]) for t in row["status"]["tools"]} == {
        ("jira_search", "read", "low"),
        ("jira_update", "write", "high"),
    }

    from app import db
    from app.services import credentials

    stored = db.query_one("SELECT auth_token_sealed FROM mcp_servers WHERE name = 'jira'")
    assert stored is not None and bytes(stored["auth_token_sealed"]) != b"tok-secret"
    assert credentials.unseal(stored["auth_token_sealed"]) == "tok-secret"

    listing = client.get("/api/mcp/servers", headers=ava).json()
    assert listing["sealing"] is True
    assert [s["name"] for s in listing["personal"]] == ["jira"]
    assert "tok-secret" not in json.dumps(listing)
    assert client.get("/api/mcp/servers", headers=bo).json()["personal"] == []

    dup = client.post(
        "/api/mcp/servers", json={"name": "jira", "url": "https://other.example/"}, headers=ava
    )
    assert dup.status_code == 409

    assert client.delete(f"/api/mcp/servers/{row['id']}", headers=bo).status_code == 404
    assert client.delete(f"/api/mcp/servers/{row['id']}", headers=ava).status_code == 200
    assert client.get("/api/mcp/servers", headers=ava).json()["personal"] == []


def test_a_failed_connect_is_a_field_not_an_error(client, sealed):
    ava = _bootstrap("ava")
    added = client.post(
        "/api/mcp/servers", json={"name": "dead", "url": "https://down.example/mcp"}, headers=ava
    )
    assert added.status_code == 200
    assert added.json()["status"]["connected"] is False
    assert added.json()["status"]["retry_in_seconds"] is not None


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/mcp",
        "http://[::ffff:127.0.0.1]:8000/mcp",
        "http://localhost/mcp",
        "http://169.254.169.254/latest/",
        "https://user:pw@host.example/mcp",
        "ftp://host.example/mcp",
    ],
)
def test_the_url_check_refuses_this_host_and_odd_schemes(client, sealed, url):
    ava = _bootstrap("ava")
    refused = client.post("/api/mcp/servers", json={"name": "x", "url": url}, headers=ava)
    assert refused.status_code == 400, refused.text
    assert url not in refused.text


def test_a_bad_name_is_refused(client, sealed):
    ava = _bootstrap("ava")
    assert (
        client.post(
            "/api/mcp/servers", json={"name": "Jira Prod", "url": "https://h.example/"}, headers=ava
        ).status_code
        == 400
    )


def test_a_token_without_the_key_is_refused_and_the_form_is_told(client, sealed, monkeypatch):
    monkeypatch.setattr(config, "CREDENTIAL_KEY", "")
    ava = _bootstrap("ava")
    assert client.get("/api/mcp/servers", headers=ava).json()["sealing"] is False
    refused = client.post(
        "/api/mcp/servers",
        json={"name": "jira", "url": "https://jira.example/mcp", "auth_token": "tok"},
        headers=ava,
    )
    assert refused.status_code == 400
    assert "SKEIN_CREDENTIAL_KEY" in refused.json()["detail"]
    # without a token the server can still be added
    assert (
        client.post(
            "/api/mcp/servers",
            json={"name": "jira", "url": "https://jira.example/mcp"},
            headers=ava,
        ).status_code
        == 200
    )


def test_a_person_registers_a_bounded_number_of_servers(client, sealed):
    from app.services import mcp_servers

    ava = _bootstrap("ava")
    for n in range(mcp_servers.LIMIT):
        assert (
            client.post(
                "/api/mcp/servers", json={"name": f"s{n}", "url": "https://h.example/"}, headers=ava
            ).status_code
            == 200
        )
    refused = client.post(
        "/api/mcp/servers", json={"name": "one-more", "url": "https://h.example/"}, headers=ava
    )
    assert refused.status_code == 400
    assert f"up to {mcp_servers.LIMIT}" in refused.json()["detail"]


def test_offboarding_and_rename_carry_the_rows(client, sealed):
    from app import db
    from app.agents import mcp_tools
    from app.services import mcp_servers, users

    ava = _bootstrap("ava")
    users.ensure_user("ava")
    client.post("/api/mcp/servers", json={"name": "jira", "url": "https://j.example/"}, headers=ava)
    assert "personal:ava:jira" in mcp_tools._connections
    users.rename_user("ava", "avery", actor="admin")
    assert "personal:ava:jira" not in mcp_tools._connections, (
        "a renamed owner's token stayed cached"
    )
    assert [r["owner"] for r in db.query("SELECT owner FROM mcp_servers")] == ["avery"]
    assert mcp_servers.list_for("avery")[0]["server_id"] == "personal:avery:jira"
    users.set_active("avery", False, actor="admin")
    assert db.query("SELECT 1 FROM mcp_servers") == []
