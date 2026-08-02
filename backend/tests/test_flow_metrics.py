"""Flow metrics: cycle time, WIP, staleness gradation, and the completed_at stamp they read."""

from datetime import datetime, timedelta, timezone


def _ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def test_completed_at_stamped_and_cleared(client, fresh_db):
    t = client.post("/api/tasks", json={"title": "flow me"}).json()
    client.patch(f"/api/tasks/{t['id']}", json={"status": "done"})
    row = fresh_db.query_one("SELECT * FROM tasks WHERE id = ?", (t["id"],))
    assert row["completed_at"] is not None

    client.patch(f"/api/tasks/{t['id']}", json={"status": "in_progress"})
    row = fresh_db.query_one("SELECT * FROM tasks WHERE id = ?", (t["id"],))
    assert row["completed_at"] is None


def test_flow_metrics_cycle_wip_stale(client, fresh_db):
    done = client.post("/api/tasks", json={"title": "shipped"}).json()
    client.patch(f"/api/tasks/{done['id']}", json={"status": "done"})
    stale = client.post("/api/tasks", json={"title": "stuck", "assignee": "ava"}).json()
    client.patch(f"/api/tasks/{stale['id']}", json={"status": "in_progress"})
    fresh_db.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (_ago(10), stale["id"]))

    flow = client.get("/api/portfolio/flow").json()
    assert flow["cycle_time"]["tasks_done"] == 1
    assert sum(flow["throughput_by_week"].values()) == 1
    assert flow["wip_by_person"][0]["person"] == "ava"
    assert [s["id"] for s in flow["stale_wip"]] == [stale["id"]]


def test_slas_constants_wired():
    from app.services import digest, insights, portfolio, slas

    assert portfolio.STALE_WIP_DAYS == slas.STALE_WIP_DAYS
    assert insights.AGING_WIP_DAYS == slas.AGING_WIP_DAYS
    assert digest.DIGEST_STALLED_DAYS == slas.DIGEST_STALLED_DAYS
