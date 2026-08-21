"""Typed workflow steps and composed playbook execution."""

import threading
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from app.extensions import (
    AppSettings,
    ExtensionRegistry,
    ExtensionValidationError,
    PolicyContribution,
    PolicyDecision,
    PolicyEffect,
    SkeinModule,
    WorkflowActionContribution,
)
from app.extensions.policy import PolicyInput, PolicySubject
from app.main import create_app
from app.public import CreateTaskCommand, PublicError
from app.public.workflow import WorkflowEngine, _issue_workflow_context


class SendIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str


class SendOut(BaseModel):
    sent: bool


class CreateWorkIn(BaseModel):
    title: str


class CreateWorkOut(BaseModel):
    task_id: int


def _action(calls: list[str]) -> WorkflowActionContribution:
    def send(_context, request: SendIn):
        calls.append(request.target)
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
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.4.0",
        workflow_actions=(_action(calls),),
        policies=(
            (PolicyContribution("atlas.workplace.regulated-release", _review_regulated),)
            if policy
            else ()
        ),
    )
    registry = ExtensionRegistry.build((module,))
    return WorkflowEngine(registry.workflow_actions, registry.policy_engine)


def _context(engine: WorkflowEngine, subject: PolicySubject, origin: str, **values):
    return _issue_workflow_context(engine, subject, origin, **values)


def test_async_workflow_actions_are_rejected_during_composition():
    async def send(_context, _request):
        return {"sent": True}

    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.4.0",
        workflow_actions=(replace(_action([]), handler=send),),
    )

    with pytest.raises(ExtensionValidationError, match="synchronous handler"):
        ExtensionRegistry.build((module,))


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
        _context(
            engine,
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


def test_a_caller_created_workflow_context_has_no_execution_authority(fresh_db):
    import app.public as public_contracts
    from app.public.workflow import WorkflowContext

    calls: list[str] = []
    engine = _engine(calls, policy=False)
    fabricated = WorkflowContext(PolicySubject("victim"), "workflow")

    with pytest.raises(PublicError) as raised:
        engine.run(engine.prepare(_steps()), fabricated)

    assert raised.value.code == "WORKFLOW_CONTEXT_REQUIRED"
    # The workflow engine is core machinery, not extension API. The composed
    # application is the only execution entry point, so the public package
    # must not export these names.
    assert "WorkflowContext" not in public_contracts.__all__
    assert "WorkflowEngine" not in public_contracts.__all__
    assert "WorkflowResult" not in public_contracts.__all__
    assert calls == []
    assert fresh_db.query_one("SELECT 1 AS present FROM activity") is None


def test_one_grant_cannot_approve_two_occurrences_of_the_same_action(fresh_db):
    calls: list[str] = []

    def review_send(request: PolicyInput):
        if request.action == "atlas.notification.send":
            return PolicyDecision(
                PolicyEffect.REVIEW,
                ("Each notification needs its own verdict.",),
            )
        return None

    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.4.0",
        workflow_actions=(_action(calls),),
        policies=(PolicyContribution("atlas.workplace.review-send", review_send),),
    )
    registry = ExtensionRegistry.build((module,))
    engine = WorkflowEngine(registry.workflow_actions, registry.policy_engine)
    steps = engine.prepare(
        [
            {
                "type": "action",
                "name": "atlas.workplace.notify-manager",
                "input": {"target": "first"},
            },
            {
                "type": "action",
                "name": "atlas.workplace.notify-manager",
                "input": {"target": "second"},
            },
        ]
    )
    subject = PolicySubject("atlas-sync", kind="service")
    first = engine.run(steps, _context(engine, subject, "workflow"))
    assert first.status == "review_required"
    assert first.review_key == "root.0"

    second = engine.run(
        steps,
        _context(
            engine,
            subject,
            "workflow",
            approval_grants={first.review_key: first.review_fingerprint},
        ),
    )
    assert second.status == "review_required"
    assert second.review_key == "root.1"
    assert calls == ["first"]


