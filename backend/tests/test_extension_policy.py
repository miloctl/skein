"""Policy, identity, and governed-tool contracts for workplace modules."""

import asyncio
import json
import time
from dataclasses import replace
from typing import ClassVar

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.extensions import (
    AppSettings,
    ContextContribution,
    IdentityContribution,
    PolicyContribution,
    PolicyDecision,
    PolicyEffect,
    PolicyEngine,
    PolicyInput,
    PolicyResource,
    PolicySubject,
    RouteContribution,
    RouteOperationContribution,
    ServiceIdentityContribution,
    SkeinModule,
    SpecialistContribution,
    ToolContribution,
)
from app.extensions.agents import missing_specialist_capabilities, resolve_context
from app.extensions.policy import reset_policy_engine, set_policy_engine
from app.extensions.registry import ExtensionRegistry
from app.extensions.tools import ToolCallContext, execute_tool
from app.main import create_app
from app.tools._gate import gated_write


class _RemoteTool:
    tool_name = "atlas_remote"
    tool_spec: ClassVar = {
        "name": "atlas_remote",
        "description": "Remote Atlas operation",
        "inputSchema": {"json": {"type": "object", "properties": {}}},
    }

    def __init__(self):
        self.called = False

    async def stream(self, tool_use, invocation_state, **kwargs):
        self.called = True
        yield {"toolUseId": tool_use["toolUseId"], "status": "success", "content": []}


class SyncIn(BaseModel):
    external_id: str


class SyncOut(BaseModel):
    updated: str


def _workplace_rule(request: PolicyInput):
    if request.action == "atlas.update" and (
        request.resource.project_type == "regulated" or request.tool_risk in ("high", "critical")
    ):
        return PolicyDecision(
            PolicyEffect.REVIEW,
            ("Regulated or high-risk Atlas updates need manager review.",),
            ("record-manager-verdict",),
            ("delivery-managers",),
            ("acme.approve-atlas",),
        )
    if request.action == "task.create" and request.subject.name == "blocked-user":
        return PolicyDecision(PolicyEffect.DENY, ("This subject cannot create tasks.",))
    return None


def _module(handler=lambda external_id: {"updated": external_id}) -> SkeinModule:
    def tool_handler(_context, request: SyncIn):
        return handler(request.external_id)

    return SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("acme.workplace.policy", _workplace_rule),),
        identities=(
            IdentityContribution(
                "acme.workplace.identity",
                lambda name, groups, strong: {
                    "roles": ("manager",) if name == "manager" else (),
                    "capabilities": (
                        ("acme.approve-atlas",) if "delivery-managers" in groups else ()
                    ),
                    "acme.strong": strong,
                },
            ),
        ),
        tools=(
            ToolContribution(
                name="acme.workplace.atlas-update",
                version="1.0.0",
                model_name="acme_atlas_update",
                description="Update one Atlas work item through the governed adapter.",
                handler=tool_handler,
                input_schema=SyncIn,
                output_schema=SyncOut,
                effect="write",
                risk="high",
                policy_action="atlas.update",
                allowed_agents=("acme.workplace.delivery",),
                timeout_seconds=1,
            ),
        ),
        contexts=(
            ContextContribution(
                "acme.workplace.delivery-context",
                lambda user: f"Delivery indicators visible to {user}.",
                policy_action="acme.delivery-context.read",
                required_capabilities=("acme.use-delivery-specialist",),
            ),
        ),
        specialists=(
            SpecialistContribution(
                name="acme.workplace.delivery",
                version="1.0.0",
                display_name="Acme Delivery Specialist",
                description="Reviews delivery risk and Atlas synchronization.",
                system_prompt="Treat Atlas data as reported context.",
                tools=("acme.workplace.atlas-update",),
                context_sources=("acme.workplace.delivery-context",),
                required_capabilities=("acme.use-delivery-specialist",),
            ),
        ),
    )


def test_policy_can_use_subject_project_origin_and_tool_risk(fresh_db):
    engine = ExtensionRegistry.build((_module(),)).policy_engine
    decision = engine.decide(
        PolicyInput(
            PolicySubject("manager", roles=("manager",), groups=("delivery-managers",)),
            "atlas.update",
            PolicyResource("atlas-item", "A-7", "regulated", "internal"),
            "agent_tool",
            agent="acme.workplace.delivery",
            tool="acme.workplace.atlas-update",
            tool_effect="write",
            tool_risk="high",
        )
    )
    assert decision.effect == PolicyEffect.REVIEW
    assert decision.approver_groups == ("delivery-managers",)
    assert decision.approver_capabilities == ("acme.approve-atlas",)


def test_governed_tool_does_not_run_when_policy_requires_review(fresh_db):
    calls = []
    registry = ExtensionRegistry.build((_module(lambda external_id: calls.append(external_id)),))
    result = asyncio.run(
        execute_tool(
            registry.tool("acme.workplace.atlas-update"),
            {"external_id": "A-7"},
            ToolCallContext(PolicySubject("manager"), "acme.workplace.delivery"),
            registry.policy_engine,
        )
    )
    assert result.status == "review_required"
    assert result.error_code == "review_required"
    assert calls == []


def test_authorized_manager_resumes_the_exact_reviewed_tool_call(fresh_db):
    from app.extensions.tools import execute_reviewed_tool
    from app.services import review, users

    calls: list[str] = []

    def handler(external_id: str):
        calls.append(external_id)
        return {"updated": external_id}

    registry = ExtensionRegistry.build((_module(handler),))
    result = asyncio.run(
        execute_tool(
            registry.tool("acme.workplace.atlas-update"),
            {"external_id": "A-7"},
            ToolCallContext(
                registry.refresh_subject(PolicySubject("requester")),
                "acme.workplace.delivery",
            ),
            registry.policy_engine,
        )
    )
    assert result.status == "review_required"
    assert result.review_id > 0
    assert calls == []

    users.ensure_user("manager")

    def resume(invocation, _change_id):
        execution = asyncio.run(
            execute_reviewed_tool(
                registry.tool("acme.workplace.atlas-update"),
                invocation,
                registry,
            )
        )
        return execution.model_dump(mode="json")

    with pytest.raises(PermissionError, match="configured workplace approver"):
        review.approve_change(
            result.review_id,
            actor="manager",
            extension_executor=resume,
            policy_registry=registry,
        )
    approved = review.approve_change(
        result.review_id,
        actor="manager",
        reviewer_groups=("delivery-managers",),
        reviewer_capabilities=("acme.approve-atlas",),
        extension_executor=resume,
        policy_registry=registry,
    )
    assert approved["result"]["status"] == "completed"
    assert approved["result"]["output"] == {"updated": "A-7"}
    assert calls == ["A-7"]
    stored = fresh_db.query_one(
        "SELECT status, result FROM extension_review_invocations WHERE change_id = ?",
        (result.review_id,),
    )
    assert stored["status"] == "approved"
    assert json.loads(stored["result"])["status"] == "completed"


def test_reviewed_tool_fails_closed_when_current_policy_changes(fresh_db):
    from app.extensions.tools import execute_reviewed_tool

    calls: list[str] = []
    required = {"group": "delivery-managers"}
    policy_calls = {"count": 0}

    def rule(request: PolicyInput):
        if request.action == "atlas.update":
            policy_calls["count"] += 1
            return PolicyDecision(
                PolicyEffect.REVIEW,
                approver_groups=(required["group"],),
            )
        return None

    base = _module(lambda external_id: calls.append(external_id) or {"updated": external_id})
    module = replace(
        base,
        policies=(PolicyContribution("acme.workplace.changing-policy", rule),),
    )
    registry = ExtensionRegistry.build((module,))
    subject = registry.refresh_subject(PolicySubject("requester"))
    queued = asyncio.run(
        execute_tool(
            registry.tools[0],
            {"external_id": "A-9"},
            ToolCallContext(subject, "acme.workplace.delivery"),
            registry.policy_engine,
        )
    )
    stored = fresh_db.query_one(
        "SELECT invocation FROM extension_review_invocations WHERE change_id = ?",
        (queued.review_id,),
    )
    required["group"] = "security-managers"
    resumed = asyncio.run(
        execute_reviewed_tool(
            registry.tools[0],
            json.loads(stored["invocation"]),
            registry,
        )
    )
    assert resumed.status == "review_required"
    assert resumed.approver_groups == ["security-managers"]
    assert calls == []


def test_review_verdict_supplies_the_current_grant_to_contributed_tool(fresh_db):
    from app.extensions.tools import execute_reviewed_tool
    from app.services import review, users

    calls: list[str] = []
    required = {"group": "delivery-managers"}
    policy_calls = {"count": 0}

    def rule(request: PolicyInput):
        if request.action == "atlas.update":
            policy_calls["count"] += 1
            return PolicyDecision(
                PolicyEffect.REVIEW,
                approver_groups=(required["group"],),
            )
        return None

    base = _module(lambda external_id: calls.append(external_id) or {"updated": external_id})
    registry = ExtensionRegistry.build(
        (
            replace(
                base,
                policies=(PolicyContribution("acme.workplace.current-policy", rule),),
            ),
        )
    )
    for name in ("requester", "security-manager"):
        users.ensure_user(name)
    queued = asyncio.run(
        execute_tool(
            registry.tools[0],
            {"external_id": "A-10"},
            ToolCallContext(PolicySubject("requester"), "acme.workplace.delivery"),
            registry.policy_engine,
        )
    )
    required["group"] = "security-managers"

    def resume(invocation, _change_id):
        return asyncio.run(
            execute_reviewed_tool(registry.tools[0], invocation, registry)
        ).model_dump(mode="json")

    approved = review.approve_change(
        queued.review_id,
        actor="security-manager",
        reviewer_groups=("security-managers",),
        extension_executor=resume,
        policy_registry=registry,
    )

    assert approved["result"]["status"] == "completed"
    assert calls == ["A-10"]
    assert policy_calls["count"] == 2
    assert fresh_db.query_one(
        "SELECT COUNT(*) AS count FROM pending_changes WHERE entity = 'extension_tool'"
    ) == {"count": 1}


def test_governed_tool_checks_agent_capability_input_and_output(fresh_db):
    registry = ExtensionRegistry.build((_module(),))
    tool = registry.tool("acme.workplace.atlas-update")
    wrong_agent = asyncio.run(
        execute_tool(
            tool,
            {"external_id": "A-7"},
            ToolCallContext(PolicySubject("manager"), "chief"),
            registry.policy_engine,
        )
    )
    invalid = asyncio.run(
        execute_tool(
            tool,
            {},
            ToolCallContext(PolicySubject("manager"), "acme.workplace.delivery"),
            registry.policy_engine,
        )
    )
    assert wrong_agent.error_code == "agent_not_allowed"
    assert invalid.error_code == "invalid_input"


def test_unknown_tool_effect_is_denied_by_the_core_policy(fresh_db):
    module = _module()
    unknown = replace(
        module.tools[0],
        name="acme.workplace.unclassified",
        effect="unknown",
        risk="low",
    )
    specialist = replace(module.specialists[0], tools=(unknown.name,))
    registry = ExtensionRegistry.build(
        (replace(module, tools=(unknown,), specialists=(specialist,)),)
    )
    result = asyncio.run(
        execute_tool(
            unknown,
            {"external_id": "A-7"},
            ToolCallContext(PolicySubject("manager"), "acme.workplace.delivery"),
            registry.policy_engine,
        )
    )
    assert result.status == "refused"
    assert result.error_code == "policy_denied"


