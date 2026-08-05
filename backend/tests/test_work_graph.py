"""The work graph: relinking tasks and milestones, and the waiting_on dependency edges that surface in health and forecast."""

import pytest


def test_milestone_relink_unlink_and_bad_id(client):
    from app import db
    from app.services import engagements, work

    e = engagements.create_engagement("Delta")
    m = work.create_milestone("orphaned milestone")  # project=default, no link

    assert (
        client.patch(f"/api/milestones/{m['id']}", json={"engagement_id": 9999}).status_code == 400
    )

    client.patch(f"/api/milestones/{m['id']}", json={"engagement_id": e["id"]})
    row = db.query_one("SELECT engagement_id FROM milestones WHERE id = ?", (m["id"],))
    assert row["engagement_id"] == e["id"]

    client.patch(f"/api/milestones/{m['id']}", json={"engagement_id": -1})
    row = db.query_one("SELECT engagement_id FROM milestones WHERE id = ?", (m["id"],))
    assert row["engagement_id"] is None


def test_task_relink_across_milestones(client):
    from app import db
    from app.services import work

    m1 = work.create_milestone("first home")
    m2 = work.create_milestone("second home")
    t = work.create_task("wandering task", milestone_id=m1["id"])

    assert client.patch(f"/api/tasks/{t['id']}", json={"milestone_id": 9999}).status_code == 400

    client.patch(f"/api/tasks/{t['id']}", json={"milestone_id": m2["id"]})
    row = db.query_one("SELECT milestone_id FROM tasks WHERE id = ?", (t["id"],))
    assert row["milestone_id"] == m2["id"]

    client.patch(f"/api/tasks/{t['id']}", json={"milestone_id": -1})
    row = db.query_one("SELECT milestone_id FROM tasks WHERE id = ?", (t["id"],))
    assert row["milestone_id"] is None


def test_waiting_on_validation_and_clear(client, fresh_db):
    from app.services import blockers, work

    t = work.create_task(title="build the thing", actor="m")
    b = blockers.raise_blocker("vendor key", actor="m")
    with pytest.raises(ValueError, match="waiting_on must look like"):
        work.update_task(t["id"], waiting_on="nonsense", actor="m")
    with pytest.raises(ValueError, match="not found"):
        work.update_task(t["id"], waiting_on="blocker:999", actor="m")
    with pytest.raises(ValueError, match="wait on itself"):
        work.update_task(t["id"], waiting_on=f"task:{t['id']}", actor="m")
    work.update_task(t["id"], waiting_on=f"blocker:{b['id']}", actor="m")
    row = fresh_db.query_row("SELECT * FROM tasks WHERE id = ?", (t["id"],))
    assert row["waiting_on_type"] == "blocker" and row["waiting_on_id"] == b["id"]
    work.update_task(t["id"], waiting_on="-", actor="m")
    row = fresh_db.query_row("SELECT * FROM tasks WHERE id = ?", (t["id"],))
    assert row["waiting_on_type"] is None


def test_waiting_on_surfaces_in_health_and_forecast(client, fresh_db):
    from app.services import blockers, engagements, work

    engagements.create_engagement("Comet", actor="m")
    m = work.create_milestone(title="M1", project="Comet", due_date="2099-01-01", actor="m")
    t = work.create_task(title="stuck task", milestone_id=m["id"], actor="m")
    b = blockers.raise_blocker("upstream", actor="m")
    work.update_task(t["id"], waiting_on=f"blocker:{b['id']}", actor="m")
    health = client.get("/api/portfolio/health").json()
    receipts = health[0]["receipts"]
    assert any("waiting on blocker" in r for r in receipts)
    forecast = client.get("/api/portfolio/forecast").json()
    assert forecast["forecasts"][0]["waiting_on"] == [f"blocker #{b['id']} (task #{t['id']})"]


def test_satisfied_waits_stop_yellowing(client, fresh_db):
    from app.services import blockers, engagements, work

    engagements.create_engagement("Nimbus", actor="m")
    m = work.create_milestone(title="M1", project="Nimbus", actor="m")
    t = work.create_task(title="stuck", milestone_id=m["id"], actor="m")
    b = blockers.raise_blocker("upstream", actor="m")
    work.update_task(t["id"], waiting_on=f"blocker:{b['id']}", actor="m")
    assert client.get("/api/portfolio/health").json()[0]["health"] == "yellow"
    blockers.resolve_blocker(b["id"], actor="m")
    health = client.get("/api/portfolio/health").json()[0]
    assert health["health"] == "green"  # dependency cleared → receipt gone
    assert not any("waiting" in r for r in health["receipts"])


def test_agent_tools_cover_waiting_on_and_charter_fields(fresh_db, monkeypatch):
    from app import config
    from app.services import blockers as blockers_svc
    from app.services import work as work_svc
    from app.tools import collab as tc
    from app.tools import work as tw

    monkeypatch.setattr(config, "AGENT_REVIEW", False)
    t = work_svc.create_task(title="agent task", actor="m")
    b = blockers_svc.raise_blocker("dep", actor="m")
    tw.update_task(t["id"], waiting_on=f"blocker:{b['id']}")
    row = fresh_db.query_row("SELECT waiting_on_type FROM tasks WHERE id = ?", (t["id"],))
    assert row["waiting_on_type"] == "blocker"
    tc.record_decision(
        "Charter via agent",
        "agents can draft charter entries",
        review_by="2099-01-01",
        category="charter",
    )
    assert len(fresh_db.query("SELECT * FROM decisions WHERE category = 'charter'")) == 1
