"""Commands, events, and data boundaries used by private packages."""

import time
from dataclasses import replace

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.extensions import (
    AppSettings,
    EventContribution,
    EventExecutionContext,
    ExtensionMigration,
    ExtensionRegistry,
    ExtensionValidationError,
    JobExecutionContext,
    MigrationContribution,
    PolicyContribution,
    PolicyDecision,
    PolicyEffect,
    PolicyResource,
    RouteContribution,
    RouteOperationContribution,
    ServiceIdentityContribution,
    SkeinModule,
    ToolHandlerContext,
    WorkflowActionContext,
)
from app.extensions.data import ExtensionStore
from app.extensions.fastapi import ExtensionRouteServices
from app.extensions.policy import PolicyInput, PolicySubject
from app.main import create_app
from app.public import CommandContext, CreateTaskCommand, PublicError, UpdateTaskCommand, WorkItems
from app.public.events import dispatch_events
from app.public.work import _bind_execution_context


def _context(work_items: WorkItems, **changes) -> CommandContext:
    values = {
        "subject": PolicySubject("atlas-sync", kind="service"),
        "namespace": "atlas.workplace.sync",
        "correlation_id": "sync-42",
        "project_type": "regulated",
        "read_as_human": False,
    }
    values.update(changes)
    execution_context = JobExecutionContext(
        policy=work_items._policy,
        work_items=work_items,
        subject=values["subject"],
        run_id=values["correlation_id"],
        namespace=values["namespace"],
    )
    execution = _bind_execution_context(
        work_items,
        execution_context,
        subject=values["subject"],
        namespace=values["namespace"],
        receipt_namespace=f"job:{values['namespace']}",
        correlation_id=values["correlation_id"],
        read_name=values["subject"].name if values["read_as_human"] else "",
        read_strong=values["subject"].strong if values["read_as_human"] else False,
    )
    return execution.command_context(project_type=values["project_type"])


def _event_context() -> EventExecutionContext:
    registry = ExtensionRegistry.build(
        (
            SkeinModule(
                module_id="atlas.workplace",
                version="1.0.0",
                extension_api="1.0",
                minimum_core="0.2.0",
                maximum_core_exclusive="0.3.0",
                service_identities=(
                    ServiceIdentityContribution(
                        "atlas.workplace.event-identity",
                        "atlas-events",
                    ),
                ),
            ),
        )
    )
    return EventExecutionContext(
        registry.policy_engine,
        WorkItems(registry.policy_engine),
        registry.service_subject,
    )


def _event(name, handler, event_types, **changes) -> EventContribution:
    values = {
        "name": name,
        "handler": handler,
        "event_types": event_types,
        "service_identity": "atlas-events",
        "policy_action": "atlas.events.deliver",
        "effect": "write",
        "risk": "medium",
    }
    values.update(changes)
    return EventContribution(**values)


def test_public_work_commands_keep_service_invariants_and_emit_safe_events(fresh_db):
    registry = ExtensionRegistry.build(())
    work = WorkItems(registry.policy_engine)

    created = work.create_task(
        CreateTaskCommand(title="Secret launch", description="Do not publish this body"),
        _context(work),
    )
    updated = work.update_task(
        UpdateTaskCommand(task_id=created.id, status="in_progress", priority="high"),
        _context(work),
    )

    assert updated.status == "in_progress"
    assert updated.priority == "high"
    assert updated.origin == "extension:atlas.workplace.sync"
    activity = fresh_db.query("SELECT action, actor FROM activity ORDER BY id")
    assert activity == [
        {"action": "create_task", "actor": "atlas-sync"},
        {"action": "update_task", "actor": "atlas-sync"},
    ]
    outbox = fresh_db.query("SELECT event_type, payload FROM extension_outbox ORDER BY created_at")
    assert [row["event_type"] for row in outbox] == [
        "skein.task.created",
        "skein.task.updated",
    ]
    assert all("Secret launch" not in row["payload"] for row in outbox)
    assert all("Do not publish" not in row["payload"] for row in outbox)


def test_public_work_rejects_a_forged_execution_context(fresh_db):
    facade = WorkItems(ExtensionRegistry.build(()).policy_engine)
    forged = CommandContext(
        PolicySubject("atlas-sync", kind="service"),
        origin="human",
        namespace="atlas.workplace.sync",
        actor="mira",
        actor_kind="human",
    )

    with pytest.raises(PublicError) as raised:
        facade.create_task(CreateTaskCommand(title="Forged attribution"), forged)

    assert raised.value.code == "COMMAND_CONTEXT_REQUIRED"
    assert fresh_db.query_one("SELECT 1 AS present FROM tasks") is None
    assert fresh_db.query_one("SELECT 1 AS present FROM activity") is None


def test_caller_constructed_execution_boundaries_cannot_mint_command_authority(fresh_db):
    facade = WorkItems(ExtensionRegistry.build(()).policy_engine)
    subject = PolicySubject("atlas-sync", kind="service")
    policy = facade._policy
    contexts = (
        JobExecutionContext(policy, facade, subject, "run", "atlas.workplace.job"),
        ToolHandlerContext(subject, policy, facade, "atlas-agent", "call", "atlas.workplace.tool"),
        EventExecutionContext(
            policy,
            facade,
            lambda _name: subject,
            subject,
            "delivery",
            "atlas.workplace.events",
        ),
        WorkflowActionContext(subject, policy, facade, "atlas.workplace.action", "workflow"),
        ExtensionRouteServices(subject, policy, facade, "atlas.workplace.routes", "request"),
    )

    for context in contexts:
        with pytest.raises(PublicError) as raised:
            context.command_context()
        assert raised.value.code == "COMMAND_CONTEXT_REQUIRED"

    assert not hasattr(facade, "_bind_execution_context")

    assert fresh_db.query_one("SELECT 1 AS present FROM tasks") is None


def test_bound_provenance_and_issued_commands_cannot_be_changed_by_the_handler(fresh_db):
    facade = WorkItems(ExtensionRegistry.build(()).policy_engine)
    granted = PolicySubject("atlas-sync", kind="service")
    fabricated = PolicySubject("mira", kind="human", capabilities=("admin",))
    execution = JobExecutionContext(
        facade._policy,
        facade,
        granted,
        "atlas-run",
        "atlas.workplace.sync",
    )
    bound = _bind_execution_context(
        facade,
        execution,
        subject=granted,
        namespace="atlas.workplace.sync",
        receipt_namespace="job:atlas.workplace.sync",
        correlation_id="atlas-run",
    )
    object.__setattr__(bound, "subject", fabricated)
    object.__setattr__(bound, "namespace", "evil.workplace.sync")
    command_context = bound.command_context()

    assert command_context.subject == granted
    assert command_context.namespace == "atlas.workplace.sync"
    object.__setattr__(command_context, "actor", "mira")
    with pytest.raises(PublicError) as raised:
        facade.create_task(CreateTaskCommand(title="forged actor"), command_context)
    assert raised.value.code == "COMMAND_CONTEXT_REQUIRED"
    assert fresh_db.query_one("SELECT 1 AS present FROM tasks") is None


