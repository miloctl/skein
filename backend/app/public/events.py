"""Versioned public events and durable subscriber delivery."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from .. import db

if TYPE_CHECKING:
    from ..extensions.contracts import EventContribution

log = logging.getLogger("skein.extensions.events")


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


def emit_event(
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
    contributions: Sequence[EventContribution], *, limit: int = 100
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
        complete = True
        error = False
        for contribution in matches:
            prior = db.query_one(
                "SELECT 1 AS present FROM extension_event_deliveries"
                " WHERE event_id = ? AND subscriber = ?",
                (event.event_id, contribution.name),
            )
            if prior:
                continue
            try:
                contribution.handler(event)
                db.execute(
                    "INSERT OR IGNORE INTO extension_event_deliveries"
                    " (event_id, subscriber, delivered_at) VALUES (?, ?, ?)",
                    (event.event_id, contribution.name, db.now()),
                )
            except Exception:
                complete = False
                error = True
                log.exception(
                    "event subscriber failed",
                    extra={"event_id": event.event_id, "subscriber": contribution.name},
                )
        if complete:
            db.execute(
                "UPDATE extension_outbox SET status = 'delivered', delivered_at = ?,"
                " last_error_code = '' WHERE event_id = ?",
                (db.now(), event.event_id),
            )
            delivered += 1
            continue
        if error:
            attempts = int(row["attempts"]) + 1
            max_attempts = max((item.max_attempts for item in matches), default=5)
            status = "dead" if attempts >= max_attempts else "pending"
            db.execute(
                "UPDATE extension_outbox SET status = ?, attempts = ?,"
                " last_error_code = 'SUBSCRIBER_ERROR' WHERE event_id = ?",
                (status, attempts, event.event_id),
            )
            if status == "dead":
                dead += 1
            else:
                failed += 1
    return {"delivered": delivered, "failed": failed, "dead": dead}
