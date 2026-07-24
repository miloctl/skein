def test_health(client):
    body = client.get("/health").json()
    assert body["ok"] is True
    assert body["provider"] == "mock"


def test_rest_write_paths_record_provenance(client):
    m = client.post("/api/milestones", json={"title": "Q3 launch"}).json()
    t = client.post("/api/tasks", json={"title": "Cut release", "milestone_id": m["id"]}).json()
    client.patch(f"/api/tasks/{t['id']}", json={"status": "in_progress"})

    tasks = client.get("/api/tasks").json()
    assert tasks[0]["created_by"] == "tester"
    assert tasks[0]["milestone_title"] == "Q3 launch"

    r = client.patch(f"/api/tasks/{t['id']}", json={"status": "bogus"})
    assert r.status_code == 400


def test_question_answer_flow(client):
    q = client.post(
        "/api/questions", json={"question": "Who owns infra?", "assigned_to": "tester"}
    ).json()
    # open question + its assignment notification
    assert client.get("/api/attention").json()["count"] == 2
    client.post(f"/api/questions/{q['id']}/answer", json={"answer": "Alice does"})
    client.post("/api/notifications/read", json={"notification_id": 0})
    assert client.get("/api/attention").json()["count"] == 0


def test_briefing_shape(client):
    client.post("/api/blockers", json={"title": "Stuck", "owner": "tester"})
    b = client.get("/api/briefing").json()
    assert b["user"] == "tester"
    assert len(b["needs_you"]["your_blockers"]) == 1


def test_capture_endpoint(client):
    out = client.post("/api/capture", json={"text": "todo: write tests"}).json()
    assert out["kind"] == "task"


def test_review_flow_via_api(client):
    from app.services import review

    p = review.propose_change(
        "note", "create", {"topic": "convention", "content": "use uv"}, actor="agent-1"
    )
    pending = client.get("/api/review").json()
    assert pending[0]["payload"]["topic"] == "convention"

    client.post(f"/api/review/{p['id']}/approve", json={"note": "lgtm"})
    notes = client.get("/api/notes").json()
    assert notes[0]["origin"] == "agent_verified"


def test_intake_flow_via_api(client):
    r = client.post("/api/intake", json={"title": "New request"}).json()
    client.post(
        f"/api/intake/{r['id']}/score", json={"reach": 4, "impact": 4, "confidence": 4, "effort": 4}
    )
    resp = client.post(
        f"/api/intake/{r['id']}/disposition",
        json={"disposition": "deferred", "reason": "next quarter"},
    )
    assert resp.json()["status"] == "deferred"


def test_playbooks_and_engagement_api(client):
    assert {p["slug"] for p in client.get("/api/playbooks").json()} >= {
        "incident",
        "migration",
        "prototype",
    }
    created = client.post(
        "/api/playbooks/instantiate",
        json={"playbook": "prototype", "engagement_name": "Demo proto"},
    ).json()
    eng_id = created["engagement"]["id"]
    pack = client.post(f"/api/engagements/{eng_id}/handoff").json()
    assert "Demo proto" in pack["markdown"]


def test_search_api(client):
    client.post("/api/notes", json={"topic": "wal", "content": "SQLite WAL mode rocks"})
    hits = client.get("/api/search", params={"q": "sqlite"}).json()
    assert hits and hits[0]["entity"] == "note"


def test_users_autoregister(client):
    client.get("/api/briefing", headers={"X-User": "newperson"})
    names = {u["name"] for u in client.get("/api/users").json()}
    assert "newperson" in names


def test_admin_backup_and_export(client):
    b = client.post("/api/admin/backup").json()
    assert b["path"].endswith(".db")
    e = client.get("/api/admin/export").json()
    assert e["tables"]["users"] >= 1


def _read_chat(client, message):
    with client.stream("POST", "/api/chat", json={"thread_id": "t", "message": message}) as resp:
        assert resp.status_code == 200
        return resp.read().decode()


def test_mock_chat_help_and_capture(client):
    assert "Mock agent" in _read_chat(client, "/help")

    _read_chat(client, "blocked on missing credentials")
    blockers = client.get("/api/blockers").json()
    assert any("credentials" in b["title"] for b in blockers)


def test_mock_chat_plan_and_search(client):
    out = _read_chat(client, "/plan incident Payments outage")
    assert "Payments outage" in out
    assert client.get("/api/engagements").json()[0]["name"] == "Payments outage"

    out = _read_chat(client, "/search cutover")
    assert "data:" in out
