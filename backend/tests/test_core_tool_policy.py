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


def test_reviewed_stock_tool_uses_current_grant_and_saved_identities(fresh_db, monkeypatch):
    from app.agents.core_tools import execute_reviewed_core
    from app.agents.identity import agent_identity, requester_identity
    from app.services import review, users, work

    observed: dict[str, str] = {}
    required = {"group": "old-managers"}
    policy_calls = {"count": 0}

    class IdentityDelegate(_Delegate):
        async def stream(self, tool_use, _invocation_state, **_kwargs):
            self.called = True
            observed["agent"] = agent_identity()
            observed["requester"] = requester_identity()
            yield {"toolUseId": tool_use["toolUseId"], "status": "success", "content": []}

    def review_progress(request):
        if request.action == "skein.tool.report_progress":
            policy_calls["count"] += 1
            return PolicyDecision(
                PolicyEffect.REVIEW,
                approver_groups=(required["group"],),
            )
        return None

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
    for name in ("requester", "manager"):
        users.ensure_user(name)
    task = work.create_task("Report progress", actor="requester")
    delegate = IdentityDelegate("report_progress")
    wrapper = GovernedCoreTool(delegate, effect="write", risk="high")
    token = set_policy_engine(registry.policy_engine)
    receipts.start()

    async def queue():
        return [
            event
            async for event in wrapper._stream(
                {
                    "toolUseId": "progress-current",
                    "input": {"task_id": task["id"], "note": "On track"},
                },
                {},
                PolicySubject("requester"),
                "research-agent",
                "",
            )
        ]

    try:
        events = asyncio.run(queue())
        review_id = receipts.drain()[0]["ref"]
    finally:
        receipts.reset()
        reset_policy_engine(token)
    assert events[-1]["completionStatus"] == "review_required"

    required["group"] = "new-managers"
    monkeypatch.setattr("app.tools.ALL_TOOLS", [delegate])
    approved = review.approve_change(
        review_id,
        actor="manager",
        reviewer_groups=("new-managers",),
        extension_executor=lambda invocation, _change_id: asyncio.run(
            execute_reviewed_core(invocation, registry)
        ),
        policy_registry=registry,
    )

    assert approved["result"]["status"] == "completed"
    assert observed == {"agent": "research-agent", "requester": "requester"}
    assert policy_calls["count"] == 2
    assert fresh_db.query_one(
        "SELECT COUNT(*) AS count FROM pending_changes WHERE entity = 'extension_core_tool'"
    ) == {"count": 1}


def test_stock_tool_review_preserves_crew_scope(fresh_db):
    from app import db
    from app.services import crews, review, scope, users, work

    for name in ("owner", "outsider"):
        users.ensure_user(name)
    crew_id = crews.create_crew("Private delivery", actor="owner")["id"]
    task = work.create_task(
        "Crew progress",
        actor="owner",
        visibility="crew",
        crew_id=crew_id,
    )
    engine = PolicyEngine(
        (
            lambda request: (
                PolicyDecision(PolicyEffect.REVIEW)
                if request.action == "skein.tool.report_progress"
                else None
            ),
        )
    )
    wrapper = GovernedCoreTool(_Delegate("report_progress"), effect="write", risk="high")
    token = set_policy_engine(engine)
    receipts.start()

    async def queue():
        return [
            event
            async for event in wrapper._stream(
                {
                    "toolUseId": "crew-progress",
                    "input": {"task_id": task["id"], "note": "On track"},
                },
                {},
                PolicySubject("owner"),
                "delivery-agent",
                "",
            )
        ]

    try:
        asyncio.run(queue())
        review_id = receipts.drain()[0]["ref"]
    finally:
        receipts.reset()
        reset_policy_engine(token)

    assert fresh_db.query_one(
        "SELECT review_visibility, review_crew_id FROM pending_changes WHERE id = ?",
        (review_id,),
    ) == {"review_visibility": "crew", "review_crew_id": crew_id}
    outsider = scope.Viewer("outsider", True)
    assert review_id not in {item["id"] for item in review.list_changes(viewer=outsider)}
    with pytest.raises(db.NotFound):
        review.approve_change(review_id, actor="outsider", viewer=outsider)
    with pytest.raises(db.NotFound):
        review.reject_change(review_id, actor="outsider", viewer=outsider)
