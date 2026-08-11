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
    PolicyInput,
    PolicyResource,
    PolicySubject,
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
        )
    approved = review.approve_change(
        result.review_id,
        actor="manager",
        reviewer_groups=("delivery-managers",),
        reviewer_capabilities=("acme.approve-atlas",),
        extension_executor=resume,
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
    assert review.status_code == 409
    assert read_denied.status_code == 403
    assert read_denied.json()["code"] == "POLICY_DENIED"
    assert review.json() == {
        "detail": "This action needs review before it can run.",
        "code": "POLICY_REVIEW_REQUIRED",
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


def test_contributed_tools_cannot_shadow_a_core_model_tool():
    module = _module()
    collision = replace(module.tools[0], model_name="create_task")
    specialist = replace(module.specialists[0], tools=(collision.name,))
    with pytest.raises(ValueError, match="collides with core"):
        create_app(modules=(replace(module, tools=(collision,), specialists=(specialist,)),))


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
    )
    assert approved["result"]["status"] == "completed"
    assert remote.called is True


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