def test_workflow_grant_fails_closed_when_policy_obligations_change(fresh_db):
    calls: list[str] = []
    requirement = {"group": "delivery-managers"}

    def changing_policy(request: PolicyInput):
        if request.action == "atlas.notification.send":
            return PolicyDecision(
                PolicyEffect.REVIEW,
                approver_groups=(requirement["group"],),
            )
        return None

    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.4.0",
        workflow_actions=(_action(calls),),
        policies=(PolicyContribution("atlas.workplace.changing-policy", changing_policy),),
    )
    registry = ExtensionRegistry.build((module,))
    engine = WorkflowEngine(registry.workflow_actions, registry.policy_engine)
    steps = engine.prepare(
        [
            {
                "type": "action",
                "name": "atlas.workplace.notify-manager",
                "input": {"target": "delivery"},
            }
        ]
    )
    subject = PolicySubject("atlas-sync", kind="service")
    before = engine.run(steps, _context(engine, subject, "workflow"))
    requirement["group"] = "security-managers"
    after = engine.run(
        steps,
        _context(
            engine,
            subject,
            "workflow",
            approval_grants={before.review_key: before.review_fingerprint},
        ),
    )
    assert after.status == "review_required"
    assert after.obligations == ("approver-group:security-managers",)
    assert calls == []


def test_the_nonregulated_branch_runs_the_action_and_checkpoints(fresh_db):
    calls: list[str] = []
    engine = _engine(calls)
    result = engine.run(
        engine.prepare(_steps()),
        _context(
            engine,
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
    # ORDERED, and the whole ledger: an unordered single-row SELECT returned
    # the first-inserted row under SQLite and returns any row here. The run
    # writes the attempt first and the outcome after it, and both matter.
    assert [r["action"] for r in fresh_db.query("SELECT action FROM activity ORDER BY seq")] == [
        "workflow_action_attempt",
        "workflow_action",
    ]


def test_unknown_actions_and_invalid_shapes_fail_before_execution(fresh_db):
    engine = _engine([])
    with pytest.raises(PublicError) as unknown:
        engine.prepare([{"type": "action", "name": "atlas.workplace.missing"}])
    assert unknown.value.code == "UNKNOWN_WORKFLOW_ACTION"
    with pytest.raises(PublicError) as invalid:
        engine.prepare([{"type": "sleep", "seconds": 10}])
    assert invalid.value.code == "INVALID_WORKFLOW"


def test_workflow_actions_require_a_policy_action():
    action = replace(_action([]), policy_action="")
    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.4.0",
        workflow_actions=(action,),
    )
    with pytest.raises(ValueError, match="needs a policy action"):
        ExtensionRegistry.build((module,))


def test_workflow_write_timeout_reports_unknown_completion(fresh_db):
    calls: list[str] = []

    def slow(_context, request: SendIn):
        time.sleep(0.08)
        calls.append(request.target)
        return {"sent": True}

    action = replace(_action(calls), handler=slow, timeout_seconds=0.01)
    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.4.0",
        workflow_actions=(action,),
    )
    registry = ExtensionRegistry.build((module,))
    engine = WorkflowEngine(registry.workflow_actions, registry.policy_engine)
    result = engine.run(
        engine.prepare(
            [
                {
                    "type": "action",
                    "name": "atlas.workplace.notify-manager",
                    "input": {"target": "delivery"},
                }
            ]
        ),
        _context(engine, PolicySubject("atlas-sync", kind="service"), "workflow"),
    )
    assert result.status == "completion_unknown"
    assert result.error_code == "DEADLINE_EXCEEDED"
    time.sleep(0.1)
    assert calls == ["delivery"]
    receipts = fresh_db.query(
        "SELECT action FROM activity WHERE action LIKE 'workflow_action_%' ORDER BY id"
    )
    assert receipts == [
        {"action": "workflow_action_attempt"},
        {"action": "workflow_action_completion_unknown"},
    ]


def test_workflow_write_exception_after_side_effect_reports_unknown_completion(fresh_db):
    calls: list[str] = []

    def write_then_fail(_context, request: SendIn):
        calls.append(request.target)
        raise RuntimeError("remote response was lost")

    action = replace(_action(calls), handler=write_then_fail)
    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.4.0",
        workflow_actions=(action,),
    )
    registry = ExtensionRegistry.build((module,))
    engine = WorkflowEngine(registry.workflow_actions, registry.policy_engine)
    result = engine.run(
        engine.prepare(
            [
                {
                    "type": "action",
                    "name": "atlas.workplace.notify-manager",
                    "input": {"target": "delivery"},
                }
            ]
        ),
        _context(engine, PolicySubject("atlas-sync", kind="service"), "workflow"),
    )
    assert calls == ["delivery"]
    assert result.status == "completion_unknown"
    assert result.error_code == "ACTION_ERROR"


