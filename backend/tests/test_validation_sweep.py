"""Fixes from the 2026-07-25 full-app validation sweep."""


def test_users_listing_excludes_anonymous(client):
    client.get("/api/briefing", headers={"X-User": ""})  # ensure_user("anonymous")
    names = [u["name"] for u in client.get("/api/users").json()]
    assert "anonymous" not in names
    names_all = [u["name"] for u in client.get("/api/users", params={"all": 1}).json()]
    assert "anonymous" not in names_all


def test_events_from_date_filter(client):
    client.post("/api/events", json={"title": "past sync", "starts_at": "2020-01-01T10:00"})
    client.post("/api/events", json={"title": "future sync", "starts_at": "2099-01-01T10:00"})
    all_titles = [e["title"] for e in client.get("/api/events").json()]
    assert {"past sync", "future sync"} <= set(all_titles)
    upcoming = [
        e["title"] for e in client.get("/api/events", params={"from_date": "2098-12-31"}).json()
    ]
    assert upcoming == ["future sync"]


def test_ship_notification_is_plain_text(client, fresh_db):
    from app.services import engagements

    eng = engagements.create_engagement("Plain notify check", actor="tester", origin="human")
    engagements.update_engagement(
        eng["id"], status="closed", conclusion="achieved", actor="tester", origin="human"
    )
    row = fresh_db.query_one(
        "SELECT message FROM notifications WHERE message LIKE '%Plain notify check%'"
    )
    assert row and "**" not in row["message"]
    note = fresh_db.query_one(
        "SELECT content FROM notes WHERE topic = 'shipped-Plain notify check'"
    )
    assert note and "**" in note["content"]  # the note keeps markdown
