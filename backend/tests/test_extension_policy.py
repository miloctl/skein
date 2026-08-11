"""Policy, identity, and governed-tool contracts for workplace modules."""

import asyncio
import json
from dataclasses import replace
from typing import ClassVar

from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.extensions import (
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
    return SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
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
                handler=handler,
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


def test_mcp_tools_need_complete_metadata_and_pass_through_policy(fresh_db):
    from app.agents.identity import reset_agent_identity, set_agent_identity
    from app.agents.mcp_tools import GovernedMCPTool, MCPToolMetadata, _metadata

    assert _metadata({"tools": {}}, "atlas_remote") is None
    remote = _RemoteTool()
    governed = GovernedMCPTool(
        remote,
        MCPToolMetadata(
            effect="write",
            risk="high",
            policy_action="atlas.update",
            allowed_agents=("acme.workplace.delivery",),
            timeout_seconds=1,
            error_codes=("remote_error",),
            receipt="required",
            provenance="service",
        ),
    )
    registry = ExtensionRegistry.build((_module(),))
    policy_token = set_policy_engine(registry.policy_engine)
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
        reset_policy_engine(policy_token)
    assert events[-1]["status"] == "error"
    assert remote.called is False
