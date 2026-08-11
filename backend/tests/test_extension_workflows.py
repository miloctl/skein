"""Typed workflow steps and composed playbook execution."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from app.extensions import (
    AppSettings,
    ExtensionRegistry,
    PolicyContribution,
    PolicyDecision,
    PolicyEffect,
    SkeinModule,
    WorkflowActionContribution,
)
from app.extensions.policy import PolicyInput, PolicySubject
from app.main import create_app
from app.public import PublicError, WorkflowContext, WorkflowEngine


class SendIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str


class SendOut(BaseModel):
    sent: bool


def _action(calls: list[str]) -> WorkflowActionContribution:
    def send(target: str):
        calls.append(target)
        return {"sent": True}

    return WorkflowActionContribution(
        name="atlas.workplace.notify-manager",
        version="1.0.0",
        handler=send,
        input_schema=SendIn,
        output_schema=SendOut,
        effect="write",
        risk="medium",
        policy_action="atlas.notification.send",
        error_codes=("REMOTE_UNAVAILABLE",),
    )


def _review_regulated(request: PolicyInput):
    if request.action == "atlas.release.approve" and request.resource.project_type == "regulated":
        return PolicyDecision(
            PolicyEffect.REVIEW,
            ("A delivery manager must approve a regulated release.",),
            approver_groups=("delivery-managers",),
        )
    return None


def _engine(calls: list[str], *, policy=True) -> WorkflowEngine:
    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        workflow_actions=(_action(calls),),
        policies=(
            (PolicyContribution("atlas.workplace.regulated-release", _review_regulated),)
            if policy
            else ()
        ),
    )
    registry = ExtensionRegistry.build((module,))
    return WorkflowEngine(registry.workflow_actions, registry.policy_engine)


def _steps():
    return [
        {"type": "checkpoint", "name": "work-created"},
        {
            "type": "condition",
            "field": "regulated",
            "equals": True,
            "then": [
                {
                    "type": "approval",
                    "name": "manager-approval",
                    "action": "atlas.release.approve",
                    "resource_type": "release",
                    "risk": "high",
                }
            ],
        },
        {
            "type": "action",
            "name": "atlas.workplace.notify-manager",
            "input": {"target": "delivery"},
        },
        {"type": "checkpoint", "name": "manager-notified"},
    ]


def test_a_condition_and_policy_review_stop_external_actions(fresh_db):
    calls: list[str] = []
    engine = _engine(calls)
    result = engine.run(
        engine.prepare(_steps()),
        WorkflowContext(
            PolicySubject("atlas-sync", kind="service"),
            "workflow",
            project_type="regulated",
            values={"regulated": True},
        ),
    )
    assert result.status == "review_required"
    assert result.checkpoint == "manager-approval"
    assert result.completed == ("work-created",)
    assert result.obligations == ("approver-group:delivery-managers",)
    assert calls == []
    assert fresh_db.query_one("SELECT 1 AS present FROM activity") is None


def test_the_nonregulated_branch_runs_the_action_and_checkpoints(fresh_db):
    calls: list[str] = []
    engine = _engine(calls)
    result = engine.run(
        engine.prepare(_steps()),
        WorkflowContext(
            PolicySubject("atlas-sync", kind="service"),
            "workflow",
            project_type="standard",
            values={"regulated": False},
        ),
    )
    assert result.status == "completed"
    assert result.completed == (
        "work-created",
        "atlas.workplace.notify-manager",
        "manager-notified",
    )
    assert result.outputs == {"atlas.workplace.notify-manager": {"sent": True}}
    assert calls == ["delivery"]
    assert fresh_db.query_one("SELECT action FROM activity")["action"] == "workflow_action"


def test_unknown_actions_and_invalid_shapes_fail_before_execution(fresh_db):
    engine = _engine([])
    with pytest.raises(PublicError) as unknown:
        engine.prepare([{"type": "action", "name": "atlas.workplace.missing"}])
    assert unknown.value.code == "UNKNOWN_WORKFLOW_ACTION"
    with pytest.raises(PublicError) as invalid:
        engine.prepare([{"type": "sleep", "seconds": 10}])
    assert invalid.value.code == "INVALID_WORKFLOW"


def test_direct_instantiation_cannot_silently_skip_a_workflow(fresh_db, tmp_path, monkeypatch):
    from app import config
    from app.services import playbooks

    overlay = tmp_path / "playbooks"
    overlay.mkdir()
    (overlay / "regulated.yaml").write_text(
        """\
schema_version: 1
name: Regulated delivery
description: A workflow-backed plan
milestones:
  - title: Prepare
workflow:
  - type: checkpoint
    name: prepared
"""
    )
    monkeypatch.setattr(config, "PLAYBOOKS_OVERLAY", overlay)
    with pytest.raises(ValueError, match="composed application"):
        playbooks.instantiate("regulated", "Direct bypass", actor="tester")
    assert fresh_db.query_one("SELECT 1 AS present FROM engagements") is None


def test_the_real_app_factory_executes_a_workplace_playbook(fresh_db, tmp_path, monkeypatch):
    from app import config

    calls: list[str] = []
    overlay = tmp_path / "playbooks"
    overlay.mkdir()
    (overlay / "standard.yaml").write_text(
        """\
schema_version: 1
name: Standard delivery
description: A composed workplace process
milestones:
  - title: Prepare delivery
workflow:
  - type: action
    name: atlas.workplace.notify-manager
    input:
      target: delivery
  - type: checkpoint
    name: notification-sent
"""
    )
    monkeypatch.setattr(config, "PLAYBOOKS_OVERLAY", overlay)
    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        workflow_actions=(_action(calls),),
    )
    settings = replace(AppSettings.from_config(), scheduler_enabled=False)
    with TestClient(create_app(settings, (module,)), headers={"X-User": "tester"}) as client:
        response = client.post(
            "/api/playbooks/instantiate",
            json={"playbook": "standard", "engagement_name": "Atlas rollout"},
        )
    assert response.status_code == 200, response.text
    assert response.json()["workflow"]["status"] == "completed"
    assert calls == ["delivery"]
