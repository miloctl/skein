"""Stable contracts for trusted extension packages."""

from .errors import PublicError
from .events import DomainEvent, EventActor, ResourceReference, dispatch_events, emit_event
from .work import (
    CommandContext,
    CreateTaskCommand,
    TaskView,
    UpdateTaskCommand,
    WorkItems,
)

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
    "dispatch_events",
    "emit_event",
]
