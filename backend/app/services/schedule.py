"""Team calendar services."""

import re
from datetime import datetime

from .. import db


def schedule_event(
    title: str,
    starts_at: str,
    ends_at: str = "",
    description: str = "",
    attendees: str = "",
    *,
    actor: str = "system",
    origin: str = "human",
) -> dict:
    if not title.strip():
        raise ValueError("event title is required")
    for label, value in (("starts_at", starts_at), ("ends_at", ends_at)):
        if not value and label == "ends_at":
            continue
        try:
            datetime.fromisoformat(value)
        except (TypeError, ValueError):
            raise ValueError(f"{label} must be an ISO timestamp (e.g. 2026-07-24T15:00)") from None
    eid = db.execute(
        "INSERT INTO events (title, description, starts_at, ends_at, attendees,"
        " origin, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (title, description, starts_at, ends_at or None, attendees, origin, actor, db.now()),
    )
    from .search import index_record

    index_record("event", eid, title, f"{description} {attendees} {starts_at}")
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
    row = db.query_one("SELECT title FROM events WHERE id = ?", (event_id,))
    if not row:
        raise ValueError(f"no event #{event_id}")
    db.execute("DELETE FROM events WHERE id = ?", (event_id,))
    from .search import deindex_record

    deindex_record("event", event_id)  # search must never cite a cancelled event
    db.log_activity(actor, "cancel_event", f"#{event_id} {row['title']}")
    return {"id": event_id, "cancelled": True}


def _ics_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\r", "\\n")
        .replace("\n", "\\n")
    )


def _ics_dt(iso: str) -> str:
    """RFC 5545 DATE-TIME is exactly YYYYMMDDTHHMMSS — pad the seconds our
    API's own suggested format (2026-07-24T15:00) omits."""
    out = iso.replace("-", "").replace(":", "")[:15]
    if len(out) == 13:  # date + T + HHMM
        out += "00"
    return out


def _ics_dt_lines(prop: str, iso: str) -> list[str]:
    value = _ics_dt(iso)
    if re.fullmatch(r"\d{8}", value):
        return [f"{prop};VALUE=DATE:{value}"]
    if re.fullmatch(r"\d{8}T\d{6}", value):
        return [f"{prop}:{value}"]
    return []  # malformed stored timestamp: drop the property, not the feed


def ics_feed() -> str:
    """Events + open milestone/commitment due dates as an iCalendar feed.
    Team-visible data only; LAN-only surface (hosted calendar clients would
    mirror titles off-box — prefer local clients)."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Skein//calendar//EN",
        "X-WR-CALNAME:Skein",
    ]
    for e in db.query("SELECT * FROM events ORDER BY starts_at LIMIT 500"):
        start = _ics_dt_lines("DTSTART", e["starts_at"])
        if not start:
            continue
        lines += [
            "BEGIN:VEVENT",
            f"UID:event-{e['id']}@skein",
            *start,
            *(_ics_dt_lines("DTEND", e["ends_at"]) if e["ends_at"] else []),
            f"SUMMARY:{_ics_escape(e['title'])}",
            *([f"DESCRIPTION:{_ics_escape(e['description'])}"] if e["description"] else []),
            "END:VEVENT",
        ]
    for m in db.query(
        "SELECT id, title, due_date FROM milestones"
        " WHERE status != 'done' AND due_date IS NOT NULL ORDER BY due_date LIMIT 200"
    ):
        lines += [
            "BEGIN:VEVENT",
            f"UID:milestone-{m['id']}@skein",
            f"DTSTART;VALUE=DATE:{m['due_date'].replace('-', '')}",
            f"SUMMARY:{_ics_escape('🎯 due: ' + m['title'])}",
            "END:VEVENT",
        ]
    for c in db.query(
        "SELECT id, promise, due_date FROM commitments"
        " WHERE status = 'open' AND due_date IS NOT NULL ORDER BY due_date LIMIT 200"
    ):
        lines += [
            "BEGIN:VEVENT",
            f"UID:commitment-{c['id']}@skein",
            f"DTSTART;VALUE=DATE:{c['due_date'].replace('-', '')}",
            f"SUMMARY:{_ics_escape('🤝 promised: ' + c['promise'][:80])}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