def test_public_command_receipts_are_isolated_by_contribution(fresh_db):
    facade = WorkItems(ExtensionRegistry.build(()).policy_engine)
    command = CreateTaskCommand(title="Shared external ID", idempotency_key="external:42")

    first = facade.create_task(
        command,
        _context(facade, namespace="atlas.workplace.sync"),
    )
    second = facade.create_task(
        command.model_copy(update={"title": "A separate source"}),
        _context(facade, namespace="acme.workplace.sync"),
    )

    assert first.id != second.id
    receipts = fresh_db.query(
        "SELECT namespace, idempotency_key FROM extension_command_receipts ORDER BY namespace"
    )
    assert receipts == [
        {"namespace": "job:acme.workplace.sync", "idempotency_key": "external:42"},
        {"namespace": "job:atlas.workplace.sync", "idempotency_key": "external:42"},
    ]


def test_command_receipts_are_isolated_when_contribution_kinds_share_a_name(fresh_db):
    facade = WorkItems(ExtensionRegistry.build(()).policy_engine)
    subject = PolicySubject("atlas-sync", kind="service")
    name = "atlas.workplace.sync"
    job_context = JobExecutionContext(facade._policy, facade, subject, "job-run", name)
    job = _bind_execution_context(
        facade,
        job_context,
        subject=subject,
        namespace=name,
        receipt_namespace=f"job:{name}",
        correlation_id="job-run",
    )
    tool_context = ToolHandlerContext(
        subject, facade._policy, facade, "atlas-agent", "tool-run", name
    )
    tool = _bind_execution_context(
        facade,
        tool_context,
        subject=subject,
        namespace=name,
        receipt_namespace=f"tool:{name}",
        correlation_id="tool-run",
        actor="atlas-agent",
        actor_kind="agent",
    )
    command = CreateTaskCommand(title="job task", idempotency_key="same-key")

    first = facade.create_task(command, job.command_context())
    second = facade.create_task(
        command.model_copy(update={"title": "tool task"}),
        tool.command_context(),
    )

    assert first.id != second.id
    assert [
        row["namespace"]
        for row in fresh_db.query(
            "SELECT namespace FROM extension_command_receipts ORDER BY namespace"
        )
    ] == ["job:atlas.workplace.sync", "tool:atlas.workplace.sync"]


def test_public_command_and_event_share_the_transaction(fresh_db, monkeypatch):
    from app.services import work as service_work

    def fail_event(*_args, **_kwargs):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(service_work, "_emit_task_event", fail_event)
    facade = WorkItems(ExtensionRegistry.build(()).policy_engine)
    with pytest.raises(RuntimeError, match="outbox unavailable"):
        facade.create_task(CreateTaskCommand(title="rolled back"), _context(facade))
    assert fresh_db.query_one("SELECT 1 AS present FROM tasks") is None
    assert fresh_db.query_one("SELECT 1 AS present FROM activity") is None


def test_rest_and_agent_service_writes_emit_the_same_public_events(fresh_db):
    from app.services import work as service_work

    settings = replace(AppSettings.from_config(), scheduler_enabled=False)
    with TestClient(create_app(settings), headers={"X-User": "mira"}) as client:
        created = client.post("/api/tasks", json={"title": "Human task"})
        assert created.status_code == 200
    service_work.update_task(
        created.json()["id"],
        status="in_progress",
        actor="scout",
        origin="agent",
    )

    events = fresh_db.query("SELECT event_type, payload FROM extension_outbox ORDER BY created_at")
    assert [item["event_type"] for item in events] == [
        "skein.task.created",
        "skein.task.updated",
    ]
    assert '"origin":"human"' in events[0]["payload"]
    assert '"origin":"agent"' in events[1]["payload"]


def test_a_workplace_policy_can_require_a_manager_before_the_write(fresh_db):
    def manager_review(request: PolicyInput):
        if (
            request.action == "work.task.create"
            and request.resource.project_type == "regulated"
            and request.subject.kind == "service"
        ):
            return PolicyDecision(
                PolicyEffect.REVIEW,
                ("Regulated work needs a manager.",),
                approver_groups=("delivery-managers",),
            )
        return None

    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("atlas.workplace.manager-review", manager_review),),
    )
    facade = WorkItems(ExtensionRegistry.build((module,)).policy_engine)
    with pytest.raises(PublicError) as raised:
        facade.create_task(CreateTaskCommand(title="needs review"), _context(facade))
    assert raised.value.code == "REVIEW_REQUIRED"
    assert raised.value.obligations == ("approver-group:delivery-managers",)
    assert fresh_db.query_one("SELECT 1 AS present FROM tasks") is None


def test_linked_engagement_context_overrides_caller_policy_context(fresh_db):
    from app.services import engagements

    engagement = engagements.create_engagement(
        "Regulated launch",
        project_class="regulated",
        actor="atlas-sync",
    )

    def require_review(request: PolicyInput):
        if request.action == "work.task.create" and request.resource.project_type == "regulated":
            return PolicyDecision(PolicyEffect.REVIEW, ("Regulated work needs review.",))
        return None

    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("atlas.workplace.regulated", require_review),),
    )
    facade = WorkItems(ExtensionRegistry.build((module,)).policy_engine)
    with pytest.raises(PublicError) as raised:
        facade.create_task(
            CreateTaskCommand(
                title="Linked work",
                engagement_id=engagement["id"],
            ),
            _context(facade, project_type="standard"),
        )
    assert raised.value.code == "REVIEW_REQUIRED"
    assert fresh_db.query_one("SELECT title FROM tasks") is None


@pytest.mark.parametrize("caller_project_type", ["standard", ""])
def test_crew_engagement_context_overrides_caller_policy_context(fresh_db, caller_project_type):
    from app.services import crews, engagements

    crew_id = crews.create_crew("Regulated delivery", actor="atlas-sync")["id"]
    engagement = engagements.create_engagement(
        "Crew-regulated launch",
        project_class="regulated",
        actor="atlas-sync",
        visibility="crew",
        crew_id=crew_id,
    )

    def require_review(request: PolicyInput):
        if request.action == "work.task.create" and request.resource.project_type == "regulated":
            return PolicyDecision(PolicyEffect.REVIEW, ("Regulated work needs review.",))
        return None

    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("atlas.workplace.regulated", require_review),),
    )
    facade = WorkItems(ExtensionRegistry.build((module,)).policy_engine)
    with pytest.raises(PublicError) as raised:
        facade.create_task(
            CreateTaskCommand(
                title="Crew-linked work",
                engagement_id=engagement["id"],
                visibility="crew",
                crew_id=crew_id,
            ),
            _context(
                facade,
                project_type=caller_project_type,
                subject=PolicySubject("atlas-sync", strong=True),
                read_as_human=True,
            ),
        )
    assert raised.value.code == "REVIEW_REQUIRED"
    assert fresh_db.query_one("SELECT title FROM tasks") is None


