"""What a meeting was for, and whether anything came out of it.

The calendar recorded that time was spent and nothing else. A recurring
meeting that produces nothing for weeks is the most expensive thing on a
team's calendar and the hardest to see, because every instance looks
reasonable on its own.
"""

from datetime import UTC, date, datetime, timedelta

from app import db
from app.services import insights, schedule, scope


def _past(
    hours: int, title: str = "Weekly sync", attendees: str = "a, b, c", length: float = 1.0
) -> int:
    """A finished meeting. Naive UTC, which is what `_canon` stores — a bare
    `datetime.now()` here would share the bug it is meant to catch."""
    start = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=hours)
    eid = schedule.schedule_event(
        title,
        start.isoformat(timespec="minutes"),
        ends_at=(start + timedelta(hours=length)).isoformat(timespec="minutes"),
        attendees=attendees,
        agenda="decide the thing",
        actor="ava",
    )["id"]
    # A meeting is on the calendar BEFORE it runs. Creating a past-dated event
    # right now is a different thing entirely — a playbook ritual, or a meeting
    # typed in afterwards — and meetings_awaiting_outcome excludes those on
    # purpose, so a fixture that skips this pins the wrong rule.
    db.execute(
        "UPDATE events SET created_at = ? WHERE id = ?",
        ((datetime.now(UTC) - timedelta(days=1, hours=hours)).isoformat(), eid),
    )
    return eid


def test_a_finished_meeting_with_no_outcome_reaches_my_day(client):
    _past(schedule.OUTCOME_ASK_AFTER_HOURS + 1)
    items = client.get("/api/briefing").json()["attention"]
    meeting = next(i for i in items if i["kind"] == "meeting")
    assert meeting["group"] == "notice"
    # the agenda is what makes "did this produce anything" answerable
    assert "decide the thing" in meeting["reason"]
    assert meeting["link"] == "/ingest"


def test_a_meeting_still_running_is_not_asked_about(client):
    """The window opens hours after the start, not at it: a meeting is not
    over when it begins, and asking during it is noise."""
    _past(0)
    items = client.get("/api/briefing").json()["attention"]
    assert not [i for i in items if i["kind"] == "meeting"]


def test_recording_an_outcome_clears_it(client):
    eid = _past(schedule.OUTCOME_ASK_AFTER_HOURS + 1)
    r = client.post(f"/api/events/{eid}/outcome", json={"outcome": "recorded"})
    assert r.status_code == 200
    items = client.get("/api/briefing").json()["attention"]
    assert not [i for i in items if i["kind"] == "meeting"]


def test_none_is_an_answer_too(client):
    """A meeting that produced nothing is a fact worth having — it is what the
    weekly finding counts."""
    eid = _past(schedule.OUTCOME_ASK_AFTER_HOURS + 1)
    client.post(f"/api/events/{eid}/outcome", json={"outcome": "none"})
    assert (
        db.query_one("SELECT outcome_status FROM events WHERE id = ?", (eid,))["outcome_status"]
        == "none"
    )
    assert client.post(f"/api/events/{eid}/outcome", json={"outcome": "maybe"}).status_code == 400


def test_a_recurring_silent_meeting_becomes_a_finding_with_its_cost(client):
    # every instance on a PAST day: the rule excludes today, because a meeting
    # from this morning can still record an outcome this afternoon
    for week in range(schedule.OUTCOME_SILENT_WEEKS):
        _past(24 * (1 + 7 * week) + 5, title="Weekly sync", attendees="a, b, c, d")
    insights.run_findings()
    got = [f for f in insights.list_findings() if f["rule_id"] == "meeting_no_outcome"]
    assert got, "a recurring meeting with no outcome fired nothing"
    # the receipt is the argument: without hours burned this is an opinion
    # about somebody's calendar
    # four attendees for one hour, three times over
    assert got[0]["receipt"]["attendee_hours"] == 12.0
    assert got[0]["receipt"]["instances"] >= schedule.OUTCOME_SILENT_WEEKS
    assert "12.0 attendee-hours" in got[0]["message"]


def test_an_all_day_block_contributes_no_hours_rather_than_twenty_four(client):
    """A date-only row is an all-day block whose real length nobody recorded.
    Counting the calendar span put a three-day offsite at 144 attendee-hours,
    which is the kind of number a manager checks and then stops trusting."""
    for week in range(schedule.OUTCOME_SILENT_WEEKS):
        day = (db.today() - timedelta(days=1 + 7 * week)).isoformat()
        schedule.schedule_event("Offsite", day, attendees="a, b", actor="ava")
    insights.run_findings()
    got = [f for f in insights.list_findings() if f["rule_id"] == "meeting_no_outcome"]
    assert got and got[0]["receipt"]["attendee_hours"] == 0
    assert "attendee-hours" not in got[0]["message"]
    assert f"ran {schedule.OUTCOME_SILENT_WEEKS} times" in got[0]["message"]


