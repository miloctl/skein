"""Typed work commands and queries for trusted extensions."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Annotated, Any
from weakref import ReferenceType, WeakKeyDictionary, ref

from pydantic import BaseModel, ConfigDict, Field

from .. import db
from ..extensions.policy import (
    PolicyEffect,
    PolicyEngine,
    PolicyInput,
    PolicyResource,
    PolicySubject,
)
from ..services import scope, work
from .errors import PublicError


@dataclass(frozen=True)
class CommandContext:
    subject: PolicySubject
    origin: str
    namespace: str = ""
    receipt_namespace: str = ""
    correlation_id: str = ""
    project_type: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    actor: str = ""
    actor_kind: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))

    @property
    def execution_actor(self) -> str:
        return self.actor or self.subject.name

    @property
    def execution_actor_kind(self) -> str:
        return self.actor_kind or self.subject.kind


@dataclass(frozen=True)
class _ExecutionGrant:
    subject: PolicySubject
    namespace: str
    receipt_namespace: str
    correlation_id: str
    actor: str
    actor_kind: str
    read_name: str
    read_strong: bool


@dataclass(frozen=True)
class _IssuedCommand:
    signature: tuple[Any, ...]
    read_name: str
    read_strong: bool


_RegistryPayload = _ExecutionGrant | _IssuedCommand | tuple[Any, ...]
_IdentityRegistry = dict[int, tuple[ReferenceType[Any], _RegistryPayload]]
_BOUND_EXECUTIONS: WeakKeyDictionary[object, _IdentityRegistry] = WeakKeyDictionary()
_ISSUED_COMMANDS: WeakKeyDictionary[object, _IdentityRegistry] = WeakKeyDictionary()


def _remember_identity(
    registry: _IdentityRegistry, value: object, payload: _RegistryPayload
) -> None:
    """Retain authority by object identity without retaining the request object."""
    identity = id(value)

    def forget(reference: ReferenceType[Any]) -> None:
        current = registry.get(identity)
        if current is not None and current[0] is reference:
            registry.pop(identity, None)

    reference = ref(value, forget)
    registry[identity] = (reference, payload)


def _identity_payload(registry: _IdentityRegistry, value: object) -> _RegistryPayload | None:
    current = registry.get(id(value))
    if current is None or current[0]() is not value:
        return None
    return current[1]


def _command_signature(context: CommandContext) -> tuple[Any, ...]:
    return (
        context.subject,
        context.origin,
        context.namespace,
        context.receipt_namespace,
        context.correlation_id,
        context.project_type,
        deepcopy(dict(context.attributes)),
        context.actor,
        context.actor_kind,
    )


def _bind_execution_context[ExecutionContextT](
    work_items: WorkItems,
    context: ExecutionContextT,
    *,
    subject: PolicySubject,
    namespace: str,
    receipt_namespace: str,
    correlation_id: str,
    actor: str = "",
    actor_kind: str = "",
    read_name: str = "",
    read_strong: bool = False,
) -> ExecutionContextT:
    """Bind a core-created adapter object to one immutable provenance grant.

    This helper is an internal composition function. It is not a method on the
    public facade supplied to extension handlers.
    """
    if not namespace.strip():
        raise ValueError("A command namespace is required.")
    if not receipt_namespace.strip():
        raise ValueError("A command receipt namespace is required.")
    grant = _ExecutionGrant(
        subject,
        namespace,
        receipt_namespace,
        correlation_id,
        actor or subject.name,
        actor_kind or subject.kind,
        read_name,
        read_strong,
    )
    registry = _BOUND_EXECUTIONS.setdefault(work_items, {})
    _remember_identity(registry, context, grant)
    return context


class CreateTaskCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: Annotated[str, Field(max_length=work.TITLE_LEN)]
    description: Annotated[str, Field(max_length=work.DESCRIPTION_LEN)] = ""
    milestone_id: int = 0
    engagement_id: int = 0
    assignee: Annotated[str, Field(max_length=64)] = ""
    priority: Annotated[str, Field(max_length=10)] = "medium"
    due_date: Annotated[str, Field(max_length=10)] = ""
    visibility: str = scope.WORKSPACE
    crew_id: int = 0
    status: str = "todo"
    idempotency_key: Annotated[str, Field(max_length=200)] = ""


class UpdateTaskCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: int
    status: str | None = None
    assignee: str | None = None
    priority: str | None = None
    due_date: str | None = None
    description: Annotated[str | None, Field(max_length=work.DESCRIPTION_LEN)] = None
    title: Annotated[str | None, Field(max_length=work.TITLE_LEN)] = None
    committed_week: str | None = None
    waiting_on: str | None = None
    milestone_id: int | None = None
    engagement_id: int | None = None
    forge_url: str | None = None


class TaskView(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    title: str
    description: str
    status: str
    assignee: str
    priority: str
    due_date: str | None
    milestone_id: int | None
    engagement_id: int | None
    visibility: str
    crew_id: int | None
    origin: str
    created_by: str
    created_at: str
    updated_at: str


class WorkItems:
    """The public facade for task commands and queries."""

    def __init__(self, policy: PolicyEngine) -> None:
        self._policy = policy

    def _issue_context(
        self,
        execution_context: object,
        *,
        project_type: str = "",
        attributes: dict[str, Any] | None = None,
    ) -> CommandContext:
        """Create the provenance context used by one composed execution boundary."""
        grant = _identity_payload(_BOUND_EXECUTIONS.get(self, {}), execution_context)
        if not isinstance(grant, _ExecutionGrant):
            raise PublicError(
                "COMMAND_CONTEXT_REQUIRED",
                "Use the command context from the composed execution boundary.",
                status_code=403,
            )
        context = CommandContext(
            subject=grant.subject,
            origin=f"extension:{grant.namespace}",
            namespace=grant.namespace,
            receipt_namespace=grant.receipt_namespace,
            correlation_id=grant.correlation_id,
            project_type=project_type,
            attributes=attributes or {},
            actor=grant.actor,
            actor_kind=grant.actor_kind,
        )
        issued = _ISSUED_COMMANDS.setdefault(self, {})
        _remember_identity(
            issued,
            context,
            _IssuedCommand(_command_signature(context), grant.read_name, grant.read_strong),
        )
        return context

    def _require_issued_context(self, context: CommandContext) -> None:
        signature = _identity_payload(_ISSUED_COMMANDS.get(self, {}), context)
        if not isinstance(signature, _IssuedCommand) or signature.signature != _command_signature(
            context
        ):
            raise PublicError(
                "COMMAND_CONTEXT_REQUIRED",
                "Use the command context from the composed execution boundary.",
                status_code=403,
            )

    def _authorize(
        self,
        context: CommandContext,
        action: str,
        resource: PolicyResource,
    ) -> None:
        decision = self._policy.decide(
            PolicyInput(
                subject=context.subject,
                action=action,
                resource=resource,
                origin=context.origin,
                context=context.attributes,
            )
        )
        if decision.effect == PolicyEffect.DENY:
            raise PublicError(
                "POLICY_DENIED",
                "The policy denied this action.",
                status_code=403,
                obligations=decision.obligations,
            )
        if decision.effect == PolicyEffect.REVIEW:
            obligations = (
                *decision.obligations,
                *(f"approver-group:{group}" for group in decision.approver_groups),
                *(
                    f"approver-capability:{capability}"
                    for capability in decision.approver_capabilities
                ),
            )
            raise PublicError(
                "REVIEW_REQUIRED",
                "A policy review is required before this action.",
                status_code=409,
                obligations=obligations,
            )

    def _viewer(self, context: CommandContext) -> scope.Viewer:
        """Return only the read authority granted by the composition boundary."""
        issued = _identity_payload(_ISSUED_COMMANDS.get(self, {}), context)
        if not isinstance(issued, _IssuedCommand):
            return scope.NOBODY
        return scope.Viewer(issued.read_name, issued.read_strong)

    @staticmethod
    def _visible_engagement(engagement_id: int, viewer: scope.Viewer) -> dict | None:
        visible, params = scope.visible_filter(viewer, "engagements", "engagement")
        return db.query_one(
            f"SELECT engagement.id, engagement.project_class,"  # noqa: S608 -- scope emits only bound marks
            " engagement.visibility, engagement.crew_id"
            " FROM engagements engagement"
            f" WHERE engagement.id = ? AND {visible}",
            (engagement_id, *params),
        )

    @staticmethod
    def _visible_milestone(milestone_id: int, viewer: scope.Viewer) -> dict | None:
        milestone_visible, milestone_params = scope.visible_filter(
            viewer, "milestones", "milestone"
        )
        engagement_visible, engagement_params = scope.visible_filter(
            viewer, "engagements", "engagement"
        )
        row = db.query_one(
            f"SELECT milestone.id, milestone.engagement_id,"  # noqa: S608 -- scope emits only bound marks
            " milestone.visibility, milestone.crew_id,"
            " engagement.id AS visible_engagement_id, engagement.project_class,"
            " engagement.visibility AS engagement_visibility,"
            " engagement.crew_id AS engagement_crew_id"
            " FROM milestones milestone LEFT JOIN engagements engagement"
            " ON engagement.id = milestone.engagement_id"
            f" AND {engagement_visible}"
            f" WHERE milestone.id = ? AND {milestone_visible}",
            (*engagement_params, milestone_id, *milestone_params),
        )
        if not row or (row["engagement_id"] and not row["visible_engagement_id"]):
            return None
        return row

    def _relationship_context(
        self,
        engagement_id: int,
        milestone_id: int,
        viewer: scope.Viewer,
        *,
        fallback_project_type: str = "",
        error_code: str,
        conceal_as_task: int = 0,
        child_visibility: str = scope.WORKSPACE,
        child_crew_id: int | None = None,
    ) -> dict[str, Any]:
        """Resolve all task links without exposing an unreadable parent."""
        engagement = None
        milestone = None
        if engagement_id:
            engagement = self._visible_engagement(engagement_id, viewer)
            if engagement is None:
                table, row_id = (
                    ("tasks", conceal_as_task)
                    if conceal_as_task
                    else ("engagements", engagement_id)
                )
                raise PublicError(
                    error_code,
                    scope.missing_text(table, row_id),
                    status_code=404 if conceal_as_task else 400,
                )
        if milestone_id:
            milestone = self._visible_milestone(milestone_id, viewer)
            if milestone is None:
                table, row_id = (
                    ("tasks", conceal_as_task) if conceal_as_task else ("milestones", milestone_id)
                )
                raise PublicError(
                    error_code,
                    scope.missing_text(table, row_id),
                    status_code=404 if conceal_as_task else 400,
                )

        def require_containment(parent: dict, *, milestone_parent: bool = False) -> None:
            parent_visibility = (
                parent.get("engagement_visibility")
                if milestone_parent
                else parent.get("visibility")
            )
            parent_crew_id = (
                parent.get("engagement_crew_id") if milestone_parent else parent.get("crew_id")
            )
            if parent_visibility and not scope.relationship_contains(
                str(parent_visibility),
                parent_crew_id,
                child_visibility,
                child_crew_id,
            ):
                if conceal_as_task:
                    raise PublicError(
                        error_code,
                        scope.missing_text("tasks", conceal_as_task),
                        status_code=404,
                    )
                raise PublicError(
                    error_code,
                    "A task cannot be visible to more people than its linked work."
                    " Use the same or a narrower visibility.",
                )

        if engagement is not None:
            require_containment(engagement)
        if milestone is not None:
            require_containment(milestone)
            if milestone.get("visible_engagement_id"):
                require_containment(milestone, milestone_parent=True)
                if engagement is not None and int(engagement["id"]) != int(
                    milestone["visible_engagement_id"]
                ):
                    if conceal_as_task:
                        raise PublicError(
                            error_code,
                            scope.missing_text("tasks", conceal_as_task),
                            status_code=404,
                        )
                    raise PublicError(
                        error_code,
                        "A task's milestone and engagement must belong to the same engagement.",
                    )

        effective_engagement = engagement_id or int((milestone or {}).get("engagement_id") or 0)
        project_type = fallback_project_type
        if engagement is not None:
            project_type = str(engagement["project_class"] or "")
        elif milestone is not None and milestone["project_class"]:
            project_type = str(milestone["project_class"])
        attributes: dict[str, Any] = {"project_type": project_type}
        if effective_engagement:
            attributes["engagement_id"] = effective_engagement
        if milestone_id:
            attributes["milestone_id"] = milestone_id
        return attributes

    def _task_state(
        self, task_id: int, context: CommandContext
    ) -> tuple[dict, scope.Viewer, dict[str, Any]]:
        viewer = self._viewer(context)
        visible, params = scope.visible_filter(viewer, "tasks", "task")
        row = db.query_one(
            f"SELECT task.* FROM tasks task WHERE task.id = ? AND {visible}",  # noqa: S608 -- scope emits only bound marks
            (task_id, *params),
        )
        if row is None:
            raise PublicError(
                "TASK_NOT_FOUND",
                scope.missing_text("tasks", task_id),
                status_code=404,
            )
        attributes = self._relationship_context(
            int(row["engagement_id"] or 0),
            int(row["milestone_id"] or 0),
            viewer,
            error_code="TASK_NOT_FOUND",
            conceal_as_task=task_id,
            child_visibility=str(row["visibility"]),
            child_crew_id=row["crew_id"],
        )
        attributes.update(
            classification=str(row["visibility"] or ""),
            crew_id=str(row["crew_id"] or ""),
        )
        return row, viewer, attributes

    def get_task(self, task_id: int, context: CommandContext) -> TaskView:
        self._require_issued_context(context)
        # One snapshot binds relationship visibility, policy, and the view
        # returned to the extension. A concurrent relink cannot change the
        # project after policy evaluates it.
        with db.read_transaction():
            _row, viewer, attributes = self._task_state(task_id, context)
            self._authorize(
                context,
                "work.task.read",
                PolicyResource(
                    "task",
                    str(task_id),
                    project_type=attributes["project_type"],
                    classification=attributes["classification"],
                    attributes=attributes,
                ),
            )
            return self._task_view(task_id, viewer=viewer)

    def create_task(self, command: CreateTaskCommand, context: CommandContext) -> TaskView:
        # Keep linked-project resolution, policy, idempotency, and creation in
        # one serialized write boundary.
        with db.transaction():
            return self._create_task_locked(command, context)

    def _create_task_locked(self, command: CreateTaskCommand, context: CommandContext) -> TaskView:
        self._require_issued_context(context)
        viewer = self._viewer(context)
        link_attributes = self._relationship_context(
            command.engagement_id,
            command.milestone_id,
            viewer,
            fallback_project_type=context.project_type,
            error_code="TASK_CREATE_REJECTED",
            child_visibility=command.visibility,
            child_crew_id=command.crew_id or None,
        )
        requested_attributes = {
            **command.model_dump(exclude={"idempotency_key"}, mode="json"),
            **link_attributes,
            "classification": command.visibility,
            "crew_id": command.crew_id,
        }
        self._authorize(
            context,
            "work.task.create",
            PolicyResource(
                "task",
                project_type=link_attributes["project_type"],
                classification=command.visibility,
                attributes=requested_attributes,
            ),
        )
        if command.status != "todo":
            self._authorize(
                context,
                "work.task.update",
                PolicyResource(
                    "task",
                    project_type=link_attributes["project_type"],
                    classification=command.visibility,
                    attributes=requested_attributes,
                ),
            )
        try:
            with db.transaction():
                prior = None
                if command.idempotency_key:
                    prior = db.query_one(
                        "SELECT result_id FROM extension_command_receipts"
                        " WHERE namespace = ? AND idempotency_key = ?",
                        (context.receipt_namespace, command.idempotency_key),
                    )
                if prior:
                    return self.get_task(int(prior["result_id"]), context)
                values = command.model_dump(exclude={"status", "idempotency_key"})
                result = work.create_task(
                    **values,
                    actor=context.execution_actor,
                    origin=context.origin,
                    correlation_id=context.correlation_id,
                    event_actor_kind=context.execution_actor_kind,
                )
                if command.status != "todo":
                    work.update_task(
                        result["id"],
                        status=command.status,
                        actor=context.execution_actor,
                        origin=context.origin,
                        note=f" through {context.origin}",
                        correlation_id=context.correlation_id,
                        event_actor_kind=context.execution_actor_kind,
                    )
                if command.idempotency_key:
                    db.execute(
                        "INSERT INTO extension_command_receipts"
                        " (namespace, idempotency_key, result_type, result_id, created_at)"
                        " VALUES (?, ?, 'task', ?, ?)",
                        (
                            context.receipt_namespace,
                            command.idempotency_key,
                            result["id"],
                            db.now(),
                        ),
                    )
                # This is the result of the caller's own write, not a query.
                # Do not manufacture a read-capable Viewer for a machine or a
                # weak identity merely to populate the command result.
                return self._written_task_view(result["id"])
        except PublicError:
            raise
        except (ValueError, PermissionError) as exc:
            raise PublicError("TASK_CREATE_REJECTED", str(exc)) from exc

    def update_task(self, command: UpdateTaskCommand, context: CommandContext) -> TaskView:
        self._require_issued_context(context)
        try:
            # BEGIN IMMEDIATE serializes the authoritative target lookup,
            # policy decision, and mutation. A concurrent relink cannot move
            # the task under a stricter policy between the check and write.
            with db.transaction():
                current_row, viewer, current_attributes = self._task_state(command.task_id, context)
                self._authorize(
                    context,
                    "work.task.read",
                    PolicyResource(
                        "task",
                        str(command.task_id),
                        project_type=current_attributes["project_type"],
                        classification=current_attributes["classification"],
                        attributes=current_attributes,
                    ),
                )
                changes = command.model_dump(exclude={"task_id"}, exclude_none=True)
                if not changes:
                    raise PublicError("EMPTY_COMMAND", "The command does not contain a change.")
                engagement_id = int(current_row["engagement_id"] or 0)
                milestone_id = int(current_row["milestone_id"] or 0)
                if changes.get("engagement_id"):
                    engagement_id = max(int(changes["engagement_id"]), 0)
                if changes.get("milestone_id"):
                    milestone_id = max(int(changes["milestone_id"]), 0)
                attributes = self._relationship_context(
                    engagement_id,
                    milestone_id,
                    viewer,
                    error_code="TASK_UPDATE_REJECTED",
                    child_visibility=str(current_row["visibility"]),
                    child_crew_id=current_row["crew_id"],
                )
                attributes.update(
                    classification=str(current_row["visibility"] or ""),
                    crew_id=str(current_row["crew_id"] or ""),
                )
                requested_attributes = {**changes, **attributes}
                self._authorize(
                    context,
                    "work.task.update",
                    PolicyResource(
                        "task",
                        str(command.task_id),
                        project_type=attributes["project_type"],
                        classification=attributes["classification"],
                        attributes=requested_attributes,
                    ),
                )
                work.update_task(
                    command.task_id,
                    **changes,
                    actor=context.execution_actor,
                    origin=context.origin,
                    note=f" through {context.origin}",
                    correlation_id=context.correlation_id,
                    event_actor_kind=context.execution_actor_kind,
                )
                result = self._task_view(command.task_id, viewer=viewer)
        except PublicError:
            raise
        except db.NotFound as exc:
            raise PublicError("TASK_NOT_FOUND", str(exc), status_code=404) from exc
        except PermissionError as exc:
            raise PublicError("TASK_UPDATE_FORBIDDEN", str(exc), status_code=403) from exc
        except ValueError as exc:
            raise PublicError("TASK_UPDATE_REJECTED", str(exc)) from exc
        return result

    @staticmethod
    def _task_view(task_id: int, *, viewer: scope.Viewer = scope.NOBODY) -> TaskView:
        try:
            return TaskView.model_validate(work.get_task(task_id, viewer))
        except db.NotFound as exc:
            raise PublicError("TASK_NOT_FOUND", str(exc), status_code=404) from exc

    @staticmethod
    def _written_task_view(task_id: int) -> TaskView:
        """Return the exact row created by this command as its write receipt."""
        row = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if row is None:
            raise PublicError(
                "TASK_NOT_FOUND",
                scope.missing_text("tasks", task_id),
                status_code=404,
            )
        return TaskView.model_validate(row)
