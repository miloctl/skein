"""Fixes from the 3-week dogfood run: capture grammar gaps, question
reassignment, review-notification clearing, findings dispositions in the
feed, pulse feedback, user deactivation, intake lead/kill_criteria."""


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


def test_question_reassign_and_notify(client):
    client.post("/api/users/growth-interests", json={"interests": "x"}, headers={"X-User": "dana"})
    qid = client.post("/api/questions", json={"question": "who owns the roadmap?"}).json()["id"]
    r = client.patch(f"/api/questions/{qid}", json={"assigned_to": "dana"})
    assert r.status_code == 200
    assert client.get("/api/questions").json()[0]["assigned_to"] == "dana"


def test_review_resolution_clears_notification(client, fresh_db):
    from app.services import review

    p = review.propose_change(
        "task", "create", {"title": "from an agent"}, actor="scout", origin="agent"
    )
    unread = fresh_db.query(
        "SELECT * FROM notifications WHERE read_at IS NULL AND message LIKE ?",
        (f"Review needed: #{p['id']}%",),
    )
    assert unread
    review.approve_change(p["id"], actor="tester")
    still = fresh_db.query(
        "SELECT * FROM notifications WHERE read_at IS NULL AND message LIKE ?",
        (f"Review needed: #{p['id']}%",),
    )
    assert not still


def test_findings_feed_carries_disposition(client, fresh_db):
    from app.services import insights

    fresh_db.execute(
        "INSERT INTO findings (rule_id, subject, severity, message, receipt, week, created_at)"
        " VALUES ('question_aging', 'question-9', 'low', 'm', '{}', ?, ?)",
        (insights._week(), fresh_db.now()),
    )
    fid = fresh_db.query("SELECT id FROM findings")[0]["id"]
    rows = insights.list_findings()
    assert rows[0]["disposition"] == ""
    insights.disposition_finding(fid, "dismissed", reason="test", actor="tester")
    rows = insights.list_findings()
    assert rows[0]["disposition"] == "dismissed"


def test_pulse_feedback_without_input_text(client):
    r = client.post("/api/feedback", json={"kind": "pulse", "verdict": "up"})
    assert r.status_code == 200
    r = client.post("/api/feedback", json={"kind": "chat", "verdict": "up"})
    assert r.status_code == 422


def test_user_deactivate_needs_strong_identity(client):
    client.post("/api/users/growth-interests", json={"interests": ""}, headers={"X-User": "typo"})
    r = client.post("/api/users/typo/active", json={"active": False})
    assert r.status_code == 403


def test_user_deactivate_removes_from_roster(client, fresh_db):
    from app.services import users

    users.ensure_user("typo")
    users.set_active("typo", False, actor="tester")
    assert "typo" not in [u["name"] for u in users.list_users()]
    # history stays; the row still exists
    assert "typo" in [u["name"] for u in users.list_users(active_only=False)]


def test_intake_accept_carries_lead_and_kill_criteria(client):
    rid = client.post(
        "/api/intake",
        json={"title": "Shadow alerts", "requester": "pm"},
    ).json()["id"]
    r = client.post(
        f"/api/intake/{rid}/disposition",
        json={
            "disposition": "accepted",
            "reason": "cheap probe",
            "kind": "experiment",
            "timebox_end": "2026-08-08",
            "lead": "tester",
            "kill_criteria": "FP rate >20% after a week",
        },
    )
    assert r.status_code == 200
    eng = next(e for e in client.get("/api/engagements").json() if e["name"] == "Shadow alerts")
    assert eng["lead"] == "tester"
    assert eng["kill_criteria"] == "FP rate >20% after a week"


def test_digest_groups_job_stale(client, fresh_db):
    from app.services import insights

    for job in ("a-job", "b-job", "c-job"):
        fresh_db.execute(
            "INSERT INTO findings (rule_id, subject, severity, message, receipt, week, created_at)"
            " VALUES ('job_stale', ?, 'high', 'stale', '{}', ?, ?)",
            (job, insights._week(), fresh_db.now()),
        )
    rows = insights.digest_findings()
    stale = [r for r in rows if r["rule_id"] == "job_stale"]
    assert len(stale) == 1
    assert "3 scheduled jobs" in stale[0]["message"]


def test_ingest_question_line_assigns(client):
    client.post("/api/users/growth-interests", json={"interests": "x"}, headers={"X-User": "mira"})
    r = client.post("/api/ingest", json={"text": "q: mira — did the export finish?"})
    pid = r.json()["proposals"][0]["id"]
    client.post(f"/api/review/{pid}/approve", json={})
    q = client.get("/api/questions").json()[0]
    assert q["assigned_to"] == "mira"
    assert q["question"] == "did the export finish?"


def test_eval_capture_freetext_correction_is_unscored(client):
    client.post(
        "/api/feedback",
        json={
            "kind": "capture",
            "input_text": "decision: x — review by 2026-10-01",
            "output": "decision",
            "verdict": "corrected",
            "correction": "review_by should have been parsed",
        },
    )
    client.post(
        "/api/feedback",
        json={
            "kind": "capture",
            "input_text": "todo: ship it",
            "output": "task",
            "verdict": "up",
        },
    )
    out = client.get("/api/eval/capture").json()
    assert out["cases"] == 1 and out["passed"] == 1
    assert len(out["unscored"]) == 1
