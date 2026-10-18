"""A remote MCP call after a scoped read never carries that text out unreviewed.

A turn that read a row only the requester or their crew can read
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


def _run(effect, *, scoped):
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
