"""Team calendar services."""

import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from .. import config, db
from . import policy_context, scope
from .scope import WORKSPACE_ONLY

# a date, or a date-prefixed ISO timestamp - both compare correctly against
# the stored starts_at strings
DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ].*)?$")


def _canon(label: str, value: str) -> str:
    # normalize at write time: fromisoformat accepts space separators and
    # offsets, but the ICS builder (and string comparisons) only survive
    # the plain YYYY-MM-DDTHH:MM shape - store exactly that, in UTC
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"{label} must be an ISO timestamp (for example 2026-07-24T15:00)"
        ) from None
    # Both bounds protect every READER, not only this write. strftime %Y does
    # not zero-pad a year under 1000 on glibc, so a typo like 0026 stored as
    # "26-10-20T15:00" - and db.local_wall refuses that in every list, so one
    # row made GET /api/events answer 400 for everyone. astimezone overflows
    # past year 9999, at this conversion in a zone west of UTC and at
    # local_wall's in a zone east of it.
    if not 1000 <= dt.year <= 9000:
        raise ValueError(f"{label} must be in a year from 1000 to 9000")
    if len(value) == 10:
        return value  # date-only stays a date: an all-day VEVENT, not midnight
    # no offset means the TEAM's clock: the calendar's datetime-local field,
    # playbook rituals and the agent tools all send one. Stored as typed, it
    # reads as UTC in every reader that converts (db.local_wall)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=config.TZ)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M")


def _check_span(starts_at: str, ends_at: str) -> None:
    # an ICS client drops or misplaces an event whose DTEND precedes DTSTART,
    # or whose start is a date and whose end is a time
    if ends_at and (len(ends_at) != len(starts_at) or ends_at <= starts_at):
        raise ValueError("ends_at must be after starts_at, and of the same kind (date or time)")


def _check_engagement(engagement_id: int, actor: str, *, tier: str, crew_id: int | None) -> None:
    # the same guard add_promise puts on its own engagement link: unchecked,
    # a bad id raises IntegrityError (a 500 from a value the caller sent) and
    # a readable-looking id lets an event attach to another crew's private
    # engagement - which migration 008 exists to attribute hours to
    efrag, ep = scope.visible_filter(scope.Viewer.for_actor(actor), "engagements")
    engagement = db.query_one(
        f"SELECT visibility, crew_id FROM engagements WHERE id = ? AND {efrag}",  # noqa: S608 - scope.visible_filter emits only bound marks
        (engagement_id, *ep),
    )
    if engagement is None:
        raise ValueError(scope.missing_text("engagements", engagement_id))
    # the rule a task keeps: every policy reader drops an event whose
    # engagement it cannot read, so a narrower engagement hides the event from
    # its own readers, and the rows linked to it stay wider than it
    scope.assert_relationship_contains(
        engagement["visibility"], engagement["crew_id"], tier, crew_id, child_label="event"
    )