def test_write_timeout_reports_unknown_completion_and_does_not_claim_cancellation(fresh_db):
    calls: list[str] = []
    module = _module()

    def slow(_context, request: SyncIn):
        time.sleep(0.08)
        calls.append(request.external_id)
        return {"updated": request.external_id}

    contribution = replace(
        module.tools[0],
        handler=slow,
        risk="low",
        policy_action="atlas.background.write",
        timeout_seconds=0.01,
    )
    specialist = replace(module.specialists[0], tools=(contribution.name,))
    registry = ExtensionRegistry.build(
        (replace(module, policies=(), tools=(contribution,), specialists=(specialist,)),)
    )
    result = asyncio.run(
        execute_tool(
            contribution,
            {"external_id": "A-LATE"},
            ToolCallContext(PolicySubject("manager"), "acme.workplace.delivery"),
            registry.policy_engine,
        )
    )
    assert result.status == "completion_unknown"
    assert result.error_code == "deadline_exceeded"
    time.sleep(0.1)
    assert calls == ["A-LATE"]


def test_write_exception_after_side_effect_reports_unknown_completion(fresh_db):
    calls: list[str] = []
    module = _module()

    def write_then_fail(_context, request: SyncIn):
        calls.append(request.external_id)
        raise RuntimeError("remote response was lost")

    contribution = replace(
        module.tools[0],
        handler=write_then_fail,
        risk="low",
        policy_action="atlas.background.write",
    )
    specialist = replace(module.specialists[0], tools=(contribution.name,))
    registry = ExtensionRegistry.build(
        (replace(module, policies=(), tools=(contribution,), specialists=(specialist,)),)
    )
    result = asyncio.run(
        execute_tool(
            contribution,
            {"external_id": "A-UNKNOWN"},
            ToolCallContext(PolicySubject("manager"), "acme.workplace.delivery"),
            registry.policy_engine,
        )
    )
    assert calls == ["A-UNKNOWN"]
    assert result.status == "completion_unknown"
    assert result.error_code == "internal_error"


def test_workplace_policy_also_narrows_the_existing_agent_gate(fresh_db):
    registry = ExtensionRegistry.build((_module(),))
    token = set_policy_engine(registry.policy_engine)
    try:
        result = json.loads(
            gated_write(
                "task",
                "create",
                {"title": "must not land"},
                lambda: {"id": 1},
                actor="blocked-user",
            )
        )
    finally:
        reset_policy_engine(token)
    assert "forbidden" in result["error"]


def test_agent_policy_uses_persisted_classification_for_non_task_entities(fresh_db):
    from app.services import blockers

    blocker = blockers.raise_blocker(
        "private blocker",
        actor="mira",
        visibility="private",
    )

    def deny_private(request: PolicyInput):
        if request.resource.classification == "private":
            return PolicyDecision(PolicyEffect.DENY, ("private records are protected",))
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("acme.workplace.private-records", deny_private),),
    )
    registry = ExtensionRegistry.build((module,))
    token = set_policy_engine(registry.policy_engine)
    try:
        result = json.loads(
            gated_write(
                "blocker_edit",
                "update",
                {"title": "must not land"},
                lambda: pytest.fail("private update bypassed policy"),
                entity_id=blocker["id"],
                actor="agent",
            )
        )
    finally:
        reset_policy_engine(token)
    assert "forbidden" in result["error"]


def test_capability_endpoint_uses_the_composed_identity_and_policy(fresh_db):
    with TestClient(create_app(modules=(_module(),)), headers={"X-User": "manager"}) as client:
        response = client.get("/api/capabilities?actions=atlas.update&project_type=regulated")
    assert response.status_code == 200
    body = response.json()
    assert body["roles"] == ["manager"]
    assert body["actions"]["atlas.update"]["effect"] == "review"


def test_specialist_context_is_policy_controlled_and_audited(fresh_db):
    calls: list[str] = []

    def deny_context(request: PolicyInput):
        if request.action == "acme.context.read":
            return PolicyDecision(PolicyEffect.DENY, ("Context access is closed.",))
        return None

    contribution = ContextContribution(
        "acme.workplace.context",
        lambda query: calls.append(query) or "secret context",
        policy_action="acme.context.read",
        risk="medium",
    )
    policy = PolicyEngine((deny_context,))

    with pytest.raises(PermissionError, match="policy denied"):
        resolve_context(
            contribution,
            "mira",
            PolicySubject("mira"),
            "acme.workplace.specialist",
            policy,
        )

    assert calls == []
    assert fresh_db.query_one(
        "SELECT actor, action, detail FROM activity WHERE action = 'external_tool'"
    ) == {
        "actor": "acme.workplace.specialist",
        "action": "external_tool",
        "detail": "acme.workplace.context refused (policy_denied)",
    }


def test_specialist_context_has_a_bounded_output(fresh_db):
    contribution = ContextContribution(
        "acme.workplace.context",
        lambda _query: "too long",
        policy_action="acme.context.read",
        max_output_chars=3,
    )

    with pytest.raises(ValueError, match="output limit"):
        resolve_context(
            contribution,
            "mira",
            PolicySubject("mira"),
            "acme.workplace.specialist",
            PolicyEngine(()),
        )


def test_identity_contributions_aggregate_roles_and_capabilities():
    first = replace(
        _module(),
        module_id="acme.first",
        policies=(),
        identities=(
            IdentityContribution(
                "acme.first.identity",
                lambda _name, _groups, _strong: {
                    "roles": ("manager",),
                    "capabilities": ("first.use",),
                },
            ),
        ),
        tools=(),
        contexts=(),
        specialists=(),
    )
    second = replace(
        first,
        module_id="acme.second",
        identities=(
            IdentityContribution(
                "acme.second.identity",
                lambda _name, _groups, _strong: {
                    "roles": ("auditor",),
                    "capabilities": ("second.use",),
                },
            ),
        ),
    )
    attributes = ExtensionRegistry.build((first, second)).identity_attributes("mira", (), True)
    assert attributes["roles"] == ("manager", "auditor")
    assert attributes["capabilities"] == ("first.use", "second.use")


def _rest_rule(request: PolicyInput):
    if request.action == "skein.rest.post.tasks" and request.subject.name == "blocked-user":
        return PolicyDecision(PolicyEffect.DENY, ("Task creation is disabled.",))
    if request.action == "skein.rest.patch.tasks":
        return PolicyDecision(
            PolicyEffect.REVIEW,
            ("Task changes need a delivery manager.",),
            ("record-manager-verdict",),
            ("delivery-managers",),
            ("acme.approve-task",),
        )
    if request.action == "skein.rest.get.tasks" and request.subject.name == "blocked-reader":
        return PolicyDecision(PolicyEffect.DENY, ("Task reads are disabled.",))
    return None


def test_workplace_policy_governs_existing_rest_mutations(fresh_db):
    from app.services import work

    task = work.create_task("Existing task", actor="manager")
    module = replace(
        _module(),
        policies=(PolicyContribution("acme.workplace.rest-policy", _rest_rule),),
    )
    with TestClient(create_app(modules=(module,))) as client:
        denied = client.post(
            "/api/tasks",
            headers={"X-User": "blocked-user"},
            json={"title": "Must not land"},
        )
        review = client.patch(
            f"/api/tasks/{task['id']}",
            headers={"X-User": "manager"},
            json={"status": "in_progress"},
        )
        read_denied = client.get(
            "/api/tasks",
            headers={"X-User": "blocked-reader"},
        )

    assert denied.status_code == 403
    assert denied.json()["code"] == "POLICY_DENIED"
    assert review.status_code == 403
    assert read_denied.status_code == 403
    assert read_denied.json()["code"] == "POLICY_DENIED"
    assert review.json() == {
        "detail": (
            "This direct route cannot resume a reviewed action. Use a governed tool or workflow."
        ),
        "code": "POLICY_REVIEW_UNSUPPORTED",
        "retryable": False,
        "obligations": [
            "record-manager-verdict",
            "approver-group:delivery-managers",
            "approver-capability:acme.approve-task",
        ],
    }
    assert fresh_db.query_one("SELECT status FROM tasks WHERE id = ?", (task["id"],)) == {
        "status": "todo"
    }


def test_rest_task_policy_uses_persisted_project_class(fresh_db):
    from app.services import engagements

    engagement = engagements.create_engagement(
        "Regulated launch",
        project_class="regulated",
        actor="manager",
    )

    def deny_regulated_task(request: PolicyInput):
        if (
            request.action == "skein.rest.post.tasks"
            and request.resource.project_type == "regulated"
        ):
            return PolicyDecision(PolicyEffect.DENY, ("regulated task creation is paused",))
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("acme.workplace.regulated-task", deny_regulated_task),),
    )
    with TestClient(create_app(modules=(module,)), headers={"X-User": "manager"}) as client:
        response = client.post(
            "/api/tasks",
            json={"title": "must not land", "engagement_id": engagement["id"]},
        )
    assert response.status_code == 403
    assert fresh_db.query_one("SELECT id FROM tasks WHERE title = 'must not land'") is None


def test_rest_delegation_uses_transaction_bound_task_policy_context(fresh_db):
    from app.services import engagements, work

    engagement = engagements.create_engagement(
        "Regulated launch",
        project_class="regulated",
        actor="manager",
    )
    task = work.create_task(
        "Regulated delegation",
        engagement_id=engagement["id"],
        actor="manager",
    )
    seen: list[tuple[str, bool]] = []

    def deny_regulated_delegation(request: PolicyInput):
        if request.action == "skein.rest.post.tasks.delegate":
            seen.append((request.resource.project_type, fresh_db._ambient.get() is not None))
            if request.resource.project_type == "regulated":
                return PolicyDecision(PolicyEffect.DENY, ("regulated delegation is paused",))
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(
            PolicyContribution(
                "acme.workplace.regulated-delegation",
                deny_regulated_delegation,
            ),
        ),
    )
    with TestClient(create_app(modules=(module,)), headers={"X-User": "manager"}) as client:
        response = client.post(
            f"/api/tasks/{task['id']}/delegate",
            json={"agent": "agent", "sponsor": "manager"},
        )

    assert response.status_code == 403
    assert ("regulated", True) in seen
    assert fresh_db.query_one(
        "SELECT delegated_agent, sponsor FROM tasks WHERE id = ?", (task["id"],)
    ) == {"delegated_agent": "", "sponsor": ""}


def test_rest_worklog_read_uses_transaction_bound_task_policy_context(fresh_db):
    from app.services import engagements, work

    engagement = engagements.create_engagement(
        "Regulated launch",
        project_class="regulated",
        actor="manager",
    )
    task = work.create_task(
        "Regulated worklog",
        engagement_id=engagement["id"],
        actor="manager",
    )
    seen: list[tuple[str, bool]] = []

    def deny_regulated_worklog(request: PolicyInput):
        if request.action == "skein.rest.get.tasks.worklog":
            seen.append((request.resource.project_type, fresh_db._ambient.get() is not None))
            if request.resource.project_type == "regulated":
                return PolicyDecision(PolicyEffect.DENY, ("regulated worklogs are closed",))
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(
            PolicyContribution(
                "acme.workplace.regulated-worklog",
                deny_regulated_worklog,
            ),
        ),
    )
    with TestClient(create_app(modules=(module,)), headers={"X-User": "manager"}) as client:
        response = client.get(f"/api/tasks/{task['id']}/worklog")

    assert response.status_code == 403
    assert ("regulated", True) in seen


def test_rest_task_policy_does_not_inspect_a_hidden_relationship_before_refusal(fresh_db):
    from app.services import crews, engagements, scope

    crew_id = crews.create_crew("Hidden delivery", actor="other-person")["id"]
    hidden = engagements.create_engagement(
        "Hidden regulated launch",
        project_class="regulated",
        actor="other-person",
        visibility=scope.CREW,
        crew_id=crew_id,
    )["id"]

    def deny_regulated_task(request: PolicyInput):
        if (
            request.action == "skein.rest.post.tasks"
            and request.resource.project_type == "regulated"
        ):
            return PolicyDecision(PolicyEffect.DENY, ("regulated task creation is paused",))
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("acme.workplace.regulated-task", deny_regulated_task),),
    )
    with TestClient(create_app(modules=(module,)), headers={"X-User": "manager"}) as client:
        hidden_response = client.post(
            "/api/tasks", json={"title": "must not land", "engagement_id": hidden}
        )
        fresh_db.execute("DELETE FROM engagements WHERE id = ?", (hidden,))
        absent_response = client.post(
            "/api/tasks", json={"title": "must not land", "engagement_id": hidden}
        )

    assert hidden_response.status_code == absent_response.status_code == 400
    assert hidden_response.json() == absent_response.json()
    assert hidden_response.json()["detail"] == f"no engagement #{hidden}"
    assert fresh_db.query_one("SELECT id FROM tasks WHERE title = 'must not land'") is None


