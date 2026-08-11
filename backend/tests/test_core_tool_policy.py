"""Composed workplace policy covers stock model-facing tools."""

import asyncio
from typing import ClassVar

import pytest

from app.agents import receipts
from app.agents.core_tools import GovernedCoreTool
from app.extensions import (
    PolicyContribution,
    PolicyDecision,
    PolicyEffect,
    PolicyEngine,
    PolicySubject,
    SkeinModule,
)
from app.extensions.policy import reset_policy_engine, set_policy_engine
from app.extensions.registry import ExtensionRegistry


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


def test_stock_tool_rejection_uses_the_current_project_context(fresh_db):
    from app.services import engagements, review, users, work

    standard = engagements.create_engagement("Standard work", "standard", actor="mira")
    regulated = engagements.create_engagement("Regulated work", "regulated", actor="mira")
    task = work.create_task(
        "Move this task",
        engagement_id=standard["id"],
        actor="mira",
    )

    def review_progress(request):
        if request.action != "skein.tool.report_progress":
            return None
        return PolicyDecision(
            PolicyEffect.REVIEW,
            approver_groups=(f"{request.resource.project_type}-managers",),
        )

    registry = ExtensionRegistry.build(
        (
            SkeinModule(
                module_id="acme.workplace",
                version="1.0.0",
                extension_api="1.0",
                minimum_core="0.2.0",
                maximum_core_exclusive="0.3.0",
                policies=(PolicyContribution("acme.workplace.progress-review", review_progress),),
            ),
        )
    )
    users.ensure_user("requester")
    users.ensure_user("manager")
    wrapper = GovernedCoreTool(_Delegate("report_progress"), effect="write", risk="high")
    token = set_policy_engine(registry.policy_engine)
    receipts.start()

    async def run():
        return [
            event
            async for event in wrapper._stream(
                {
                    "toolUseId": "progress-1",
                    "input": {"task_id": task["id"], "note": "Progress"},
                },
                {},
                PolicySubject("requester"),
                "delivery-agent",
                "",
            )
        ]

    try:
        events = asyncio.run(run())
        review_id = receipts.drain()[0]["ref"]
    finally:
        receipts.reset()
        reset_policy_engine(token)
    assert events[-1]["completionStatus"] == "review_required"

    fresh_db.execute(
        "UPDATE tasks SET engagement_id = ? WHERE id = ?",
        (regulated["id"], task["id"]),
    )
    with pytest.raises(PermissionError, match="configured workplace approver"):
        review.reject_change(
            review_id,
            actor="manager",
            reviewer_groups=("standard-managers",),
            policy_registry=registry,
        )
    rejected = review.reject_change(
        review_id,
        actor="manager",
        reviewer_groups=("regulated-managers",),
        policy_registry=registry,
    )
    assert rejected["status"] == "rejected"