@pytest.mark.parametrize("caller_project_type", ["standard", ""])
def test_crew_milestone_context_overrides_caller_policy_context(fresh_db, caller_project_type):
    from app.services import crews, engagements
    from app.services import work as service_work

    crew_id = crews.create_crew("Regulated delivery", actor="atlas-sync")["id"]
    engagements.create_engagement(
        "Crew-regulated launch",
        project_class="regulated",
        actor="atlas-sync",
        visibility="crew",
        crew_id=crew_id,
    )
    milestone = service_work.create_milestone(
        "Regulated gate",
        project="Crew-regulated launch",
        actor="atlas-sync",
        visibility="crew",
        crew_id=crew_id,
    )["id"]

    def require_review(request: PolicyInput):
        if request.action == "work.task.create" and request.resource.project_type == "regulated":
            return PolicyDecision(PolicyEffect.REVIEW, ("Regulated work needs review.",))
        return None

    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("atlas.workplace.regulated", require_review),),
    )
    facade = WorkItems(ExtensionRegistry.build((module,)).policy_engine)
    with pytest.raises(PublicError) as raised:
        facade.create_task(
            CreateTaskCommand(
                title="Milestone-linked work",
                milestone_id=milestone,
                visibility="crew",
                crew_id=crew_id,
            ),
            _context(
                facade,
                project_type=caller_project_type,
                subject=PolicySubject("atlas-sync", strong=True),
                read_as_human=True,
            ),
        )
    assert raised.value.code == "REVIEW_REQUIRED"
    assert fresh_db.query_one("SELECT title FROM tasks") is None


def test_public_create_rejects_links_to_different_engagements(fresh_db):
    from app.services import engagements
    from app.services import work as service_work

    direct = engagements.create_engagement("Direct project", project_class="standard")["id"]
    engagements.create_engagement("Milestone project", project_class="regulated")
    milestone = service_work.create_milestone("Regulated gate", project="Milestone project")["id"]
    facade = WorkItems(ExtensionRegistry.build(()).policy_engine)

    with pytest.raises(PublicError, match="must belong to the same engagement") as raised:
        facade.create_task(
            CreateTaskCommand(
                title="Conflicting links",
                engagement_id=direct,
                milestone_id=milestone,
            ),
            _context(facade, project_type="standard"),
        )

    assert raised.value.code == "TASK_CREATE_REJECTED"
    assert fresh_db.query_one("SELECT id FROM tasks WHERE title = 'Conflicting links'") is None


def test_invisible_link_and_absent_link_have_the_same_public_refusal(fresh_db):
    from app.services import crews, engagements

    crew_id = crews.create_crew("Hidden delivery", actor="other-person")["id"]
    hidden_id = engagements.create_engagement(
        "Hidden engagement",
        project_class="regulated",
        actor="other-person",
        visibility="crew",
        crew_id=crew_id,
    )["id"]
    facade = WorkItems(ExtensionRegistry.build(()).policy_engine)
    command = CreateTaskCommand(title="Unresolved link", engagement_id=hidden_id)

    with pytest.raises(PublicError) as hidden:
        facade.create_task(command, _context(facade, project_type="standard"))
    fresh_db.execute("DELETE FROM engagements WHERE id = ?", (hidden_id,))
    with pytest.raises(PublicError) as absent:
        facade.create_task(command, _context(facade, project_type="standard"))

    assert (hidden.value.code, hidden.value.detail) == (
        absent.value.code,
        absent.value.detail,
    )
    assert fresh_db.query_one("SELECT title FROM tasks") is None


@pytest.mark.parametrize("hidden_project_type", ["regulated", "standard"])
def test_visible_milestone_does_not_expose_a_hidden_parent_project_class(
    fresh_db, hidden_project_type
):
    from app.services import crews, engagements, scope
    from app.services import work as service_work

    crew_id = crews.create_crew("Hidden delivery", actor="other-person")["id"]
    hidden_id = engagements.create_engagement(
        "Hidden engagement",
        project_class=hidden_project_type,
        actor="other-person",
        visibility="crew",
        crew_id=crew_id,
    )["id"]
    milestone = service_work.create_milestone(
        "Visible milestone",
        project="Hidden engagement",
        actor="atlas-sync",
    )["id"]
    fresh_db.execute(
        "UPDATE milestones SET engagement_id = ? WHERE id = ?",
        (hidden_id, milestone),
    )

    def deny_regulated(request: PolicyInput):
        if request.action == "work.task.create" and request.resource.project_type == "regulated":
            return PolicyDecision(PolicyEffect.DENY, ("Regulated work is closed.",))
        return None

    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("atlas.workplace.regulated", deny_regulated),),
    )
    facade = WorkItems(ExtensionRegistry.build((module,)).policy_engine)
    with pytest.raises(PublicError) as raised:
        facade.create_task(
            CreateTaskCommand(title="Hidden-parent work", milestone_id=milestone),
            _context(facade, project_type="standard"),
        )

    assert (raised.value.code, raised.value.detail) == (
        "TASK_CREATE_REJECTED",
        scope.missing_text("milestones", milestone),
    )
    assert fresh_db.query_one("SELECT title FROM tasks") is None


@pytest.mark.parametrize("hidden_project_type", ["regulated", "standard"])
def test_public_update_does_not_expose_a_hidden_engagement(fresh_db, hidden_project_type):
    from app.services import crews, engagements, scope
    from app.services import work as service_work

    crew_id = crews.create_crew("Hidden delivery", actor="other-person")["id"]
    hidden_id = engagements.create_engagement(
        "Hidden engagement",
        project_class=hidden_project_type,
        actor="other-person",
        visibility="crew",
        crew_id=crew_id,
    )["id"]
    task = service_work.create_task("Visible task", actor="atlas-sync")["id"]

    def deny_regulated(request: PolicyInput):
        if request.action == "work.task.update" and request.resource.project_type == "regulated":
            return PolicyDecision(PolicyEffect.DENY, ("Regulated work is closed.",))
        return None

    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("atlas.workplace.regulated", deny_regulated),),
    )
    facade = WorkItems(ExtensionRegistry.build((module,)).policy_engine)
    command = UpdateTaskCommand(task_id=task, engagement_id=hidden_id)

    with pytest.raises(PublicError) as hidden:
        facade.update_task(command, _context(facade, project_type="standard"))
    fresh_db.execute("DELETE FROM engagements WHERE id = ?", (hidden_id,))
    with pytest.raises(PublicError) as absent:
        facade.update_task(command, _context(facade, project_type="standard"))

    assert (hidden.value.code, hidden.value.detail) == (
        "TASK_UPDATE_REJECTED",
        scope.missing_text("engagements", hidden_id),
    )
    assert (absent.value.code, absent.value.detail) == (
        hidden.value.code,
        hidden.value.detail,
    )
    assert fresh_db.query_one("SELECT engagement_id FROM tasks WHERE id = ?", (task,)) == {
        "engagement_id": None
    }


