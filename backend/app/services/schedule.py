"""Team calendar services."""

from datetime import datetime

from .. import db


def schedule_event(title: str, starts_at: str, ends_at: str = "", description: str = "",
                   attendees: str = "", *, actor: str = "system", origin: str = "human") -> dict:
    if not title.strip():
        raise ValueError("event title is required")
    for label, value in (("starts_at", starts_at), ("ends_at", ends_at)):
        if not value and label == "ends_at":
            continue
        try:
            datetime.fromisoformat(value)
        except (TypeError, ValueError):
            raise ValueError(f"{label} must be an ISO timestamp (e.g. 2026-07-24T15:00)")
    eid = db.execute(
        "INSERT INTO events (title, description, starts_at, ends_at, attendees,"
        " origin, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (title, description, starts_at, ends_at or None, attendees, origin, actor, db.now()),
    )
    db.log_activity(actor, "schedule_event", f"#{eid} {title} @ {starts_at}")
    return {"id": eid, "title": title, "starts_at": starts_at}


def list_events(from_date: str = "", limit: int = 50) -> list[dict]:
    if from_date:
        return db.query(
            "SELECT * FROM events WHERE starts_at >= ? ORDER BY starts_at LIMIT ?",
            (from_date, limit),
        )
    return db.query("SELECT * FROM events ORDER BY starts_at LIMIT ?", (limit,))


def cancel_event(event_id: int, *, actor: str = "system", origin: str = "human") -> dict:
    db.execute("DELETE FROM events WHERE id = ?", (event_id,))
    db.log_activity(actor, "cancel_event", f"#{event_id}")
    return {"id": event_id, "cancelled": True}
