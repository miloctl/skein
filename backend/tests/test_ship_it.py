"""The Ship It moment: recap composition, zero-stat omission, and the team notification."""


def test_ship_it_counts_only_linked_blockers(client, fresh_db, monkeypatch):
    from app.services import notifications

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)
    e = client.post("/api/engagements", json={"name": "Scoped"}).json()
    m = client.post("/api/milestones", json={"title": "m", "project": "Scoped"}).json()
    t = client.post("/api/tasks", json={"title": "t", "milestone_id": m["id"]}).json()
    b = client.post("/api/blockers", json={"title": "ours", "task_id": t["id"]}).json()
    client.post(f"/api/blockers/{b['id']}/resolve", json={})
    other = client.post("/api/blockers", json={"title": "unrelated"}).json()
    client.post(f"/api/blockers/{other['id']}/resolve", json={})

    client.patch(f"/api/engagements/{e['id']}", json={"status": "closed", "conclusion": "achieved"})
    note = fresh_db.query_one("SELECT content FROM notes WHERE topic = 'shipped-Scoped'")
    assert "1 blockers survived" in note["content"]


def test_ship_it_recap_and_notification(fresh_db, monkeypatch):
    from app.services import engagements, notifications

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)
    e = engagements.create_engagement("Big launch", actor="ava")
    engagements.update_engagement(e["id"], status="closed", conclusion="achieved", actor="ava")

    notes = fresh_db.query("SELECT * FROM notes WHERE topic = 'shipped-Big launch'")
    assert notes and "Shipped" in notes[0]["content"]
    msgs = [n["message"] for n in notifications.list_notifications("team")]
    assert any("Shipped" in m for m in msgs)


def test_ship_it_recap_omits_zero_stats(fresh_db, monkeypatch):
    from app.services import engagements, notifications

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)
    e = engagements.create_engagement("Bare", actor="ava")
    engagements.update_engagement(e["id"], status="closed", conclusion="achieved", actor="ava")

    note = fresh_db.query_one("SELECT content FROM notes WHERE topic = 'shipped-Bare'")
    assert "Shipped: Bare" in note["content"]
    for phrase in ("0 milestones", "0 tasks done", "0 blockers survived"):
        assert phrase not in note["content"]
    assert "·" not in note["content"]  # no orphaned separators when every stat is zero
    msg = fresh_db.query_one(
        "SELECT message FROM notifications WHERE user = 'team' AND message LIKE '%Shipped: Bare%'"
    )
    assert "·" not in msg["message"]


def test_ship_it_recap_omits_zero_day_duration(fresh_db, monkeypatch):
    from app.services import engagements, notifications

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)
    e = engagements.create_engagement("SameDay", actor="ava")
    engagements.update_engagement(e["id"], status="closed", conclusion="achieved", actor="ava")
    note = fresh_db.query_one("SELECT content FROM notes WHERE topic = 'shipped-SameDay'")
    assert "0 days" not in note["content"]


def test_ship_it_recap_single_stat_no_orphan_separators(client, fresh_db, monkeypatch):
    from app.services import notifications

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)
    e = client.post("/api/engagements", json={"name": "OneStat"}).json()
    client.post("/api/milestones", json={"title": "m1", "project": "OneStat"})
    client.patch(f"/api/engagements/{e['id']}", json={"status": "closed", "conclusion": "achieved"})

    content = fresh_db.query_one("SELECT content FROM notes WHERE topic = 'shipped-OneStat'")[
        "content"
    ]
    assert "1 milestones" in content
    assert "tasks done" not in content and "blockers survived" not in content
    assert content.count("·") == 1 and not content.rstrip().endswith("·")
