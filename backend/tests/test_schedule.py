"""Calendar events: cancel deindexes, agent cancels are always proposals, and bad legacy rows never break the feed."""

import pytest
from conftest import _strong


def test_cancel_event_deindexes_and_404s_on_missing(client):
    from app.services import schedule, search

    e = schedule.schedule_event("Quarterly offsite kickoff", "2026-08-01T10:00")
    assert any(h["entity"] == "event" for h in search.search("offsite"))

    assert client.delete(f"/api/events/{e['id']}").json()["cancelled"] is True
    assert [h for h in search.search("offsite") if h["entity"] == "event"] == []
    assert client.delete(f"/api/events/{e['id']}").status_code == 404


def test_event_cancel_is_always_a_proposal(client, fresh_db, monkeypatch):
    import json as j

    from app import config
    from app.agents.identity import reset_agent_identity, set_agent_identity
    from app.services import schedule, users
    from app.tools.schedule import cancel_event as cancel_tool

    monkeypatch.setattr(config, "AGENT_REVIEW", False)  # ALWAYS_REVIEW must not care
    users.ensure_user("scout", kind="agent")
    e = schedule.schedule_event("standup sync", "2026-08-10T10:00")
    token = set_agent_identity("scout")
    try:
        out = j.loads(cancel_tool(event_id=e["id"]))
    finally:
        reset_agent_identity(token)
    assert out.get("note") == "queued for human review"
    assert fresh_db.query_one("SELECT id FROM events WHERE id = ?", (e["id"],))
    # the reviewer sees what would be destroyed
    diff = client.get(f"/api/review/{out['id']}/diff").json()["diff"]
    assert diff["current"]["title"] == "standup sync"
    r = client.post(f"/api/review/{out['id']}/approve", json={}, headers=_strong(client))
    assert r.json()["status"] == "approved"
    assert not fresh_db.query_one("SELECT id FROM events WHERE id = ?", (e["id"],))


def test_doomed_event_cancel_proposal_auto_rejects(client, fresh_db, monkeypatch):
    import json as j

    from app import config
    from app.agents.identity import reset_agent_identity, set_agent_identity
    from app.services import schedule, users
    from app.tools.schedule import cancel_event as cancel_tool

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    users.ensure_user("scout", kind="agent")
    e = schedule.schedule_event("doomed", "2026-08-11T10:00")
    token = set_agent_identity("scout")
    try:
        out = j.loads(cancel_tool(event_id=e["id"]))
    finally:
        reset_agent_identity(token)
    schedule.cancel_event(e["id"], actor="mira")  # REST got there first
    r = client.post(f"/api/review/{out['id']}/approve", json={}, headers=_strong(client))
    assert r.status_code == 400 and "auto-rejected" in r.json()["detail"]
    row = fresh_db.query_one(
        "SELECT status, review_note FROM pending_changes WHERE id = ?", (out["id"],)
    )
    assert row["status"] == "rejected" and "target vanished" in row["review_note"]


def test_events_from_date_filter(client):
    client.post("/api/events", json={"title": "past sync", "starts_at": "2020-01-01T10:00"})
    client.post("/api/events", json={"title": "future sync", "starts_at": "2099-01-01T10:00"})
    all_titles = [e["title"] for e in client.get("/api/events").json()]
    assert {"past sync", "future sync"} <= set(all_titles)
    upcoming = [
        e["title"] for e in client.get("/api/events", params={"from_date": "2098-12-31"}).json()
    ]
    assert upcoming == ["future sync"]


def test_dates_are_validated_and_ics_survives_bad_legacy_rows(client, fresh_db):
    from app.services import engagements, promises, users, work

    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        work.create_task(title="t", due_date="soon")
    with pytest.raises(ValueError, match="real date"):
        work.create_milestone("m", due_date="2026-02-31")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        promises.add_promise("p", due_date="07/30/2026")
    users.ensure_user("mira")
    e = engagements.create_engagement("Dated")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        engagements.allocate("mira", e["id"], 50, starts_on="tomorrow")
    # clear sentinel still passes
    t = work.create_task(title="ok", due_date="2026-08-01")
    work.update_task(t["id"], due_date="-", actor="mira")
    # a bad date already in the DB (pre-validation rows) must not sink the feed
    fresh_db.execute(
        "INSERT INTO promises (promise, due_date, status, audience, created_by,"
        " created_at, updated_at) VALUES ('legacy', 'soon', 'open', 'external', 'mira', ?, ?)",
        (fresh_db.now(), fresh_db.now()),
    )
    feed = client.get("/api/calendar.ics").text
    assert "soon" not in feed and feed.rstrip().endswith("END:VCALENDAR")


# ---- the team zone: input is team wall time, storage is UTC, output is local ----