def test_workflow_run_id_is_unique_between_runs_and_stable_for_a_retry(fresh_db):
    correlations: list[str] = []

    def capture_correlation(context, _request: SendIn):
        correlations.append(context.correlation_id)
        return {"sent": True}

    action = replace(_action([]), handler=capture_correlation)
    registry = ExtensionRegistry.build(
        (
            SkeinModule(
                module_id="atlas.workplace",
                version="1.0.0",
                extension_api="1.0",
                minimum_core="0.2.0",
                maximum_core_exclusive="0.4.0",
                workflow_actions=(action,),
            ),
        )
    )
    engine = WorkflowEngine(registry.workflow_actions, registry.policy_engine)
    steps = engine.prepare(
        [
            {
                "type": "action",
                "name": "atlas.workplace.notify-manager",
                "input": {"target": "delivery"},
            }
        ]
    )
    first = _context(engine, PolicySubject("atlas-sync", kind="service"), "workflow")
    second = _context(engine, PolicySubject("atlas-sync", kind="service"), "workflow")

    assert engine.run(steps, first).status == "completed"
    assert engine.run(steps, second).status == "completed"
    assert engine.run(steps, first).status == "completed"

    assert correlations[0] != correlations[1]
    assert correlations[0] == correlations[2]
    assert correlations[0].startswith(f"{first.run_id}:")


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
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.4.0",
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


def test_playbook_execution_uses_the_exact_successful_preflight(
    fresh_db,
    tmp_path,
    monkeypatch,
):
    from app import config
    from app.services import playbooks

    overlay = tmp_path / "playbooks"
    overlay.mkdir()
    (overlay / "race.yaml").write_text(
        """\
schema_version: 1
name: Policy race
project_class: standard
milestones:
  - title: Prepare
workflow:
  - type: action
    name: atlas.workplace.notify-manager
    input:
      target: delivery
"""
    )
    monkeypatch.setattr(config, "PLAYBOOKS_OVERLAY", overlay)
    calls: list[str] = []
    policy_calls = {"count": 0}

    def changing_policy(request: PolicyInput):
        if request.action != "atlas.notification.send":
            return None
        policy_calls["count"] += 1
        if policy_calls["count"] > 1:
            return PolicyDecision(PolicyEffect.REVIEW)
        return PolicyDecision(PolicyEffect.PERMIT)

    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.4.0",
        workflow_actions=(_action(calls),),
        policies=(PolicyContribution("atlas.workplace.changing", changing_policy),),
    )
    registry = ExtensionRegistry.build((module,))
    engine = WorkflowEngine(registry.workflow_actions, registry.policy_engine)
    context = _context(
        engine,
        PolicySubject("mira"),
        "human",
        project_type="standard",
    )
    result = playbooks.instantiate(
        "race",
        "Race engagement",
        actor="mira",
        workflow_engine=engine,
        workflow_context=context,
    )
    assert result["workflow"]["status"] == "completed"
    assert policy_calls["count"] == 1
    assert calls == ["delivery"]
    assert fresh_db.query_one("SELECT name FROM engagements") == {"name": "Race engagement"}


def test_inner_workflow_review_rejects_playbook_drift(fresh_db, tmp_path, monkeypatch):
    from app import config
    from app.services import users

    overlay = tmp_path / "playbooks"
    overlay.mkdir()
    definition = overlay / "reviewed.yaml"
    definition.write_text(
        """\
schema_version: 1
name: Reviewed workflow
project_class: standard
milestones:
  - title: Prepare
workflow:
  - type: action
    name: atlas.workplace.notify-manager
    input:
      target: delivery
"""
    )
    monkeypatch.setattr(config, "PLAYBOOKS_OVERLAY", overlay)
    calls: list[str] = []

    def review_action(request: PolicyInput):
        if request.action == "atlas.notification.send":
            return PolicyDecision(
                PolicyEffect.REVIEW,
                approver_capabilities=("delivery-manager",),
            )
        return None

    def identity(name, _groups, _strong):
        return {"capabilities": ("delivery-manager",) if name == "manager" else ()}

    from app.extensions import IdentityContribution

    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.4.0",
        workflow_actions=(_action(calls),),
        policies=(PolicyContribution("atlas.workplace.review", review_action),),
        identities=(IdentityContribution("atlas.workplace.identity", identity),),
    )
    users.ensure_user("requester")
    users.ensure_user("manager")
    with TestClient(create_app(modules=(module,))) as client:
        queued = client.post(
            "/api/playbooks/instantiate",
            headers={"X-User": "requester"},
            json={"playbook": "reviewed", "engagement_name": "Must stay absent"},
        ).json()["workflow"]
        definition.write_text(
            """\
schema_version: 1
name: Reviewed workflow
project_class: regulated
milestones:
  - title: Prepare
    tasks:
      - Added after workflow review
workflow:
  - type: action
    name: atlas.workplace.notify-manager
    input:
      target: delivery
"""
        )
        response = client.post(
            f"/api/review/{queued['review_id']}/approve",
            headers={"X-User": "manager"},
            json={"note": "Approve the old workflow."},
        )
    assert response.status_code == 403
    assert "playbook changed" in response.json()["detail"].lower()
    assert fresh_db.query_one("SELECT id FROM engagements WHERE name = 'Must stay absent'") is None
    assert calls == []