def test_rest_policy_loads_domain_context_for_an_existing_resource(fresh_db):
    from app.services import engagements, work

    engagement = engagements.create_engagement(
        "Regulated launch",
        project_class="regulated",
        actor="manager",
    )
    task = work.create_task(
        "Regulated task",
        engagement_id=engagement["id"],
        actor="manager",
    )

    def deny_regulated_read(request: PolicyInput):
        if (
            request.action == "skein.rest.get.tasks"
            and request.resource.id
            and request.resource.project_type == "regulated"
        ):
            return PolicyDecision(PolicyEffect.DENY, ("regulated task read is paused",))
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("acme.workplace.regulated-read", deny_regulated_read),),
    )
    with TestClient(create_app(modules=(module,)), headers={"X-User": "manager"}) as client:
        response = client.get(f"/api/tasks/{task['id']}")
        listing = client.get("/api/tasks")
    assert response.status_code == 403
    assert response.json()["code"] == "POLICY_DENIED"
    assert listing.status_code == 200


@pytest.mark.parametrize("project_class", ["regulated", "standard"])
def test_rest_task_read_checks_visibility_before_project_policy(fresh_db, project_class):
    from app.services import crews, engagements, scope, work

    crew_id = crews.create_crew(f"Hidden {project_class}", actor="other-person")["id"]
    engagement = engagements.create_engagement(
        f"Hidden {project_class} engagement",
        project_class=project_class,
        actor="other-person",
        visibility=scope.CREW,
        crew_id=crew_id,
    )["id"]
    task = work.create_task(
        f"Hidden {project_class} task",
        engagement_id=engagement,
        actor="other-person",
        visibility=scope.CREW,
        crew_id=crew_id,
    )["id"]

    def deny_regulated_read(request: PolicyInput):
        if (
            request.action == "skein.rest.get.tasks"
            and request.resource.project_type == "regulated"
        ):
            return PolicyDecision(PolicyEffect.DENY, ("regulated task read is paused",))
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("acme.workplace.regulated-read", deny_regulated_read),),
    )
    with TestClient(create_app(modules=(module,)), headers={"X-User": "manager"}) as client:
        response = client.get(f"/api/tasks/{task}")

    assert response.status_code == 404
    assert response.json()["detail"] == f"no task #{task}"


def test_all_contributed_routes_receive_the_composed_policy(fresh_db):
    from fastapi import APIRouter

    from app.extensions import RouteContribution, RouteOperationContribution

    router = APIRouter(prefix="/api/extensions/acme.workplace")

    @router.post("/unguarded")
    def unguarded():
        return {"unsafe": True}

    @router.get("/unguarded-read")
    def unguarded_read():
        return {"unsafe": True}

    def deny_route(request: PolicyInput):
        if request.action in ("acme.route.write", "acme.route.read"):
            return PolicyDecision(PolicyEffect.DENY, ("This route is disabled.",))
        return None

    module = replace(
        _module(),
        routes=(
            RouteContribution(
                "acme.workplace.routes",
                router,
                (
                    RouteOperationContribution(
                        "POST",
                        "/api/extensions/acme.workplace/unguarded",
                        "acme.route.write",
                        PolicyResource("acme-data"),
                        "write",
                        "high",
                    ),
                    RouteOperationContribution(
                        "GET",
                        "/api/extensions/acme.workplace/unguarded-read",
                        "acme.route.read",
                        PolicyResource("acme-data"),
                        "read",
                        "low",
                    ),
                ),
            ),
        ),
        policies=(PolicyContribution("acme.workplace.deny-route", deny_route),),
    )
    settings = replace(AppSettings.from_config(), scheduler_enabled=False)
    with TestClient(create_app(settings, (module,)), headers={"X-User": "mira"}) as client:
        response = client.post("/api/extensions/acme.workplace/unguarded")
        read_response = client.get("/api/extensions/acme.workplace/unguarded-read")
    assert response.status_code == 403
    assert response.json()["code"] == "POLICY_DENIED"
    assert read_response.status_code == 403
    assert read_response.json()["code"] == "POLICY_DENIED"


def test_contributed_route_policy_receives_the_declared_path_resource_id(fresh_db):
    router = APIRouter(prefix="/api/extensions/acme.workplace")

    @router.get("/items/{item_id}")
    def read_item(item_id: str):
        return {"id": item_id}

    seen: list[str] = []

    def protect_item(request: PolicyInput):
        if request.action == "acme.item.read":
            seen.append(request.resource.id)
            if request.resource.id == "secret":
                return PolicyDecision(PolicyEffect.DENY, ("Secret item is closed.",))
        return None

    module = replace(
        _module(),
        routes=(
            RouteContribution(
                "acme.workplace.items",
                router,
                (
                    RouteOperationContribution(
                        "GET",
                        "/api/extensions/acme.workplace/items/{item_id}",
                        "acme.item.read",
                        PolicyResource("acme-item"),
                        "read",
                        "low",
                        resource_id_param="item_id",
                    ),
                ),
            ),
        ),
        policies=(PolicyContribution("acme.workplace.item-policy", protect_item),),
    )
    with TestClient(create_app(modules=(module,)), headers={"X-User": "mira"}) as client:
        response = client.get("/api/extensions/acme.workplace/items/secret")

    assert response.status_code == 403
    assert seen == ["secret"]


class _FakeModel:
    stateful = False

    def __init__(self):
        self.config = {"model_id": "fake"}

    def get_config(self):
        return self.config

    def update_config(self, **kwargs):
        self.config.update(kwargs)


def test_specialist_and_governed_tool_join_the_real_agent_composition(fresh_db, monkeypatch):
    from app import config
    from app.agents import team_agent

    registry = ExtensionRegistry.build((_module(),))
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "")
    monkeypatch.setattr(config, "SESSIONS_DIR", config.DATA_DIR / "sessions")
    monkeypatch.setattr(team_agent, "_model", lambda **_: _FakeModel())

    specialist = team_agent.build_agent(
        "acme-specialist",
        user="manager",
        persona="acme.workplace.delivery",
        stateless=True,
        extensions=registry,
        policy_subject=PolicySubject(
            "manager",
            capabilities=("acme.use-delivery-specialist",),
        ),
    )
    assert specialist.tool_names == ["acme_atlas_update"]
    assert "Treat Atlas data as reported context" in specialist.system_prompt
    assert "Delivery indicators visible to manager" in specialist.system_prompt

    chief = team_agent.build_agent("acme-chief", user="manager", extensions=registry)
    assert "acme_atlas_update" in chief.tool_names
    assert "`acme.workplace.delivery`" in chief.system_prompt
    assert missing_specialist_capabilities(
        registry,
        "acme.workplace.delivery",
        PolicySubject("manager"),
    ) == ("acme.use-delivery-specialist",)
    assert (
        missing_specialist_capabilities(
            registry,
            "acme.workplace.delivery",
            PolicySubject("manager", capabilities=("acme.use-delivery-specialist",)),
        )
        == ()
    )


def test_unattended_agent_identity_reaches_the_real_contributed_wrapper(fresh_db, monkeypatch):
    from app import config
    from app.agents import receipts, team_agent
    from app.extensions.policy import (
        reset_policy_subject,
        set_policy_subject,
    )
    from app.services import delegation, users

    observed: dict[str, str] = {}

    def handler(context, request: SyncIn):
        observed["agent"] = context.agent
        observed["subject"] = context.subject.name
        return {"updated": request.external_id}

    base = _module()
    contributed = replace(
        base.tools[0],
        handler=handler,
        allowed_agents=("research-agent",),
        resource=lambda _request: PolicyResource("task"),
    )
    registry = ExtensionRegistry.build(
        (replace(base, policies=(), tools=(contributed,), specialists=()),)
    )
    users.ensure_user("manager")
    delegation.set_authority(
        "research-agent",
        "task",
        "autonomous",
        actor="manager",
    )
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "")
    monkeypatch.setattr(config, "AGENT_REVIEW", False)
    monkeypatch.setattr(team_agent, "_model", lambda **_: _FakeModel())
    subject = PolicySubject(
        "research-agent",
        kind="agent",
        strong=True,
        source="agent-runner",
    )
    built = team_agent.build_agent(
        "run-research-agent",
        user="research-agent",
        extensions=registry,
        policy_subject=subject,
    )
    wrapped = built.tool_registry.registry["acme_atlas_update"]
    invoke = next(
        candidate
        for name in ("original_function", "_tool_func", "func", "__wrapped__")
        if callable(candidate := getattr(wrapped, name, None))
    )
    policy_token = set_policy_engine(registry.policy_engine)
    subject_token = set_policy_subject(subject)
    receipts.start()
    try:
        result = json.loads(asyncio.run(invoke(external_id="ATLAS-RUNNER")))
        recorded = receipts.drain()
    finally:
        receipts.reset()
        reset_policy_subject(subject_token)
        reset_policy_engine(policy_token)

    assert result["status"] == "completed"
    assert observed == {"agent": "research-agent", "subject": "research-agent"}
    assert recorded == [
        {
            "kind": "wrote",
            "entity": "acme.workplace.atlas-update",
            "detail": "completed",
            "ref": 0,
            "actor": "research-agent",
        }
    ]
    assert fresh_db.query_one(
        "SELECT actor, action FROM activity WHERE action = 'external_tool'"
    ) == {"actor": "research-agent", "action": "external_tool"}


def test_contributed_specialist_identity_cannot_be_claimed_by_a_human(fresh_db):
    from app.services.users import ensure_user

    ensure_user("acme.workplace.delivery", kind="human")
    settings = replace(AppSettings.from_config(), scheduler_enabled=False)
    with (
        pytest.raises(RuntimeError, match="already owned by a human"),
        TestClient(create_app(settings, (_module(),))),
    ):
        pass


def test_keyless_direct_specialist_uses_the_request_policy_subject(fresh_db):
    module = replace(
        _module(),
        identities=(
            IdentityContribution(
                "acme.workplace.identity",
                lambda name, _groups, _strong: {
                    "roles": (),
                    "capabilities": (
                        ("acme.use-delivery-specialist",) if name == "manager" else ()
                    ),
                },
            ),
        ),
    )
    settings = replace(AppSettings.from_config(), scheduler_enabled=False)
    with TestClient(create_app(settings, (module,)), headers={"X-User": "manager"}) as client:
        response = client.post(
            "/api/chat",
            json={
                "thread_id": "extension-specialist",
                "message": "/as acme.workplace.delivery summarize delivery",
            },
        )
    assert response.status_code == 200
    assert "Acme Delivery Specialist is available" in response.text
    assert "needs a workplace capability" not in response.text


def test_contributed_tools_cannot_shadow_a_core_model_tool():
    module = _module()
    collision = replace(module.tools[0], model_name="create_task")
    specialist = replace(module.specialists[0], tools=(collision.name,))
    with pytest.raises(ValueError, match="collides with core"):
        create_app(modules=(replace(module, tools=(collision,), specialists=(specialist,)),))


def test_governed_tools_require_a_policy_action():
    module = _module()
    invalid = replace(module.tools[0], policy_action="")
    specialist = replace(module.specialists[0], tools=(invalid.name,))
    with pytest.raises(ValueError, match="needs a policy action"):
        ExtensionRegistry.build((replace(module, tools=(invalid,), specialists=(specialist,)),))