def _team_zone(monkeypatch, name):
    from zoneinfo import ZoneInfo

    from app import config

    monkeypatch.setattr(config, "TZ", ZoneInfo(name))


def test_a_start_without_an_offset_is_team_wall_time(client, fresh_db, monkeypatch):
    """The dashboard's datetime-local field, playbook rituals, and the agent
    tool all send a naive time and mean the team's clock. Stored as typed, it
    read as UTC everywhere that converts (the day window, the outcome ask)."""
    from app.services import schedule

    _team_zone(monkeypatch, "America/New_York")
    naive = schedule.schedule_event("Standup", "2026-09-23T09:00")
    offset = schedule.schedule_event("Review", "2026-09-23T09:00-04:00")
    assert naive["starts_at"] == offset["starts_at"] == "2026-09-23T13:00"


def test_event_rows_carry_the_team_wall_time(client, fresh_db, monkeypatch):
    from app.services import schedule

    _team_zone(monkeypatch, "America/New_York")
    schedule.schedule_event("Standup", "2026-09-23T09:00", "2026-09-23T09:30")
    schedule.schedule_event("Offsite", "2026-09-24")
    rows = {r["title"]: r for r in client.get("/api/events").json()}
    assert rows["Standup"]["starts_local"] == "2026-09-23T09:00"
    assert rows["Standup"]["ends_local"] == "2026-09-23T09:30"
    assert rows["Offsite"]["starts_local"] == "2026-09-24"


def test_the_digest_prints_event_times_on_the_team_clock(client, fresh_db, monkeypatch):
    from app import db
    from app.services import digest, schedule

    _team_zone(monkeypatch, "America/New_York")
    day = db.today().isoformat()
    # stored as 13:00 UTC; printed raw, the team reads a 09:00 meeting as 13:00
    schedule.schedule_event("Standup", f"{day}T09:00-04:00")
    text = digest.build_digest()
    line = next(x for x in text.splitlines() if "Standup" in x)
    assert "09:00" in line and "13:00" not in line


@pytest.mark.parametrize("zone", ["UTC", "America/Los_Angeles", "Asia/Tokyo"])
def test_an_all_day_event_is_listed_on_its_own_day_only(client, fresh_db, monkeypatch, zone):
    """A date-only row sorts before every timestamp of its own day, so a
    window of timestamps dropped it at and west of UTC, and in the west it
    admitted tomorrow's all-day event instead."""
    from datetime import timedelta

    from app import db
    from app.services import schedule

    _team_zone(monkeypatch, zone)
    today = db.today()
    schedule.schedule_event("Offsite today", today.isoformat())
    schedule.schedule_event("Offsite tomorrow", (today + timedelta(days=1)).isoformat())
    schedule.schedule_event("Offsite yesterday", (today - timedelta(days=1)).isoformat())
    assert [e["title"] for e in schedule.team_day_events(today)] == ["Offsite today"]


def test_the_ics_feed_marks_timed_events_as_utc_and_stamps_them(client, fresh_db, monkeypatch):
    """A time without Z is floating: every calendar client shows it at that
    wall time in ITS OWN zone, so two readers see one meeting at two hours."""
    from app.services import schedule

    _team_zone(monkeypatch, "America/New_York")
    schedule.schedule_event("Standup", "2026-09-23T09:00")
    body = client.get("/api/calendar.ics").text
    assert "DTSTART:20260923T130000Z" in body
    events = body.split("BEGIN:VEVENT")[1:]
    assert events and all("DTSTAMP:" in e for e in events)


def test_the_ics_feed_keeps_upcoming_events_behind_a_long_history(client, fresh_db):
    from datetime import timedelta

    from app import db

    old = db.today() - timedelta(days=400)
    for i in range(510):
        db.execute(
            "INSERT INTO events (title, starts_at, origin, created_by, created_at, visibility)"
            " VALUES (?, ?, 'human', 'tester', ?, 'workspace')",
            (f"old {i}", (old + timedelta(hours=i)).isoformat(), db.now()),
        )
    client.post(
        "/api/events",
        json={
            "title": "Next week kickoff",
            "starts_at": (db.today() + timedelta(days=7)).isoformat(),
        },
    )
    assert "Next week kickoff" in client.get("/api/calendar.ics").text


@pytest.mark.parametrize(
    "starts, ends",
    [("2026-09-23T10:00", "2026-09-23T09:00"), ("2026-09-23", "2026-09-23T17:00")],
)
def test_an_end_before_the_start_or_of_another_kind_is_refused(client, fresh_db, starts, ends):
    r = client.post("/api/events", json={"title": "Bad", "starts_at": starts, "ends_at": ends})
    assert r.status_code == 400
