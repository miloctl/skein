"""Policy, identity, and governed-tool contracts for workplace modules."""

import asyncio
import json
import time
from dataclasses import replace
from typing import ClassVar

import pytest
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
    ServiceIdentityContribution,
    SkeinModule,
    SpecialistContribution,
    ToolContribution,
)
from app.extensions.agents import missing_specialist_capabilities
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

    def rule(request: PolicyInput):
        if request.action == "atlas.update":
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


def test_all_contributed_routes_receive_the_composed_policy(fresh_db):
    from fastapi import APIRouter

    from app.extensions import RouteContribution

    router = APIRouter(prefix="/api/extensions/acme.workplace")

    @router.post("/unguarded")
    def unguarded():
        return {"unsafe": True}

    @router.get("/unguarded-read")
    def unguarded_read():
        return {"unsafe": True}

    def deny_route(request: PolicyInput):
        if request.action in (
            "skein.rest.post.extensions.acme.workplace.unguarded",
            "skein.rest.get.extensions.acme.workplace.unguarded-read",
        ):
            return PolicyDecision(PolicyEffect.DENY, ("This route is disabled.",))
        return None

    module = replace(
        _module(),
        routes=(RouteContribution("acme.workplace.routes", router),),
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
    assert work.task_policy_context(task)["project_type"] == "regulated"


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
