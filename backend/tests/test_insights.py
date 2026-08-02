"""Tests for the Insights layer: trends, findings rules, dedupe, digest."""

from datetime import datetime, timedelta, timezone

from conftest import _ago


def _mk_blocker(db, title, created_days_ago, resolved_days_ago, escalated=False):
    ts = db.now()
    bid = db.execute(
        "INSERT INTO blockers (title, status, resolved_at, escalated_at,"
        " created_at, updated_at) VALUES (?, 'resolved', ?, ?, ?, ?)",
        (
            title,
            _ago(resolved_days_ago),
            _ago(created_days_ago) if escalated else None,
            _ago(created_days_ago),
            ts,
        ),
    )
    return bid


def test_mttr_windows_and_small_n_gate(fresh_db):
    from app.services import insights

    # 4 blockers in the current window: below the n>=8 gate — no finding
    for i in range(4):
        _mk_blocker(fresh_db, f"b{i}", 5, 4)
    w = insights.mttr_windows()
    assert w["current"]["n"] == 4
    assert insights._r_mttr() == []


def test_mttr_regression_fires_with_receipts(fresh_db):
    from app.services import insights

    for i in range(8):  # prior window: fast clears (~2.4h)
        _mk_blocker(fresh_db, f"old{i}", 40.1, 40)
    for i in range(8):  # current window: slow clears (~48h)
        _mk_blocker(fresh_db, f"new{i}", 12, 10)
    out = insights._r_mttr()
    assert len(out) == 1
    f = out[0]
    assert f["rule_id"] == "mttr_regression" and f["severity"] == "high"
    assert f["n"] == 8 and len(f["receipt"]["slowest"]) == 3


def test_mttr_improvement_is_positive(fresh_db):
    from app.services import insights

    for i in range(8):
        _mk_blocker(fresh_db, f"old{i}", 42, 40)  # ~48h
    for i in range(8):
        _mk_blocker(fresh_db, f"new{i}", 10.1, 10)  # ~2.4h
    out = insights._r_mttr()
    assert out and out[0]["rule_id"] == "mttr_improvement"
    assert out[0]["severity"] == "positive"


def test_aging_wip_threshold(client, fresh_db):
    from app.services import insights

    assert insights._r_aging_wip() == []
    for i in range(4):
        t = client.post("/api/tasks", json={"title": f"stuck{i}"}).json()
        client.patch(f"/api/tasks/{t['id']}", json={"status": "in_progress"})
        fresh_db.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (_ago(20), t["id"]))
    out = insights._r_aging_wip()
    assert out and out[0]["n"] == 4
    assert len(out[0]["receipt"]["tasks"]) == 4


def test_review_stall_and_question_aging(client, fresh_db):
    from app.services import insights, review

    p = review.propose_change("note", "create", {"topic": "t", "content": "c"})
    fresh_db.execute("UPDATE pending_changes SET created_at = ? WHERE id = ?", (_ago(9), p["id"]))
    out = insights._r_review_stall()
    assert out and out[0]["severity"] == "high"

    q = client.post("/api/questions", json={"question": "anyone?"}).json()
    fresh_db.execute("UPDATE questions SET created_at = ? WHERE id = ?", (_ago(6), q["id"]))
    qf = insights._r_question_aging()
    assert qf and qf[0]["subject"] == f"question-{q['id']}"


def test_commitment_rules(client, fresh_db):
    from app.services import insights

    c = client.post(
        "/api/commitments",
        json={
            "promise": "board demo",
            "to_whom": "CEO",
            "due_date": (datetime.now(timezone.utc).date() + timedelta(days=3)).isoformat(),
        },
    ).json()
    out = insights._r_commitments_external()
    assert any(f["rule_id"] == "commitment_due" for f in out)

    client.post(f"/api/commitments/{c['id']}/status", json={"status": "missed"})
    out = insights._r_commitments_external()
    assert any(f["rule_id"] == "commitment_missed" and f["severity"] == "high" for f in out)


def test_decision_decay_rule(client, fresh_db):
    from app.services import collab, insights

    for i in range(3):
        client.post(
            "/api/decisions", json={"title": f"d{i}", "decision": "x", "review_by": "2026-01-01"}
        )
    collab.sweep_stale_decisions()
    out = insights._r_decision_decay()
    assert out and out[0]["n"] == 3


def test_run_findings_dedupes_within_week(client, fresh_db):
    from app.services import insights

    q = client.post("/api/questions", json={"question": "still open?"}).json()
    fresh_db.execute("UPDATE questions SET created_at = ? WHERE id = ?", (_ago(6), q["id"]))
    first = insights.run_findings()
    assert first["new"] >= 1
    second = insights.run_findings()
    assert second["new"] == 0  # same week, same subjects — silence

    stored = insights.list_findings()
    assert any(f["rule_id"] == "question_aging" for f in stored)


def test_digest_carries_top_findings(client, fresh_db):
    from app.services import digest, insights

    q = client.post("/api/questions", json={"question": "digest me?"}).json()
    fresh_db.execute("UPDATE questions SET created_at = ? WHERE id = ?", (_ago(6), q["id"]))
    insights.run_findings()
    md = digest.build_digest()
    assert "Findings this week" in md
    assert "digest me" in md


def test_digest_findings_capped_at_three(client, fresh_db):
    from app.services import insights

    for i in range(5):
        q = client.post("/api/questions", json={"question": f"q{i}?"}).json()
        fresh_db.execute("UPDATE questions SET created_at = ? WHERE id = ?", (_ago(6), q["id"]))
    insights.run_findings()
    assert len(insights.digest_findings()) == 3


def test_insights_endpoint_shape(client):
    out = client.get("/api/insights").json()
    for key in (
        "mttr",
        "automation_ratio",
        "review_trend",
        "intake_funnel",
        "token_spend_weekly",
        "adoption",
        "findings",
    ):
        assert key in out
    assert client.get("/api/findings").json() == []


def test_finding_feedback_kind_accepted(client):
    r = client.post(
        "/api/feedback",
        json={"kind": "finding", "input_text": "mttr_regression #4", "verdict": "up"},
    )
    assert r.status_code == 200


def test_automation_ratio_counts_origins(client, fresh_db):
    from app.services import insights, work

    client.post("/api/tasks", json={"title": "human task"})
    work.create_task("agent task", actor="agent", origin="agent")
    ratio = insights.automation_ratio()
    assert ratio and ratio[-1]["total"] >= 2
    assert 0 < ratio[-1]["automation_share"] < 1
