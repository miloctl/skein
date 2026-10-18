"""A remote MCP tool call takes the governance a gated write takes.

After a scoped read: a turn that read a row only the requester or their crew can read
(identity.note_scoped_read) holds that text in the model's context. Any
remote call's arguments are model-chosen, so a read-effect call is an egress
channel to the server, and a write's proposal payload quotes the arguments
to whoever reviews it. These pin the gate's rule (tools/_gate.py) on the
remote path: after a scoped read every remote call waits for the requester's
own review, and a team-visible review is refused outright.
"""

import asyncio
from typing import ClassVar

import pytest

from app import config
from app.agents import identity
from app.agents.mcp_tools import GovernedMCPTool, MCPToolMetadata
from app.extensions.policy import (
    PolicyEngine,
    PolicySubject,
    reset_policy_engine,
    reset_policy_subject,
    set_policy_engine,
    set_policy_subject,
)
from app.services import scope


class _Remote:
    tool_name = "lookup"
    tool_spec: ClassVar = {
        "name": "lookup",
        "description": "Remote lookup",
        "inputSchema": {"json": {"type": "object", "properties": {}}},
    }

    def __init__(self):
        self.called = False

    async def stream(self, tool_use, invocation_state, **kwargs):
        self.called = True
        yield {"toolUseId": tool_use["toolUseId"], "status": "success", "content": []}


def _run(effect, *, scoped, agent=""):
    remote = _Remote()
    tool = GovernedMCPTool(
        remote,
        MCPToolMetadata(
            version="1",
            effect=effect,
            risk="low",
            policy_action=f"lookup.{effect}",
            allowed_agents=(),
            required_capabilities=(),
            output_schema={"type": "object"},
            timeout_seconds=1,
            error_codes=(),
            receipt="required",
            provenance="service",
        ),
        "lookup-server",
    )
    engine = set_policy_engine(PolicyEngine())
    agent_token = identity.set_agent_identity(agent) if agent else None
    subject = set_policy_subject(PolicySubject("ava", strong=True))
    viewer = identity.set_requester_viewer(scope.Viewer("ava", True))
    if scoped:
        identity.note_scoped_read()

    async def go():
        return [
            event
            async for event in tool.stream(
                {"toolUseId": "t1", "name": "lookup", "input": {"q": "private text"}}, {}
            )
        ]

    try:
        events = asyncio.run(go())
    finally:
        if agent_token is not None:
            identity.reset_agent_identity(agent_token)
        identity.reset_requester_viewer(viewer)
        reset_policy_subject(subject)
        reset_policy_engine(engine)
    return remote, events[-1]


def test_a_remote_read_runs_when_the_turn_read_nothing_scoped(fresh_db):
    remote, result = _run("read", scoped=False)
    assert remote.called and result["status"] == "success"


def test_a_remote_read_after_a_scoped_read_waits_for_the_requesters_review(fresh_db):
    remote, result = _run("read", scoped=True)
    assert not remote.called
    assert result["completionStatus"] == "review_required"
    row = fresh_db.query_one(
        "SELECT review_visibility AS visibility, review_owner FROM pending_changes"
        " WHERE entity = 'extension_mcp_tool'"
    )
    assert (row["visibility"], row["review_owner"]) == (scope.PRIVATE, "ava")


@pytest.mark.parametrize("effect", ["read", "write"])
def test_a_team_reviewed_call_after_a_scoped_read_is_refused(fresh_db, monkeypatch, effect):
    """Under separated duties the team reviews agent changes, and the
    proposal payload would hand the private text to every teammate."""
    monkeypatch.setattr(config, "REVIEW_SEPARATION", True)
    remote, result = _run(effect, scoped=True)
    assert not remote.called
    assert result["status"] == "error" and "only you or your crew" in result["content"][0]["text"]
    assert fresh_db.query_one("SELECT count(*) AS n FROM pending_changes")["n"] == 0


def test_an_operator_can_forbid_an_agents_remote_writes(fresh_db):
    """CorePolicy reads the agent's "mcp-tool" level for every system MCP
    write. With no way to store one, nobody could forbid an agent's remote
    writes, and with SKEIN_AGENT_REVIEW=0 they applied directly."""
    from app.services import delegation, users

    users.ensure_user("boss")
    delegation.set_authority("scout", "mcp-tool", "forbidden", actor="boss")
    remote, result = _run("write", scoped=False, agent="scout")
    assert not remote.called
    assert "denied" in result["content"][0]["text"]

    delegation.set_authority("scout", "mcp-tool", "autonomous", actor="boss")
    remote, result = _run("write", scoped=False, agent="scout")
    assert remote.called and result["status"] == "success"


def test_the_authority_card_offers_remote_tools(client):
    entities = client.get("/api/agents/entities").json()["entities"]
    assert any(e["entity"] == "mcp-tool" and e["label"] != "mcp-tool" for e in entities)


def test_an_env_entry_cannot_switch_off_its_declared_governance(monkeypatch):
    """`derive` and `tier` belong to the personal rows services/mcp_servers.py
    builds. Read from SKEIN_MCP_SERVERS, `"derive": true` replaced the
    operator's declared tools block with the server's own annotations - no
    first-use review - and a stray `tier` sent every call down the personal
    path."""
    import json

    from app.agents import mcp_tools

    monkeypatch.setattr(config, "MCP_SERVERS_ERROR", "")
    monkeypatch.setattr(
        config,
        "MCP_SERVERS",
        json.dumps(
            [{"name": "ops", "url": "https://ops.internal/mcp", "derive": True, "tier": "personal"}]
        ),
    )
    [(server_id, server)] = mcp_tools._server_entries()
    assert server_id == "ops"
    assert "derive" not in server and "tier" not in server
    # with no tools block an env server governs nothing, so it exposes nothing
    assert mcp_tools._metadata(server, _Remote()) is None
