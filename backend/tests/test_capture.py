"""Quick capture: prefix precedence over content heuristics, the grammar each
prefix parses, and the mock agent's capture acknowledgement."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest


def test_a_repeated_capture_key_files_nothing_twice(client):
    """D5: the CLI outbox re-sends a capture the server already accepted when
    a crash lands between the accept and the outbox rewrite."""
    body = {"text": "todo: ship the API", "capture_key": "abc123"}
    first = client.post("/api/capture", json=body).json()
    assert first["kind"] == "task"
    replay = client.post("/api/capture", json=body).json()
    assert replay == {"kind": "duplicate", "capture_key": "abc123"}
    assert len(client.get("/api/tasks").json()) == 1

    fresh = client.post(
        "/api/capture", json={"text": "todo: ship the docs", "capture_key": "def456"}
    ).json()
    assert fresh["kind"] == "task"
    assert len(client.get("/api/tasks").json()) == 2


def test_concurrent_replays_file_once(client, monkeypatch):
    from app.services import capture

    barrier = threading.Barrier(2)
    original = capture.db.claim_job

    def overlap(job, run_key):
        barrier.wait(timeout=3)
        return original(job, run_key)

    monkeypatch.setattr(capture.db, "claim_job", overlap)
    body = {"text": "todo: one concurrent row", "capture_key": "same-key"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _index: client.post("/api/capture", json=body), range(2)))
    payloads = [response.json() for response in responses]
    assert sorted(row["kind"] for row in payloads) == ["duplicate", "task"]
    assert len(client.get("/api/tasks").json()) == 1


def test_capture_keys_are_scoped_per_user(client):
    body = {"text": "todo: same token", "capture_key": "shared-token"}
    for user in ("mira", "ava"):
        assert (
            client.post("/api/capture", json=body, headers={"X-User": user}).json()["kind"]
            == "task"
        )
    assert len(client.get("/api/tasks").json()) == 2


def test_a_refused_capture_does_not_burn_its_key(client):
    """The claim row and the capture commit or roll back together, so a retry
    of a failed capture files normally."""
    body = {"text": "", "capture_key": "retry01"}
    assert client.post("/api/capture", json=body).status_code == 400
    body["text"] = "todo: the retry files"
    assert client.post("/api/capture", json=body).json()["kind"] == "task"


def test_a_capture_without_a_key_still_files(client):
    """Older CLIs and the web composer send no key — at-least-once stays."""
    for _ in range(2):
        assert client.post("/api/capture", json={"text": "note: same twice"}).status_code == 200
    assert len(client.get("/api/notes").json()) == 2


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


def test_human_task_capture_returns_to_my_day(client):
    out = client.post("/api/capture", json={"text": "todo: write the release note"}).json()

    task = client.get(f"/api/tasks/{out['id']}").json()
    assert task["assignee"] == "tester"
    assert out["id"] in {
        row["id"] for row in client.get("/api/briefing").json()["your_work"]["tasks"]
    }


def test_agent_task_capture_stays_unassigned(fresh_db):
    from app.services import capture

    kind, entity, payload = capture.plan("todo: inspect the queue", actor="scout", origin="agent")
    assert (kind, entity, payload["assignee"]) == ("task", "task", "")

    out = capture.capture("todo: inspect the queue", actor="scout", origin="agent")
    task = fresh_db.query_row("SELECT assignee FROM tasks WHERE id = ?", (out["id"],))
    assert task["assignee"] == ""


def test_mock_agent_promise_capture_ack(client):
    out = client.post("/api/chat", json={"thread_id": "t", "message": "promised: report to legal"})
    assert "error" not in out.text.lower() or "Promise" in out.text
    rows = client.get("/api/promises").json()
    assert len(rows) == 1
    assert "report to legal" in rows[0]["promise"]
