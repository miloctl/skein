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


def _run(effect, *, scoped, agent="", tier="system"):
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
        tier,
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


def test_each_mcp_tool_level_does_what_the_authority_card_says(fresh_db, monkeypatch):
    """CorePolicy reads the agent's "mcp-tool" level for every system MCP
    write. With no way to store one, nobody could forbid an agent's remote
    writes, and with SKEIN_AGENT_REVIEW=0 they applied directly. With review
    on, each level must differ: no level waits, autonomous runs, notify runs
    and tells the team, forbidden refuses."""
    from app.services import delegation, users

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    users.ensure_user("boss")
    remote, result = _run("write", scoped=False, agent="scout")
    assert not remote.called and result["completionStatus"] == "review_required"

    delegation.set_authority("scout", "mcp-tool", "autonomous", actor="boss")
    remote, result = _run("write", scoped=False, agent="scout")
    assert remote.called and result["status"] == "success"
    told = "SELECT count(*) AS n FROM notifications WHERE \"user\" = 'team' AND message LIKE ?"
    assert fresh_db.query_one(told, ("%lookup%",))["n"] == 0

    delegation.set_authority("scout", "mcp-tool", "notify", actor="boss")
    remote, result = _run("write", scoped=False, agent="scout")
    assert remote.called and result["status"] == "success"
    assert fresh_db.query_one(told, ("%lookup%",))["n"] == 1

    delegation.set_authority("scout", "mcp-tool", "forbidden", actor="boss")
    remote, result = _run("write", scoped=False, agent="scout")
    assert not remote.called
    assert "denied" in result["content"][0]["text"]


def test_a_forbidden_agent_is_refused_on_a_personal_server_too(fresh_db, monkeypatch):
    """Every personal write waits for its owner, but a forbidden level is a
    refusal, not a review the owner is then told is moot."""
    from app.agents import mcp_tools
    from app.agents.mcp_tools import PERSONAL
    from app.services import delegation, users

    monkeypatch.setattr(mcp_tools, "_personal_row_is_current", lambda *a, **k: True)
    users.ensure_user("boss")
    delegation.set_authority("scout", "mcp-tool", "forbidden", actor="boss")
    remote, result = _run("write", scoped=False, agent="scout", tier=PERSONAL)
    assert not remote.called
    assert "denied" in result["content"][0]["text"]
    assert fresh_db.query_one("SELECT count(*) AS n FROM pending_changes")["n"] == 0


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


def test_the_documented_env_entry_loads_its_tool():
    """The module docstring and backend/.env.example point an operator at
    this entry as the complete one. An entry the metadata check refuses
    exposes no tool, and the operator sees nothing to explain why."""
    import json
    import re

    from app.agents import mcp_tools

    example = re.search(r"SKEIN_MCP_SERVERS='(\[.*?\])'", mcp_tools.__doc__, re.S)
    [entry] = json.loads(example.group(1))
    remote = _Remote()
    remote.tool_name = "lookup"
    assert mcp_tools._metadata(entry, remote) is not None
