"""The CI webhook: deduped blocker on red, auto-resolve on green."""


def test_ci_webhook_dedupe_and_resolve(client):
    fail = {
        "repo": "team/app",
        "branch": "main",
        "status": "failure",
        "run_url": "https://ci/run/1",
    }
    first = client.post("/api/webhooks/ci", json=fail).json()
    assert first["raised"]
    assert client.post("/api/webhooks/ci", json=fail).json()["deduped"]

    blockers = client.get("/api/blockers").json()
    assert any("CI red" in b["title"] for b in blockers)

    ok = client.post("/api/webhooks/ci", json={**fail, "status": "success"}).json()
    assert len(ok["resolved"]) == 1
    assert client.get("/api/blockers").json() == []

    ignored = client.post("/api/webhooks/ci", json={**fail, "branch": "feature/x"}).json()
    assert "ignored" in ignored


def test_ci_webhook_github_actions_shape(client):
    payload = {
        "workflow_run": {
            "status": "completed",
            "conclusion": "failure",
            "head_branch": "main",
            "html_url": "https://gh/run/9",
        },
        "repository": {"full_name": "team/repo"},
    }
    out = client.post("/api/webhooks/ci", json=payload).json()
    assert out["raised"]

    cancelled = {**payload, "workflow_run": {**payload["workflow_run"], "conclusion": "cancelled"}}
    assert "ignored" in client.post("/api/webhooks/ci", json=cancelled).json()
