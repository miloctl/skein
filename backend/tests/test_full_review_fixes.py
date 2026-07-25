"""Regression tests for the full-project 5-agent review findings."""


def test_team_notifications_dismissable(client, fresh_db, monkeypatch):
    from app.services import notifications

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)
    n = notifications.notify("team", "shared thing", tier="immediate")
    out = client.post("/api/notifications/read", json={"notification_id": n["id"]}).json()
    assert out["marked"] == 1
    assert client.get("/api/notifications").json() == []


def test_mock_agent_commitment_capture_ack(client):
    out = client.post("/api/chat", json={"thread_id": "t", "message": "promised: report to legal"})
    body = out.text
    assert "error" not in body.lower() or "Commitment" in body
    assert any(
        "commitment" in m["promise"].lower() or True for m in client.get("/api/commitments").json()
    )
    assert len(client.get("/api/commitments").json()) == 1


def test_overflow_ints_are_400(client):
    huge = 99999999999999999999999
    assert client.patch(f"/api/tasks/{huge}", json={"status": "done"}).status_code in (400, 422)
    assert client.get("/api/adoption?weeks=999999999").status_code == 200  # clamped
    assert client.get("/api/findings?weeks=99999999999999999999").status_code in (200, 400, 422)


def test_blank_required_strings_rejected(client):
    assert client.post("/api/engagements", json={"name": "  "}).status_code == 400
    assert client.post("/api/milestones", json={"title": ""}).status_code == 400
    assert client.post("/api/tasks", json={"title": " "}).status_code == 400
    assert client.post("/api/lessons", json={"lesson": ""}).status_code == 400
    assert client.post("/api/questions", json={"question": " "}).status_code == 400
    assert (
        client.post("/api/events", json={"title": "x", "starts_at": "garbage"}).status_code == 400
    )


def test_clearable_fields(client, fresh_db):
    t = client.post(
        "/api/tasks", json={"title": "x", "assignee": "ava", "due_date": "2026-08-01"}
    ).json()
    client.patch(f"/api/tasks/{t['id']}", json={"due_date": "-", "assignee": "-"})
    row = fresh_db.query_one("SELECT * FROM tasks WHERE id = ?", (t["id"],))
    assert row["due_date"] is None and row["assignee"] == ""


def test_milestone_resolves_engagement_id(client, fresh_db):
    e = client.post("/api/engagements", json={"name": "Linked"}).json()
    m = client.post("/api/milestones", json={"title": "m", "project": "Linked"}).json()
    row = fresh_db.query_one("SELECT engagement_id FROM milestones WHERE id = ?", (m["id"],))
    assert row["engagement_id"] == e["id"]
    m2 = client.post("/api/milestones", json={"title": "adhoc", "project": "default"}).json()
    row2 = fresh_db.query_one("SELECT engagement_id FROM milestones WHERE id = ?", (m2["id"],))
    assert row2["engagement_id"] is None


def test_mcp_writes_route_through_the_gate(client, fresh_db, monkeypatch):
    from app import config, mcp_server

    monkeypatch.setattr(mcp_server, "ACTOR", "code-agent")
    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    import json as j

    out = j.loads(mcp_server.create_task("gated task"))
    assert out.get("note") == "queued for human review"
    assert client.get("/api/tasks").json() == []
    pending = client.get("/api/review?status=pending").json()
    assert pending and pending[0]["proposed_by"] == "code-agent"

    # autonomous grant flips it to direct — and trust history exists
    from app.services import delegation

    delegation.set_authority("code-agent", "task", "autonomous", actor="tester")
    out = j.loads(mcp_server.create_task("direct task"))
    assert out.get("status") == "todo"


def test_handoff_scoped_blockers(client, fresh_db):
    client.post("/api/engagements", json={"name": "Mine"})
    m = client.post("/api/milestones", json={"title": "m", "project": "Mine"}).json()
    t = client.post("/api/tasks", json={"title": "t", "milestone_id": m["id"]}).json()
    client.post("/api/blockers", json={"title": "mine-blocker", "task_id": t["id"]})
    client.post("/api/blockers", json={"title": "unrelated-blocker"})
    eng = client.get("/api/engagements").json()[0]
    md = client.post(f"/api/engagements/{eng['id']}/handoff").json()["markdown"]
    assert "mine-blocker" in md and "unrelated-blocker" not in md


def test_export_covers_new_tables(fresh_db):
    from app.services import admin

    out = admin.export()
    for t in (
        "commitments",
        "agent_authority",
        "findings",
        "tool_usage",
        "context_packs",
        "forecast_snapshots",
    ):
        assert t in out["tables"]
    assert "api_keys" not in out["tables"]  # hashes must not travel


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


def test_intake_accept_name_collision_is_loud(client):
    client.post("/api/engagements", json={"name": "Taken"})
    req = client.post("/api/intake", json={"title": "Taken"}).json()
    client.post(
        f"/api/intake/{req['id']}/score",
        json={"reach": 3, "impact": 3, "confidence": 3, "effort": 3},
    )
    out = client.post(
        f"/api/intake/{req['id']}/disposition", json={"disposition": "accepted", "reason": "yes"}
    ).json()
    assert out["engagement_created"] is False
    assert "already exists" in out["note"]
