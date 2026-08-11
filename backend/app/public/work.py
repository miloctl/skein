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
from ..services import scope, work
from .errors import PublicError
from .events import EventActor, ResourceReference, emit_event


@dataclass(frozen=True)
class CommandContext:
    subject: PolicySubject
    origin: str
    correlation_id: str = ""
    project_type: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))


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
        self._authorize(
            context,
            "work.task.read",
            PolicyResource("task", str(task_id), project_type=context.project_type),
        )
        try:
            row = work.get_task(task_id, scope.Viewer.for_actor(context.subject.name))
        except db.NotFound as exc:
            raise PublicError("TASK_NOT_FOUND", str(exc), status_code=404) from exc
        return TaskView.model_validate(row)

    def create_task(self, command: CreateTaskCommand, context: CommandContext) -> TaskView:
        self._authorize(
            context,
            "work.task.create",
            PolicyResource(
                "task",
                project_type=context.project_type,
                classification=command.visibility,
            ),
        )
        try:
            with db.transaction():
                result = work.create_task(
                    **command.model_dump(),
                    actor=context.subject.name,
                    origin=context.origin,
                )
                emit_event(
                    "skein.task.created",
                    actor=EventActor(name=context.subject.name, kind=context.subject.kind),
                    origin=context.origin,
                    resource=ResourceReference(type="task", id=str(result["id"])),
                    changes=tuple(command.model_fields_set) or ("task",),
                    correlation_id=context.correlation_id,
                    visibility=command.visibility,
                )
        except PublicError:
            raise
        except (ValueError, PermissionError) as exc:
            raise PublicError("TASK_CREATE_REJECTED", str(exc)) from exc
        return self.get_task(result["id"], context)

    def update_task(self, command: UpdateTaskCommand, context: CommandContext) -> TaskView:
        current = self.get_task(command.task_id, context)
        self._authorize(
            context,
            "work.task.update",
            PolicyResource(
                "task",
                str(command.task_id),
                project_type=context.project_type,
                classification=current.visibility,
            ),
        )
        changes = command.model_dump(exclude={"task_id"}, exclude_none=True)
        if not changes:
            raise PublicError("EMPTY_COMMAND", "The command does not contain a change.")
        try:
            with db.transaction():
                work.update_task(
                    command.task_id,
                    **changes,
                    actor=context.subject.name,
                    origin=context.origin,
                    note=f" through {context.origin}",
                )
                emit_event(
                    "skein.task.updated",
                    actor=EventActor(name=context.subject.name, kind=context.subject.kind),
                    origin=context.origin,
                    resource=ResourceReference(type="task", id=str(command.task_id)),
                    changes=tuple(changes),
                    correlation_id=context.correlation_id,
                    visibility=current.visibility,
                )
        except PublicError:
            raise
        except db.NotFound as exc:
            raise PublicError("TASK_NOT_FOUND", str(exc), status_code=404) from exc
        except PermissionError as exc:
            raise PublicError("TASK_UPDATE_FORBIDDEN", str(exc), status_code=403) from exc
        except ValueError as exc:
            raise PublicError("TASK_UPDATE_REJECTED", str(exc)) from exc
        return self.get_task(command.task_id, context)
