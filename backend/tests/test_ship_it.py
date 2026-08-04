"""The Ship It moment: recap composition, zero-stat omission, and the team notification."""

import pytest


@pytest.mark.parametrize("origin", ["agent", "agent_verified"])
def test_the_recap_note_carries_the_closers_origin(fresh_db, monkeypatch, origin):
    """_ship_it and _experiment_lesson are called on adjacent lines from the
    same `if freshly_closed:` block. _ship_it hardcoded origin="human", so an
    engagement closed by the agent path wrote the lesson as agent_verified and
    the recap beside it as human, in one transaction — an auditor filtering
    notes by origin sees a machine-generated note attributed to a person."""
    from app.services import engagements, notifications

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)
    # an experiment, so _experiment_lesson runs beside _ship_it and the two
    # notes written in one transaction must agree on who wrote them
    e = engagements.create_engagement(
        "Threaded", actor="ava", kind="experiment", timebox_end="2099-01-01"
    )
    engagements.update_engagement(
        e["id"], status="closed", conclusion="achieved", actor="scout", origin=origin
    )
    notes = fresh_db.query("SELECT topic, origin FROM notes WHERE topic LIKE 'shipped-%'")
    assert notes, "the close wrote no recap note"
    for n in notes:
        assert n["origin"] == origin, f"{n['topic']} recorded origin={n['origin']!r}"


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
