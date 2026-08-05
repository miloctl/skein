"""Quick capture: prefix precedence over content heuristics, the grammar each
prefix parses, and the mock agent's capture acknowledgement."""

import pytest


def test_q_capture_assigns_known_user(client):
    client.post("/api/users/growth-interests", json={"interests": "x"}, headers={"X-User": "mira"})
    r = client.post("/api/capture", json={"text": "q: mira — where do the traces land?"})
    assert r.status_code == 200
    q = client.get("/api/questions").json()[0]
    assert q["assigned_to"] == "mira"
    assert q["question"] == "where do the traces land?"


def test_q_capture_unknown_name_stays_text(client):
    r = client.post("/api/capture", json={"text": "q: zorblatt — is this a person?"})
    assert r.status_code == 200
    q = client.get("/api/questions").json()[0]
    assert q["assigned_to"] == ""
    assert "zorblatt" in q["question"]


def test_decision_capture_parses_review_by(client):
    client.post("/api/capture", json={"text": "decision: SVG only — review by 2026-10-01"})
    d = client.get("/api/decisions").json()[0]
    assert d["review_by"] == "2026-10-01"
    assert "review by" not in d["title"]


@pytest.mark.parametrize(
    "text,kind",
    [
        ("req: blocked on vendor sandbox", "request"),  # the live bug: prefix beats heuristic
        ("request: new dashboards", "request"),
        ("todo: fix login?", "task"),  # explicit prefix beats trailing "?"
        ("blocked: waiting on legal", "blocker"),
        ("note: we decided to punt", "note"),  # prefix beats decision heuristic
        ("q: blocked on CI", "question"),
        ("is prod ok?", "question"),  # bare trailing "?" still classifies
    ],
)
def test_capture_prefix_beats_content_heuristics(text, kind):
    from app.services import capture

    assert capture.classify(text) == kind


def test_capture_req_blocked_on_routes_to_intake_not_blockers(client):
    out = client.post("/api/capture", json={"text": "req: blocked on vendor sandbox"}).json()
    assert out["kind"] == "request"
    reqs = client.get("/api/intake").json()
    assert any(r["title"] == "blocked on vendor sandbox" for r in reqs)
    assert client.get("/api/blockers").json() == []


def test_mock_agent_promise_capture_ack(client):
    out = client.post("/api/chat", json={"thread_id": "t", "message": "promised: report to legal"})
    assert "error" not in out.text.lower() or "Promise" in out.text
    rows = client.get("/api/promises").json()
    assert len(rows) == 1
    assert "report to legal" in rows[0]["promise"]
