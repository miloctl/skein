"""Versioned public events and durable subscriber delivery."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import replace
from inspect import isawaitable
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from .. import db
from ..extensions.policy import PolicyEffect, PolicyInput, PolicyResource

if TYPE_CHECKING:
    from ..extensions.contracts import EventContribution, EventExecutionContext

log = logging.getLogger("skein.extensions.events")


class _DeliveryRefused(RuntimeError):
    def __init__(self, code: str, *, terminal: bool) -> None:
        super().__init__(code)
        self.code = code
        self.terminal = terminal


class EventActor(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    kind: str


class ResourceReference(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str
    id: str


class DomainEvent(BaseModel):
    """The public event envelope. Schema version 1 excludes row bodies."""

    model_config = ConfigDict(frozen=True)

    event_id: str
    event_type: str
    schema_version: int = 1
    timestamp: str
    actor: EventActor
    origin: str
    resource: ResourceReference
    changes: tuple[str, ...] = ()
    correlation_id: str = ""
    visibility: str = "workspace"


def _emit_event(
    event_type: str,
    *,
    actor: EventActor,
    origin: str,
    resource: ResourceReference,
    changes: Sequence[str] = (),
    correlation_id: str = "",
    visibility: str = "workspace",
) -> DomainEvent:
    """Write one event into the ambient core transaction."""
    event = DomainEvent(
        event_id=str(uuid4()),
        event_type=event_type,
        timestamp=db.now(),
        actor=actor,
        origin=origin,
        resource=resource,
        changes=tuple(dict.fromkeys(changes)),
        correlation_id=correlation_id,
        visibility=visibility,
    )
    db.execute(
        "INSERT INTO extension_outbox"
        " (event_id, event_type, schema_version, payload, visibility, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            event.event_id,
            event.event_type,
            event.schema_version,
            event.model_dump_json(),
            event.visibility,
            event.timestamp,
        ),
    )
    return event


def _matches(contribution: EventContribution, event: DomainEvent) -> bool:
    return (
        event.event_type in contribution.event_types
        and event.schema_version in contribution.schema_versions
        and event.visibility in contribution.visibilities
    )


def dispatch_events(
    contributions: Sequence[EventContribution],
    context: EventExecutionContext,
    *,
    limit: int = 100,
) -> Mapping[str, int]:
    """Deliver pending events once per matching subscriber.

    A handler must use ``event_id`` as its idempotency key. A process can stop
    after the side effect and before the delivery receipt is stored.
    """
    rows = db.query(
        "SELECT * FROM extension_outbox WHERE status = 'pending'"
        " ORDER BY created_at, event_id LIMIT ?",
        (limit,),
    )
    delivered = failed = dead = 0
    for row in rows:
        event = DomainEvent.model_validate_json(row["payload"])
        matches = [item for item in contributions if _matches(item, event)]
        pending_subscribers = False
        dead_subscribers = False
        row_error_code = ""
        for contribution in matches:
            prior = db.query_one(
                "SELECT 1 AS present FROM extension_event_deliveries"
                " WHERE event_id = ? AND subscriber = ?",
                (event.event_id, contribution.name),
            )
            if prior:
                continue
            attempt = db.query_one(
                "SELECT attempts, status FROM extension_event_attempts"
                " WHERE event_id = ? AND subscriber = ?",
                (event.event_id, contribution.name),
            )
            if attempt and attempt["status"] == "dead":
                dead_subscribers = True
                continue
            error_code = "SUBSCRIBER_ERROR"
            terminal = False
            try:
                subject = context.subject_resolver(contribution.service_identity)
                decision = context.policy.decide(
                    PolicyInput(
                        subject,
                        contribution.policy_action,
                        PolicyResource(
                            event.resource.type,
                            event.resource.id,
                            classification=event.visibility,
                            attributes={"event_type": event.event_type},
                        ),
                        "event",
                        agent=subject.name,
                        tool=contribution.name,
                        tool_effect=contribution.effect,
                        tool_risk=contribution.risk,
                    )
                )
                if decision.effect != PolicyEffect.PERMIT:
                    raise _DeliveryRefused(
                        (
                            "POLICY_REVIEW_UNSUPPORTED"
                            if decision.effect == PolicyEffect.REVIEW
                            else "POLICY_DENIED"
                        ),
                        terminal=True,
                    )
                delivery_context = replace(
                    context,
                    subject=subject,
                    delivery_id=f"{event.event_id}:{contribution.name}",
                    namespace=contribution.name,
                )
                delivery_context = context.work_items._bind_execution_context(
                    delivery_context,
                    receipt_namespace=f"event:{contribution.name}",
                )
                executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="skein-event")
                future = executor.submit(contribution.handler, event, delivery_context)
                try:
                    result = future.result(timeout=contribution.timeout_seconds)
                    if isawaitable(result):
                        close = getattr(result, "close", None)
                        if close is not None:
                            close()
                        raise _DeliveryRefused("ASYNC_HANDLER_UNSUPPORTED", terminal=True)
                except FutureTimeout as exc:
                    future.cancel()
                    raise _DeliveryRefused(
                        (
                            "COMPLETION_UNKNOWN"
                            if contribution.effect in ("write", "unknown")
                            else "SUBSCRIBER_TIMEOUT"
                        ),
                        terminal=contribution.effect in ("write", "unknown"),
                    ) from exc
                finally:
                    executor.shutdown(wait=False, cancel_futures=True)
                db.execute(
                    "INSERT OR IGNORE INTO extension_event_deliveries"
                    " (event_id, subscriber, delivered_at) VALUES (?, ?, ?)",
                    (event.event_id, contribution.name, db.now()),
                )
                db.execute(
                    "DELETE FROM extension_event_attempts WHERE event_id = ? AND subscriber = ?",
                    (event.event_id, contribution.name),
                )
            except Exception as exc:
                if isinstance(exc, _DeliveryRefused):
                    error_code = exc.code
                    terminal = exc.terminal
                attempts = int((attempt or {}).get("attempts") or 0) + 1
                status = "dead" if terminal or attempts >= contribution.max_attempts else "pending"
                db.execute(
                    "INSERT INTO extension_event_attempts"
                    " (event_id, subscriber, attempts, status, last_error_code, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(event_id, subscriber) DO UPDATE SET"
                    " attempts = excluded.attempts, status = excluded.status,"
                    " last_error_code = excluded.last_error_code, updated_at = excluded.updated_at",
                    (
                        event.event_id,
                        contribution.name,
                        attempts,
                        status,
                        error_code,
                        db.now(),
                    ),
                )
                dead_subscribers = dead_subscribers or status == "dead"
                pending_subscribers = pending_subscribers or status == "pending"
                row_error_code = error_code
                log.exception(
                    "event subscriber failed",
                    extra={"event_id": event.event_id, "subscriber": contribution.name},
                )
        if not pending_subscribers:
            final_status = "dead" if dead_subscribers else "delivered"
            db.execute(
                "UPDATE extension_outbox SET status = ?, delivered_at = ?,"
                " last_error_code = ? WHERE event_id = ?",
                (final_status, db.now(), row_error_code, event.event_id),
            )
            if final_status == "dead":
                dead += 1
            else:
                delivered += 1
            continue
        if pending_subscribers:
            attempts = int(row["attempts"]) + 1
            db.execute(
                "UPDATE extension_outbox SET status = 'pending', attempts = ?,"
                " last_error_code = ? WHERE event_id = ?",
                (attempts, row_error_code or "SUBSCRIBER_ERROR", event.event_id),
            )
            failed += 1
    return {"delivered": delivered, "failed": failed, "dead": dead}