def test_mcp_tools_need_complete_metadata_and_pass_through_policy(fresh_db, monkeypatch):
    from app.agents import mcp_tools as mcp_module
    from app.agents.identity import reset_agent_identity, set_agent_identity
    from app.agents.mcp_tools import (
        GovernedMCPTool,
        MCPToolMetadata,
        _metadata,
        execute_reviewed_mcp,
    )
    from app.extensions.policy import reset_policy_subject, set_policy_subject
    from app.services import review, users

    assert _metadata({"tools": {}}, "atlas_remote") is None
    remote = _RemoteTool()
    governed = GovernedMCPTool(
        remote,
        MCPToolMetadata(
            version="1.0.0",
            effect="write",
            risk="high",
            policy_action="atlas.update",
            allowed_agents=("acme.workplace.delivery",),
            timeout_seconds=1,
            error_codes=("remote_error",),
            required_capabilities=(),
            output_schema={"type": "object"},
            receipt="required",
            provenance="service",
        ),
        "atlas-server",
    )
    registry = ExtensionRegistry.build((_module(),))
    policy_token = set_policy_engine(registry.policy_engine)
    subject_token = set_policy_subject(registry.refresh_subject(PolicySubject("requester")))
    agent_token = set_agent_identity("acme.workplace.delivery")

    async def run():
        return [
            event
            async for event in governed.stream(
                {"toolUseId": "mcp-1", "name": "atlas_remote", "input": {}}, {}
            )
        ]

    try:
        events = asyncio.run(run())
    finally:
        reset_agent_identity(agent_token)
        reset_policy_subject(subject_token)
        reset_policy_engine(policy_token)
    assert events[-1]["status"] == "error"
    assert remote.called is False
    pending = fresh_db.query_one(
        "SELECT pc.id, eri.invocation FROM pending_changes pc"
        " JOIN extension_review_invocations eri ON eri.change_id = pc.id"
        " WHERE pc.entity = 'extension_mcp_tool'"
    )
    assert pending is not None
    users.ensure_user("manager")
    monkeypatch.setattr(mcp_module, "mcp_tools", lambda: [governed])

    def resume(invocation, _change_id):
        return asyncio.run(execute_reviewed_mcp(invocation, registry))

    approved = review.approve_change(
        pending["id"],
        actor="manager",
        reviewer_groups=("delivery-managers",),
        reviewer_capabilities=("acme.approve-atlas",),
        extension_executor=resume,
        policy_registry=registry,
    )
    assert approved["result"]["status"] == "completed"
    assert remote.called is True


def test_review_verdict_supplies_the_current_grant_to_mcp_tool(fresh_db, monkeypatch):
    from app.agents import mcp_tools as mcp_module
    from app.agents.identity import reset_agent_identity, set_agent_identity
    from app.agents.mcp_tools import GovernedMCPTool, MCPToolMetadata, execute_reviewed_mcp
    from app.extensions.policy import reset_policy_subject, set_policy_subject
    from app.services import review, users

    required = {"group": "old-managers"}
    policy_calls = {"count": 0}

    def review_remote(request: PolicyInput):
        if request.action == "atlas.remote.write":
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
                policies=(PolicyContribution("acme.workplace.remote-review", review_remote),),
            ),
        )
    )
    remote = _RemoteTool()
    governed = GovernedMCPTool(
        remote,
        MCPToolMetadata(
            version="1.0.0",
            effect="write",
            risk="high",
            policy_action="atlas.remote.write",
            allowed_agents=("agent",),
            required_capabilities=(),
            output_schema={"type": "object"},
            timeout_seconds=1,
            error_codes=(),
            receipt="required",
            provenance="service",
        ),
        "atlas-server",
    )
    users.ensure_user("requester")
    users.ensure_user("manager")
    policy_token = set_policy_engine(registry.policy_engine)
    subject_token = set_policy_subject(PolicySubject("requester"))
    agent_token = set_agent_identity("agent")

    async def queue():
        return [
            event
            async for event in governed.stream(
                {"toolUseId": "mcp-current", "name": "atlas_remote", "input": {}},
                {},
            )
        ]

    try:
        events = asyncio.run(queue())
    finally:
        reset_agent_identity(agent_token)
        reset_policy_subject(subject_token)
        reset_policy_engine(policy_token)
    assert events[-1]["completionStatus"] == "review_required"
    pending = fresh_db.query_one(
        "SELECT id FROM pending_changes WHERE entity = 'extension_mcp_tool'"
    )
    required["group"] = "new-managers"
    monkeypatch.setattr(mcp_module, "mcp_tools", lambda: [governed])
    approved = review.approve_change(
        pending["id"],
        actor="manager",
        reviewer_groups=("new-managers",),
        extension_executor=lambda invocation, _change_id: asyncio.run(
            execute_reviewed_mcp(invocation, registry)
        ),
        policy_registry=registry,
    )

    assert approved["result"]["status"] == "completed"
    assert remote.called is True
    assert policy_calls["count"] == 2
    assert fresh_db.query_one(
        "SELECT COUNT(*) AS count FROM pending_changes WHERE entity = 'extension_mcp_tool'"
    ) == {"count": 1}


def test_mcp_rejection_uses_current_tool_metadata(fresh_db, monkeypatch):
    from app.agents import mcp_tools as mcp_module
    from app.agents.identity import reset_agent_identity, set_agent_identity
    from app.agents.mcp_tools import GovernedMCPTool, MCPToolMetadata
    from app.extensions.policy import reset_policy_subject, set_policy_subject
    from app.services import review, users

    def review_remote(request: PolicyInput):
        if request.action == "atlas.remote.write":
            return PolicyDecision(
                PolicyEffect.REVIEW,
                approver_groups=(f"{request.tool_risk}-managers",),
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
                policies=(PolicyContribution("acme.workplace.remote-review", review_remote),),
            ),
        )
    )
    remote = _RemoteTool()

    def wrapper(version: str, risk: str):
        return GovernedMCPTool(
            remote,
            MCPToolMetadata(
                version=version,
                effect="write",
                risk=risk,
                policy_action="atlas.remote.write",
                allowed_agents=("agent",),
                required_capabilities=(),
                output_schema={"type": "object"},
                timeout_seconds=1,
                error_codes=(),
                receipt="required",
                provenance="service",
            ),
            "atlas-server",
        )

    original = wrapper("1.0.0", "low")
    users.ensure_user("requester")
    users.ensure_user("manager")
    policy_token = set_policy_engine(registry.policy_engine)
    subject_token = set_policy_subject(PolicySubject("requester"))
    agent_token = set_agent_identity("agent")

    async def queue():
        return [
            event
            async for event in original.stream(
                {"toolUseId": "mcp-metadata", "name": "atlas_remote", "input": {}},
                {},
            )
        ]

    try:
        asyncio.run(queue())
    finally:
        reset_agent_identity(agent_token)
        reset_policy_subject(subject_token)
        reset_policy_engine(policy_token)
    pending = fresh_db.query_one(
        "SELECT id FROM pending_changes WHERE entity = 'extension_mcp_tool'"
    )
    monkeypatch.setattr(mcp_module, "mcp_tools", lambda: [wrapper("2.0.0", "critical")])

    with pytest.raises(PermissionError, match="configured workplace approver"):
        review.reject_change(
            pending["id"],
            actor="manager",
            reviewer_groups=("low-managers",),
            policy_registry=registry,
        )
    assert review.reject_change(
        pending["id"],
        actor="manager",
        reviewer_groups=("critical-managers",),
        policy_registry=registry,
    ) == {"id": pending["id"], "status": "rejected"}


def test_mcp_metadata_rejects_empty_actions_and_string_lists():
    from app.agents.mcp_tools import _metadata

    complete = {
        "tools": {
            "remote": {
                "version": "1.0.0",
                "effect": "write",
                "risk": "high",
                "policy_action": "atlas.remote.write",
                "allowed_agents": ["agent"],
                "required_capabilities": [],
                "output_schema": {"type": "object"},
                "timeout_seconds": 10,
                "error_codes": ["remote_error"],
                "receipt": "required",
                "provenance": "service",
            }
        }
    }
    assert _metadata(complete, "remote") is not None
    complete["tools"]["remote"]["policy_action"] = "  "
    assert _metadata(complete, "remote") is None
    complete["tools"]["remote"]["policy_action"] = "atlas.remote.write"
    complete["tools"]["remote"]["allowed_agents"] = "agent"
    assert _metadata(complete, "remote") is None


def test_mcp_names_cannot_shadow_local_model_tools(monkeypatch):
    from app.agents import mcp_tools as mcp_module

    remote = _RemoteTool()
    monkeypatch.setattr(mcp_module, "_tools", [remote])
    assert mcp_module.mcp_tools({"atlas_remote"}) == []


def test_model_facing_extension_tool_reports_review_as_queued(fresh_db):
    from app.agents import receipts
    from app.extensions.agents import strands_tools
    from app.extensions.policy import reset_policy_subject, set_policy_subject

    registry = ExtensionRegistry.build((_module(),))
    tool = strands_tools(registry, "acme.workplace.delivery")[0]
    policy_token = set_policy_engine(registry.policy_engine)
    subject_token = set_policy_subject(PolicySubject("requester"))
    receipts.start()

    invoke = next(
        candidate
        for name in ("original_function", "_tool_func", "func", "__wrapped__")
        if callable(candidate := getattr(tool, name, None))
    )

    try:
        asyncio.run(invoke(external_id="ATLAS-42"))
        recorded = receipts.drain()
    finally:
        receipts.reset()
        reset_policy_subject(subject_token)
        reset_policy_engine(policy_token)
    assert recorded[0]["kind"] == "queued"
    assert recorded[0]["ref"] > 0


def test_successful_mcp_write_records_a_durable_receipt(fresh_db):
    from app.agents.identity import reset_agent_identity, set_agent_identity
    from app.agents.mcp_tools import GovernedMCPTool, MCPToolMetadata
    from app.extensions.policy import (
        reset_policy_subject,
        set_policy_subject,
    )

    remote = _RemoteTool()
    governed = GovernedMCPTool(
        remote,
        MCPToolMetadata(
            version="1.0.0",
            effect="write",
            risk="low",
            policy_action="atlas.remote.write",
            allowed_agents=("agent",),
            required_capabilities=("atlas.remote",),
            output_schema={
                "type": "object",
                "required": ["status"],
                "properties": {"status": {"type": "string"}},
            },
            timeout_seconds=1,
            error_codes=("remote_error",),
            receipt="required",
            provenance="service",
        ),
        "atlas-server",
    )
    registry = ExtensionRegistry.build(())
    policy_token = set_policy_engine(registry.policy_engine)
    subject_token = set_policy_subject(PolicySubject("mira", capabilities=("atlas.remote",)))
    agent_token = set_agent_identity("agent")

    async def run():
        return [
            event
            async for event in governed.stream(
                {"toolUseId": "mcp-2", "name": "atlas_remote", "input": {}}, {}
            )
        ]

    try:
        events = asyncio.run(run())
    finally:
        reset_agent_identity(agent_token)
        reset_policy_subject(subject_token)
        reset_policy_engine(policy_token)
    assert events[-1]["status"] == "success"
    assert remote.called is True
    assert fresh_db.query_one(
        "SELECT actor, action, detail FROM activity WHERE action = 'external_tool'"
    ) == {
        "actor": "agent",
        "action": "external_tool",
        "detail": "atlas_remote completed",
    }