@pytest.mark.parametrize("hidden_project_type", ["regulated", "standard"])
def test_public_update_does_not_expose_a_visible_milestones_hidden_parent(
    fresh_db, hidden_project_type
):
    from app.services import crews, engagements, scope
    from app.services import work as service_work

    crew_id = crews.create_crew("Hidden delivery", actor="other-person")["id"]
    hidden_id = engagements.create_engagement(
        "Hidden engagement",
        project_class=hidden_project_type,
        actor="other-person",
        visibility="crew",
        crew_id=crew_id,
    )["id"]
    milestone = service_work.create_milestone(
        "Visible milestone",
        project="Hidden engagement",
        actor="atlas-sync",
    )["id"]
    fresh_db.execute(
        "UPDATE milestones SET engagement_id = ? WHERE id = ?",
        (hidden_id, milestone),
    )
    task = service_work.create_task("Visible task", actor="atlas-sync")["id"]
    facade = WorkItems(ExtensionRegistry.build(()).policy_engine)
    command = UpdateTaskCommand(task_id=task, milestone_id=milestone)

    with pytest.raises(PublicError) as hidden:
        facade.update_task(command, _context(facade, project_type="standard"))
    fresh_db.execute("DELETE FROM milestones WHERE id = ?", (milestone,))
    with pytest.raises(PublicError) as absent:
        facade.update_task(command, _context(facade, project_type="standard"))

    assert (hidden.value.code, hidden.value.detail) == (
        "TASK_UPDATE_REJECTED",
        scope.missing_text("milestones", milestone),
    )
    assert (absent.value.code, absent.value.detail) == (
        hidden.value.code,
        hidden.value.detail,
    )
    assert fresh_db.query_one("SELECT milestone_id FROM tasks WHERE id = ?", (task,)) == {
        "milestone_id": None
    }


@pytest.mark.parametrize("hidden_project_type", ["regulated", "standard"])
def test_public_read_does_not_expose_a_hidden_task_relationship(fresh_db, hidden_project_type):
    from app.services import crews, engagements, scope
    from app.services import work as service_work

    crew_id = crews.create_crew("Hidden delivery", actor="other-person")["id"]
    hidden_id = engagements.create_engagement(
        "Hidden engagement",
        project_class=hidden_project_type,
        actor="other-person",
        visibility="crew",
        crew_id=crew_id,
    )["id"]
    task = service_work.create_task("Visible child", actor="other-person")["id"]
    # Simulate a row created before relationship audience containment shipped.
    fresh_db.execute("UPDATE tasks SET engagement_id = ? WHERE id = ?", (hidden_id, task))

    def deny_regulated(request: PolicyInput):
        if request.action == "work.task.read" and request.resource.project_type == "regulated":
            return PolicyDecision(PolicyEffect.DENY, ("Regulated work is closed.",))
        return None

    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("atlas.workplace.regulated", deny_regulated),),
    )
    facade = WorkItems(ExtensionRegistry.build((module,)).policy_engine)
    context = _context(facade, project_type="standard")

    with pytest.raises(PublicError) as hidden:
        facade.get_task(task, context)
    fresh_db.execute("DELETE FROM tasks WHERE id = ?", (task,))
    with pytest.raises(PublicError) as absent:
        facade.get_task(task, context)

    assert (hidden.value.code, hidden.value.detail) == (
        "TASK_NOT_FOUND",
        scope.missing_text("tasks", task),
    )
    assert (absent.value.code, absent.value.detail) == (
        hidden.value.code,
        hidden.value.detail,
    )


@pytest.mark.parametrize("hidden_project_type", ["regulated", "standard"])
def test_public_read_does_not_expose_a_visible_milestones_hidden_parent(
    fresh_db, hidden_project_type
):
    from app.services import crews, engagements, scope
    from app.services import work as service_work

    crew_id = crews.create_crew("Hidden delivery", actor="other-person")["id"]
    hidden_id = engagements.create_engagement(
        "Hidden engagement",
        project_class=hidden_project_type,
        actor="other-person",
        visibility="crew",
        crew_id=crew_id,
    )["id"]
    milestone = service_work.create_milestone(
        "Visible milestone",
        project="Hidden engagement",
        actor="atlas-sync",
    )["id"]
    fresh_db.execute(
        "UPDATE milestones SET engagement_id = ? WHERE id = ?",
        (hidden_id, milestone),
    )
    task = service_work.create_task("Visible child", actor="atlas-sync")["id"]
    # Simulate a legacy row whose visible milestone has a hidden parent.
    fresh_db.execute("UPDATE tasks SET milestone_id = ? WHERE id = ?", (milestone, task))
    facade = WorkItems(ExtensionRegistry.build(()).policy_engine)

    with pytest.raises(PublicError) as raised:
        facade.get_task(task, _context(facade, project_type="standard"))

    assert (raised.value.code, raised.value.detail) == (
        "TASK_NOT_FOUND",
        scope.missing_text("tasks", task),
    )


def test_public_machine_read_is_limited_to_workspace_visibility(fresh_db):
    from app.services import crews, engagements
    from app.services import work as service_work

    crew_id = crews.create_crew("Agent delivery", actor="atlas-agent")["id"]
    engagement = engagements.create_engagement(
        "Agent engagement",
        actor="atlas-agent",
        visibility="crew",
        crew_id=crew_id,
    )["id"]
    task = service_work.create_task(
        "Agent task",
        actor="atlas-agent",
        engagement_id=engagement,
        visibility="crew",
        crew_id=crew_id,
    )["id"]
    facade = WorkItems(ExtensionRegistry.build(()).policy_engine)
    execution = ToolHandlerContext(
        subject=PolicySubject("mira", strong=False),
        policy=facade._policy,
        work_items=facade,
        agent="atlas-agent",
        correlation_id="agent-read",
        namespace="atlas.workplace.agent-read",
    )
    _bind_execution_context(
        facade,
        execution,
        subject=execution.subject,
        namespace=execution.namespace,
        receipt_namespace="tool:atlas.workplace.agent-read",
        correlation_id=execution.correlation_id,
        actor=execution.agent,
        actor_kind="agent",
    )

    with pytest.raises(PublicError) as raised:
        facade.get_task(task, execution.command_context())

    assert raised.value.code == "TASK_NOT_FOUND"


def test_public_create_and_update_refuse_a_child_broader_than_its_parent(fresh_db):
    from app.services import engagements, scope
    from app.services import work as service_work

    private_parent = engagements.create_engagement(
        "Private delivery", actor="mira", visibility=scope.PRIVATE
    )["id"]
    facade = WorkItems(ExtensionRegistry.build(()).policy_engine)
    human = _context(
        facade,
        subject=PolicySubject("mira", strong=True),
        namespace="atlas.workplace.human-write",
        read_as_human=True,
    )

    with pytest.raises(PublicError) as create_refusal:
        facade.create_task(
            CreateTaskCommand(title="Too broad", engagement_id=private_parent), human
        )
    assert create_refusal.value.code == "TASK_CREATE_REJECTED"

    task = service_work.create_task("Unlinked", actor="mira")["id"]
    with pytest.raises(PublicError) as update_refusal:
        facade.update_task(UpdateTaskCommand(task_id=task, engagement_id=private_parent), human)
    assert update_refusal.value.code == "TASK_UPDATE_REJECTED"
    assert fresh_db.query_one("SELECT engagement_id FROM tasks WHERE id = ?", (task,)) == {
        "engagement_id": None
    }

    private_child = facade.create_task(
        CreateTaskCommand(
            title="Contained child",
            engagement_id=private_parent,
            visibility=scope.PRIVATE,
        ),
        human,
    )
    assert private_child.engagement_id == private_parent


