"""Team calendar services."""

import re
from datetime import UTC, date, datetime, timedelta

from .. import config, db
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
    # the same guard add_promise puts on its own engagement link: unchecked,
    # a bad id raises IntegrityError (a 500 from a value the caller sent) and
    # a readable-looking id lets an event attach to another crew's private
    # engagement — which migration 008 exists to attribute hours to
    if engagement_id:
        efrag, ep = scope.visible_filter(scope.Viewer.for_actor(actor), "engagements")
        if not db.query_one(
            f"SELECT id FROM engagements WHERE id = ? AND {efrag}",  # noqa: S608 — scope.visible_filter emits only bound marks
            (engagement_id, *ep),
        ):
            raise ValueError(scope.missing_text("engagements", engagement_id))

    def _canon(label: str, value: str) -> str:
        # normalize at write time: fromisoformat accepts space separators and
        # offsets, but the ICS builder (and string comparisons) only survive
        # the plain YYYY-MM-DDTHH:MM shape — store exactly that, in UTC
        try:
            dt = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            raise ValueError(
                f"{label} must be an ISO timestamp (for example 2026-07-24T15:00)"
            ) from None
        if len(value) == 10:
            return value  # date-only stays a date: an all-day VEVENT, not midnight
        # no offset means the TEAM's clock: the dashboard's datetime-local
        # field, playbook rituals and the agent tool all send one. Stored as
        # typed, it reads as UTC in every reader that converts (db.local_wall)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=config.TZ)
        return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M")

    starts_at = _canon("starts_at", starts_at)
    ends_at = _canon("ends_at", ends_at) if ends_at else ""
    # an ICS client drops or misplaces an event whose DTEND precedes DTSTART,
    # or whose start is a date and whose end is a time
    if ends_at and (len(ends_at) != len(starts_at) or ends_at <= starts_at):
        raise ValueError("ends_at must be after starts_at, and of the same kind (date or time)")
    from .search import index_record

    with db.transaction():
        tier, crew = scope.resolve_write(visibility, crew_id, actor=actor)
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
        rows = db.query(
            f"SELECT * FROM events WHERE starts_at >= ? AND {frag} ORDER BY starts_at LIMIT ?",  # noqa: S608 — scope.visible_filter emits only bound marks
            (from_date, *vp, limit),
        )
    else:
        rows = db.query(
            f"SELECT * FROM events WHERE {frag} ORDER BY starts_at LIMIT ?",  # noqa: S608 — scope.visible_filter emits only bound marks
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


def team_day_events(d: date) -> list[dict]:
    """Workspace events on team-day d. A date-only row sorts before every
    timestamp of its own day, so a window of timestamps alone drops it at and
    west of UTC, and admits tomorrow's all-day row in the west."""
    start, end = db.local_event_window(d)
    rows = db.query(
        f"SELECT * FROM events WHERE {WORKSPACE_ONLY}"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        " AND ((length(starts_at) > 10 AND starts_at >= ? AND starts_at < ?) OR starts_at = ?)"
        " ORDER BY starts_at",
        (start, end, d.isoformat()),
    )
    return [with_local(r) for r in rows]


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
    calendar clients would mirror titles off-box — prefer local clients)."""
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
        f"SELECT * FROM events WHERE starts_at >= ? AND {WORKSPACE_ONLY}"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
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
        f"SELECT id, title, due_date FROM milestones WHERE {WORKSPACE_ONLY}"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
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
        f"SELECT id, promise, due_date, direction FROM promises WHERE {WORKSPACE_ONLY}"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
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
    return "\r\n".join(lines) + "\r\n"


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
    — a meeting that produced nothing is a fact worth having, and the finding
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

    `starts_at` is naive UTC — `_canon` above stores
    `astimezone(UTC).replace(tzinfo=None)` — so the cutoff is naive UTC too. A
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
    # west of about UTC-5 the UTC day rolls over while the local day has not —
    # and tomorrow's all-day block entered the window for the last hours of
    # every evening. Los Angeles, Denver, Anchorage and Honolulu all showed it.
    today_local = db.today().isoformat()
    # A lower bound, or migration 008 puts every meeting in the table's whole
    # history on My Day the morning it is deployed — it defaults them all to
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
        f"SELECT * FROM events WHERE outcome_status = 'pending'"  # noqa: S608 — scope.visible_filter emits only bound marks
        f" AND starts_at < ? AND starts_at >= ? AND created_at <= starts_at"
        f" AND (length(starts_at) > 10 OR starts_at < ?)"
        f" AND {frag} ORDER BY starts_at DESC LIMIT 20",
        (cutoff, floor, today_local, *vp),
    )
    return [with_local(r) for r in rows]
