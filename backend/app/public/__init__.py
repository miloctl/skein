"""Stable contracts for trusted extension packages."""

from .errors import PublicError
from .events import DomainEvent, EventActor, ResourceReference, dispatch_events
from .work import (
    BlockerView,
    CommandContext,
    CreateBlockerCommand,
    CreateTaskCommand,
    TaskView,
    UpdateBlockerCommand,
    UpdateTaskCommand,
    WorkItems,
)

__all__ = [
    "BlockerView",
    "CommandContext",
    "CreateBlockerCommand",
    "CreateTaskCommand",
    "DomainEvent",
    "EventActor",
    "PublicError",
    "ResourceReference",
    "TaskView",
    "UpdateBlockerCommand",
    "UpdateTaskCommand",
    "WorkItems",
    "dispatch_events",
]