def test_public_machine_cannot_use_its_actor_name_as_a_private_viewer(fresh_db):
    from app.services import work as service_work

    facade = WorkItems(ExtensionRegistry.build(()).policy_engine)
    execution = JobExecutionContext(
        policy=facade._policy,
        work_items=facade,
        subject=PolicySubject("atlas-sync", kind="service"),
        run_id="private-read",
        namespace="atlas.workplace.private-read",
    )
    _bind_execution_context(
        facade,
        execution,
        subject=execution.subject,
        namespace=execution.namespace,
        receipt_namespace="job:atlas.workplace.private-read",
        correlation_id=execution.run_id,
    )
    private_task = service_work.create_task(
        "Service secret", actor="atlas-sync", visibility="private"
    )["id"]

    with pytest.raises(PublicError) as raised:
        facade.get_task(private_task, execution.command_context())

    assert raised.value.code == "TASK_NOT_FOUND"


def test_workflow_does_not_inherit_the_requesters_private_read_scope(fresh_db):
    from app.services import work as service_work

    facade = WorkItems(ExtensionRegistry.build(()).policy_engine)
    execution = WorkflowActionContext(
        subject=PolicySubject("mira", strong=True),
        policy=facade._policy,
        work_items=facade,
        namespace="atlas.workplace.workflow-read",
        correlation_id="workflow-read",
    )
    _bind_execution_context(
        facade,
        execution,
        subject=execution.subject,
        namespace=execution.namespace,
        receipt_namespace="workflow:atlas.workplace.workflow-read",
        correlation_id=execution.correlation_id,
    )
    private_task = service_work.create_task("Requester secret", actor="mira", visibility="private")[
        "id"
    ]

    with pytest.raises(PublicError) as raised:
        facade.get_task(private_task, execution.command_context())

    assert raised.value.code == "TASK_NOT_FOUND"


def test_public_read_uses_proved_human_visibility(fresh_db):
    from app.services import crews, engagements
    from app.services import work as service_work

    crew_id = crews.create_crew("Human delivery", actor="mira")["id"]
    engagement = engagements.create_engagement(
        "Human engagement",
        actor="mira",
        visibility="crew",
        crew_id=crew_id,
    )["id"]
    crew_task = service_work.create_task(
        "Crew task",
        actor="mira",
        engagement_id=engagement,
        visibility="crew",
        crew_id=crew_id,
    )["id"]
    private_task = service_work.create_task(
        "Private task",
        actor="mira",
        visibility="private",
    )["id"]
    facade = WorkItems(ExtensionRegistry.build(()).policy_engine)
    strong = _context(
        facade,
        subject=PolicySubject("mira", strong=True),
        namespace="atlas.workplace.human-read",
        read_as_human=True,
    )

    assert facade.get_task(crew_task, strong).engagement_id == engagement
    assert facade.get_task(private_task, strong).id == private_task


def test_idempotent_replay_rechecks_the_current_task_policy(fresh_db):
    from app.services import engagements
    from app.services import work as service_work

    standard = engagements.create_engagement("Standard replay", project_class="standard")["id"]
    regulated = engagements.create_engagement("Regulated replay", project_class="regulated")["id"]

    def deny_regulated_read(request: PolicyInput):
        if request.action == "work.task.read" and request.resource.project_type == "regulated":
            return PolicyDecision(PolicyEffect.DENY, ("Regulated work is closed.",))
        return None

    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("atlas.workplace.regulated", deny_regulated_read),),
    )
    facade = WorkItems(ExtensionRegistry.build((module,)).policy_engine)
    command = CreateTaskCommand(
        title="Replay task",
        engagement_id=standard,
        idempotency_key="replay-1",
    )
    context = _context(facade, project_type="standard")
    created = facade.create_task(command, context)
    service_work.update_task(created.id, engagement_id=regulated)

    with pytest.raises(PublicError) as raised:
        facade.create_task(command, context)

    assert raised.value.code == "POLICY_DENIED"


def test_public_read_serializes_policy_and_returned_relationship(fresh_db):
    from threading import Event, Thread

    from app.services import engagements
    from app.services import work as service_work

    standard = engagements.create_engagement("Standard read", project_class="standard")["id"]
    regulated = engagements.create_engagement("Regulated read", project_class="regulated")["id"]
    task = service_work.create_task("Serialized read", engagement_id=standard)["id"]
    policy_entered = Event()
    writer_attempted = Event()
    writer_done = Event()
    paused = {"value": False}

    def read_policy(request: PolicyInput):
        if request.action != "work.task.read":
            return None
        if request.resource.project_type == "regulated":
            return PolicyDecision(PolicyEffect.DENY, ("Regulated reads are closed.",))
        if not paused["value"]:
            paused["value"] = True
            policy_entered.set()
            assert writer_attempted.wait(5)
            assert writer_done.wait(5)
        return None

    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("atlas.workplace.read", read_policy),),
    )
    facade = WorkItems(ExtensionRegistry.build((module,)).policy_engine)

    def relink() -> None:
        assert policy_entered.wait(5)
        writer_attempted.set()
        service_work.update_task(task, engagement_id=regulated)
        writer_done.set()

    writer = Thread(target=relink)
    writer.start()
    result = facade.get_task(task, _context(facade, project_type="standard"))
    writer.join(5)

    assert result.engagement_id == standard
    assert writer_done.is_set()
    assert fresh_db.query_one("SELECT engagement_id FROM tasks WHERE id = ?", (task,)) == {
        "engagement_id": regulated
    }


def test_public_read_redacts_a_policy_denied_relationship(fresh_db):
    from app.services import engagements
    from app.services import work as service_work

    engagements.create_engagement("Public linked project")
    milestone = service_work.create_milestone(
        "Public denied milestone", project="Public linked project"
    )["id"]
    task = service_work.create_task("Public outer task", milestone_id=milestone)["id"]

    def deny_milestone(request: PolicyInput):
        if (
            request.action == "work.task.read"
            and request.resource.type == "milestone"
            and request.resource.id == str(milestone)
        ):
            return PolicyDecision(PolicyEffect.DENY, ("This milestone is closed.",))
        return None

    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("atlas.workplace.link-policy", deny_milestone),),
    )
    facade = WorkItems(ExtensionRegistry.build((module,)).policy_engine)

    viewed = facade.get_task(task, _context(facade, project_type="standard"))
    updated = facade.update_task(
        UpdateTaskCommand(task_id=task, title="Updated public outer task"),
        _context(facade, project_type="standard"),
    )

    assert viewed.id == task
    assert viewed.milestone_id is None
    assert updated.id == task
    assert updated.milestone_id is None


