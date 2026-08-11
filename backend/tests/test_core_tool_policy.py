"""Composed workplace policy covers stock model-facing tools."""

import asyncio
from typing import ClassVar

from app.agents import receipts
from app.agents.core_tools import GovernedCoreTool
from app.extensions import (
    PolicyDecision,
    PolicyEffect,
    PolicyEngine,
    PolicySubject,
)
from app.extensions.policy import reset_policy_engine, set_policy_engine


class _Delegate:
    tool_spec: ClassVar = {
        "name": "list_tasks",
        "description": "list",
        "inputSchema": {"json": {"type": "object"}},
    }

    def __init__(self, name: str = "list_tasks") -> None:
        self.tool_name = name
        self.called = False

    async def stream(self, tool_use, _invocation_state, **_kwargs):
        self.called = True
        yield {"toolUseId": tool_use["toolUseId"], "status": "success", "content": []}


def test_workplace_policy_can_deny_a_stock_read_tool(fresh_db):
    delegate = _Delegate()
    wrapper = GovernedCoreTool(delegate)
    engine = PolicyEngine(
        (
            lambda request: (
                PolicyDecision(PolicyEffect.DENY, ("read not allowed",))
                if request.action == "skein.tool.list_tasks"
                else None
            ),
        )
    )
    token = set_policy_engine(engine)

    async def run():
        return [
            event
            async for event in wrapper._stream(
                {"toolUseId": "read-1", "input": {}},
                {},
                PolicySubject("mira"),
                "agent",
                "",
            )
        ]

    try:
        events = asyncio.run(run())
    finally:
        reset_policy_engine(token)
    assert events[-1]["completionStatus"] == "denied"
    assert delegate.called is False


def test_stock_specialized_write_creates_a_resumable_review(fresh_db):
    delegate = _Delegate("claim_delegated_task")
    wrapper = GovernedCoreTool(delegate, effect="write", risk="high")
    engine = PolicyEngine(
        (
            lambda request: (
                PolicyDecision(
                    PolicyEffect.REVIEW,
                    ("manager review",),
                    approver_groups=("delivery-managers",),
                )
                if request.action == "skein.tool.claim_delegated_task"
                else None
            ),
        )
    )
    token = set_policy_engine(engine)
    receipts.start()

    async def run():
        return [
            event
            async for event in wrapper._stream(
                {"toolUseId": "write-1", "input": {"task_id": 42}},
                {},
                PolicySubject("mira"),
                "agent",
                "",
            )
        ]

    try:
        events = asyncio.run(run())
        written = receipts.drain()
    finally:
        receipts.reset()
        reset_policy_engine(token)
    assert events[-1]["completionStatus"] == "review_required"
    assert delegate.called is False
    assert written[0]["kind"] == "queued"
    assert written[0]["ref"] > 0
    row = fresh_db.query_one(
        "SELECT entity FROM pending_changes WHERE id = ?", (written[0]["ref"],)
    )
    assert row == {"entity": "extension_core_tool"}
