"""Editing and rescheduling an event: partial updates with a merged time
check, the clear sentinel, no tier change, and an agent edit that a reviewer
reads on the team clock."""

import json
from zoneinfo import ZoneInfo

from conftest import _strong
from fastapi.testclient import TestClient

from app import config


def _event(client, **body):
    r = client.post("/api/events", json={"title": "sync", "starts_at": "2026-10-01T10:00", **body})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_an_empty_field_keeps_its_value(client, fresh_db):
    eid = _event(client, ends_at="2026-10-01T11:00", description="weekly")
    r = client.patch(f"/api/events/{eid}", json={"title": "renamed"})
    assert r.status_code == 200 and r.json()["updated"] == ["title"]
    row = fresh_db.query_one("SELECT * FROM events WHERE id = ?", (eid,))
    assert (row["title"], row["starts_at"], row["ends_at"], row["description"]) == (
        "renamed",
        "2026-10-01T10:00",
        "2026-10-01T11:00",
        "weekly",
    )


def test_the_time_check_reads_the_stored_half(client, fresh_db):
    eid = _event(client, ends_at="2026-10-01T11:00")
    # a new start past the stored end: each half alone looks valid
    r = client.patch(f"/api/events/{eid}", json={"starts_at": "2026-10-01T12:00"})
    assert r.status_code == 400 and "ends_at must be after starts_at" in r.json()["detail"]
    moved = client.patch(
        f"/api/events/{eid}", json={"starts_at": "2026-10-02T12:00", "ends_at": "2026-10-02T13:00"}
    )
    assert moved.status_code == 200
    # a date start with a time end is two kinds
    r = client.patch(f"/api/events/{eid}", json={"starts_at": "2026-10-03"})
    assert r.status_code == 400 and "same kind" in r.json()["detail"]


def test_a_dash_clears_and_a_tier_is_refused(client, fresh_db):
    eid = _event(client, ends_at="2026-10-01T11:00", description="weekly", agenda="a")
    r = client.patch(f"/api/events/{eid}", json={"ends_at": "-", "description": "-"})
    assert r.status_code == 200
    row = fresh_db.query_one("SELECT * FROM events WHERE id = ?", (eid,))
    assert (row["ends_at"], row["description"], row["agenda"]) == (None, "", "a")
    assert client.patch(f"/api/events/{eid}", json={"visibility": "private"}).status_code == 422
    r = client.patch(f"/api/events/{eid}", json={})
    assert r.status_code == 400 and "nothing to update" in r.json()["detail"]


def test_an_edit_refreshes_search(client, fresh_db):
    from app.services import search

    eid = _event(client)
    client.patch(f"/api/events/{eid}", json={"title": "quarterly offsite"})
    assert any(h["entity"] == "event" and h["entity_id"] == eid for h in search.search("offsite"))


def test_a_reviewer_reads_an_agent_move_on_the_team_clock(client, fresh_db, monkeypatch):
    from app.agents.identity import reset_agent_identity, set_agent_identity
    from app.services import users
    from app.tools.schedule import update_event as update_tool

    monkeypatch.setattr(config, "TZ", ZoneInfo("America/Los_Angeles"))
    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    users.ensure_user("scout", kind="agent")
    eid = _event(client, ends_at="2026-10-01T11:00")
    token = set_agent_identity("scout")
    try:
        out = json.loads(
            update_tool(event_id=eid, starts_at="2026-10-01T11:00", ends_at="2026-10-01T12:00")
        )
    finally:
        reset_agent_identity(token)
    assert out.get("note") == "queued for human review"
    diff = client.get(f"/api/review/{out['id']}/diff").json()["diff"]
    assert diff["current"] == {"starts_at": "2026-10-01T10:00", "ends_at": "2026-10-01T11:00"}
    assert diff["proposed"] == {"starts_at": "2026-10-01T11:00", "ends_at": "2026-10-01T12:00"}
    r = client.post(f"/api/review/{out['id']}/approve", json={}, headers=_strong(client))
    assert r.json()["status"] == "approved"
    row = fresh_db.query_one("SELECT starts_at FROM events WHERE id = ?", (eid,))
    assert row["starts_at"] == "2026-10-01T18:00"  # 11:00 in Los Angeles, in UTC


def test_an_edit_of_a_deleted_event_auto_rejects(client, fresh_db, monkeypatch):
    from app.agents.identity import reset_agent_identity, set_agent_identity
    from app.services import schedule, users
    from app.tools.schedule import update_event as update_tool

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    users.ensure_user("scout", kind="agent")
    eid = _event(client)
    token = set_agent_identity("scout")
    try:
        out = json.loads(update_tool(event_id=eid, title="later"))
    finally:
        reset_agent_identity(token)
    schedule.cancel_event(eid, actor="tester")
    r = client.post(f"/api/review/{out['id']}/approve", json={}, headers=_strong(client))
    assert r.status_code == 400 and "auto-rejected" in r.json()["detail"]


def test_a_project_rule_judges_an_event_edit(fresh_db):
    from test_policy_axis import CANARY, _app, _engagements

    from app.services import policy_context, schedule

    _std, reg = _engagements("mallory")
    eid = schedule.schedule_event(
        f"sync {CANARY}", "2026-10-02T10:00", engagement_id=reg, actor="mallory"
    )["id"]
    # the context the gate and the review read, from the STORED link
    ctx = policy_context.for_change("event_edit", eid, {"title": "x"}, actor="mallory")
    assert ctx["project_type"] == "regulated"
    with TestClient(_app(), headers={"X-User": "mallory"}) as c:
        assert c.patch(f"/api/events/{eid}", json={"title": "x"}).status_code == 403