def test_core_agent_approval_rechecks_current_workplace_policy(fresh_db):
    from app.agents.identity import reset_requester_identity, set_requester_identity
    from app.extensions.policy import reset_policy_subject, set_policy_subject
    from app.services import users

    state = {"effect": PolicyEffect.REVIEW}

    def mutable_policy(request: PolicyInput):
        if request.action == "task.create":
            return PolicyDecision(state["effect"], ("current workplace rule",))
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("acme.workplace.mutable-policy", mutable_policy),),
    )
    registry = ExtensionRegistry.build((module,))
    users.ensure_user("requester")
    policy_token = set_policy_engine(registry.policy_engine)
    subject_token = set_policy_subject(PolicySubject("requester"))
    requester_token = set_requester_identity("requester")
    try:
        proposal = json.loads(
            gated_write(
                "task",
                "create",
                {"title": "must not land"},
                lambda: pytest.fail("a reviewed write executed before approval"),
                actor="agent",
            )
        )
    finally:
        reset_requester_identity(requester_token)
        reset_policy_subject(subject_token)
        reset_policy_engine(policy_token)

    state["effect"] = PolicyEffect.DENY
    with TestClient(create_app(modules=(module,)), headers={"X-User": "manager"}) as client:
        response = client.post(f"/api/review/{proposal['id']}/approve", json={"note": ""})
    assert response.status_code == 403
    assert fresh_db.query_one("SELECT id FROM tasks WHERE title = 'must not land'") is None
    assert fresh_db.query_one(
        "SELECT status FROM pending_changes WHERE id = ?", (proposal["id"],)
    ) == {"status": "pending"}


def test_legacy_unbound_agent_review_cannot_bypass_workplace_policy(fresh_db):
    from app.services import review, users

    def deny_task(request: PolicyInput):
        if request.action == "task.create":
            return PolicyDecision(PolicyEffect.DENY, ("Task creation is paused.",))
        return None

    registry = ExtensionRegistry.build(
        (
            SkeinModule(
                module_id="acme.workplace",
                version="1.0.0",
                extension_api="1.0",
                minimum_core="0.2.0",
                maximum_core_exclusive="0.3.0",
                policies=(PolicyContribution("acme.workplace.task-policy", deny_task),),
            ),
        )
    )
    users.ensure_user("legacy-agent", kind="agent")
    users.ensure_user("manager")
    proposal = review.propose_change(
        "task",
        "create",
        {"title": "Must remain absent"},
        actor="legacy-agent",
        origin="agent",
        policy_context=None,
    )
    assert fresh_db.query_one(
        "SELECT review_contract_version FROM pending_changes WHERE id = ?",
        (proposal["id"],),
    ) == {"review_contract_version": 1}
    fresh_db.execute(
        "UPDATE pending_changes SET review_contract_version = 0 WHERE id = ?",
        (proposal["id"],),
    )

    with pytest.raises(PermissionError, match="no policy binding"):
        review.approve_change(
            proposal["id"],
            actor="manager",
            policy_registry=registry,
        )
    assert fresh_db.query_one("SELECT id FROM tasks WHERE title = 'Must remain absent'") is None
    assert review.reject_change(
        proposal["id"],
        actor="manager",
        policy_registry=registry,
    ) == {"id": proposal["id"], "status": "rejected"}


def test_core_agent_approval_refuses_a_deactivated_requester(fresh_db):
    from app.agents.identity import reset_requester_identity, set_requester_identity
    from app.extensions.policy import reset_policy_subject, set_policy_subject
    from app.services import users

    def review_task(request: PolicyInput):
        if request.action == "task.create":
            return PolicyDecision(PolicyEffect.REVIEW)
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("acme.workplace.review-task", review_task),),
    )
    registry = ExtensionRegistry.build((module,))
    users.ensure_user("requester")
    policy_token = set_policy_engine(registry.policy_engine)
    subject_token = set_policy_subject(PolicySubject("requester"))
    requester_token = set_requester_identity("requester")
    try:
        proposal = json.loads(
            gated_write(
                "task",
                "create",
                {"title": "revoked request"},
                lambda: pytest.fail("a reviewed write executed before approval"),
            )
        )
    finally:
        reset_requester_identity(requester_token)
        reset_policy_subject(subject_token)
        reset_policy_engine(policy_token)
    users.set_active("requester", False)

    with TestClient(create_app(modules=(module,)), headers={"X-User": "manager"}) as client:
        response = client.post(f"/api/review/{proposal['id']}/approve", json={"note": ""})
    assert response.status_code == 403
    assert fresh_db.query_one("SELECT id FROM tasks WHERE title = 'revoked request'") is None


def test_subject_refresh_fails_closed_when_directory_claims_are_unavailable(fresh_db):
    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        identities=(
            IdentityContribution(
                "acme.workplace.directory",
                lambda _name, groups, _strong: {
                    "roles": (),
                    "capabilities": ("acme.approve",) if "managers" in groups else (),
                },
                resolver=lambda _name: None,
            ),
        ),
    )
    registry = ExtensionRegistry.build((module,))
    with pytest.raises(PermissionError, match="could not be refreshed"):
        registry.refresh_subject(PolicySubject("mira", groups=("managers",)))


def test_zero_group_directory_subject_still_requires_refresh(fresh_db):
    from app.services import users

    users.ensure_user("mira")
    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        identities=(
            IdentityContribution(
                "acme.workplace.directory",
                lambda _name, _groups, _strong: {},
                resolver=lambda _name: None,
            ),
        ),
    )
    subject = PolicySubject(
        "mira",
        strong=True,
        source="oidc",
        refresh_required=True,
    )
    with pytest.raises(PermissionError, match="could not be refreshed"):
        ExtensionRegistry.build((module,)).refresh_subject(subject)


def test_profile_resolver_cannot_mask_unavailable_group_directory(fresh_db):
    from app.services import users

    users.ensure_user("mira")
    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        identities=(
            IdentityContribution(
                "acme.workplace.directory",
                lambda _name, groups, _strong: {
                    "capabilities": ("acme.approve",) if "managers" in groups else (),
                },
                resolver=lambda _name: None,
            ),
            IdentityContribution(
                "acme.workplace.profile",
                lambda *_args: {},
                resolver=lambda _name: {"active": True},
                resolves_groups=False,
            ),
        ),
    )
    with pytest.raises(PermissionError, match="could not be refreshed"):
        ExtensionRegistry.build((module,)).refresh_subject(
            PolicySubject(
                "mira",
                groups=("managers",),
                capabilities=("acme.approve",),
                strong=True,
                source="oidc",
                refresh_required=True,
            )
        )


def test_registry_rejects_multiple_authoritative_group_resolvers():
    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        identities=(
            IdentityContribution(
                "acme.workplace.first-directory",
                lambda *_args: {},
                resolver=lambda _name: {"groups": ()},
                resolves_groups=True,
            ),
            IdentityContribution(
                "acme.workplace.second-directory",
                lambda *_args: {},
                resolver=lambda _name: {"groups": ()},
                resolves_groups=True,
            ),
        ),
    )
    with pytest.raises(ValueError, match="only one identity contribution can resolve groups"):
        ExtensionRegistry.build((module,))


def test_legacy_two_resolver_identity_package_remains_compatible(fresh_db):
    from app.services import users

    users.ensure_user("mira")
    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        identities=(
            IdentityContribution(
                "acme.workplace.directory",
                lambda *_args: {},
                resolver=lambda _name: {"groups": ("delivery-managers",)},
            ),
            IdentityContribution(
                "acme.workplace.profile",
                lambda *_args: {},
                resolver=lambda _name: {"active": True},
            ),
        ),
    )
    refreshed = ExtensionRegistry.build((module,)).refresh_subject(
        PolicySubject(
            "mira",
            groups=("old-group",),
            source="oidc",
            refresh_required=True,
        )
    )
    assert refreshed.groups == ("delivery-managers",)


def test_rest_playbook_policy_uses_authoritative_project_class(fresh_db):
    def deny_prototype_playbooks(request: PolicyInput):
        if request.action == "playbook.create" and request.resource.project_type == "prototype":
            return PolicyDecision(PolicyEffect.DENY, ("prototype work is closed",))
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("acme.workplace.playbook-policy", deny_prototype_playbooks),),
    )
    with TestClient(create_app(modules=(module,)), headers={"X-User": "mira"}) as client:
        response = client.post(
            "/api/playbooks/instantiate",
            json={"playbook": "prototype", "engagement_name": "Must not exist"},
        )
    assert response.status_code == 403
    assert response.json()["code"] == "POLICY_DENIED"
    assert fresh_db.query_one("SELECT id FROM engagements WHERE name = 'Must not exist'") is None


def test_stock_agent_playbook_tool_uses_authoritative_project_class(fresh_db):
    from app.tools.platform import start_engagement_from_playbook

    def deny_prototype_playbooks(request: PolicyInput):
        if request.action == "playbook.create" and request.resource.project_type == "prototype":
            return PolicyDecision(PolicyEffect.DENY, ("prototype work is closed",))
        return None

    registry = ExtensionRegistry.build(
        (
            SkeinModule(
                module_id="acme.workplace",
                version="1.0.0",
                extension_api="1.0",
                minimum_core="0.2.0",
                maximum_core_exclusive="0.3.0",
                policies=(
                    PolicyContribution(
                        "acme.workplace.playbook-policy",
                        deny_prototype_playbooks,
                    ),
                ),
            ),
        )
    )
    invoke = next(
        candidate
        for name in ("original_function", "_tool_func", "func", "__wrapped__")
        if callable(candidate := getattr(start_engagement_from_playbook, name, None))
    )
    token = set_policy_engine(registry.policy_engine)
    try:
        result = json.loads(
            invoke(
                playbook_slug="prototype",
                engagement_name="Agent must not create this",
            )
        )
    finally:
        reset_policy_engine(token)
    assert "forbidden" in result["error"]
    assert (
        fresh_db.query_one("SELECT id FROM engagements WHERE name = 'Agent must not create this'")
        is None
    )


def test_rest_playbook_fails_if_the_definition_changes_after_policy(
    fresh_db,
    tmp_path,
    monkeypatch,
):
    from app import config
    from app.services import policy_context

    overlay = tmp_path / "playbooks"
    overlay.mkdir()
    definition = overlay / "moving.yaml"
    definition.write_text(
        """\
schema_version: 1
name: Moving definition
project_class: standard
milestones:
  - title: Before policy
"""
    )
    monkeypatch.setattr(config, "PLAYBOOKS_OVERLAY", overlay)
    original = policy_context.for_route

    def change_after_policy(resource_type, resource_id, payload):
        attributes = original(resource_type, resource_id, payload)
        if resource_type == "playbooks":
            definition.write_text(
                """\
schema_version: 1
name: Moving definition
project_class: regulated
milestones:
  - title: After policy
"""
            )
        return attributes

    monkeypatch.setattr(policy_context, "for_route", change_after_policy)
    with TestClient(create_app(), headers={"X-User": "mira"}) as client:
        response = client.post(
            "/api/playbooks/instantiate",
            json={"playbook": "moving", "engagement_name": "Race must not land"},
        )
    assert response.status_code == 409
    assert response.json()["code"] == "PLAYBOOK_CHANGED"
    assert (
        fresh_db.query_one("SELECT id FROM engagements WHERE name = 'Race must not land'") is None
    )


