"""The calendar range read: overlap on the team clock, exclusive all-day ends,
tier and policy filtering per kind, and time away that plans the future."""

from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.services import scope


def _range(start, end, viewer=None, **kw):
    from app.services import schedule

    # a Viewer resolves its crews once, so each call builds its own
    return schedule.calendar_range(start, end, viewer or scope.Viewer("tester", True), **kw)


def _titles(out, kind="events", key="title"):
    return {r[key] for r in out[kind]}


@pytest.mark.parametrize(
    ("params", "said"),
    [
        ({"start": "2026-10-01", "end": "2026-11-12"}, "at most 42 days"),
        ({"start": "2026-10-10", "end": "2026-10-01"}, "on or after start"),
        ({"start": "", "end": "2026-10-01"}, "start is required"),
        ({"start": "2026-02-30", "end": "2026-03-01"}, "start"),
    ],
)
def test_a_bad_range_is_a_400(client, params, said):
    r = client.get("/api/calendar", params=params)
    assert r.status_code == 400 and said in r.json()["detail"]


def test_a_meeting_that_started_before_the_window_is_on_it(fresh_db):
    from app.services import schedule

    schedule.schedule_event("offsite", "2026-09-30T15:00", "2026-10-02T12:00")
    schedule.schedule_event("ended at the edge", "2026-09-30T20:00", "2026-10-01T00:00")
    out = _range("2026-10-01", "2026-10-07")
    assert "offsite" in _titles(out)
    # an end exactly at the window start covers none of it
    assert "ended at the edge" not in _titles(out)


def test_the_team_clock_decides_the_day(fresh_db, monkeypatch):
    from app.services import schedule

    monkeypatch.setattr(config, "TZ", ZoneInfo("America/Los_Angeles"))
    # 23:30 on the team clock is 06:30 UTC the next day
    schedule.schedule_event("late call", "2026-10-01T23:30")
    assert "late call" in _titles(_range("2026-10-01", "2026-10-01"))
    assert "late call" not in _titles(_range("2026-10-02", "2026-10-02"))
    (row,) = _range("2026-10-01", "2026-10-01")["events"]
    assert row["starts_local"] == "2026-10-01T23:30"


def test_an_all_day_end_is_exclusive(fresh_db):
    from app.services import schedule

    schedule.schedule_event("one day", "2026-10-01", "2026-10-02")
    schedule.schedule_event("three days", "2026-10-01", "2026-10-04")
    schedule.schedule_event("no end", "2026-10-05")
    assert _titles(_range("2026-10-01", "2026-10-01")) == {"one day", "three days"}
    assert _titles(_range("2026-10-02", "2026-10-03")) == {"three days"}
    assert _titles(_range("2026-10-04", "2026-10-05")) == {"no end"}


def test_each_kind_reads_at_the_viewers_tier(fresh_db):
    from app.services import crews, promises, schedule, users, work

    for name in ("tester", "ana", "ben"):
        users.ensure_user(name)
    crew = crews.create_crew("ops", actor="ana")
    crews.add_member(crew["id"], "tester", actor="ana")
    schedule.schedule_event("team sync", "2026-10-02T10:00")
    schedule.schedule_event("ana alone", "2026-10-02T11:00", actor="ana", visibility="private")
    schedule.schedule_event("mine alone", "2026-10-02T12:00", actor="tester", visibility="private")
    schedule.schedule_event(
        "ops only", "2026-10-02T13:00", actor="ana", visibility="crew", crew_id=crew["id"]
    )
    work.create_task("ana's secret", due_date="2026-10-03", actor="ana", visibility="private")
    work.create_task("shared task", due_date="2026-10-03", actor="ana")
    work.create_task("finished", due_date="2026-10-03", actor="ana")
    done = db.query_one("SELECT id FROM tasks WHERE title = 'finished'")["id"]
    work.update_task(done, status="done", actor="ana")
    promises.add_promise("ana's promise", "Acme", "2026-10-04", actor="ana", visibility="private")
    promises.add_promise("team promise", "Acme", "2026-10-04", actor="ana")

    out = _range("2026-10-01", "2026-10-07")
    assert _titles(out) == {"team sync", "mine alone", "ops only"}
    assert _titles(out, "tasks") == {"shared task"}
    assert _titles(out, "promises", "promise") == {"team promise"}
    ben = _range("2026-10-01", "2026-10-07", scope.Viewer("ben", True))
    assert _titles(ben) == {"team sync"}


