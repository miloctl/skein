"""Engagements: orphan-milestone adoption, rename propagation, the task and milestone links, and closing with work still open."""

import pytest


def _unread_for(fresh_db, user, like):
    return fresh_db.query_one(
        "SELECT * FROM notifications WHERE user = ? AND message LIKE ? AND read_at IS NULL",
        (user, like),
    )


def test_create_engagement_adopts_orphan_milestones(fresh_db):
    from app.services import engagements, work
    from app.services.portfolio import _linked_blockers  # noqa: F401 — import sanity

    work.create_milestone(title="Early milestone", project="Comet", actor="tester")
    eng = engagements.create_engagement(name="Comet", actor="tester")
    row = fresh_db.query_row("SELECT engagement_id FROM milestones")
    assert row["engagement_id"] == eng["id"]


def test_ship_it_and_handoff_survive_rename(client, fresh_db):
    from app.services import engagements, handoff, work

    eng = engagements.create_engagement(name="Old Name", actor="tester")
    work.create_milestone(title="M1", project="Old Name", actor="tester")
    fresh_db.execute("UPDATE engagements SET name = 'New Name' WHERE id = ?", (eng["id"],))
    result = handoff.generate_handoff(eng["id"], actor="tester")
    assert "M1" in result["markdown"]  # name join would have lost the milestone


def test_engagement_rename_propagates_and_reindexes(client):
    from app import db
    from app.services import engagements, search, work

    e = engagements.create_engagement("Aurora Launch")
    m = work.create_milestone("ship v1", project="Aurora Launch")

    client.patch(f"/api/engagements/{e['id']}", json={"name": "Borealis Launch"})
    row = db.query_one("SELECT project, engagement_id FROM milestones WHERE id = ?", (m["id"],))
    assert row["project"] == "Borealis Launch" and row["engagement_id"] == e["id"]
    hits = [h for h in search.search("Borealis") if h["entity"] == "engagement"]
    assert hits and hits[0]["entity_id"] == e["id"]


def test_engagement_rename_collision_rejected(client):
    from app.services import engagements

    engagements.create_engagement("Taken")
    e = engagements.create_engagement("Original")
    with pytest.raises(ValueError, match="already exists"):
        engagements.update_engagement(e["id"], name="Taken")
    assert client.patch(f"/api/engagements/{e['id']}", json={"name": "Taken"}).status_code == 400


def test_failed_rename_close_does_not_orphan_milestone_labels(fresh_db):
    from app import db
    from app.services import engagements, work

    e = engagements.create_engagement("Aurora")
    m = work.create_milestone("ship", project="Aurora")
    with pytest.raises(ValueError, match="conclusion"):
        engagements.update_engagement(e["id"], name="Borealis", status="closed")
    row = db.query_one("SELECT project FROM milestones WHERE id = ?", (m["id"],))
    assert row["project"] == "Aurora"  # rename never landed; the label must not move


def test_milestone_resolves_engagement_id(client, fresh_db):
    e = client.post("/api/engagements", json={"name": "Linked"}).json()
    m = client.post("/api/milestones", json={"title": "m", "project": "Linked"}).json()
    row = fresh_db.query_one("SELECT engagement_id FROM milestones WHERE id = ?", (m["id"],))
    assert row["engagement_id"] == e["id"]
    m2 = client.post("/api/milestones", json={"title": "adhoc", "project": "default"}).json()
    row2 = fresh_db.query_one("SELECT engagement_id FROM milestones WHERE id = ?", (m2["id"],))
    assert row2["engagement_id"] is None


def test_unmatched_project_milestone_files_team_notification(fresh_db):
    from app import db
    from app.services import work

    work.create_milestone("lost milestone", project="ghost-project")
    rows = db.query("SELECT * FROM notifications WHERE user = 'team' AND tier = 'digest'")
    assert any("ghost-project" in r["message"] for r in rows)

    db.execute("DELETE FROM notifications")
    work.create_milestone("plain milestone")  # project=default: no nag
    assert db.query("SELECT * FROM notifications") == []


def test_task_links_to_engagement_directly(client, fresh_db):
    from app.services import engagements, portfolio, work

    eng = engagements.create_engagement(name="Direct-link", actor="tester")
    t = work.create_task(title="orphan work", engagement_id=eng["id"], actor="tester")
    health = {h["name"]: h for h in portfolio.engagement_health()}
    assert "Direct-link" in health
    # the direct-linked task counts as engagement work (silence check sees it)
    row = fresh_db.query_one("SELECT engagement_id FROM tasks WHERE id = ?", (t["id"],))
    assert row["engagement_id"] == eng["id"]


def test_task_engagement_in_handoff_and_pack(client):
    from app.services import context_pack, engagements, handoff, work

    eng = engagements.create_engagement(name="Pack-link", actor="tester")
    work.create_task(title="direct task for pack", engagement_id=eng["id"], actor="tester")
    pack = context_pack.build_engagement_pack(eng["id"])
    assert "direct task for pack" in pack
    h = handoff.generate_handoff(eng["id"], actor="tester")
    if "path" in h:
        from pathlib import Path

        text = Path(h["path"]).read_text()
    else:
        text = str(h)
    assert "direct task for pack" in text


def test_create_task_rejects_unknown_engagement(client):
    r = client.post("/api/tasks", json={"title": "x", "engagement_id": 999})
    assert r.status_code == 400


def test_close_with_open_tasks_is_loud(fresh_db):
    from app.services import engagements, work

    eng = engagements.create_engagement("loose ends", actor="claude")
    work.create_task("straggler", engagement_id=eng["id"], actor="claude")
    out = engagements.update_engagement(
        eng["id"], status="closed", conclusion="achieved", actor="claude"
    )
    assert out["open_tasks"] == 1
    assert _unread_for(fresh_db, "team", "%open task%")