def test_inner_workflow_approval_consumes_the_current_policy_grant(fresh_db, tmp_path, monkeypatch):
    from app import config
    from app.extensions import IdentityContribution
    from app.services import users

    overlay = tmp_path / "playbooks"
    overlay.mkdir()
    (overlay / "current.yaml").write_text(
        """\
schema_version: 1
name: Current workflow
project_class: standard
milestones:
  - title: Prepare
workflow:
  - type: action
    name: atlas.workplace.notify-manager
    input:
      target: delivery
"""
    )
    monkeypatch.setattr(config, "PLAYBOOKS_OVERLAY", overlay)
    calls: list[str] = []
    required = {"capability": "old-approver"}
    policy_calls = {"count": 0}

    def review_action(request: PolicyInput):
        if request.action == "atlas.notification.send":
            policy_calls["count"] += 1
            return PolicyDecision(
                PolicyEffect.REVIEW,
                approver_capabilities=(required["capability"],),
            )
        return None

    def identity(name, _groups, _strong):
        return {
            "capabilities": ((required["capability"],) if name == "current-manager" else ()),
        }

    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.4.0",
        workflow_actions=(_action(calls),),
        policies=(PolicyContribution("atlas.workplace.current-review", review_action),),
        identities=(IdentityContribution("atlas.workplace.identity", identity),),
    )
    for name in ("requester", "current-manager"):
        users.ensure_user(name)
    with TestClient(create_app(modules=(module,))) as client:
        queued = client.post(
            "/api/playbooks/instantiate",
            headers={"X-User": "requester"},
            json={"playbook": "current", "engagement_name": "Current grant"},
        ).json()["workflow"]
        required["capability"] = "new-approver"
        approved = client.post(
            f"/api/review/{queued['review_id']}/approve",
            headers={"X-User": "current-manager"},
            json={"note": "Approved with the current policy."},
        )

    assert approved.status_code == 200, approved.text
    assert approved.json()["result"]["workflow"]["status"] == "completed"
    assert calls == ["delivery"]
    assert policy_calls["count"] == 2
    assert fresh_db.query_one(
        "SELECT COUNT(*) AS count FROM pending_changes WHERE entity = 'extension_workflow'"
    ) == {"count": 1}