def test_stock_agent_playbook_review_rejects_definition_drift(
    fresh_db,
    tmp_path,
    monkeypatch,
):
    from app import config
    from app.agents.identity import reset_requester_identity, set_requester_identity
    from app.extensions.policy import reset_policy_subject, set_policy_subject
    from app.services import review, users
    from app.tools.platform import start_engagement_from_playbook

    overlay = tmp_path / "playbooks"
    overlay.mkdir()
    definition = overlay / "agent_review.yaml"
    definition.write_text(
        """\
schema_version: 1
name: Reviewed agent playbook
project_class: standard
milestones:
  - title: Reviewed milestone
"""
    )
    monkeypatch.setattr(config, "PLAYBOOKS_OVERLAY", overlay)

    def review_playbooks(request: PolicyInput):
        if request.action == "playbook.create":
            return PolicyDecision(
                PolicyEffect.REVIEW,
                approver_groups=("playbook-approvers",),
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
                policies=(PolicyContribution("acme.workplace.review-playbook", review_playbooks),),
            ),
        )
    )
    users.ensure_user("requester")
    users.ensure_user("manager")
    invoke = next(
        candidate
        for name in ("original_function", "_tool_func", "func", "__wrapped__")
        if callable(candidate := getattr(start_engagement_from_playbook, name, None))
    )
    policy_token = set_policy_engine(registry.policy_engine)
    subject_token = set_policy_subject(PolicySubject("requester"))
    requester_token = set_requester_identity("requester")
    try:
        queued = json.loads(
            invoke(
                playbook_slug="agent_review",
                engagement_name="Changed agent review",
            )
        )
    finally:
        reset_requester_identity(requester_token)
        reset_policy_subject(subject_token)
        reset_policy_engine(policy_token)
    definition.write_text(
        """\
schema_version: 1
name: Reviewed agent playbook
project_class: regulated
milestones:
  - title: Changed after review
"""
    )
    with pytest.raises(PermissionError, match="playbook changed"):
        review.approve_change(
            queued["id"],
            actor="manager",
            reviewer_groups=("playbook-approvers",),
            policy_registry=registry,
        )
    pending = fresh_db.query_one(
        "SELECT payload, policy_context FROM pending_changes WHERE id = ?",
        (queued["id"],),
    )
    payload = json.loads(pending["payload"])
    payload.pop("expected_definition_digest")
    policy_context = json.loads(pending["policy_context"])
    policy_context["contract"]["payload"].pop("expected_definition_digest")
    fresh_db.execute(
        "UPDATE pending_changes SET payload = ?, policy_context = ? WHERE id = ?",
        (json.dumps(payload), json.dumps(policy_context), queued["id"]),
    )
    with pytest.raises(PermissionError, match="no content digest"):
        review.approve_change(
            queued["id"],
            actor="manager",
            reviewer_groups=("playbook-approvers",),
            policy_registry=registry,
        )
    assert review.reject_change(
        queued["id"],
        actor="manager",
        reviewer_groups=("playbook-approvers",),
        policy_registry=registry,
    ) == {"id": queued["id"], "status": "rejected"}
    assert fresh_db.query_one(
        "SELECT status FROM pending_changes WHERE id = ?", (queued["id"],)
    ) == {"status": "rejected"}
    assert fresh_db.query_one(
        "SELECT read_at FROM notifications WHERE message LIKE ?",
        (f"Review needed: #{queued['id']}%",),
    )["read_at"]
    assert fresh_db.query_one(
        "SELECT action FROM activity WHERE action = 'reject_change'"
        " AND detail LIKE ? ORDER BY id DESC LIMIT 1",
        (f"#{queued['id']}%",),
    ) == {"action": "reject_change"}
    assert (
        fresh_db.query_one("SELECT id FROM engagements WHERE name = 'Changed agent review'") is None
    )


def test_rest_playbook_policy_review_resumes_before_any_work(fresh_db):
    from app.services import users

    def identity(name, _groups, _strong):
        return {
            "capabilities": ("acme.approve-playbook",) if name == "manager" else (),
        }

    def review_prototype_playbooks(request: PolicyInput):
        if request.action == "playbook.create" and request.resource.project_type == "prototype":
            return PolicyDecision(
                PolicyEffect.REVIEW,
                approver_capabilities=("acme.approve-playbook",),
            )
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        identities=(IdentityContribution("acme.workplace.identity", identity),),
        policies=(
            PolicyContribution("acme.workplace.playbook-review", review_prototype_playbooks),
        ),
    )
    users.ensure_user("requester")
    users.ensure_user("manager")
    with TestClient(create_app(modules=(module,))) as client:
        queued = client.post(
            "/api/playbooks/instantiate",
            headers={"X-User": "requester"},
            json={"playbook": "prototype", "engagement_name": "Reviewed prototype"},
        )
        assert queued.status_code == 200, queued.text
        workflow = queued.json()["workflow"]
        assert workflow["status"] == "review_required"
        assert workflow["checkpoint"] == "workplace-policy"
        assert workflow["review_id"] > 0
        assert (
            fresh_db.query_one("SELECT id FROM engagements WHERE name = 'Reviewed prototype'")
            is None
        )

        approved = client.post(
            f"/api/review/{workflow['review_id']}/approve",
            headers={"X-User": "manager"},
            json={"note": "Approved."},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["result"]["engagement"]["name"] == "Reviewed prototype"

    assert fresh_db.query_one("SELECT name FROM engagements WHERE name = 'Reviewed prototype'") == {
        "name": "Reviewed prototype"
    }


def test_playbook_approval_uses_one_current_policy_decision(fresh_db):
    from app.services import users

    calls = {"count": 0}

    def identity(name, _groups, _strong):
        return {"capabilities": ("old-approver",) if name == "manager" else ()}

    def changing_review(request: PolicyInput):
        if request.action != "playbook.create":
            return None
        calls["count"] += 1
        capability = "new-approver" if calls["count"] >= 4 else "old-approver"
        return PolicyDecision(
            PolicyEffect.REVIEW,
            approver_capabilities=(capability,),
        )

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        identities=(IdentityContribution("acme.workplace.identity", identity),),
        policies=(PolicyContribution("acme.workplace.changing-review", changing_review),),
    )
    users.ensure_user("requester")
    users.ensure_user("manager")
    with TestClient(create_app(modules=(module,))) as client:
        queued = client.post(
            "/api/playbooks/instantiate",
            headers={"X-User": "requester"},
            json={"playbook": "prototype", "engagement_name": "Single policy verdict"},
        ).json()["workflow"]
        approved = client.post(
            f"/api/review/{queued['review_id']}/approve",
            headers={"X-User": "manager"},
            json={"note": "Approved under the current verdict."},
        )
    assert approved.status_code == 200, approved.text
    assert calls["count"] == 2
    assert fresh_db.query_one(
        "SELECT name FROM engagements WHERE name = 'Single policy verdict'"
    ) == {"name": "Single policy verdict"}


def test_legacy_playbook_review_can_be_rejected_but_not_approved(fresh_db):
    from app.services import users

    def identity(name, _groups, _strong):
        return {"capabilities": ("playbook-approver",) if name == "manager" else ()}

    def review_playbook(request: PolicyInput):
        if request.action == "playbook.create":
            return PolicyDecision(
                PolicyEffect.REVIEW,
                approver_capabilities=("playbook-approver",),
            )
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        identities=(IdentityContribution("acme.workplace.identity", identity),),
        policies=(PolicyContribution("acme.workplace.playbook-review", review_playbook),),
    )
    users.ensure_user("requester")
    users.ensure_user("manager")
    with TestClient(create_app(modules=(module,))) as client:
        review_id = client.post(
            "/api/playbooks/instantiate",
            headers={"X-User": "requester"},
            json={"playbook": "prototype", "engagement_name": "Legacy pending review"},
        ).json()["workflow"]["review_id"]
        stored = fresh_db.query_one(
            "SELECT invocation FROM extension_review_invocations WHERE change_id = ?",
            (review_id,),
        )
        invocation = json.loads(stored["invocation"])
        invocation.pop("definition_digest")
        fresh_db.execute(
            "UPDATE extension_review_invocations SET invocation = ? WHERE change_id = ?",
            (json.dumps(invocation), review_id),
        )

        approval = client.post(
            f"/api/review/{review_id}/approve",
            headers={"X-User": "manager"},
            json={"note": "Old release proposal."},
        )
        rejection = client.post(
            f"/api/review/{review_id}/reject",
            headers={"X-User": "manager"},
            json={"note": "Replace this legacy proposal."},
        )
    assert approval.status_code == 403
    assert "no content digest" in approval.json()["detail"].lower()
    assert rejection.status_code == 200, rejection.text
    assert fresh_db.query_one("SELECT status FROM pending_changes WHERE id = ?", (review_id,)) == {
        "status": "rejected"
    }
    assert fresh_db.query_one(
        "SELECT status FROM extension_review_invocations WHERE change_id = ?", (review_id,)
    ) == {"status": "rejected"}
    assert fresh_db.query_one(
        "SELECT read_at FROM notifications WHERE message LIKE ?",
        (f"Review needed: #{review_id}%",),
    )["read_at"]


def test_removed_playbook_review_can_be_rejected(fresh_db, tmp_path, monkeypatch):
    from app import config
    from app.services import users

    overlay = tmp_path / "playbooks"
    overlay.mkdir()
    definition = overlay / "removed.yaml"
    definition.write_text(
        """\
schema_version: 1
name: Removed delivery
project_class: standard
milestones:
  - title: Prepare
"""
    )
    monkeypatch.setattr(config, "PLAYBOOKS_OVERLAY", overlay)

    def identity(name, _groups, _strong):
        return {"capabilities": ("playbook-approver",) if name == "manager" else ()}

    def review_playbook(request: PolicyInput):
        if request.action == "playbook.create":
            return PolicyDecision(
                PolicyEffect.REVIEW,
                approver_capabilities=("playbook-approver",),
            )
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        identities=(IdentityContribution("acme.workplace.identity", identity),),
        policies=(PolicyContribution("acme.workplace.playbook-review", review_playbook),),
    )
    for name in ("requester", "manager"):
        users.ensure_user(name)
    with TestClient(create_app(modules=(module,))) as client:
        review_id = client.post(
            "/api/playbooks/instantiate",
            headers={"X-User": "requester"},
            json={"playbook": "removed", "engagement_name": "Removed review"},
        ).json()["workflow"]["review_id"]
        definition.unlink()
        rejected = client.post(
            f"/api/review/{review_id}/reject",
            headers={"X-User": "manager"},
            json={"note": "The playbook is no longer available."},
        )

    assert rejected.status_code == 200, rejected.text
    assert fresh_db.query_one("SELECT status FROM pending_changes WHERE id = ?", (review_id,)) == {
        "status": "rejected"
    }
    assert fresh_db.query_one(
        "SELECT status FROM extension_review_invocations WHERE change_id = ?",
        (review_id,),
    ) == {"status": "rejected"}


def test_playbook_policy_review_rejects_definition_drift(
    fresh_db,
    tmp_path,
    monkeypatch,
):
    from app import config
    from app.services import users

    overlay = tmp_path / "playbooks"
    overlay.mkdir()
    definition = overlay / "governed.yaml"
    definition.write_text(
        """\
schema_version: 1
name: Governed delivery
project_class: standard
milestones:
  - title: Prepare
    tasks:
      - Reviewed task
"""
    )
    monkeypatch.setattr(config, "PLAYBOOKS_OVERLAY", overlay)

    def identity(name, _groups, _strong):
        return {
            "capabilities": (
                ("standard-approvers",)
                if name == "standard-manager"
                else (("regulated-approvers",) if name == "regulated-manager" else ())
            )
        }

    def review_playbooks(request: PolicyInput):
        if request.action == "playbook.create":
            return PolicyDecision(
                PolicyEffect.REVIEW,
                approver_capabilities=(f"{request.resource.project_type}-approvers",),
            )
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        identities=(IdentityContribution("acme.workplace.identity", identity),),
        policies=(PolicyContribution("acme.workplace.playbook-review", review_playbooks),),
    )
    for name in ("requester", "standard-manager", "regulated-manager"):
        users.ensure_user(name)
    with TestClient(create_app(modules=(module,))) as client:
        queued = client.post(
            "/api/playbooks/instantiate",
            headers={"X-User": "requester"},
            json={"playbook": "governed", "engagement_name": "Changed delivery"},
        ).json()["workflow"]
        definition.write_text(
            """\
schema_version: 1
name: Governed delivery
project_class: regulated
milestones:
  - title: Prepare
    tasks:
      - Reviewed task
      - Added after review
"""
        )
        stale = client.post(
            f"/api/review/{queued['review_id']}/approve",
            headers={"X-User": "standard-manager"},
            json={"note": "Approve the old definition."},
        )
        assert stale.status_code == 403
        assert "playbook changed" in stale.json()["detail"].lower()
        assert (
            fresh_db.query_one("SELECT id FROM engagements WHERE name = 'Changed delivery'") is None
        )

        current = client.post(
            "/api/playbooks/instantiate",
            headers={"X-User": "requester"},
            json={"playbook": "governed", "engagement_name": "Changed delivery"},
        ).json()["workflow"]
        approved = client.post(
            f"/api/review/{current['review_id']}/approve",
            headers={"X-User": "regulated-manager"},
            json={"note": "Approve the current definition."},
        )
    assert approved.status_code == 200, approved.text
    assert fresh_db.query_one("SELECT project_class FROM engagements") == {
        "project_class": "regulated"
    }