def test_public_update_policy_uses_target_engagement(fresh_db):
    from app.services import engagements
    from app.services import work as service_work

    standard = engagements.create_engagement("Standard", project_class="standard")["id"]
    regulated = engagements.create_engagement("Regulated", project_class="regulated")["id"]
    task = service_work.create_task("Move me", engagement_id=standard)["id"]

    def deny_regulated(request: PolicyInput):
        if request.action == "work.task.update" and request.resource.project_type == "regulated":
            return PolicyDecision(PolicyEffect.DENY, ("Regulated updates are closed.",))
        return None

    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("atlas.workplace.target", deny_regulated),),
    )
    facade = WorkItems(ExtensionRegistry.build((module,)).policy_engine)
    with pytest.raises(PublicError, match="policy denied") as raised:
        facade.update_task(
            UpdateTaskCommand(task_id=task, engagement_id=regulated),
            _context(facade, project_type="standard"),
        )
    assert raised.value.code == "POLICY_DENIED"


def test_public_create_with_initial_status_requires_update_policy(fresh_db):
    def deny_updates(request: PolicyInput):
        if request.action == "work.task.update":
            return PolicyDecision(PolicyEffect.DENY, ("Task updates are closed.",))
        return None

    registry = ExtensionRegistry.build(
        (
            SkeinModule(
                module_id="atlas.workplace",
                version="1.0.0",
                extension_api="1.0",
                minimum_core="0.2.0",
                maximum_core_exclusive="0.3.0",
                policies=(PolicyContribution("atlas.workplace.no-updates", deny_updates),),
            ),
        )
    )
    facade = WorkItems(registry.policy_engine)

    with pytest.raises(PublicError) as raised:
        facade.create_task(
            CreateTaskCommand(title="Already complete", status="done"),
            _context(facade),
        )

    assert raised.value.code == "POLICY_DENIED"
    assert fresh_db.query_one("SELECT id FROM tasks") is None


def test_public_task_policy_receives_the_requested_fields(fresh_db):
    observed: dict[str, dict] = {}

    def capture(request: PolicyInput):
        if request.action in ("work.task.create", "work.task.update"):
            observed[request.action] = dict(request.resource.attributes)
        return None

    registry = ExtensionRegistry.build(
        (
            SkeinModule(
                module_id="atlas.workplace",
                version="1.0.0",
                extension_api="1.0",
                minimum_core="0.2.0",
                maximum_core_exclusive="0.3.0",
                policies=(PolicyContribution("atlas.workplace.capture", capture),),
            ),
        )
    )
    facade = WorkItems(registry.policy_engine)
    created = facade.create_task(
        CreateTaskCommand(
            title="Requested title",
            description="Requested description",
            priority="high",
        ),
        _context(facade),
    )
    facade.update_task(
        UpdateTaskCommand(task_id=created.id, title="Changed title", priority="urgent"),
        _context(facade),
    )

    assert observed["work.task.create"]["title"] == "Requested title"
    assert observed["work.task.create"]["priority"] == "high"
    assert observed["work.task.update"]["title"] == "Changed title"
    assert observed["work.task.update"]["priority"] == "urgent"


def test_public_update_serializes_policy_decision_and_mutation(fresh_db):
    from app.services import work as service_work

    task = service_work.create_task("Serialized")["id"]
    observed = {"inside_write_transaction": False}

    def workplace_policy(request: PolicyInput):
        if request.action == "work.task.update":
            observed["inside_write_transaction"] = fresh_db._ambient.get() is not None
        return None

    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("atlas.workplace.serialized", workplace_policy),),
    )
    facade = WorkItems(ExtensionRegistry.build((module,)).policy_engine)

    updated = facade.update_task(
        UpdateTaskCommand(task_id=task, title="Policy-bound title"),
        _context(facade, project_type="standard"),
    )

    assert observed["inside_write_transaction"] is True
    assert updated.title == "Policy-bound title"


def test_public_update_policy_uses_target_milestone(fresh_db):
    from app.services import engagements, policy_context
    from app.services import work as service_work

    standard = engagements.create_engagement("Standard", project_class="standard")["id"]
    engagements.create_engagement("Regulated", project_class="regulated")
    regulated_milestone = service_work.create_milestone("Regulated gate", project="Regulated")["id"]
    task = service_work.create_task("Move me", engagement_id=standard)["id"]

    def deny_regulated(request: PolicyInput):
        if request.action == "work.task.update" and request.resource.project_type == "regulated":
            return PolicyDecision(PolicyEffect.DENY, ("Regulated updates are closed.",))
        return None

    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("atlas.workplace.target", deny_regulated),),
    )
    facade = WorkItems(ExtensionRegistry.build((module,)).policy_engine)

    with pytest.raises(PublicError, match="must belong to the same engagement"):
        facade.update_task(
            UpdateTaskCommand(task_id=task, milestone_id=regulated_milestone),
            _context(facade, project_type="standard"),
        )

    # One command can clear the old direct link and set the new milestone.
    # Policy evaluates that coherent final relationship before the write.
    with pytest.raises(PublicError, match="policy denied"):
        facade.update_task(
            UpdateTaskCommand(
                task_id=task,
                engagement_id=-1,
                milestone_id=regulated_milestone,
            ),
            _context(facade, project_type="standard"),
        )
    row = fresh_db.query_one("SELECT engagement_id, milestone_id FROM tasks WHERE id = ?", (task,))
    assert row == {"engagement_id": standard, "milestone_id": None}
    assert policy_context.existing("task", task)["project_type"] == "standard"


def test_public_update_policy_uses_milestone_when_no_direct_engagement(fresh_db):
    from app.services import engagements
    from app.services import work as service_work

    engagements.create_engagement("Regulated", project_class="regulated")
    regulated_milestone = service_work.create_milestone("Regulated gate", project="Regulated")["id"]
    task = service_work.create_task("Unlinked")["id"]

    def deny_regulated(request: PolicyInput):
        if request.action == "work.task.update" and request.resource.project_type == "regulated":
            return PolicyDecision(PolicyEffect.DENY, ("Regulated updates are closed.",))
        return None

    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("atlas.workplace.target", deny_regulated),),
    )
    facade = WorkItems(ExtensionRegistry.build((module,)).policy_engine)
    with pytest.raises(PublicError, match="policy denied"):
        facade.update_task(
            UpdateTaskCommand(task_id=task, milestone_id=regulated_milestone),
            _context(facade, project_type="standard"),
        )
    assert fresh_db.query_one("SELECT milestone_id FROM tasks WHERE id = ?", (task,)) == {
        "milestone_id": None
    }