def test_only_my_tasks_narrows_to_the_caller(client):
    from app.services import work

    work.create_task("for me", due_date="2026-10-03", assignee="tester", actor="tester")
    work.create_task("for ana", due_date="2026-10-03", assignee="ana", actor="tester")
    params = {"start": "2026-10-01", "end": "2026-10-07"}
    assert _titles(client.get("/api/calendar", params=params).json(), "tasks") == {
        "for me",
        "for ana",
    }
    mine = client.get("/api/calendar", params={**params, "mine": "true"}).json()
    assert _titles(mine, "tasks") == {"for me"}


def test_time_away_shows_others_only_from_today(fresh_db):
    from app.services import absences, users

    for name in ("tester", "ana", "ben", "cy"):
        users.ensure_user(name)
    today = db.today()

    def day(n):
        return (today + timedelta(days=n)).isoformat()

    absences.add_absence("ana", day(-10), day(-5), actor="ana", visibility="workspace")
    absences.add_absence("tester", day(-10), day(-5), actor="tester")
    absences.add_absence("ana", day(3), day(4), "focus", actor="ana", visibility="workspace")
    absences.add_absence("ben", day(3), day(4), "oncall", "pager", actor="ben", dates_shared=True)
    absences.add_absence("cy", day(3), day(4), actor="cy")  # only me: no team effect

    out = _range(day(-14), day(14))
    by_person = {}
    for row in out["time_away"]:
        by_person.setdefault(row["person"], []).append(row)
    # another person's past window judges them; your own is yours
    assert [r["starts_on"] for r in by_person["ana"]] == [day(3)]
    assert by_person["ana"][0]["kind"] == "focus"
    assert [r["starts_on"] for r in by_person["tester"]] == [day(-10)]
    # dates shared, reason kept: masked the way capacity_ahead masks it
    assert by_person["ben"] == [
        {"person": "ben", "kind": "away", "starts_on": day(3), "ends_on": day(4)}
    ]
    assert "cy" not in by_person


def test_a_kind_at_its_limit_is_named(fresh_db, monkeypatch):
    from app.services import schedule, work

    monkeypatch.setattr(schedule, "CALENDAR_LIMIT", 1)
    work.create_task("one", due_date="2026-10-03", actor="tester")
    work.create_task("two", due_date="2026-10-04", actor="tester")
    out = _range("2026-10-01", "2026-10-07")
    assert [t["title"] for t in out["tasks"]] == ["one"]
    assert out["truncated"] == ["tasks"]


def test_one_event_reads_at_its_tier(client):
    from app.services import schedule, users

    users.ensure_user("ana")
    team = schedule.schedule_event("team sync", "2026-10-02T10:00")["id"]
    hidden = schedule.schedule_event(
        "ana alone", "2026-10-02T11:00", actor="ana", visibility="private"
    )
    r = client.get(f"/api/events/{team}")
    assert r.status_code == 200 and r.json()["starts_local"] == "2026-10-02T10:00"
    missing = client.get(f"/api/events/{hidden['id']}")
    absent = client.get("/api/events/999999")
    assert missing.status_code == absent.status_code == 404
    assert missing.json()["detail"].replace(str(hidden["id"]), "N") == absent.json()[
        "detail"
    ].replace("999999", "N")


def test_the_calendar_honors_the_project_rule(fresh_db):
    from test_policy_axis import CANARY, _app, _engagements

    from app.services import schedule, work

    std, reg = _engagements("mallory")
    schedule.schedule_event("standard sync", "2026-10-02T10:00", engagement_id=std, actor="mallory")
    blocked = schedule.schedule_event(
        f"sync {CANARY}", "2026-10-02T11:00", engagement_id=reg, actor="mallory"
    )["id"]
    work.create_task("standard task", due_date="2026-10-03", engagement_id=std, actor="mallory")
    work.create_task(f"task {CANARY}", due_date="2026-10-03", engagement_id=reg, actor="mallory")
    with TestClient(_app(), headers={"X-User": "mallory"}) as c:
        r = c.get("/api/calendar", params={"start": "2026-10-01", "end": "2026-10-07"})
        assert r.status_code == 200 and CANARY not in r.text
        assert "standard sync" in r.text and "standard task" in r.text
        assert c.get(f"/api/events/{blocked}").status_code == 403
