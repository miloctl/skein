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


def _unread_for(fresh_db, user, like):
    return fresh_db.query_one(
        "SELECT * FROM notifications WHERE user = ? AND message LIKE ? AND read_at IS NULL",
        (user, like),
    )


def test_answer_notifies_asker(fresh_db):
    from app.services import collab

    q = collab.ask_question("who owns DNS?", asked_by="mira", actor="mira")
    collab.answer_question(q["id"], "tomas does", actor="claude")
    assert _unread_for(fresh_db, "mira", "%was answered%")


def test_blocker_resolution_notifies_waiting_task_owner(fresh_db):
    from app.services import blockers, work

    b = blockers.raise_blocker("vendor key missing", actor="tomas", owner="tomas")
    t = work.create_task("integrate vendor", assignee="mira", actor="mira")
    work.update_task(t["id"], waiting_on=f"blocker:{b['id']}", actor="mira")
    blockers.resolve_blocker(b["id"], resolution="key arrived", actor="tomas")
    assert _unread_for(fresh_db, "mira", "%can move again%")


def test_intake_disposition_notifies_requester(fresh_db):
    from app.services import intake

    r = intake.submit_request("try skein for docs", requester="mira", actor="mira")
    intake.disposition_request(r["id"], "declined", "out of scope this season", actor="claude")
    assert _unread_for(fresh_db, "mira", "%was declined%")


def test_close_with_open_tasks_is_loud(fresh_db):
    from app.services import engagements, work

    eng = engagements.create_engagement("loose ends", actor="claude")
    work.create_task("straggler", engagement_id=eng["id"], actor="claude")
    out = engagements.update_engagement(
        eng["id"], status="closed", conclusion="achieved", actor="claude"
    )
    assert out["open_tasks"] == 1
    assert _unread_for(fresh_db, "team", "%open task%")