def test_policy_approval_requirements_are_enforced_on_the_verdict(fresh_db):
    from app.services import review, users

    users.ensure_user("reviewer")
    proposal = review.propose_change(
        "task",
        "create",
        {"title": "approved task"},
        actor="agent",
        approver_groups=("delivery-managers",),
        approver_capabilities=("atlas.approve",),
    )
    with pytest.raises(PermissionError, match="configured workplace approver"):
        review.approve_change(
            proposal["id"],
            actor="reviewer",
            reviewer_groups=("delivery-managers",),
        )
    result = review.approve_change(
        proposal["id"],
        actor="reviewer",
        reviewer_groups=("delivery-managers",),
        reviewer_capabilities=("atlas.approve",),
    )
    assert result["status"] == "approved"
    assert fresh_db.query_one("SELECT title FROM tasks") == {"title": "approved task"}
    assert fresh_db.query_one(
        "SELECT reviewer_qualifications FROM pending_changes WHERE id = ?",
        (proposal["id"],),
    ) == {
        "reviewer_qualifications": (
            '{"matched_groups": ["delivery-managers"], "matched_capabilities": ["atlas.approve"]}'
        )
    }


def test_extension_review_preview_obeys_its_declared_audience(fresh_db):
    from app.services import review, scope

    proposal = review.propose_extension_invocation(
        "tool",
        {"tool": "atlas.workplace.private", "preview": {"count": 1}},
        {"tool": "atlas.workplace.private", "version": "1.0.0"},
        summary="Private Atlas action",
        actor="atlas-agent",
        requested_by="mira",
        review_visibility=scope.PRIVATE,
        review_owner="mira",
    )
    assert review.list_changes(viewer=scope.Viewer("other", True)) == []
    visible = review.list_changes(viewer=scope.Viewer("mira", True))
    assert [item["id"] for item in visible] == [proposal["id"]]
    assert visible[0]["payload"] == {
        "tool": "atlas.workplace.private",
        "preview": {"count": 1},
    }


def test_public_query_does_not_treat_a_forgeable_name_as_private_access(fresh_db):
    registry = ExtensionRegistry.build(())
    facade = WorkItems(registry.policy_engine)
    created = facade.create_task(
        CreateTaskCommand(title="private", visibility="private"),
        _context(facade, subject=PolicySubject("mira"), namespace="atlas.workplace.private"),
    )
    with pytest.raises(PublicError) as raised:
        facade.get_task(
            created.id,
            _context(facade, subject=PolicySubject("mira"), namespace="atlas.workplace.private"),
        )
    assert raised.value.code == "TASK_NOT_FOUND"


def test_event_delivery_retries_and_uses_event_id_as_the_receipt(fresh_db):
    calls: list[str] = []

    def subscriber(event, _context):
        calls.append(event.event_id)
        if len(calls) == 1:
            raise RuntimeError("temporary remote error")

    contribution = _event(
        "atlas.workplace.sync",
        subscriber,
        ("skein.task.created",),
    )
    facade = WorkItems(ExtensionRegistry.build(()).policy_engine)
    event_task = facade.create_task(CreateTaskCommand(title="delivery"), _context(facade))

    context = _event_context()
    assert dispatch_events((contribution,), context) == {
        "delivered": 0,
        "failed": 1,
        "dead": 0,
    }
    assert dispatch_events((contribution,), context) == {
        "delivered": 1,
        "failed": 0,
        "dead": 0,
    }
    assert dispatch_events((contribution,), context) == {
        "delivered": 0,
        "failed": 0,
        "dead": 0,
    }
    assert len(calls) == 2
    assert calls[0] == calls[1]
    delivery = fresh_db.query_one("SELECT * FROM extension_event_deliveries")
    assert delivery["event_id"] == calls[0]
    assert event_task.id > 0


def test_workspace_subscribers_do_not_receive_private_events(fresh_db):
    calls = []
    contribution = _event(
        "atlas.workplace.sync",
        lambda event, _context: calls.append(event),
        ("skein.task.created",),
    )
    facade = WorkItems(ExtensionRegistry.build(()).policy_engine)
    facade.create_task(
        CreateTaskCommand(title="private", visibility="private"),
        _context(facade, subject=PolicySubject("mira")),
    )
    assert dispatch_events((contribution,), _event_context())["delivered"] == 1
    assert calls == []


def test_each_event_subscriber_has_its_own_retry_budget(fresh_db):
    strict_calls: list[str] = []
    tolerant_calls: list[str] = []

    def strict(event, _context):
        strict_calls.append(event.event_id)
        raise RuntimeError("permanent")

    def tolerant(event, _context):
        tolerant_calls.append(event.event_id)
        if len(tolerant_calls) == 1:
            raise RuntimeError("temporary")

    contributions = (
        _event(
            "atlas.workplace.strict",
            strict,
            ("skein.task.created",),
            max_attempts=1,
        ),
        _event(
            "atlas.workplace.tolerant",
            tolerant,
            ("skein.task.created",),
            max_attempts=3,
        ),
    )
    facade = WorkItems(ExtensionRegistry.build(()).policy_engine)
    facade.create_task(
        CreateTaskCommand(title="delivery"),
        _context(facade),
    )
    context = _event_context()
    assert dispatch_events(contributions, context) == {
        "delivered": 0,
        "failed": 1,
        "dead": 0,
    }
    assert dispatch_events(contributions, context) == {
        "delivered": 0,
        "failed": 0,
        "dead": 1,
    }
    assert len(strict_calls) == 1
    assert len(tolerant_calls) == 2
    attempts = fresh_db.query(
        "SELECT subscriber, attempts, status FROM extension_event_attempts ORDER BY subscriber"
    )
    assert attempts == [{"subscriber": "atlas.workplace.strict", "attempts": 1, "status": "dead"}]


def test_event_policy_denial_stops_the_handler_before_external_effects(fresh_db):
    calls: list[str] = []

    def deny_event(request: PolicyInput):
        if request.action == "atlas.events.deliver":
            return PolicyDecision(PolicyEffect.DENY, ("Delivery is paused.",))
        return None

    registry = ExtensionRegistry.build(
        (
            SkeinModule(
                module_id="atlas.workplace",
                version="1.0.0",
                extension_api="1.0",
                minimum_core="0.2.0",
                maximum_core_exclusive="0.3.0",
                policies=(PolicyContribution("atlas.workplace.event-policy", deny_event),),
                service_identities=(
                    ServiceIdentityContribution(
                        "atlas.workplace.event-identity",
                        "atlas-events",
                    ),
                ),
            ),
        )
    )
    facade = WorkItems(registry.policy_engine)
    facade.create_task(
        CreateTaskCommand(title="delivery"),
        _context(facade),
    )
    contribution = _event(
        "atlas.workplace.delivery",
        lambda event, _context: calls.append(event.event_id),
        ("skein.task.created",),
    )
    result = dispatch_events(
        (contribution,),
        EventExecutionContext(
            registry.policy_engine,
            WorkItems(registry.policy_engine),
            registry.service_subject,
        ),
    )
    assert result == {"delivered": 0, "failed": 0, "dead": 1}
    assert calls == []
    assert fresh_db.query_one("SELECT last_error_code FROM extension_outbox") == {
        "last_error_code": "POLICY_DENIED"
    }