def test_a_meeting_named_after_a_person_is_never_a_finding(client):
    """A 1:1 is both the meeting most likely to produce no recordable outcome
    and the one whose TITLE is two people's names. This finding is team-wide,
    and a named person's wasted hours is a judgment of the past."""
    from app.services import users

    users.ensure_user("mira")
    for week in range(schedule.OUTCOME_SILENT_WEEKS):
        _past(24 * (1 + 7 * week) + 5, title="1:1 Mira / ava")
    insights.run_findings()
    assert not [f for f in insights.list_findings() if f["rule_id"] == "meeting_no_outcome"]


def test_a_meeting_that_recorded_its_outcome_is_not_in_the_finding(client):
    for week in range(schedule.OUTCOME_SILENT_WEEKS):
        eid = _past(24 * (1 + 7 * week) + 5, title="Weekly sync")
        schedule.record_outcome(eid, "recorded", actor="ava")
    insights.run_findings()
    assert not [f for f in insights.list_findings() if f["rule_id"] == "meeting_no_outcome"]


def test_the_awaiting_list_is_viewer_scoped(client):
    eid = _past(schedule.OUTCOME_ASK_AFTER_HOURS + 1)
    db.execute("UPDATE events SET visibility = 'private' WHERE id = ?", (eid,))
    assert schedule.meetings_awaiting_outcome(scope.Viewer("someone-else", True)) == []


def test_the_interrupt_ratio_becomes_a_finding(client):
    """The interrupt ledger shipped with the cockpit and nothing read it, so
    the number reached whoever opened that page on Monday and nobody else."""
    from app.services import portfolio, work

    week = db.today().strftime("%G-W%V")
    for i in range(9):
        t = work.create_task(f"unplanned {i}", actor="ava")["id"]
        work.update_task(t, status="done", actor="ava")
    # every one finished in the week it was created and was never committed,
    # which is exactly what the ledger counts as unplanned
    assert portfolio.flow_metrics()["interrupts"]["unplanned"] >= 8
    insights.run_findings()
    got = [f for f in insights.list_findings() if f["rule_id"] == "interrupt_load"]
    assert got, f"nothing fired for week {week}"
    assert "commitment line" in got[0]["message"]
    assert "belonged to" in got[0]["message"]
    assert got[0]["receipt"]["unplanned"] >= 8


def test_the_interrupt_rule_is_silent_under_the_small_n_floor(client):
    """ "50% was unplanned" over two tasks is noise wearing a percentage. Every
    other verdict in this engine is withheld the same way."""
    from app.services import work

    for i in range(2):
        t = work.create_task(f"unplanned {i}", actor="ava")["id"]
        work.update_task(t, status="done", actor="ava")
    insights.run_findings()
    assert not [f for f in insights.list_findings() if f["rule_id"] == "interrupt_load"]


def test_a_meeting_written_down_after_it_started_is_not_asked_about(client):
    """A playbook ritual is scheduled at a fixed hour on the kickoff DAY, so
    instantiating one in the afternoon writes an event in the past that nobody
    sat in. Skein asked what came out of a meeting nobody attended."""
    from app.services import playbooks

    made = playbooks.instantiate("incident", "Afternoon kickoff", lead="ava", actor="ava")
    titles = {e["title"] for e in made["events"]}
    asked = {e["title"] for e in schedule.meetings_awaiting_outcome(scope.Viewer("ava", True))}
    assert not (titles & asked), "a ritual created after its own start time was asked about"

    # a meeting that WAS on the calendar before it ran is still asked about
    eid = _past(schedule.OUTCOME_ASK_AFTER_HOURS + 1)
    assert eid in {e["id"] for e in schedule.meetings_awaiting_outcome(scope.Viewer("ava", True))}


def test_a_departed_teammates_meeting_is_still_never_named(client):
    """`list_users()` defaults to active members only, so a teammate who left
    dropped out of the guard while their recurring 1:1 stayed in the table.
    This finding reaches the digest and the exec readout, which leaves."""
    from app.services import users

    users.ensure_user("Priya")
    db.execute("UPDATE users SET active = 0 WHERE name = 'Priya'")
    for week in range(schedule.OUTCOME_SILENT_WEEKS):
        _past(24 * (1 + 7 * week) + 5, title="Priya weekly review")
    insights.run_findings()
    named = [f for f in insights.list_findings() if f["rule_id"] == "meeting_no_outcome"]
    assert not [f for f in named if "Priya" in f["message"]]


def test_an_event_that_is_not_there_is_a_404_not_a_400(client):
    """The id is in the PATH, which scope.missing_text says is the 404 case.
    db.NotFound subclasses ValueError, so a bare except swallowed it — and the
    sibling stakeholders route already answers 404 for the same row."""
    assert client.post("/api/events/9999/outcome", json={"outcome": "none"}).status_code == 404
    # a bad VALUE in the body stays a 400: the addressed row exists
    eid = _past(schedule.OUTCOME_ASK_AFTER_HOURS + 1)
    assert client.post(f"/api/events/{eid}/outcome", json={"outcome": "maybe"}).status_code == 400