def test_extension_rejection_uses_current_approver_group(fresh_db):
    from app.services import review, users

    required = {"group": "old-approvers"}

    def current_approval_policy(request: PolicyInput):
        if request.action == "acme.sync":
            return PolicyDecision(
                PolicyEffect.REVIEW,
                approver_groups=(required["group"],),
            )
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("acme.workplace.current-approval", current_approval_policy),),
        tools=(
            ToolContribution(
                name="acme.workplace.sync",
                version="1.0.0",
                model_name="acme_sync",
                description="Test current approval authority.",
                handler=lambda _context, request: {"updated": request.external_id},
                input_schema=SyncIn,
                output_schema=SyncOut,
                effect="write",
                risk="high",
                policy_action="acme.sync",
            ),
        ),
    )
    registry = ExtensionRegistry.build((module,))
    for name in ("requester", "old-manager", "new-manager"):
        users.ensure_user(name)
    queued = asyncio.run(
        execute_tool(
            registry.tools[0],
            {"external_id": "ATLAS-9"},
            ToolCallContext(PolicySubject("requester"), "acme-agent"),
            registry.policy_engine,
        )
    )
    assert queued.status == "review_required"
    required["group"] = "new-approvers"

    with pytest.raises(PermissionError, match="configured workplace approver"):
        review.reject_change(
            queued.review_id,
            actor="old-manager",
            reviewer_groups=("old-approvers",),
            policy_registry=registry,
        )
    rejected = review.reject_change(
        queued.review_id,
        actor="new-manager",
        reviewer_groups=("new-approvers",),
        policy_registry=registry,
    )
    assert rejected["status"] == "rejected"


def test_removed_tool_review_can_be_settled_without_retired_capability(fresh_db):
    from app.services import review, users

    def old_policy(request: PolicyInput):
        if request.action == "acme.sync":
            return PolicyDecision(
                PolicyEffect.REVIEW,
                approver_capabilities=("acme.old-approver",),
            )
        return None

    tool = ToolContribution(
        name="acme.workplace.sync",
        version="1.0.0",
        model_name="acme_sync",
        description="A retired synchronization action.",
        handler=lambda _context, request: {"updated": request.external_id},
        input_schema=SyncIn,
        output_schema=SyncOut,
        effect="write",
        risk="high",
        policy_action="acme.sync",
    )
    old_module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("acme.workplace.old-policy", old_policy),),
        tools=(tool,),
    )
    old_registry = ExtensionRegistry.build((old_module,))
    users.ensure_user("requester")
    users.ensure_user("current-manager")
    queued = asyncio.run(
        execute_tool(
            tool,
            {"external_id": "ATLAS-REMOVED"},
            ToolCallContext(PolicySubject("requester"), "acme-agent"),
            old_registry.policy_engine,
        )
    )
    current_module = replace(old_module, policies=(), tools=())
    current_registry = ExtensionRegistry.build((current_module,))

    rejected = review.reject_change(
        queued.review_id,
        actor="current-manager",
        policy_registry=current_registry,
    )

    assert rejected["status"] == "rejected"
    row = fresh_db.query_one(
        "SELECT status, reviewer_qualifications FROM pending_changes WHERE id = ?",
        (queued.review_id,),
    )
    assert row == {
        "status": "rejected",
        "reviewer_qualifications": '{"matched_groups": [], "matched_capabilities": [], "stale_contract": true}',
    }
    assert fresh_db.query_one(
        "SELECT status FROM extension_review_invocations WHERE change_id = ?",
        (queued.review_id,),
    ) == {"status": "rejected"}


def test_removed_identity_owning_module_does_not_strand_its_oidc_review(fresh_db):
    from app.services import review, users

    def old_policy(request: PolicyInput):
        if request.action == "acme.sync":
            return PolicyDecision(
                PolicyEffect.REVIEW,
                approver_groups=("acme-managers",),
            )
        return None

    tool = ToolContribution(
        name="acme.workplace.sync",
        version="1.0.0",
        model_name="acme_sync",
        description="A synchronization action owned by a removable module.",
        handler=lambda _context, request: {"updated": request.external_id},
        input_schema=SyncIn,
        output_schema=SyncOut,
        effect="write",
        risk="high",
        policy_action="acme.sync",
    )
    old_module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        identities=(
            IdentityContribution(
                "acme.workplace.directory",
                lambda _name, groups, _strong: {"groups": groups},
                resolver=lambda _name: {"groups": ("requesters",)},
                resolves_groups=True,
            ),
        ),
        policies=(PolicyContribution("acme.workplace.policy", old_policy),),
        tools=(tool,),
    )
    old_registry = ExtensionRegistry.build((old_module,))
    users.ensure_user("requester")
    users.ensure_user("current-manager")
    requester = PolicySubject(
        "requester",
        groups=("requesters",),
        source="oidc",
        refresh_required=True,
        strong=True,
    )
    queued = asyncio.run(
        execute_tool(
            tool,
            {"external_id": "ACME-REMOVED"},
            ToolCallContext(requester, "acme-agent"),
            old_registry.policy_engine,
        )
    )

    rejected = review.reject_change(
        queued.review_id,
        actor="current-manager",
        policy_registry=ExtensionRegistry.build(()),
    )

    assert rejected["status"] == "rejected"
    assert fresh_db.query_one(
        "SELECT status FROM pending_changes WHERE id = ?", (queued.review_id,)
    ) == {"status": "rejected"}


def test_extension_rejection_recomputes_the_tool_resource(fresh_db):
    from app.services import review, users

    target = {"project_type": "standard"}
    transaction_state: list[bool] = []

    def current_target_policy(request: PolicyInput):
        if request.action == "acme.sync":
            transaction_state.append(fresh_db._ambient.get() is not None)
            return PolicyDecision(
                PolicyEffect.REVIEW,
                approver_groups=(f"{request.resource.project_type}-approvers",),
            )
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("acme.workplace.current-target", current_target_policy),),
        tools=(
            ToolContribution(
                name="acme.workplace.sync",
                version="1.0.0",
                model_name="acme_sync",
                description="Test current target authority.",
                handler=lambda _context, request: {"updated": request.external_id},
                input_schema=SyncIn,
                output_schema=SyncOut,
                effect="write",
                risk="high",
                policy_action="acme.sync",
                resource=lambda request: PolicyResource(
                    "work-item",
                    request.external_id,
                    project_type=target["project_type"],
                ),
            ),
        ),
    )
    registry = ExtensionRegistry.build((module,))
    for name in ("requester", "standard-manager", "regulated-manager"):
        users.ensure_user(name)
    queued = asyncio.run(
        execute_tool(
            registry.tools[0],
            {"external_id": "ATLAS-10"},
            ToolCallContext(PolicySubject("requester"), "acme-agent"),
            registry.policy_engine,
        )
    )
    target["project_type"] = "regulated"
    transaction_state.clear()

    with pytest.raises(PermissionError, match="configured workplace approver"):
        review.reject_change(
            queued.review_id,
            actor="standard-manager",
            reviewer_groups=("standard-approvers",),
            policy_registry=registry,
        )
    rejected = review.reject_change(
        queued.review_id,
        actor="regulated-manager",
        reviewer_groups=("regulated-approvers",),
        policy_registry=registry,
    )
    assert rejected["status"] == "rejected"
    assert transaction_state and all(transaction_state)


def test_rejection_serializes_current_policy_with_the_verdict(fresh_db):
    from threading import Event, Thread
    from time import sleep

    from app.services import engagements, policy_context, review, users, work

    standard = engagements.create_engagement("standard", project_class="standard")["id"]
    regulated = engagements.create_engagement("regulated", project_class="regulated")["id"]
    task = work.create_task("review target", engagement_id=standard)["id"]
    policy_entered = Event()
    writer_attempted = Event()
    writer_done = Event()
    armed = {"value": False, "paused": False}

    def current_resource(_request):
        domain = policy_context.existing("task", task)
        return PolicyResource(
            "task",
            str(task),
            project_type=str(domain.get("project_type") or ""),
            classification=str(domain.get("classification") or ""),
        )

    def rule(request: PolicyInput):
        if request.action != "acme.sync":
            return None
        if armed["value"] and not armed["paused"]:
            armed["paused"] = True
            policy_entered.set()
            assert writer_attempted.wait(5)
            sleep(0.05)
            assert not writer_done.is_set()
        return PolicyDecision(
            PolicyEffect.REVIEW,
            approver_groups=(f"{request.resource.project_type}-approvers",),
        )

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("acme.workplace.current-target", rule),),
        tools=(
            ToolContribution(
                name="acme.workplace.sync",
                version="1.0.0",
                model_name="acme_sync",
                description="Test serialized rejection authority.",
                handler=lambda _context, request: {"updated": request.external_id},
                input_schema=SyncIn,
                output_schema=SyncOut,
                effect="write",
                risk="high",
                policy_action="acme.sync",
                resource=current_resource,
            ),
        ),
    )
    registry = ExtensionRegistry.build((module,))
    for name in ("requester", "standard-manager"):
        users.ensure_user(name)
    queued = asyncio.run(
        execute_tool(
            registry.tools[0],
            {"external_id": "ATLAS-SERIAL"},
            ToolCallContext(PolicySubject("requester"), "acme-agent"),
            registry.policy_engine,
        )
    )
    armed["value"] = True

    def relink() -> None:
        assert policy_entered.wait(5)
        writer_attempted.set()
        work.update_task(task, engagement_id=regulated)
        writer_done.set()

    writer = Thread(target=relink)
    writer.start()
    rejected = review.reject_change(
        queued.review_id,
        actor="standard-manager",
        reviewer_groups=("standard-approvers",),
        policy_registry=registry,
    )
    writer.join(5)

    assert rejected["status"] == "rejected"
    assert writer_done.is_set()
    assert fresh_db.query_one("SELECT engagement_id FROM tasks WHERE id = ?", (task,)) == {
        "engagement_id": regulated
    }


def test_subject_refresh_never_increases_authentication_strength(fresh_db):
    from app.services import users

    users.ensure_user("mira")
    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        identities=(
            IdentityContribution(
                "acme.workplace.directory",
                lambda _name, _groups, strong: {
                    "capabilities": ("acme.strong-only",) if strong else (),
                },
            ),
        ),
    )
    refreshed = ExtensionRegistry.build((module,)).refresh_subject(
        PolicySubject("mira", strong=False, source="trusted-header")
    )
    assert refreshed.strong is False
    assert "acme.strong-only" not in refreshed.capabilities


def test_review_resume_cannot_upgrade_a_weak_requester(fresh_db):
    from app.agents.identity import reset_requester_identity, set_requester_identity
    from app.extensions.policy import reset_policy_subject, set_policy_subject
    from app.services import review, users

    state = {"require_strong": False}

    def identity(_name, _groups, strong):
        return {"capabilities": ("acme.strong",) if strong else ()}

    def rule(request: PolicyInput):
        if request.action != "task.create":
            return None
        if not state["require_strong"]:
            return PolicyDecision(PolicyEffect.REVIEW)
        if "acme.strong" not in request.subject.capabilities:
            return PolicyDecision(PolicyEffect.DENY, ("strong identity is now required",))
        return PolicyDecision(PolicyEffect.PERMIT)

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        identities=(IdentityContribution("acme.workplace.identity", identity),),
        policies=(PolicyContribution("acme.workplace.strong-policy", rule),),
    )
    registry = ExtensionRegistry.build((module,))
    users.ensure_user("requester")
    policy_token = set_policy_engine(registry.policy_engine)
    subject_token = set_policy_subject(
        PolicySubject("requester", strong=False, source="trusted-header")
    )
    requester_token = set_requester_identity("requester")
    try:
        proposal = json.loads(
            gated_write(
                "task",
                "create",
                {"title": "weak request"},
                lambda: pytest.fail("reviewed write ran early"),
            )
        )
    finally:
        reset_requester_identity(requester_token)
        reset_policy_subject(subject_token)
        reset_policy_engine(policy_token)
    state["require_strong"] = True
    users.ensure_user("manager")
    with pytest.raises(PermissionError, match="current workplace policy denies"):
        review.approve_change(
            proposal["id"],
            actor="manager",
            policy_registry=registry,
        )
    assert fresh_db.query_one("SELECT id FROM tasks WHERE title = 'weak request'") is None


