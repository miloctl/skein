"""Handoff package generation and its scoping."""


def test_handoff_scoped_blockers(client, fresh_db):
    client.post("/api/engagements", json={"name": "Mine"})
    m = client.post("/api/milestones", json={"title": "m", "project": "Mine"}).json()
    t = client.post("/api/tasks", json={"title": "t", "milestone_id": m["id"]}).json()
    client.post("/api/blockers", json={"title": "mine-blocker", "task_id": t["id"]})
    client.post("/api/blockers", json={"title": "unrelated-blocker"})
    eng = client.get("/api/engagements").json()[0]
    md = client.post(f"/api/engagements/{eng['id']}/handoff").json()["markdown"]
    assert "mine-blocker" in md and "unrelated-blocker" not in md
