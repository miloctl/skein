"""REST error shapes: overflow integers, blank required strings, and the clear sentinels."""

import pytest


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


def test_api_tester_regressions(client):
    # FK violations are clean 400s, not 500s
    assert (
        client.post("/api/tasks", json={"title": "orphan", "milestone_id": 999999}).status_code
        == 400
    )
    assert client.post("/api/engagements/999999/allocate", json={"person": "a"}).status_code == 400
    assert (
        client.post("/api/lessons", json={"lesson": "x", "engagement_id": 999999}).status_code
        == 400
    )

    # playbook slug traversal rejected
    r = client.post(
        "/api/playbooks/instantiate", json={"playbook": "/tmp/pwned", "engagement_name": "t"}
    )
    assert r.status_code == 400
    r = client.post(
        "/api/playbooks/instantiate", json={"playbook": "../secrets", "engagement_name": "t"}
    )
    assert r.status_code == 400

    # 0-row updates are 400s, not silent success
    assert client.patch("/api/tasks/999999", json={"status": "done"}).status_code == 404
    assert client.patch("/api/milestones/999999", json={"status": "done"}).status_code == 404
    assert (
        client.post(
            "/api/intake/999999/score", json={"reach": 3, "impact": 3, "confidence": 3, "effort": 3}
        ).status_code
        == 404
    )

    # disposition is terminal
    req = client.post("/api/intake", json={"title": "once"}).json()
    client.post(
        f"/api/intake/{req['id']}/score",
        json={"reach": 3, "impact": 3, "confidence": 3, "effort": 3},
    )
    client.post(
        f"/api/intake/{req['id']}/disposition", json={"disposition": "accepted", "reason": "yes"}
    )
    r = client.post(
        f"/api/intake/{req['id']}/disposition", json={"disposition": "declined", "reason": "no"}
    )
    assert r.status_code == 400

    # double-resolve is a 400
    b = client.post("/api/blockers", json={"title": "once-only"}).json()
    client.post(f"/api/blockers/{b['id']}/resolve", json={})
    assert client.post(f"/api/blockers/{b['id']}/resolve", json={}).status_code == 400


def test_clear_sentinel_rejected_on_create_paths(fresh_db):
    from app.services import engagements, promises, users, work

    with pytest.raises(ValueError, match="only clears"):
        work.create_task(title="t", due_date="-")
    with pytest.raises(ValueError, match="only clears"):
        promises.add_promise("p", due_date="-")
    users.ensure_user("mira")
    e = engagements.create_engagement("SentinelCheck")
    with pytest.raises(ValueError, match="only clears"):
        engagements.allocate("mira", e["id"], 50, starts_on="-")
