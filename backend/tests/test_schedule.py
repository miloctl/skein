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