def test_service_subject_refresh_does_not_use_human_identity_mapping(fresh_db):
    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        identities=(
            IdentityContribution(
                "acme.workplace.humans",
                lambda *_args: {"roles": ("human-only",)},
            ),
        ),
        service_identities=(
            ServiceIdentityContribution(
                "acme.workplace.sync-identity",
                "acme-sync",
                roles=("integration",),
            ),
        ),
    )
    refreshed = ExtensionRegistry.build((module,)).refresh_subject(
        PolicySubject("acme-sync", kind="service")
    )
    assert refreshed.kind == "service"
    assert refreshed.roles == ("integration",)
    assert refreshed.source == "service"


def test_target_project_context_governs_agent_relationship_changes(fresh_db):
    from app.agents.identity import reset_requester_identity, set_requester_identity
    from app.extensions.policy import reset_policy_subject, set_policy_subject
    from app.services import engagements, work

    standard = engagements.create_engagement("standard", project_class="standard")["id"]
    regulated = engagements.create_engagement("regulated", project_class="regulated")["id"]
    task = work.create_task("move me", engagement_id=standard)["id"]

    def deny_regulated(request: PolicyInput):
        if request.action == "task.update" and request.resource.project_type == "regulated":
            return PolicyDecision(PolicyEffect.DENY, ("regulated target",))
        return None

    engine = PolicyEngine((deny_regulated,))
    policy_token = set_policy_engine(engine)
    subject_token = set_policy_subject(PolicySubject("mira"))
    requester_token = set_requester_identity("mira")
    try:
        result = json.loads(
            gated_write(
                "task",
                "update",
                {"engagement_id": regulated},
                lambda: work.update_task(task, engagement_id=regulated),
                entity_id=task,
            )
        )
    finally:
        reset_requester_identity(requester_token)
        reset_policy_subject(subject_token)
        reset_policy_engine(policy_token)
    assert "error" in result
    assert fresh_db.query_one("SELECT engagement_id FROM tasks WHERE id = ?", (task,)) == {
        "engagement_id": standard
    }


def test_review_revalidation_uses_the_proposed_relationship_target(fresh_db):
    from app.agents.identity import reset_requester_identity, set_requester_identity
    from app.extensions.policy import reset_policy_subject, set_policy_subject
    from app.services import engagements, review, users, work

    standard = engagements.create_engagement("standard", project_class="standard")["id"]
    regulated = engagements.create_engagement("regulated", project_class="regulated")["id"]
    task = work.create_task("review move", engagement_id=standard)["id"]
    state = {"deny": False}

    def target_rule(request: PolicyInput):
        if request.action != "task.update":
            return None
        if state["deny"] and request.resource.project_type == "regulated":
            return PolicyDecision(PolicyEffect.DENY, ("regulated moves are closed",))
        return PolicyDecision(PolicyEffect.REVIEW)

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("acme.workplace.target-policy", target_rule),),
    )
    registry = ExtensionRegistry.build((module,))
    users.ensure_user("requester")
    users.ensure_user("manager")
    policy_token = set_policy_engine(registry.policy_engine)
    subject_token = set_policy_subject(PolicySubject("requester"))
    requester_token = set_requester_identity("requester")
    try:
        proposal = json.loads(
            gated_write(
                "task",
                "update",
                {"engagement_id": regulated},
                lambda: pytest.fail("reviewed write ran early"),
                entity_id=task,
            )
        )
    finally:
        reset_requester_identity(requester_token)
        reset_policy_subject(subject_token)
        reset_policy_engine(policy_token)
    state["deny"] = True
    with pytest.raises(PermissionError, match="current workplace policy denies"):
        review.approve_change(
            proposal["id"],
            actor="manager",
            policy_registry=registry,
        )
    assert fresh_db.query_one("SELECT engagement_id FROM tasks WHERE id = ?", (task,)) == {
        "engagement_id": standard
    }


def test_milestone_link_supplies_task_project_context(fresh_db):
    from app.services import engagements, policy_context, work

    engagements.create_engagement("regulated", project_class="regulated")
    milestone = work.create_milestone("gate", project="regulated")["id"]
    task = work.create_task("linked only through milestone", milestone_id=milestone)["id"]
    assert policy_context.existing("task", task)["project_type"] == "regulated"
    assert (
        policy_context.for_change("task", 0, {"milestone_id": milestone})["project_type"]
        == "regulated"
    )
    assert policy_context.existing("task", task)["project_type"] == "regulated"


def test_rest_policy_ignores_unpersisted_context_fields(fresh_db):
    from app.services import collab, users
    from app.services.api_keys import create_key

    users.ensure_user("mira")
    note = collab.save_note(
        "private",
        "original",
        author="mira",
        actor="mira",
        visibility="private",
    )

    def protect_private(request: PolicyInput):
        if (
            request.action == "skein.rest.patch.notes"
            and request.resource.classification == "private"
        ):
            return PolicyDecision(PolicyEffect.DENY, ("private note is protected",))
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("acme.workplace.private-notes", protect_private),),
    )
    key = create_key("mira", "test")["key"]
    with TestClient(create_app(modules=(module,))) as client:
        response = client.patch(
            f"/api/notes/{note['id']}",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "content": "spoofed",
                "visibility": "workspace",
                "project_class": "standard",
            },
        )
    assert response.status_code == 403
    assert fresh_db.query_one("SELECT content FROM notes WHERE id = ?", (note["id"],)) == {
        "content": "original"
    }


def test_core_agent_approval_observes_directory_group_removal(fresh_db):
    from app.agents.identity import reset_requester_identity, set_requester_identity
    from app.extensions.policy import reset_policy_subject, set_policy_subject
    from app.services import users

    directory = {"groups": ("operators",)}

    def identity(_name, groups, _strong):
        return {
            "roles": (),
            "capabilities": ("acme.request-task",) if "operators" in groups else (),
        }

    def policy(request: PolicyInput):
        if request.action != "task.create":
            return None
        if "acme.request-task" not in request.subject.capabilities:
            return PolicyDecision(PolicyEffect.DENY, ("operator access was removed",))
        return PolicyDecision(PolicyEffect.REVIEW)

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        identities=(
            IdentityContribution(
                "acme.workplace.directory",
                identity,
                resolver=lambda _name: {"active": True, "groups": directory["groups"]},
            ),
        ),
        policies=(PolicyContribution("acme.workplace.operator-policy", policy),),
    )
    registry = ExtensionRegistry.build((module,))
    users.ensure_user("requester")
    policy_token = set_policy_engine(registry.policy_engine)
    subject_token = set_policy_subject(
        PolicySubject(
            "requester",
            groups=("operators",),
            capabilities=("acme.request-task",),
        )
    )
    requester_token = set_requester_identity("requester")
    try:
        proposal = json.loads(
            gated_write(
                "task",
                "create",
                {"title": "group-revoked request"},
                lambda: pytest.fail("a reviewed write executed before approval"),
            )
        )
    finally:
        reset_requester_identity(requester_token)
        reset_policy_subject(subject_token)
        reset_policy_engine(policy_token)
    directory["groups"] = ()

    with TestClient(create_app(modules=(module,)), headers={"X-User": "manager"}) as client:
        response = client.post(f"/api/review/{proposal['id']}/approve", json={"note": ""})
    assert response.status_code == 403
    assert fresh_db.query_one("SELECT id FROM tasks WHERE title = 'group-revoked request'") is None


def test_identity_mapper_rejects_string_role_and_capability_containers():
    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        identities=(
            IdentityContribution(
                "acme.workplace.invalid-identity",
                lambda _name, _groups, _strong: {
                    "roles": "manager",
                    "capabilities": "acme.approve",
                },
            ),
        ),
    )
    with pytest.raises(ValueError, match="roles must be a list or tuple"):
        ExtensionRegistry.build((module,)).identity_attributes("mira", (), True)


def test_keyless_capture_obeys_the_domain_policy(fresh_db):
    def deny_agent_task(request: PolicyInput):
        if request.action == "task.create" and request.origin == "agent":
            return PolicyDecision(PolicyEffect.DENY, ("agent task creation is disabled",))
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("acme.workplace.keyless-policy", deny_agent_task),),
    )
    with TestClient(create_app(modules=(module,)), headers={"X-User": "mira"}) as client:
        response = client.post(
            "/api/chat",
            json={"thread_id": "keyless-policy", "message": "task: forbidden capture"},
        )
    assert response.status_code == 200
    assert "forbidden" in response.text
    assert fresh_db.query_one("SELECT id FROM tasks WHERE title = 'forbidden capture'") is None


def test_reviewed_mcp_call_is_bound_to_one_server(fresh_db, monkeypatch):
    from app.agents import mcp_tools as mcp_module
    from app.agents.mcp_tools import (
        GovernedMCPTool,
        MCPToolMetadata,
        execute_reviewed_mcp,
    )

    metadata = MCPToolMetadata(
        version="1.0.0",
        effect="write",
        risk="low",
        policy_action="atlas.remote.write",
        allowed_agents=("agent",),
        required_capabilities=(),
        output_schema={"type": "object"},
        timeout_seconds=1,
        error_codes=(),
        receipt="required",
        provenance="service",
    )
    first = _RemoteTool()
    second = _RemoteTool()
    server_a = GovernedMCPTool(first, metadata, "server-a")
    server_b = GovernedMCPTool(second, metadata, "server-b")
    monkeypatch.setattr(mcp_module, "mcp_tools", lambda: [server_a, server_b])
    invocation = {
        "tool": "atlas_remote",
        "server": "server-b",
        "version": "1.0.0",
        "tool_use": {"toolUseId": "bound-call", "input": {}},
        "invocation_state": {},
        "subject": {
            "name": "mira",
            "kind": "human",
            "roles": [],
            "groups": [],
            "capabilities": [],
            "attributes": {},
        },
        "agent": "agent",
        "approval_fingerprint": "",
    }
    result = asyncio.run(execute_reviewed_mcp(invocation, ExtensionRegistry.build(())))
    assert result["status"] == "completed"
    assert first.called is False
    assert second.called is True


def test_mcp_name_collisions_are_omitted_from_agent_composition(monkeypatch):
    from app.agents import mcp_tools as mcp_module

    class FakeClient:
        def __init__(self, _factory):
            self.remote = _RemoteTool()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def list_tools_sync(self):
            return [self.remote]

    metadata = {
        "version": "1.0.0",
        "effect": "write",
        "risk": "high",
        "policy_action": "atlas.remote.write",
        "allowed_agents": ["agent"],
        "required_capabilities": [],
        "output_schema": {"type": "object"},
        "timeout_seconds": 1,
        "error_codes": [],
        "receipt": "required",
        "provenance": "service",
    }
    monkeypatch.setattr(
        mcp_module.config,
        "MCP_SERVERS",
        json.dumps(
            [
                {
                    "name": "server-a",
                    "url": "https://a.invalid/mcp",
                    "tools": {"atlas_remote": metadata},
                },
                {
                    "name": "server-b",
                    "url": "https://b.invalid/mcp",
                    "tools": {"atlas_remote": metadata},
                },
            ]
        ),
    )
    monkeypatch.setattr("strands.tools.mcp.MCPClient", FakeClient)
    tools, clients = mcp_module._connect_servers()
    assert tools == []
    assert len(clients) == 2
