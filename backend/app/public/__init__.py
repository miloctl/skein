"""Stable contracts for trusted extension packages."""

from .errors import PublicError
from .events import DomainEvent, EventActor, ResourceReference
from .work import (
    CommandContext,
    CreateTaskCommand,
    TaskView,
    UpdateTaskCommand,
    WorkItems,
)
from .workflow import WorkflowContext, WorkflowEngine, WorkflowResult

__all__ = [
    "CommandContext",
    "CreateTaskCommand",
    "DomainEvent",
    "EventActor",
    "PublicError",
    "ResourceReference",
    "TaskView",
    "UpdateTaskCommand",
    "WorkItems",
    "WorkflowContext",
    "WorkflowEngine",
    "WorkflowResult",
]
