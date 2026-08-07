"""Team calendar services."""

import re
from datetime import UTC, date, datetime

from .. import db
from . import scope
from .scope import WORKSPACE_ONLY

# a date, or a date-prefixed ISO timestamp — both compare correctly against
# the stored starts_at strings
DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ].*)?$")


def schedule_event(
    title: str,
    starts_at: str,
    ends_at: str = "",
    description: str = "",
    attendees: str = "",
    *,
    actor: str = "system",
    origin: str = "human",
    visibility: str = scope.WORKSPACE,
    crew_id: int = 0,
) -> dict:
    if not title.strip():
        raise ValueError("event title is required")

    def _canon(label: str, value: str) -> str:
        # normalize at write time: fromisoformat accepts space separators and
        # offsets, but the ICS builder (and string comparisons) only survive
        # the plain YYYY-MM-DDTHH:MM shape — store exactly that
        try:
            dt = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            raise ValueError(
                f"{label} must be an ISO timestamp (for example 2026-07-24T15:00)"
            ) from None
        if len(value) == 10:
            return value  # date-only stays a date: an all-day VEVENT, not midnight
        if dt.tzinfo is not None:
            dt = dt.astimezone(UTC).replace(tzinfo=None)
        return dt.strftime("%Y-%m-%dT%H:%M")

    starts_at = _canon("starts_at", starts_at)
    ends_at = _canon("ends_at", ends_at) if ends_at else ""
    from .search import index_record

    with db.transaction():
        tier, crew = scope.resolve_write(visibility, crew_id, actor=actor)
        eid = db.execute(
            "INSERT INTO events (title, description, starts_at, ends_at, attendees,"
            " origin, created_by, created_at, visibility, crew_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                title,
                description,
                starts_at,
                ends_at or None,
                attendees,
                origin,
                actor,
                db.now(),
                tier,
                crew,
            ),
        )
        index_record("event", eid, title, f"{description} {attendees} {starts_at}")
        db.log_activity(
            actor, "schedule_event", scope.detail(tier, f"#{eid}", f"{title} @ {starts_at}")
        )
    return {"id": eid, "title": title, "starts_at": starts_at}


def list_events(
    from_date: str = "", limit: int = 50, viewer: scope.Viewer = scope.NOBODY
) -> list[dict]:
    if from_date:
        # a string compare against a garbage value returns [], which reads as
        # "no events" — a silent wrong answer. Every write path validates
        # dates strictly, so this read must too. Shape alone is not enough:
        # "9999-99-99" matches the pattern and is still not a date.
        head = from_date[:10]
        if not DATE_PREFIX_RE.match(from_date):
            raise ValueError("from_date must be YYYY-MM-DD or an ISO timestamp")
        try:
            date.fromisoformat(head)
        except ValueError as exc:
            raise ValueError("from_date must be a real date (YYYY-MM-DD)") from exc
    frag, vp = scope.visible_filter(viewer, "events")
    if from_date:
        return db.query(
            f"SELECT * FROM events WHERE starts_at >= ? AND {frag} ORDER BY starts_at LIMIT ?",  # noqa: S608 — scope.visible_filter emits only bound marks
            (from_date, *vp, limit),
        )
    return db.query(
        f"SELECT * FROM events WHERE {frag} ORDER BY starts_at LIMIT ?",  # noqa: S608 — scope.visible_filter emits only bound marks
        (*vp, limit),
    )


def get_event(event_id: int, viewer: scope.Viewer = scope.NOBODY) -> dict | None:
    """One event, or None. Exists so tools/schedule.py can name an event in a
    proposal summary without writing SQL — it was the only query in app/tools/,
    and the rule is that SQL lives here.

    Filtered, and the default viewer is NOBODY. tools/schedule.py puts the
    title straight into a pending_changes summary a reviewer reads, and that
    reviewer is not necessarily in the event's crew.
    """
    frag, vp = scope.visible_filter(viewer, "events")
    return db.query_one(
        f"SELECT * FROM events WHERE id = ? AND {frag}",  # noqa: S608 — scope.visible_filter emits only bound marks
        (event_id, *vp),
    )


def cancel_event(event_id: int, *, actor: str = "system", origin: str = "human") -> dict:
    from .search import deindex_record

    # one transaction: a row delete that commits without its index delete
    # leaves the cancelled event citable by search and /ask
    with db.transaction():
        row = db.query_one("SELECT * FROM events WHERE id = ?", (event_id,))
        if not row:
            raise scope.missing("events", event_id)
        scope.assert_editable("events", row, actor, verb="cancel")
        db.execute("DELETE FROM events WHERE id = ?", (event_id,))
        deindex_record("event", event_id)  # search must never cite a cancelled event
        db.log_activity(
            actor, "cancel_event", scope.detail(row["visibility"], f"#{event_id}", row["title"])
        )
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
    """Events + open milestone/promise due dates as an iCalendar feed.
    Team-visible data only; keep the feed inside the trusted network (hosted
    calendar clients would mirror titles off-box — prefer local clients)."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Skein//calendar//EN",
        "X-WR-CALNAME:Skein",
    ]
    for e in db.query(
        f"SELECT * FROM events WHERE {WORKSPACE_ONLY} ORDER BY starts_at LIMIT 500"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
    ):
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
        f"SELECT id, title, due_date FROM milestones WHERE {WORKSPACE_ONLY}"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        " AND status != 'done' AND due_date IS NOT NULL ORDER BY due_date LIMIT 200"
    ):
        start = _ics_dt_lines("DTSTART", m["due_date"])
        if not start:  # a malformed stored date must not sink the whole feed
            continue
        lines += [
            "BEGIN:VEVENT",
            f"UID:milestone-{m['id']}@skein",
            *start,
            f"SUMMARY:{_ics_escape('due: ' + m['title'])}",
            "END:VEVENT",
        ]
    for c in db.query(
        f"SELECT id, promise, due_date FROM promises WHERE {WORKSPACE_ONLY}"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        " AND status = 'open' AND due_date IS NOT NULL ORDER BY due_date LIMIT 200"
    ):
        start = _ics_dt_lines("DTSTART", c["due_date"])
        if not start:
            continue
        lines += [
            "BEGIN:VEVENT",
            f"UID:promise-{c['id']}@skein",
            *start,
            f"SUMMARY:{_ics_escape('promised: ' + c['promise'][:80])}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