def test_reviewed_workflow_action_writes_through_the_public_facade(fresh_db, tmp_path, monkeypatch):
    from app import config, db
    from app.extensions import IdentityContribution
    from app.public import WorkItems
    from app.services import users

    overlay = tmp_path / "playbooks"
    overlay.mkdir()
    (overlay / "reviewed-write.yaml").write_text(
        """\
schema_version: 1
name: Reviewed write workflow
project_class: standard
milestones:
  - title: Prepare
workflow:
  - type: action
    name: atlas.workplace.create-task
    input:
      title: Created by the reviewed workflow
"""
    )
    (overlay / "reviewed-partial-write.yaml").write_text(
        """\
schema_version: 1
name: Reviewed partial write workflow
project_class: standard
milestones:
  - title: Prepare
workflow:
  - type: action
    name: atlas.workplace.create-task
    input:
      title: Must roll back the failed command
"""
    )
    monkeypatch.setattr(config, "PLAYBOOKS_OVERLAY", overlay)
    handler_threads: list[int] = []
    owner_threads: list[int] = []
    original_create_locked = WorkItems._create_task_locked

    def record_owner(work_items, command, context):
        owner_threads.append(threading.get_ident())
        return original_create_locked(work_items, command, context)

    monkeypatch.setattr(WorkItems, "_create_task_locked", record_owner)

    def create_task(context, request: CreateWorkIn):
        handler_threads.append(threading.get_ident())
        command_context = context.command_context(project_type="standard")
        command = CreateTaskCommand(
            title=request.title,
            idempotency_key=f"reviewed-workflow-task:{request.title}",
        )
        task = context.work_items.create_task(command, command_context)
        replay = context.work_items.create_task(command, command_context)
        assert replay.id == task.id
        return {"task_id": task.id}

    def review_action(request: PolicyInput):
        if request.action == "atlas.task.create":
            return PolicyDecision(
                PolicyEffect.REVIEW,
                approver_capabilities=("atlas.approve",),
            )
        return None

    def identity(name, _groups, _strong):
        return {"capabilities": ("atlas.approve",) if name == "manager" else ()}

    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.4.0",
        workflow_actions=(
            WorkflowActionContribution(
                name="atlas.workplace.create-task",
                version="1.0.0",
                handler=create_task,
                input_schema=CreateWorkIn,
                output_schema=CreateWorkOut,
                effect="write",
                risk="high",
                policy_action="atlas.task.create",
                timeout_seconds=1,
            ),
        ),
        policies=(PolicyContribution("atlas.workplace.review-write", review_action),),
        identities=(IdentityContribution("atlas.workplace.identity", identity),),
    )
    for name in ("requester", "manager"):
        users.ensure_user(name)

    with TestClient(create_app(modules=(module,))) as client:
        queued = client.post(
            "/api/playbooks/instantiate",
            headers={"X-User": "requester"},
            json={
                "playbook": "reviewed-write",
                "engagement_name": "Reviewed workflow project",
            },
        ).json()["workflow"]
        original_execute = db.execute
        failed = {"once": False}

        def fail_first_settlement(sql, params=()):
            if "SET status = 'approved', result = ?" in sql and not failed["once"]:
                failed["once"] = True
                raise RuntimeError("forced settlement failure")
            return original_execute(sql, params)

        monkeypatch.setattr(db, "execute", fail_first_settlement)
        first = client.post(
            f"/api/review/{queued['review_id']}/approve",
            headers={"X-User": "manager"},
            json={"note": "First attempt."},
        )
        assert first.status_code == 400, first.text
        assert (
            fresh_db.query_one(
                "SELECT id FROM tasks WHERE title = ?", ("Created by the reviewed workflow",)
            )
            is None
        )
        assert (
            fresh_db.query_one(
                "SELECT id FROM engagements WHERE name = ?", ("Reviewed workflow project",)
            )
            is None
        )
        assert (
            fresh_db.query_one(
                "SELECT result_id FROM extension_command_receipts WHERE idempotency_key = ?",
                ("reviewed-workflow-task:Created by the reviewed workflow",),
            )
            is None
        )
        assert fresh_db.query_one(
            "SELECT status FROM pending_changes WHERE id = ?", (queued["review_id"],)
        ) == {"status": "pending"}
        started = time.monotonic()
        approved = client.post(
            f"/api/review/{queued['review_id']}/approve",
            headers={"X-User": "manager"},
            json={"note": "Approved."},
        )
        elapsed = time.monotonic() - started

        from app.services import mentions

        callback_runs: list[str] = []
        before_partial = {
            "activity": fresh_db.query_one(
                "SELECT COUNT(*) AS count FROM activity WHERE action = 'create_task'"
            )["count"],
            "outbox": fresh_db.query_one(
                "SELECT COUNT(*) AS count FROM extension_outbox"
                " WHERE event_type = 'skein.task.created'"
            )["count"],
        }

        def fail_after_local_writes(*_args, **_kwargs):
            db.execute(
                'INSERT INTO notifications ("user", tier, message, created_at)'
                " VALUES ('manager', 'passive', 'must roll back', ?)",
                (db.now(),),
            )
            assert db.on_commit(lambda: callback_runs.append("ran"))
            raise ValueError("forced post-insert command failure")

        monkeypatch.setattr(mentions, "scan", fail_after_local_writes)
        partial_queued = client.post(
            "/api/playbooks/instantiate",
            headers={"X-User": "requester"},
            json={
                "playbook": "reviewed-partial-write",
                "engagement_name": "Reviewed partial command project",
            },
        ).json()["workflow"]
        partial_approved = client.post(
            f"/api/review/{partial_queued['review_id']}/approve",
            headers={"X-User": "manager"},
            json={"note": "Record the uncertain command result."},
        )

    assert approved.status_code == 200, approved.text
    assert elapsed < 2
    assert approved.json()["result"]["workflow"]["status"] == "completed"
    assert len(handler_threads) == 3
    assert owner_threads
    assert set(owner_threads).isdisjoint(handler_threads)
    assert fresh_db.query_one(
        "SELECT title, created_by FROM tasks WHERE title = ?",
        ("Created by the reviewed workflow",),
    ) == {"title": "Created by the reviewed workflow", "created_by": "requester"}
    assert fresh_db.query_one(
        "SELECT COUNT(*) AS count FROM tasks WHERE title = ?",
        ("Created by the reviewed workflow",),
    ) == {"count": 1}
    assert fresh_db.query_one(
        "SELECT namespace, idempotency_key FROM extension_command_receipts"
        " WHERE idempotency_key = ?",
        ("reviewed-workflow-task:Created by the reviewed workflow",),
    ) == {
        "namespace": "workflow:atlas.workplace.create-task",
        "idempotency_key": "reviewed-workflow-task:Created by the reviewed workflow",
    }
    assert partial_approved.status_code == 200, partial_approved.text
    partial_workflow = partial_approved.json()["result"]["workflow"]
    assert partial_workflow["status"] == "completion_unknown"
    assert partial_workflow["error_code"] == "ACTION_ERROR"
    assert fresh_db.query_one(
        "SELECT status FROM pending_changes WHERE id = ?",
        (partial_queued["review_id"],),
    ) == {"status": "approved"}
    assert fresh_db.query_one(
        "SELECT status FROM extension_review_invocations WHERE change_id = ?",
        (partial_queued["review_id"],),
    ) == {"status": "approved"}
    assert (
        fresh_db.query_one(
            "SELECT id FROM tasks WHERE title = ?", ("Must roll back the failed command",)
        )
        is None
    )
    assert (
        fresh_db.query_one(
            "SELECT result_id FROM extension_command_receipts WHERE idempotency_key = ?",
            ("reviewed-workflow-task:Must roll back the failed command",),
        )
        is None
    )
    assert (
        fresh_db.query_one("SELECT COUNT(*) AS count FROM activity WHERE action = 'create_task'")[
            "count"
        ]
        == before_partial["activity"]
    )
    assert (
        fresh_db.query_one(
            "SELECT COUNT(*) AS count FROM extension_outbox WHERE event_type = 'skein.task.created'"
        )["count"]
        == before_partial["outbox"]
    )
    assert fresh_db.query_one(
        "SELECT COUNT(*) AS count FROM notifications WHERE message = 'must roll back'"
    ) == {"count": 0}
    assert callback_runs == []