def test_an_ordinary_title_is_not_suppressed_by_a_short_name(client):
    """`name in title` is the trap: a roster holding Ram, Ian and Ana
    suppressed "Program review", "Alliance sync" and "Analytics review" —
    three ordinary titles out of four, and silently, because a suppressed
    finding looks exactly like a team with nothing wrong."""
    from app.services import users

    for n in ("Ram", "Ian", "Ana"):
        users.ensure_user(n)
    for title in ("Program review", "Alliance sync", "Analytics review", "Roadmap sync"):
        for week in range(schedule.OUTCOME_SILENT_WEEKS):
            _past(24 * (1 + 7 * week) + 5, title=title, attendees="a, b, c, d, e, f")
    insights.run_findings()
    fired = {
        f["receipt"]["title"]
        for f in insights.list_findings()
        if f["rule_id"] == "meeting_no_outcome"
    }
    assert fired == {"Program review", "Alliance sync", "Analytics review", "Roadmap sync"}

    # and the guard still holds where it is meant to
    for week in range(schedule.OUTCOME_SILENT_WEEKS):
        _past(24 * (1 + 7 * week) + 5, title="1:1 Ram / Ana", attendees="a, b")
    insights.run_findings()
    assert not [
        f
        for f in insights.list_findings()
        if f["rule_id"] == "meeting_no_outcome" and "1:1" in f["receipt"]["title"]
    ]


def test_an_all_day_block_today_is_not_asked_about_during_it(client, monkeypatch):
    """A date-only row sorts before every timestamp on its own day, so the
    four-hour "not during the meeting" guard did nothing for an all-day block
    — it entered the window at 04:00 UTC, during the day it covers."""

    # The clock is FROZEN mid-day. Unfrozen, the bug is `starts_at < now - 4h`,
    # so a date-only row only enters the window once UTC passes 04:00 — and
    # this test passed against the broken code on any run before then. A CI
    # job that happens to run overnight gets a green from a test that
    # discriminates nothing.
    class _Noon(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 9, 12, 30, tzinfo=tz or UTC)

    monkeypatch.setattr(schedule, "datetime", _Noon)
    today = db.today().isoformat()
    eid = schedule.schedule_event("All-hands offsite", today, attendees="a, b", actor="ava")["id"]
    db.execute(
        "UPDATE events SET created_at = ? WHERE id = ?",
        ((datetime.now(UTC) - timedelta(days=3)).isoformat(), eid),
    )
    asked = {e["id"] for e in schedule.meetings_awaiting_outcome(scope.Viewer("ava", True))}
    assert eid not in asked

    # yesterday's all-day block is over, and is still asked about
    past = schedule.schedule_event(
        "Past offsite", (db.today() - timedelta(days=1)).isoformat(), attendees="a", actor="ava"
    )["id"]
    db.execute(
        "UPDATE events SET created_at = ? WHERE id = ?",
        ((datetime.now(UTC) - timedelta(days=3)).isoformat(), past),
    )
    assert past in {e["id"] for e in schedule.meetings_awaiting_outcome(scope.Viewer("ava", True))}


def test_a_future_all_day_block_is_silent_when_the_utc_day_has_turned(client, monkeypatch):
    """`starts_at != today` compared a TEAM-LOCAL date against a naive-UTC
    window. West of about UTC-5 the UTC day rolls over while the local day has
    not, so tomorrow's all-day block entered the window for the last hours of
    every evening — measured in Los Angeles, Denver, Anchorage and Honolulu.

    Patching db.today and the clock together IS that state: 06:00 UTC on the
    10th is 23:00 on the 9th in Denver.
    """

    class _Rolled(datetime):
        @classmethod
        def now(cls, tzinfo=None):
            return datetime(2026, 8, 10, 6, 0, tzinfo=tzinfo or UTC)

    monkeypatch.setattr(schedule, "datetime", _Rolled)
    monkeypatch.setattr(schedule.db, "today", lambda: date(2026, 8, 9))

    eid = schedule.schedule_event("Offsite", "2026-08-10", attendees="a", actor="ava")["id"]
    db.execute("UPDATE events SET created_at = ? WHERE id = ?", ("2026-08-01T09:00+00:00", eid))
    asked = {e["id"] for e in schedule.meetings_awaiting_outcome(scope.Viewer("ava", True))}
    assert eid not in asked, "asked about an all-day block that has not started"

    # yesterday's all-day block is over, and is still asked about
    over = schedule.schedule_event("Past offsite", "2026-08-08", attendees="a", actor="ava")["id"]
    db.execute("UPDATE events SET created_at = ? WHERE id = ?", ("2026-08-01T09:00+00:00", over))
    assert over in {e["id"] for e in schedule.meetings_awaiting_outcome(scope.Viewer("ava", True))}