def schedule_event(
    title: str,
    starts_at: str,
    ends_at: str = "",
    description: str = "",
    attendees: str = "",
    agenda: str = "",
    engagement_id: int = 0,
    *,
    actor: str = "system",
    origin: str = "human",
    visibility: str = scope.WORKSPACE,
    crew_id: int = 0,
) -> dict:
    if not title.strip():
        raise ValueError("event title is required")
    starts_at = _canon("starts_at", starts_at)
    ends_at = _canon("ends_at", ends_at) if ends_at else ""
    _check_span(starts_at, ends_at)
    from .search import index_record

    with db.transaction():
        tier, crew = scope.resolve_write(visibility, crew_id, actor=actor)
        if engagement_id:
            _check_engagement(engagement_id, actor, tier=tier, crew_id=crew)
        eid = db.execute(
            "INSERT INTO events (title, description, starts_at, ends_at, attendees,"
            " agenda, engagement_id, origin, created_by, created_at, visibility, crew_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " RETURNING id",
            (
                title,
                description,
                starts_at,
                ends_at or None,
                attendees,
                agenda,
                engagement_id or None,
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


def update_event(
    event_id: int,
    title: str = "",
    starts_at: str = "",
    ends_at: str = "",
    description: str = "",
    attendees: str = "",
    agenda: str = "",
    engagement_id: int = 0,
    *,
    actor: str = "system",
    origin: str = "human",
) -> dict:
    """Change or reschedule an event. An empty field is unchanged, and "-"
    clears ends_at, description, attendees or agenda. engagement_id 0 is
    unchanged and a negative one unlinks.

    No visibility: an update never changes a tier (policy_context.py, the
    note above `for_change`'s classification), and an event that narrowed
    would leave the rows linked to it wider than it.
    """
    from .search import index_record

    with db.transaction():
        # FOR UPDATE first: the merged start/end check below reads the stored
        # half, and a concurrent edit of the other half would pass both checks
        # and store an end before its start
        row = db.query_one("SELECT * FROM events WHERE id = ? FOR UPDATE", (event_id,))
        if not row:
            raise scope.missing("events", event_id)
        scope.assert_editable("events", row, actor, verb="update")
        fields: dict = {}
        if title:
            if not title.strip():
                raise ValueError("event title is required")
            fields["title"] = title
        if starts_at or ends_at:
            start = _canon("starts_at", starts_at) if starts_at else row["starts_at"]
            if ends_at == "-":
                end = ""
            elif ends_at:
                end = _canon("ends_at", ends_at)
            else:
                end = row["ends_at"] or ""
            _check_span(start, end)
            if starts_at:
                fields["starts_at"] = start
            if ends_at:
                fields["ends_at"] = end or None
        for name, value in (
            ("description", description),
            ("attendees", attendees),
            ("agenda", agenda),
        ):
            if value:
                fields[name] = "" if value == "-" else value
        if engagement_id:
            if engagement_id > 0:
                _check_engagement(
                    engagement_id, actor, tier=row["visibility"], crew_id=row["crew_id"]
                )
            fields["engagement_id"] = engagement_id if engagement_id > 0 else None
        if not fields:
            raise ValueError("nothing to update")
        sets = ", ".join(f"{k} = ?" for k in fields)
        db.execute(
            f"UPDATE events SET {sets} WHERE id = ?",  # noqa: S608 - keys come from the fixed names above
            (*fields.values(), event_id),
        )
        new = {**row, **fields}
        index_record(
            "event",
            event_id,
            new["title"],
            f"{new['description']} {new['attendees']} {new['starts_at']}",
        )
        # field names only, never the text: the ledger outlives the event
        db.log_activity(
            actor,
            "update_event",
            scope.detail(row["visibility"], f"#{event_id}", " ".join(fields)),
        )
    return {"id": event_id, "updated": list(fields)}


# kind -> (table, title column): the kinds the capture grammar files
# (services/capture.py::PATTERNS), which is what comes out of a meeting.
# Migration 044 gives each table its event_id. A kind added here needs that
# column first, and sharing._PARENTS lists the tables again: without its
# entry a row shares to the roster while its meeting stays narrower.
LINKED = {
    "note": ("notes", "topic"),
    "task": ("tasks", "title"),
    "decision": ("decisions", "title"),
    "question": ("questions", "question"),
    "blocker": ("blockers", "title"),
    "promise": ("promises", "promise"),
    "intake": ("intake_requests", "title"),
}
# a void task leaves every list (work.py), this one included
_LINKED_LIVE = {"task": " AND status <> 'void'"}
EVENT_ITEMS_LIMIT = 100


def _linked_table(kind: str) -> str:
    if kind not in LINKED:
        raise ValueError(f"kind must be one of: {', '.join(LINKED)}")
    return LINKED[kind][0]


def check_event_link(
    event_id: int, *, actor: str, tier: str, crew_id: int | None, label: str
) -> None:
    """Refuse a link to an event the writer cannot read, or to one narrower
    than the row: every reader of the row reads its event_id, and a private
    meeting's sequential id is data (scope.relationship_contains).

    The probe holds the event FOR KEY SHARE, so a concurrent cancel waits for
    this transaction. Unheld, the cancel commits between the probe and the
    insert, and the foreign key turns the caller's write into a 500.
    """
    frag, vp = scope.visible_filter(scope.Viewer.for_actor(actor), "events")
    event = db.query_one(
        f"SELECT visibility, crew_id FROM events WHERE id = ? AND {frag} FOR KEY SHARE",  # noqa: S608 - scope.visible_filter emits only bound marks
        (event_id, *vp),
    )
    if event is None:
        raise ValueError(scope.missing_text("events", event_id))
    scope.assert_relationship_contains(
        event["visibility"], event["crew_id"], tier, crew_id, child_label=label
    )


def link_item(event_id: int, kind: str, item_id: int, *, actor: str = "system") -> dict:
    """Record that an existing row came out of a meeting."""
    table = _linked_table(kind)
    with db.transaction():
        # the event, then the row: review.approve_change holds a create's
        # parents in that order, and the reverse order deadlocks against it
        policy_context.hold_resource("event", event_id)
        row = db.query_one(f"SELECT * FROM {table} WHERE id = ? FOR UPDATE", (item_id,))  # noqa: S608 - table from LINKED
        if not row:
            raise scope.missing(table, item_id)
        scope.assert_editable(table, row, actor, verb="link")
        check_event_link(
            event_id, actor=actor, tier=row["visibility"], crew_id=row["crew_id"], label=kind
        )
        db.execute(f"UPDATE {table} SET event_id = ? WHERE id = ?", (event_id, item_id))  # noqa: S608 - table from LINKED
        db.log_activity(
            actor,
            "link_event",
            scope.detail(row["visibility"], f"{kind} #{item_id}", f"to event #{event_id}"),
        )
    return {"event_id": event_id, "kind": kind, "id": item_id}


def unlink_item(event_id: int, kind: str, item_id: int, *, actor: str = "system") -> dict:
    table = _linked_table(kind)
    with db.transaction():
        row = db.query_one(f"SELECT * FROM {table} WHERE id = ? FOR UPDATE", (item_id,))  # noqa: S608 - table from LINKED
        if not row:
            raise scope.missing(table, item_id)
        scope.assert_editable(table, row, actor, verb="unlink")
        if int(row.get("event_id") or 0) != event_id:
            raise ValueError("This record is not linked to this meeting.")
        db.execute(f"UPDATE {table} SET event_id = NULL WHERE id = ?", (item_id,))  # noqa: S608 - table from LINKED
        db.log_activity(
            actor,
            "unlink_event",
            scope.detail(row["visibility"], f"{kind} #{item_id}", f"from event #{event_id}"),
        )
    return {"event_id": event_id, "kind": kind, "id": item_id, "unlinked": True}


def event_items(
    event_id: int,
    viewer: scope.Viewer = scope.NOBODY,
    *,
    resource_filter: Callable[[str, int, dict[str, str]], bool] | None = None,
) -> dict[str, list[dict]]:
    """What came out of one meeting, each kind at the viewer's own tier and
    through the workplace policy, as engagement_brief.brief reads the rows
    under an engagement."""
    if get_event(event_id, viewer) is None:
        raise scope.missing("events", event_id)
    out: dict[str, list[dict]] = {}
    for kind, (table, title) in LINKED.items():
        frag, vp = scope.visible_filter(viewer, table)
        rows = db.query(
            f"SELECT id, {title} AS title, visibility, crew_id FROM {table}"  # noqa: S608 - table and column from LINKED, scope.visible_filter emits only bound marks
            f" WHERE event_id = ? AND {frag}{_LINKED_LIVE.get(kind, '')} ORDER BY id LIMIT ?",
            (event_id, *vp, EVENT_ITEMS_LIMIT),
        )
        out[kind] = policy_context.filter_resource_rows(kind, rows, viewer, resource_filter)
    return out


def linked_counts(
    event_ids: list[int],
    viewer: scope.Viewer = scope.NOBODY,
    *,
    resource_filter: Callable[[str, int, dict[str, str]], bool] | None = None,
) -> dict[int, int]:
    """{event id: rows that came out of it}, counting only rows the viewer
    can read and the policy permits. A count that includes a hidden row tells
    the reader that row exists."""
    if not event_ids:
        return {}
    marks = ",".join("?" for _ in event_ids)
    parts, params = [], []
    for kind, (table, _title) in LINKED.items():
        frag, vp = scope.visible_filter(viewer, table)
        parts.append(
            f"SELECT '{kind}' AS kind, id, event_id FROM {table}"  # noqa: S608 - kind and table from LINKED, scope.visible_filter emits only bound marks
            f" WHERE event_id IN ({marks}) AND {frag}{_LINKED_LIVE.get(kind, '')}"
        )
        params += [*event_ids, *vp]
    rows = db.query(
        f"SELECT kind, id, event_id FROM ({' UNION ALL '.join(parts)}) linked LIMIT ?",  # noqa: S608 - kinds and tables from LINKED, scope.visible_filter emits only bound marks
        (*params, len(event_ids) * EVENT_ITEMS_LIMIT),
    )
    counts: dict[int, int] = {}
    for kind in LINKED:
        of_kind = [r for r in rows if r["kind"] == kind]
        for r in policy_context.filter_resource_rows(kind, of_kind, viewer, resource_filter):
            counts[int(r["event_id"])] = counts.get(int(r["event_id"]), 0) + 1
    return counts


def list_events(
    from_date: str = "", limit: int = 50, viewer: scope.Viewer = scope.NOBODY
) -> list[dict]:
    if from_date:
        # a string compare against a garbage value returns [], which reads as
        # "no events" - a silent wrong answer. Every write path validates
        # dates strictly, so this read must too. Shape alone is not enough:
        # "9999-99-99" matches the pattern and is still not a date.
        head = from_date[:10]
        if not DATE_PREFIX_RE.match(from_date):
            raise ValueError("from_date must be YYYY-MM-DD or an ISO timestamp")
        try:
            date.fromisoformat(head)
            if len(from_date) > 10:
                # the pattern accepts any tail; "2026-10-01T99" compared as a
                # string and answered [] as if nothing was planned
                datetime.fromisoformat(from_date)
        except ValueError as exc:
            raise ValueError("from_date must be a real date (YYYY-MM-DD) or ISO timestamp") from exc
    frag, vp = scope.visible_filter(viewer, "events")
    if from_date:
        rows = db.query(
            f"SELECT * FROM events WHERE starts_at >= ? AND {frag} ORDER BY starts_at LIMIT ?",  # noqa: S608 - scope.visible_filter emits only bound marks
            (from_date, *vp, limit),
        )
    else:
        rows = db.query(
            f"SELECT * FROM events WHERE {frag} ORDER BY starts_at LIMIT ?",  # noqa: S608 - scope.visible_filter emits only bound marks
            (*vp, limit),
        )
    return [with_local(r) for r in rows]


def with_local(row: dict) -> dict:
    """An event row plus its times on the team clock, the shape a person
    typed. starts_at stays UTC for anything that compares or converts."""
    return {
        **row,
        "starts_local": db.local_wall(row["starts_at"]),
        "ends_local": db.local_wall(row["ends_at"]) if row.get("ends_at") else None,
    }


# A six-week month grid. Every kind below costs one scoped query per call, and
# a wider window is a report, not a calendar.
CALENDAR_MAX_DAYS = 42
# Per kind. The query asks for one row more, and a kind that returns it is
# named in `truncated`: a grid that drops rows without saying so reads as
# "nothing else is due".
CALENDAR_LIMIT = 500

# An end that can be drawn: the same kind as the start and after it. Rows
# written before schedule_event checked the end can hold neither (ics_feed
# drops their DTEND for the same reason). NULL falls back to the start below.
_DRAWN_END = (
    "(CASE WHEN length(ends_at) = length(starts_at) AND ends_at > starts_at THEN ends_at END)"
)


def calendar_range(
    start: str,
    end: str,
    viewer: scope.Viewer = scope.NOBODY,
    *,
    resource_filter: Callable[[str, int, dict[str, str]], bool] | None = None,
    assignee: str = "",
) -> dict:
    """Everything with a date between two team days, inclusive: events, open
    task, milestone and promise due dates, and time away.

    `assignee` narrows tasks to one person, for "only my tasks".
    """
    from .absences import TEAM_SEES_DATES

    for label, value in (("start", start), ("end", end)):
        if not value:
            raise ValueError(f"{label} is required (YYYY-MM-DD)")
        db.validate_date(label, value, allow_clear=False)
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if last < first:
        raise ValueError("end must be on or after start")
    if (last - first).days >= CALENDAR_MAX_DAYS:
        raise ValueError(f"a calendar range covers at most {CALENDAR_MAX_DAYS} days")
    win_start = db.local_event_window(first)[0]
    win_end = db.local_event_window(last)[1]
    truncated: list[str] = []

    def capped(kind: str, rows: list[dict]) -> list[dict]:
        if len(rows) > CALENDAR_LIMIT:
            truncated.append(kind)
            return rows[:CALENDAR_LIMIT]
        return rows

    def permitted(entity: str, rows: list[dict]) -> list[dict]:
        return policy_context.filter_resource_rows(entity, rows, viewer, resource_filter)

    # OVERLAP, not start-in-window: a meeting that began before the first day
    # and runs into it belongs on the grid. A timed row compares against the
    # team-day window in naive UTC; a date-only row compares as dates, and its
    # end is exclusive, as DTEND;VALUE=DATE is in the ICS feed (RFC 5545).
    # ponytail: `starts_at < ?` has no lower bound, so this reads every event
    # before the window end; a span cap or a range index fixes it if events
    # reach six figures.
    frag, vp = scope.visible_filter(viewer, "events")
    events = db.query(
        f"SELECT * FROM events WHERE {frag} AND ("  # noqa: S608 - scope.visible_filter emits only bound marks
        f"(length(starts_at) > 10 AND starts_at < ? AND COALESCE({_DRAWN_END} > ?, starts_at >= ?))"
        f" OR (length(starts_at) = 10 AND starts_at <= ? AND COALESCE({_DRAWN_END} > ?, starts_at >= ?))"
        ") ORDER BY starts_at, id LIMIT ?",
        (*vp, win_end, win_start, win_start, end, start, start, CALENDAR_LIMIT + 1),
    )
    frag, vp = scope.visible_filter(viewer, "tasks")
    mine = " AND assignee = ?" if assignee else ""
    tasks = db.query(
        # the link columns stay out: a task's milestone and engagement ids
        # are redacted per reader elsewhere (work.redact_task_relationships),
        # and a calendar needs none of them
        "SELECT id, title, due_date, assignee, status, priority, visibility, crew_id"  # noqa: S608 - scope.visible_filter emits only bound marks
        f" FROM tasks WHERE status NOT IN ('done', 'void') AND due_date >= ? AND due_date <= ?"
        f" AND {frag}{mine} ORDER BY due_date, id LIMIT ?",
        (start, end, *vp, *([assignee] if assignee else []), CALENDAR_LIMIT + 1),
    )
    frag, vp = scope.visible_filter(viewer, "milestones")
    milestones = db.query(
        "SELECT id, title, due_date, status, owner, visibility, crew_id FROM milestones"  # noqa: S608 - scope.visible_filter emits only bound marks
        f" WHERE status != 'done' AND due_date >= ? AND due_date <= ? AND {frag}"
        " ORDER BY due_date, id LIMIT ?",
        (start, end, *vp, CALENDAR_LIMIT + 1),
    )
    frag, vp = scope.visible_filter(viewer, "promises")
    promises = db.query(
        "SELECT id, promise, to_whom, due_date, direction, visibility, crew_id FROM promises"  # noqa: S608 - scope.visible_filter emits only bound marks
        f" WHERE status = 'open' AND due_date >= ? AND due_date <= ? AND {frag}"
        " ORDER BY due_date, id LIMIT ?",
        (start, end, *vp, CALENDAR_LIMIT + 1),
    )
    # Time away plans the future, so another person's window shows only from
    # today on; a past one is judging them (docs/INSIGHTS.md, the
    # anti-surveillance rule). Your own windows show at any date.
    today = db.today().isoformat()
    frag, vp = scope.visible_filter(viewer, "absences")
    readable = db.query(
        "SELECT id, person, kind, starts_on, ends_on, note, visibility, crew_id FROM absences"  # noqa: S608 - scope.visible_filter emits only bound marks
        f" WHERE starts_on <= ? AND ends_on >= ? AND {frag} AND (person = ? OR ends_on >= ?)"
        " ORDER BY starts_on, person LIMIT ?",
        (end, start, *vp, viewer.name, today, CALENDAR_LIMIT + 1),
    )
    # A window the viewer cannot read but whose dates its owner shared: the
    # same rows portfolio.capacity_ahead shows every signed-in user, masked
    # the same way. The kind reads "away", and the note and the row id stay
    # with the row's own tier.
    shared = capped(
        "time_away",
        db.query(
            "SELECT id, person, starts_on, ends_on, visibility, crew_id FROM absences"  # noqa: S608 - TEAM_SEES_DATES and scope.visible_filter emit only constants and bound marks
            f" WHERE starts_on <= ? AND ends_on >= ? AND {TEAM_SEES_DATES} AND NOT {frag}"
            " AND ends_on >= ? ORDER BY starts_on, person LIMIT ?",
            (end, start, *vp, today, CALENDAR_LIMIT + 1),
        ),
    )
    if resource_filter is not None:
        shared = [
            row
            for row in shared
            if resource_filter(
                "absence",
                int(row["id"]),
                {
                    "classification": str(row["visibility"]),
                    "crew_id": str(row["crew_id"] or ""),
                    "project_type": "",
                },
            )
        ]
    # the two halves share one cap, the one every other kind has
    away = capped(
        "time_away",
        permitted("absence", capped("time_away", readable))
        + [
            {
                "person": r["person"],
                "kind": "away",
                "starts_on": r["starts_on"],
                "ends_on": r["ends_on"],
            }
            for r in shared
        ],
    )
    # capped BEFORE the policy filter: a kind whose query reached its LIMIT
    # has rows past it, and a filter that then drops a few must not hide that
    return {
        "start": start,
        "end": end,
        "today": today,
        "events": [with_local(r) for r in permitted("event", capped("events", events))],
        "tasks": permitted("task", capped("tasks", tasks)),
        "milestones": permitted("milestone", capped("milestones", milestones)),
        "promises": permitted("promise", capped("promises", promises)),
        "time_away": away,
        "truncated": sorted(set(truncated)),
    }


def team_day_events(d: date) -> list[dict]:
    """Workspace events on team-day d. A date-only row sorts before every
    timestamp of its own day, so a window of timestamps alone drops it at and
    west of UTC, and admits tomorrow's all-day row in the west."""
    start, end = db.local_event_window(d)
    rows = db.query(
        f"SELECT * FROM events WHERE {WORKSPACE_ONLY}"  # noqa: S608 - scope.WORKSPACE_ONLY is a module constant
        " AND ((length(starts_at) > 10 AND starts_at >= ? AND starts_at < ?) OR starts_at = ?)"
        " ORDER BY starts_at",
        (start, end, d.isoformat()),
    )
    return [with_local(r) for r in rows]


def get_event(event_id: int, viewer: scope.Viewer = scope.NOBODY) -> dict | None:
    """One event, or None. Exists so tools/schedule.py can name an event in a
    proposal summary without writing SQL - it was the only query in app/tools/,
    and the rule is that SQL lives here.

    Filtered, and the default viewer is NOBODY. tools/schedule.py puts the
    title straight into a pending_changes summary a reviewer reads, and that
    reviewer is not necessarily in the event's crew.
    """
    frag, vp = scope.visible_filter(viewer, "events")
    return db.query_one(
        f"SELECT * FROM events WHERE id = ? AND {frag}",  # noqa: S608 - scope.visible_filter emits only bound marks
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


def _ics_fold(line: str) -> str:
    """RFC 5545 3.1: a content line is at most 75 octets, and a longer one
    continues on the next line after one space. Split between characters,
    never inside a multi-byte one, or the client reads a broken sequence."""
    parts: list[str] = []
    chunk: list[str] = []
    size = 0
    for char in line:
        width = len(char.encode())
        # a continuation line spends one octet on its leading space
        if size + width > (75 if not parts else 74):
            parts.append("".join(chunk))
            chunk, size = [], 0
        chunk.append(char)
        size += width
    parts.append("".join(chunk))
    return "\r\n ".join(parts)


def _ics_dt(iso: str) -> str:
    """RFC 5545 DATE-TIME is exactly YYYYMMDDTHHMMSS - pad the seconds our
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
        # Z: stored times are UTC. Without it the time floats, and each
        # calendar client shows it at that wall time in its own zone
        return [f"{prop}:{value}Z"]
    return []  # malformed stored timestamp: drop the property, not the feed


# How far back the feed reaches. The table keeps every event, and an
# unbounded ORDER BY starts_at LIMIT fills the feed with the oldest ones, so
# new meetings stop appearing once the history is long.
ICS_LOOKBACK_DAYS = 90


def ics_feed() -> str:
    """Events + open milestone/promise due dates as an iCalendar feed.
    Team-visible data only; keep the feed inside the trusted network (hosted
    calendar clients would mirror titles off-box - prefer local clients)."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Skein//calendar//EN",
        "X-WR-CALNAME:Skein",
    ]
    # RFC 5545 requires DTSTAMP on every VEVENT: the moment this feed was made
    stamp = f"DTSTAMP:{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}Z"
    floor = (db.today() - timedelta(days=ICS_LOOKBACK_DAYS)).isoformat()
    for e in db.query(
        f"SELECT * FROM events WHERE starts_at >= ? AND {WORKSPACE_ONLY}"  # noqa: S608 - scope.WORKSPACE_ONLY is a module constant
        " ORDER BY starts_at LIMIT 500",
        (floor,),
    ):
        start = _ics_dt_lines("DTSTART", e["starts_at"])
        if not start:
            continue
        # rows written before the write path checked the end can still hold
        # one before the start, or of the other kind: leave DTEND out
        end = e["ends_at"] or ""
        valid_end = len(end) == len(e["starts_at"]) and end > e["starts_at"]
        lines += [
            "BEGIN:VEVENT",
            f"UID:event-{e['id']}@skein",
            stamp,
            *start,
            *(_ics_dt_lines("DTEND", end) if valid_end else []),
            f"SUMMARY:{_ics_escape(e['title'])}",
            *([f"DESCRIPTION:{_ics_escape(e['description'])}"] if e["description"] else []),
            "END:VEVENT",
        ]
    for m in db.query(
        f"SELECT id, title, due_date FROM milestones WHERE {WORKSPACE_ONLY}"  # noqa: S608 - scope.WORKSPACE_ONLY is a module constant
        " AND status != 'done' AND due_date IS NOT NULL ORDER BY due_date LIMIT 200"
    ):
        start = _ics_dt_lines("DTSTART", m["due_date"])
        if not start:  # a malformed stored date must not sink the whole feed
            continue
        lines += [
            "BEGIN:VEVENT",
            f"UID:milestone-{m['id']}@skein",
            stamp,
            *start,
            f"SUMMARY:{_ics_escape('due: ' + m['title'])}",
            "END:VEVENT",
        ]
    for c in db.query(
        f"SELECT id, promise, due_date, direction FROM promises WHERE {WORKSPACE_ONLY}"  # noqa: S608 - scope.WORKSPACE_ONLY is a module constant
        " AND status = 'open' AND due_date IS NOT NULL ORDER BY due_date LIMIT 200"
    ):
        start = _ics_dt_lines("DTSTART", c["due_date"])
        if not start:
            continue
        lines += [
            "BEGIN:VEVENT",
            f"UID:promise-{c['id']}@skein",
            stamp,
            *start,
            # the direction is in the WORD: a received promise on a calendar
            # labelled "promised:" reads as the reader's own commitment, and
            # this feed renders inside somebody's mail client beside real
            # meetings
            f"SUMMARY:{_ics_escape(('awaiting: ' if c['direction'] == 'received' else 'promised: ') + c['promise'][:80])}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_ics_fold(line) for line in lines) + "\r\n"


# A meeting older than this with no outcome recorded is worth asking about.
# Hours, not days: a morning meeting must be answerable the same afternoon,
# while the room is still in memory.
OUTCOME_ASK_AFTER_HOURS = 4
# how far back My Day looks for an unanswered meeting. Past this the ask is
# archaeology, and on the morning migration 008 lands it would be a flood.
OUTCOME_ASK_LOOKBACK_DAYS = 7
# How long a recurring meeting has to produce nothing before it is a finding.
OUTCOME_SILENT_WEEKS = 3


def record_outcome(event_id: int, outcome: str, *, actor: str = "system") -> dict:
    """Mark what came out of a meeting. `recorded` or `none` are BOTH answers
    - a meeting that produced nothing is a fact worth having, and the finding
    below counts exactly those."""
    if outcome not in ("recorded", "none"):
        raise ValueError("outcome must be 'recorded' or 'none'")
    row = db.query_one("SELECT * FROM events WHERE id = ?", (event_id,))
    if not row:
        raise scope.missing("events", event_id)
    scope.assert_editable("events", row, actor, verb="update")
    db.execute("UPDATE events SET outcome_status = ? WHERE id = ?", (outcome, event_id))
    db.log_activity(actor, "record_outcome", f"#{event_id} {outcome}")
    return {"id": event_id, "outcome_status": outcome}


def meetings_awaiting_outcome(viewer: scope.Viewer = scope.NOBODY) -> list[dict]:
    """Meetings that have finished and whose outcome nobody has recorded.

    The window opens OUTCOME_ASK_AFTER_HOURS after the start, not at it: a
    meeting is not over when it begins, and asking during it is noise.

    `starts_at` is naive UTC - `_canon` above stores
    `astimezone(UTC).replace(tzinfo=None)` - so the cutoff is naive UTC too. A
    bare `datetime.now()` is the HOST's clock, which is a different instant on
    any machine that is not on UTC: west of it the window opened hours late,
    and east of it it opened during the meeting.
    """
    frag, vp = scope.visible_filter(viewer, "events")
    now = datetime.now(UTC)
    cutoff = (now - timedelta(hours=OUTCOME_ASK_AFTER_HOURS)).strftime("%Y-%m-%dT%H:%M")
    # A date-only row sorts BEFORE every timestamp on its own day
    # ('2026-08-09' < '2026-08-09T04:00'), so the four-hour guard does nothing
    # for an all-day block: it entered the window at 04:00 UTC, during the day
    # it covers. _canon keeps an all-day VEVENT date-only on purpose, so there
    # is no start time to add four hours to.
    #
    # The predicate below asks TODAY OR LATER, not `!= today`. Equality alone
    # compares a team-local date against a naive-UTC window, so in any zone
    # west of about UTC-5 the UTC day rolls over while the local day has not -
    # and tomorrow's all-day block entered the window for the last hours of
    # every evening. Los Angeles, Denver, Anchorage and Honolulu all showed it.
    today_local = db.today().isoformat()
    # A lower bound, or migration 008 puts every meeting in the table's whole
    # history on My Day the morning it is deployed - it defaults them all to
    # 'pending' and backfills nothing. A meeting nobody wrote up inside a week
    # is not going to be written up now.
    #
    # `created_at <= starts_at` below drops the rows that were never a meeting
    # anybody sat in. A playbook ritual is scheduled at a fixed hour on the
    # kickoff DAY (playbooks/*.yaml `time:`), so instantiating one in the
    # afternoon writes a 09:00 event in the past, and this rule asked what came
    # out of it. Writing a meeting down after the fact is the other case, and
    # it needs no ask either: whoever types it in knows what it produced.
    floor = (now - timedelta(days=OUTCOME_ASK_LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M")
    rows = db.query(
        f"SELECT * FROM events WHERE outcome_status = 'pending'"  # noqa: S608 - scope.visible_filter emits only bound marks
        f" AND starts_at < ? AND starts_at >= ? AND created_at <= starts_at"
        f" AND (length(starts_at) > 10 OR starts_at < ?)"
        f" AND {frag} ORDER BY starts_at DESC LIMIT 20",
        (cutoff, floor, today_local, *vp),
    )
    return [with_local(r) for r in rows]
