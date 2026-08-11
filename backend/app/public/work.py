"""Typed work commands and queries for trusted extensions."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .. import db
from ..extensions.policy import (
    PolicyEffect,
    PolicyEngine,
    PolicyInput,
    PolicyResource,
    PolicySubject,
)
from ..services import policy_context, scope, work
from .errors import PublicError


@dataclass(frozen=True)
class CommandContext:
    subject: PolicySubject
    origin: str
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


class CreateTaskCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str = Field(max_length=work.TITLE_LEN)
    description: str = Field("", max_length=work.DESCRIPTION_LEN)
    milestone_id: int = 0
    engagement_id: int = 0
    assignee: str = Field("", max_length=64)
    priority: str = Field("medium", max_length=10)
    due_date: str = Field("", max_length=10)
    visibility: str = scope.WORKSPACE
    crew_id: int = 0
    status: str = "todo"
    idempotency_key: str = Field("", max_length=200)


class UpdateTaskCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: int
    status: str | None = None
    assignee: str | None = None
    priority: str | None = None
    due_date: str | None = None
    description: str | None = Field(None, max_length=work.DESCRIPTION_LEN)
    title: str | None = Field(None, max_length=work.TITLE_LEN)
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

    def get_task(self, task_id: int, context: CommandContext) -> TaskView:
        attributes = work.task_policy_context(task_id)
        self._authorize(
            context,
            "work.task.read",
            PolicyResource(
                "task",
                str(task_id),
                project_type=str(attributes.get("project_type") or ""),
                classification=str(attributes.get("classification") or ""),
                attributes=attributes,
            ),
        )
        try:
            row = work.get_task(task_id, scope.NOBODY)
        except db.NotFound as exc:
            raise PublicError("TASK_NOT_FOUND", str(exc), status_code=404) from exc
        return TaskView.model_validate(row)

    def create_task(self, command: CreateTaskCommand, context: CommandContext) -> TaskView:
        project_type = context.project_type
        link_attributes: dict[str, Any] = {}
        if command.engagement_id:
            row = db.query_one(
                "SELECT project_class, visibility FROM engagements"
                " WHERE id = ? AND visibility = 'workspace'",
                (command.engagement_id,),
            )
            if row:
                project_type = str(row["project_class"] or "")
                link_attributes["engagement_id"] = command.engagement_id
        elif command.milestone_id:
            row = db.query_one(
                "SELECT e.project_class, m.engagement_id FROM milestones m"
                " LEFT JOIN engagements e ON e.id = m.engagement_id"
                " WHERE m.id = ? AND m.visibility = 'workspace'",
                (command.milestone_id,),
            )
            if row and row["project_class"]:
                project_type = str(row["project_class"])
                link_attributes["engagement_id"] = int(row["engagement_id"])
        self._authorize(
            context,
            "work.task.create",
            PolicyResource(
                "task",
                project_type=project_type,
                classification=command.visibility,
                attributes=link_attributes,
            ),
        )
        try:
            with db.transaction():
                prior = None
                if command.idempotency_key:
                    prior = db.query_one(
                        "SELECT result_id FROM extension_command_receipts"
                        " WHERE namespace = ? AND idempotency_key = ?",
                        (context.origin, command.idempotency_key),
                    )
                if prior:
                    return self._task_view(int(prior["result_id"]), actor=context.execution_actor)
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
                            context.origin,
                            command.idempotency_key,
                            result["id"],
                            db.now(),
                        ),
                    )
                return self._task_view(result["id"], actor=context.execution_actor)
        except PublicError:
            raise
        except (ValueError, PermissionError) as exc:
            raise PublicError("TASK_CREATE_REJECTED", str(exc)) from exc

    def update_task(self, command: UpdateTaskCommand, context: CommandContext) -> TaskView:
        current = self.get_task(command.task_id, context)
        changes = command.model_dump(exclude={"task_id"}, exclude_none=True)
        if not changes:
            raise PublicError("EMPTY_COMMAND", "The command does not contain a change.")
        # Use the same target-state resolver as REST, agent writes, and review
        # resume. A milestone relink or a direct-engagement unlink can change
        # the governing project even when engagement_id is absent or negative.
        attributes = policy_context.for_change("task", command.task_id, changes)
        self._authorize(
            context,
            "work.task.update",
            PolicyResource(
                "task",
                str(command.task_id),
                project_type=str(attributes.get("project_type") or ""),
                classification=str(attributes.get("classification") or current.visibility),
                attributes=attributes,
            ),
        )
        try:
            work.update_task(
                command.task_id,
                **changes,
                actor=context.execution_actor,
                origin=context.origin,
                note=f" through {context.origin}",
                correlation_id=context.correlation_id,
                event_actor_kind=context.execution_actor_kind,
            )
        except PublicError:
            raise
        except db.NotFound as exc:
            raise PublicError("TASK_NOT_FOUND", str(exc), status_code=404) from exc
        except PermissionError as exc:
            raise PublicError("TASK_UPDATE_FORBIDDEN", str(exc), status_code=403) from exc
        except ValueError as exc:
            raise PublicError("TASK_UPDATE_REJECTED", str(exc)) from exc
        return self._task_view(command.task_id)

    @staticmethod
    def _task_view(task_id: int, *, actor: str = "") -> TaskView:
        try:
            viewer = scope.Viewer.for_actor(actor) if actor else scope.NOBODY
            return TaskView.model_validate(work.get_task(task_id, viewer))
        except db.NotFound as exc:
            raise PublicError("TASK_NOT_FOUND", str(exc), status_code=404) from exc
