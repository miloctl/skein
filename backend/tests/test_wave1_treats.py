"""W1.6: ICS feed + review diff."""


def test_ics_feed_open_when_no_token(client, fresh_db):
    client.post(
        "/api/events",
        json={"title": "Weekly ops review", "starts_at": "2026-08-01T15:00"},
    )
    client.post("/api/milestones", json={"title": "Beta", "due_date": "2026-08-14"})
    r = client.get("/api/calendar.ics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/calendar")
    body = r.text
    assert "BEGIN:VCALENDAR" in body and "Weekly ops review" in body
    assert "due: Beta" in body


def test_ics_feed_token_semantics(client, fresh_db, monkeypatch):
    from app import config

    # dedicated feed secret — NEVER the API token (URLs land in calendar
    # configs and access logs)
    monkeypatch.setattr(config, "ICS_TOKEN", "feed-secret")
    assert client.get("/api/calendar.ics").status_code == 401
    assert client.get("/api/calendar.ics?token=feed-secret").status_code == 200
    assert client.get("/api/calendar.ics?token=é").status_code == 401  # not a 500
    # API locked but no feed secret: fail closed, and the API token must NOT work
    monkeypatch.setattr(config, "ICS_TOKEN", "")
    monkeypatch.setattr(config, "API_TOKEN", "sekrit")
    assert client.get("/api/calendar.ics").status_code == 403
    assert client.get("/api/calendar.ics?token=sekrit").status_code == 403


def test_ics_datetime_format_is_rfc5545(client, fresh_db):
    client.post("/api/events", json={"title": "Ops", "starts_at": "2026-08-01T15:00"})
    body = client.get("/api/calendar.ics").text
    assert "DTSTART:20260801T150000" in body  # padded to 15 chars, not 13


def test_review_diff_for_updates(client, fresh_db):
    from app.services import review, work

    t = work.create_task(title="old title", actor="tester")
    p = review.propose_change(
        "task",
        "update",
        {"title": "new title", "status": "in_progress"},
        entity_id=t["id"],
        actor="agent",
    )
    d = client.get(f"/api/review/{p['id']}/diff").json()
    assert d["diff"]["current"]["title"] == "old title"
    assert d["diff"]["proposed"]["title"] == "new title"
    # creates have no diff
    p2 = review.propose_change("task", "create", {"title": "x"}, actor="agent")
    assert client.get(f"/api/review/{p2['id']}/diff").json()["diff"] is None