def test_timed_out_workflow_closes_late_public_work_calls(fresh_db):
    from app.services import users

    late = threading.Event()
    errors: list[str] = []

    def create_after_deadline(context, request: CreateWorkIn):
        time.sleep(0.05)
        try:
            context.work_items.create_task(
                CreateTaskCommand(title=request.title),
                context.command_context(project_type="standard"),
            )
        except PublicError as exc:
            errors.append(exc.code)
        finally:
            late.set()
        return {"task_id": 0}

    action = WorkflowActionContribution(
        name="atlas.workplace.late-task",
        version="1.0.0",
        handler=create_after_deadline,
        input_schema=CreateWorkIn,
        output_schema=CreateWorkOut,
        effect="write",
        risk="high",
        policy_action="atlas.task.create",
        timeout_seconds=0.01,
    )
    registry = ExtensionRegistry.build(
        (
            SkeinModule(
                module_id="atlas.workplace",
                version="1.0.0",
                extension_api="1.0",
                minimum_core="0.2.0",
                maximum_core_exclusive="0.4.0",
                workflow_actions=(action,),
            ),
        )
    )
    engine = WorkflowEngine(registry.workflow_actions, registry.policy_engine)
    with fresh_db.transaction():
        result = engine.run(
            engine.prepare(
                [
                    {
                        "type": "action",
                        "name": action.name,
                        "input": {"title": "Must not appear after timeout"},
                    }
                ]
            ),
            _context(engine, PolicySubject("requester"), "workflow"),
        )

    assert result.status == "completion_unknown"
    assert result.error_code == "DEADLINE_EXCEEDED"
    assert late.wait(1)
    assert errors == ["EXECUTION_CONTEXT_CLOSED"]
    assert (
        fresh_db.query_one(
            "SELECT id FROM tasks WHERE title = ?", ("Must not appear after timeout",)
        )
        is None
    )
    users.ensure_user("database-still-usable")


