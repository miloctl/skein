"""REST smoke suite: one pass over every router so a broken import or a
missing dependency fails loudly. Depth lives in the per-behavior files."""


def test_health(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["provider"] == "mock"


def test_task_create_carries_actor_and_milestone(client):
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
    # questions render on My Day, not Inbox — the badge counts only Inbox work
    assert client.get("/api/attention").json()["inbox"] == 0
    b = client.get("/api/briefing").json()
    assert any(a["kind"] == "question" for a in b["attention"])
    client.post(f"/api/questions/{q['id']}/answer", json={"answer": "Alice does"})
    client.post("/api/notifications/read", json={"notification_id": 0})
    assert client.get("/api/attention").json()["count"] == 0


def test_attention_counts_only_inbox_work(client):
    from app.services import review

    client.post("/api/intake", json={"title": "Need a thing"})
    review.propose_change("note", "create", {"topic": "t", "content": "c"}, actor="agent-x")
    assert client.get("/api/attention").json()["inbox"] == 2


def test_attention_count_is_personal_not_the_shared_queue(client):
    """`count` is what the tab title and `skein attention` carry, and both say
    "waiting on you". It counted the Inbox — a queue anyone may work — so a
    teammate's proposal raised everybody's number and a blocker addressed to
    one person raised nobody's."""
    from app.services import review

    client.post("/api/intake", json={"title": "Need a thing"})
    review.propose_change("note", "create", {"topic": "t", "content": "c"}, actor="agent-x")
    # two things in the shared Inbox, nothing addressed to this reader
    assert client.get("/api/attention").json() == {"count": 0, "inbox": 2, "yours": 0}

    client.post("/api/blockers", json={"title": "Stuck", "owner": "tester"})
    counts = client.get("/api/attention").json()
    assert counts["yours"] == 1  # the blocker names them
    assert counts["count"] == counts["yours"]
    assert counts["inbox"] == 2  # unchanged: a blocker does not live in Inbox


def test_briefing_shape(client):
    client.post("/api/blockers", json={"title": "Stuck", "owner": "tester"})
    b = client.get("/api/briefing").json()
    assert b["user"] == "tester"
    assert len(b["needs_you"]["your_blockers"]) == 1


def test_capture_endpoint(client):
    out = client.post("/api/capture", json={"text": "todo: write tests"}).json()
    assert out["kind"] == "task"


def test_user_theme_roundtrip(client):
    theme = '{"pack":"atelier","colorway":"custom","appearance":"dark","custom":{"thread":12,"weld":200}}'
    assert client.post("/api/users/theme", json={"theme": theme}).json()["saved"] is True
    assert client.get("/api/users/theme").json()["theme"] == theme
    assert client.post("/api/users/theme", json={"theme": ""}).json()["saved"] is False
    assert client.post("/api/users/theme", json={"theme": "not json"}).status_code == 400
    assert client.post("/api/users/theme", json={"theme": '{"evil":1}'}).status_code == 400


def test_team_default_theme(client):
    from app.services.api_keys import create_key

    theme = '{"pack":"phosphor","colorway":"verdigris"}'
    # weak identity cannot set the team default
    assert client.post("/api/users/theme/default", json={"theme": theme}).status_code == 403
    headers = {"Authorization": f"Bearer {create_key('tester', 't')['key']}"}
    ok = client.post("/api/users/theme/default", json={"theme": theme}, headers=headers)
    assert ok.json()["saved"] is True
    assert client.get("/api/users/theme").json()["team_default"] == theme


def test_agents_status_shape(client):
    s = client.get("/api/agents/status").json()
    assert set(s) == {
        "provider",
        "model",
        "provider_error",
        "models_error",
        "review_gate",
        "trust_blocked",
        "runner_agents",
        "runner_daily_tokens",
        "context_strategy",
        "context_error",
    }
    assert s["provider"] == "mock" and s["model"] == ""


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


def test_users_autoregister_on_first_write_not_read(client):
    client.get("/api/briefing", headers={"X-User": "newperson"})
    names = {u["name"] for u in client.get("/api/users").json()}
    assert "newperson" not in names  # reads never mint roster rows
    client.post("/api/standups", json={"today": "here"}, headers={"X-User": "newperson"})
    names = {u["name"] for u in client.get("/api/users").json()}
    assert "newperson" in names


def test_admin_backup_and_export(client):
    # backup and export are admin surfaces: strong identity required
    assert client.post("/api/admin/backup").status_code == 403
    assert client.get("/api/admin/export").status_code == 403
    assert client.get("/api/admin/export/download").status_code == 403
    from app import db
    from app.services.api_keys import create_key

    assert db.query_one("SELECT id FROM activity WHERE action = 'export'") is None
    key = create_key("tester", "t")["key"]
    b = client.post("/api/admin/backup", headers={"Authorization": f"Bearer {key}"}).json()
    assert b["status"] == "ok"
    assert b["database_path"].endswith(".dump")
    assert b["mirror_status"] == "not_configured"
    assert b["artifacts_included"] is False
    legacy = client.get("/api/admin/export", headers={"Authorization": f"Bearer {key}"})
    legacy_body = legacy.json()
    assert legacy_body["tables"]["users"] >= 1
    assert "/" not in legacy_body["path"]
    assert legacy.headers["cache-control"] == "private, no-store"

    response = client.get("/api/admin/export/download", headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in response.headers["content-disposition"]
    assert "skein-export-" in response.headers["content-disposition"]
    assert response.headers["x-skein-filename"].startswith("skein-export-")
    assert "accept-ranges" not in response.headers
    exported = response.json()
    assert exported["users"]
    assert "path" not in exported and "tables" not in exported

    refused = client.get(
        "/api/admin/export/download",
        headers={"Authorization": f"Bearer {key}", "Range": "bytes=10-"},
    )
    assert refused.status_code == 416
    assert refused.json()["detail"] == "This export cannot resume. Start a new download."

    limited = client.get("/api/admin/export/download", headers={"Authorization": f"Bearer {key}"})
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) > 0
    receipts = db.query("SELECT actor, action FROM activity WHERE action = 'export' ORDER BY seq")
    assert receipts == [
        {"actor": "tester", "action": "export"},
        {"actor": "tester", "action": "export"},
    ]


def test_export_filename_header_is_visible_to_the_browser(client):
    from app.services.api_keys import create_key

    key = create_key("tester", "t")["key"]
    response = client.get(
        "/api/admin/export/download",
        headers={
            "Authorization": f"Bearer {key}",
            "Origin": "http://localhost:3000",
        },
    )
    assert response.status_code == 200
    exposed = response.headers["access-control-expose-headers"].lower()
    assert "x-skein-filename" in exposed


def test_browser_export_refuses_an_unbounded_blob(client, monkeypatch):
    from app import db
    from app.services import admin
    from app.services.api_keys import create_key

    monkeypatch.setattr(admin, "MAX_EXPORT_DOWNLOAD_BYTES", 1)
    key = create_key("tester", "t")["key"]
    response = client.get(
        "/api/admin/export/download",
        headers={"Authorization": f"Bearer {key}"},
    )
    assert response.status_code == 413
    assert "too large" in response.json()["detail"]
    assert db.query_one("SELECT id FROM activity WHERE action = 'export'") is None


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