def test_event_policy_receives_the_task_project_type(fresh_db):
    from app.services import engagements

    observed: list[str] = []
    calls: list[str] = []

    def deny_regulated_event(request: PolicyInput):
        if request.action == "atlas.events.deliver":
            observed.append(request.resource.project_type)
            if request.resource.project_type == "regulated":
                return PolicyDecision(PolicyEffect.DENY, ("Regulated delivery is closed.",))
        return None

    registry = ExtensionRegistry.build(
        (
            SkeinModule(
                module_id="atlas.workplace",
                version="1.0.0",
                extension_api="1.0",
                minimum_core="0.2.0",
                maximum_core_exclusive="0.3.0",
                policies=(
                    PolicyContribution(
                        "atlas.workplace.regulated-events",
                        deny_regulated_event,
                    ),
                ),
                service_identities=(
                    ServiceIdentityContribution(
                        "atlas.workplace.event-identity",
                        "atlas-events",
                    ),
                ),
            ),
        )
    )
    engagement = engagements.create_engagement(
        "Regulated event project",
        project_class="regulated",
    )["id"]
    facade = WorkItems(registry.policy_engine)
    facade.create_task(
        CreateTaskCommand(title="Regulated event", engagement_id=engagement),
        _context(facade),
    )
    contribution = _event(
        "atlas.workplace.delivery",
        lambda event, _context: calls.append(event.event_id),
        ("skein.task.created",),
    )

    result = dispatch_events(
        (contribution,),
        EventExecutionContext(
            registry.policy_engine,
            WorkItems(registry.policy_engine),
            registry.service_subject,
        ),
    )

    assert result == {"delivered": 0, "failed": 0, "dead": 1}
    assert observed == ["regulated"]
    assert calls == []


def test_write_event_timeout_is_terminal_completion_unknown(fresh_db):
    calls: list[str] = []

    def slow(event, _context):
        time.sleep(0.05)
        calls.append(event.event_id)

    facade = WorkItems(ExtensionRegistry.build(()).policy_engine)
    facade.create_task(
        CreateTaskCommand(title="delivery"),
        _context(facade),
    )
    contribution = _event(
        "atlas.workplace.slow",
        slow,
        ("skein.task.created",),
        timeout_seconds=0.01,
    )
    assert dispatch_events((contribution,), _event_context()) == {
        "delivered": 0,
        "failed": 0,
        "dead": 1,
    }
    time.sleep(0.06)
    assert len(calls) == 1
    assert fresh_db.query_one("SELECT last_error_code FROM extension_outbox") == {
        "last_error_code": "COMPLETION_UNKNOWN"
    }


def test_async_event_handlers_are_rejected_before_startup():
    async def subscriber(_event, _context):
        return None

    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        service_identities=(
            ServiceIdentityContribution(
                "atlas.workplace.event-identity",
                "atlas-events",
            ),
        ),
        events=(
            _event(
                "atlas.workplace.async-event",
                subscriber,
                ("skein.task.created",),
            ),
        ),
    )
    with pytest.raises(ExtensionValidationError, match="synchronous handler"):
        ExtensionRegistry.build((module,))


def test_extension_store_owns_its_schema_and_migrations(fresh_db, tmp_path):
    store = ExtensionStore(tmp_path / "atlas.db")
    migrations = (
        ExtensionMigration(
            1,
            "create-links",
            (
                "CREATE TABLE work_links"
                " (skein_task_id INTEGER PRIMARY KEY, external_id TEXT NOT NULL UNIQUE)",
            ),
        ),
        ExtensionMigration(
            2,
            "add-classification",
            ("ALTER TABLE work_links ADD COLUMN classification TEXT NOT NULL DEFAULT ''",),
        ),
    )
    store.migrate(migrations)
    store.migrate(migrations)
    store.execute(
        "INSERT INTO work_links (skein_task_id, external_id, classification) VALUES (?, ?, ?)",
        (41, "ATLAS-9", "internal"),
    )
    assert store.query_one("SELECT * FROM work_links WHERE skein_task_id = ?", (41,)) == {
        "skein_task_id": 41,
        "external_id": "ATLAS-9",
        "classification": "internal",
    }
    versions = store.query("SELECT version, name FROM extension_schema_version ORDER BY version")
    assert versions == [
        {"version": 1, "name": "create-links"},
        {"version": 2, "name": "add-classification"},
    ]
    assert (
        fresh_db.query_one("SELECT 1 AS present FROM sqlite_master WHERE name = 'work_links'")
        is None
    )
    changed = (
        replace(migrations[0], statements=("CREATE TABLE different (id INTEGER)",)),
        migrations[1],
    )
    with pytest.raises(ValueError, match="changed its statements"):
        store.migrate(changed)


def test_extension_store_refuses_both_core_database_paths(fresh_db):
    from app import config, db

    for path in (db.DB_PATH, config.PRIVATE_DB_PATH):
        with pytest.raises(ValueError, match="core database path"):
            ExtensionStore(path).migrate(())


def test_composition_applies_extension_migrations_before_routes(fresh_db, tmp_path):
    store = ExtensionStore(tmp_path / "atlas.db")
    router = APIRouter(prefix="/api/extensions/atlas.workplace")

    @router.get("/links")
    def links():
        return store.query("SELECT external_id FROM work_links")

    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        migrations=(
            MigrationContribution(
                "atlas.workplace.data",
                store,
                (
                    ExtensionMigration(
                        1,
                        "create-links",
                        ("CREATE TABLE work_links (external_id TEXT NOT NULL)",),
                    ),
                ),
            ),
        ),
        routes=(
            RouteContribution(
                "atlas.workplace.routes",
                router,
                (
                    RouteOperationContribution(
                        "GET",
                        "/api/extensions/atlas.workplace/links",
                        "atlas.links.read",
                        PolicyResource("atlas-link"),
                        "read",
                        "low",
                    ),
                ),
            ),
        ),
    )
    settings = replace(AppSettings.from_config(), scheduler_enabled=False)
    with TestClient(create_app(settings, (module,)), headers={"X-User": "tester"}) as client:
        assert client.get("/api/extensions/atlas.workplace/links").json() == []


def test_invalid_event_and_migration_contracts_are_rejected(tmp_path):
    store = ExtensionStore(tmp_path / "atlas.db")
    with pytest.raises(ExtensionValidationError, match="event type"):
        ExtensionRegistry.build(
            (
                SkeinModule(
                    module_id="atlas.workplace",
                    version="1.0.0",
                    extension_api="1.0",
                    minimum_core="0.2.0",
                    maximum_core_exclusive="0.3.0",
                    events=(
                        _event(
                            "atlas.workplace.empty",
                            lambda _event, _context: None,
                            (),
                        ),
                    ),
                ),
            )
        )
    with pytest.raises(ExtensionValidationError, match="ascending versions"):
        ExtensionRegistry.build(
            (
                SkeinModule(
                    module_id="atlas.workplace",
                    version="1.0.0",
                    extension_api="1.0",
                    minimum_core="0.2.0",
                    maximum_core_exclusive="0.3.0",
                    migrations=(
                        MigrationContribution(
                            "atlas.workplace.data",
                            store,
                            (
                                ExtensionMigration(2, "second", ("SELECT 1",)),
                                ExtensionMigration(1, "first", ("SELECT 1",)),
                            ),
                        ),
                    ),
                ),
            )
        )