def test_workflow_work_completed_before_deadline_commits_with_unknown_completion(fresh_db):
    finished = threading.Event()

    def create_then_finish_late(context, request: CreateWorkIn):
        context.work_items.create_task(
            CreateTaskCommand(title=request.title),
            context.command_context(project_type="standard"),
        )
        time.sleep(0.05)
        finished.set()
        return {"task_id": 0}

    action = WorkflowActionContribution(
        name="atlas.workplace.early-task",
        version="1.0.0",
        handler=create_then_finish_late,
        input_schema=CreateWorkIn,
        output_schema=CreateWorkOut,
        effect="write",
        risk="high",
        policy_action="atlas.task.create",
        timeout_seconds=0.01,
    )
    registry = ExtensionRegistry.build(
        (
            SkeinModule(
                module_id="atlas.workplace",
                version="1.0.0",
                extension_api="1.0",
                minimum_core="0.2.0",
                maximum_core_exclusive="0.4.0",
                workflow_actions=(action,),
            ),
        )
    )
    engine = WorkflowEngine(registry.workflow_actions, registry.policy_engine)
    with fresh_db.transaction():
        result = engine.run(
            engine.prepare(
                [
                    {
                        "type": "action",
                        "name": action.name,
                        "input": {"title": "Committed before timeout"},
                    }
                ]
            ),
            _context(engine, PolicySubject("requester"), "workflow"),
        )

    assert result.status == "completion_unknown"
    assert result.error_code == "DEADLINE_EXCEEDED"
    assert fresh_db.query_one(
        "SELECT title FROM tasks WHERE title = ?", ("Committed before timeout",)
    ) == {"title": "Committed before timeout"}
    assert finished.wait(1)


def test_workflow_rejection_uses_current_action_metadata(fresh_db, tmp_path, monkeypatch):
    from app import config
    from app.services import review, users

    overlay = tmp_path / "playbooks"
    overlay.mkdir()
    (overlay / "metadata.yaml").write_text(
        """\
schema_version: 1
name: Metadata workflow
project_class: standard
milestones:
  - title: Prepare
workflow:
  - type: action
    name: atlas.workplace.notify-manager
    input:
      target: delivery
"""
    )
    monkeypatch.setattr(config, "PLAYBOOKS_OVERLAY", overlay)
    calls: list[str] = []

    def review_action(request: PolicyInput):
        if request.action == "atlas.notification.send":
            return PolicyDecision(
                PolicyEffect.REVIEW,
                approver_groups=(f"{request.tool_risk}-managers",),
            )
        return None

    low_action = _action(calls)
    low_module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.4.0",
        workflow_actions=(low_action,),
        policies=(PolicyContribution("atlas.workplace.metadata-review", review_action),),
    )
    for name in ("requester", "manager"):
        users.ensure_user(name)
    with TestClient(create_app(modules=(low_module,))) as client:
        review_id = client.post(
            "/api/playbooks/instantiate",
            headers={"X-User": "requester"},
            json={"playbook": "metadata", "engagement_name": "Metadata review"},
        ).json()["workflow"]["review_id"]

    high_action = replace(low_action, version="2.0.0", risk="critical")
    current_registry = ExtensionRegistry.build(
        (replace(low_module, version="1.1.0", workflow_actions=(high_action,)),)
    )
    with pytest.raises(PermissionError, match="configured workplace approver"):
        review.reject_change(
            review_id,
            actor="manager",
            reviewer_groups=("medium-managers",),
            policy_registry=current_registry,
        )
    assert review.reject_change(
        review_id,
        actor="manager",
        reviewer_groups=("critical-managers",),
        policy_registry=current_registry,
    ) == {"id": review_id, "status": "rejected"}
