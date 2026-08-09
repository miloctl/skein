"""What a meeting was for, and whether anything came out of it.

The calendar recorded that time was spent and nothing else. A recurring
meeting that produces nothing for weeks is the most expensive thing on a
team's calendar and the hardest to see, because every instance looks
reasonable on its own.
"""

from datetime import UTC, datetime, timedelta

from app import db
from app.services import insights, schedule, scope


def _past(
    hours: int, title: str = "Weekly sync", attendees: str = "a, b, c", length: float = 1.0
) -> int:
    """A finished meeting. Naive UTC, which is what `_canon` stores — a bare
    `datetime.now()` here would share the bug it is meant to catch."""
    start = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=hours)
    return schedule.schedule_event(
        title,
        start.isoformat(timespec="minutes"),
        ends_at=(start + timedelta(hours=length)).isoformat(timespec="minutes"),
        attendees=attendees,
        agenda="decide the thing",
        actor="ava",
    )["id"]


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
