"""Tests for the Insights layer: trends, findings rules, dedupe, digest."""

from datetime import UTC, datetime, timedelta

from conftest import _ago


def _mk_blocker(db, title, created_days_ago, resolved_days_ago, escalated=False):
    ts = db.now()
    bid = db.execute(
        "INSERT INTO blockers (title, status, resolved_at, escalated_at,"
        " created_at, updated_at) VALUES (?, 'resolved', ?, ?, ?, ?)"
        " RETURNING id",
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


def test_promise_rules(client, fresh_db):
    from app.services import insights

    c = client.post(
        "/api/promises",
        json={
            "promise": "board demo",
            "to_whom": "CEO",
            "due_date": (datetime.now(UTC).date() + timedelta(days=3)).isoformat(),
        },
    ).json()
    out = insights._r_promises_external()
    assert any(f["rule_id"] == "promise_due" for f in out)

    client.post(f"/api/promises/{c['id']}/status", json={"status": "missed"})
    out = insights._r_promises_external()
    assert any(f["rule_id"] == "promise_missed" and f["severity"] == "high" for f in out)


def test_decision_decay_rule(client, fresh_db):
    from app.services import collab, insights

    for i in range(3):
        client.post(
            "/api/decisions", json={"title": f"d{i}", "decision": "x", "review_by": "2026-01-01"}
        )
    collab.sweep_stale_decisions()
    out = insights._r_decision_decay()
    assert out and out[0]["n"] == 3


def test_evidence_gap_fires_on_delegated_work_with_no_worklog(fresh_db, monkeypatch):
    """The trust loop measuring itself: a sponsor accepted an agent's work
    with nothing to audit. A delegated task with a worklog note stays silent,
    and a plain human task stays silent however it closes — judging every
    person's closing hygiene is what the anti-surveillance rule refuses."""
    from app import config
    from app.services import delegation, insights, users, work

    monkeypatch.setattr(config, "AGENT_REVIEW", False)
    users.ensure_user("mira")

    bare = work.create_task(title="accepted on trust", actor="mira")
    delegation.delegate_task(bare["id"], "scout", "mira", actor="mira")
    work.update_task(bare["id"], status="done", actor="mira")

    noted = work.create_task(title="accepted with notes", actor="mira")
    delegation.delegate_task(noted["id"], "scout", "mira", actor="mira")
    delegation.report_progress(noted["id"], "probe scaffolded, tests pass", actor="scout")
    work.update_task(noted["id"], status="done", actor="mira")

    human = work.create_task(title="a person's own task", actor="mira")
    work.update_task(human["id"], status="done", actor="mira")

    fired = insights._r_evidence_gap()
    assert [f["subject"] for f in fired] == [f"task-{bare['id']}"]
    assert fired[0]["receipt"]["agent"] == "scout"
    assert fired[0]["receipt"]["sponsor"] == "mira"


def test_a_persisting_condition_is_one_row_across_weeks(fresh_db, monkeypatch):
    """run_findings mints one row per ISO week while a condition holds, and the
    feed showed both — the same broken cron as two problems, last week's badge
    beside this week's blank. One row per (rule, subject) now, and a verdict on
    any week's row travels to the surviving one."""
    from datetime import timedelta

    from app import config
    from app.services import insights

    monkeypatch.setattr(config, "SCHEDULER_ENABLED", True)
    fresh_db.execute(
        "INSERT INTO job_outcomes (job, status, detail, duration_ms, created_at)"
        " VALUES ('daily-digest', 'ok', '', 0, '2020-01-01T00:00:00+00:00')"
    )
    first = insights.run_findings(actor="tester")
    old_row = next(f for f in first["findings"] if f["rule_id"] == "job_stale")

    week_later = insights._today() + timedelta(weeks=1)
    monkeypatch.setattr(insights, "_today", lambda: week_later)
    second = insights.run_findings(actor="tester")
    assert any(f["rule_id"] == "job_stale" for f in second["findings"])

    stale = [f for f in insights.list_findings() if f["rule_id"] == "job_stale"]
    assert len(stale) == 1
    assert stale[0]["weeks_firing"] == 2
    assert stale[0]["first_week"] < stale[0]["week"]
    assert stale[0]["audience"] == "system"
    assert stale[0]["label"] == "Scheduled job"

    # the verdict lands on last week's row id; the feed's surviving row carries it
    insights.disposition_finding(old_row["id"], "dismissed", actor="tester")
    stale = [f for f in insights.list_findings() if f["rule_id"] == "job_stale"]
    assert stale[0]["disposition"] == "dismissed"


def test_run_findings_dedupes_within_week(client, fresh_db):
    from app.services import insights

    q = client.post("/api/questions", json={"question": "still open?"}).json()
    fresh_db.execute("UPDATE questions SET created_at = ? WHERE id = ?", (_ago(6), q["id"]))
    first = insights.run_findings()
    assert first["new"] >= 1
    second = insights.run_findings()
    assert second["new"] == 0  # same week, same subjects — silence

    stored = insights.list_findings()
    question = next(f for f in stored if f["rule_id"] == "question_aging")
    assert question["audience"] == "team"
    assert question["label"] == "Question aging"


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


def test_every_findings_rule_runs(fresh_db):
    """Call each rule DIRECTLY, so a broken one fails here.

    run_findings catches per rule and logs, which is right in production — one
    broken rule must not take the weekly digest down — but it means a rule
    whose SQL no longer parses returns silently forever, and silence is what a
    clean run looks like. Every other test in this file goes through
    run_findings, so none of them can tell the difference. A PostgreSQL
    migration left `SUM(<boolean>)` in one rule and every suite still passed.
    """
    from app.services.insights import RULES

    broken = []
    for rule in RULES:
        try:
            rule()
        except Exception as exc:
            broken.append(f"{rule.__name__}: {type(exc).__name__}: {exc}")
    assert not broken, "findings rules that cannot run:\n" + "\n".join(broken)


def test_task_abandoned_fires_on_spike_then_silence_only(client, fresh_db):
    from app.services import delegation, insights, work
    from app.services.users import ensure_user

    ensure_user("research-agent", kind="agent")
    ensure_user("sponsor", kind="human")
    t = work.create_task("port the exporter", actor="sponsor")
    delegation.delegate_task(t["id"], agent="research-agent", sponsor="sponsor", actor="sponsor")
    for actor in ("research-agent", "sponsor", "research-agent"):
        delegation.report_progress(t["id"], "moved it forward", actor=actor)
    # recently active: not abandoned, whatever the note count
    assert insights._r_task_abandoned() == []

    fresh_db.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (_ago(20), t["id"]))
    fresh_db.execute(
        "UPDATE task_worklog SET created_at = ? WHERE task_id = ?", (_ago(20), t["id"])
    )
    out = insights._r_task_abandoned()
    assert out and out[0]["subject"] == f"task:{t['id']}"
    assert "3 worklog notes from 2 people" in out[0]["message"]
    # the receipt names the work, never the people
    assert "sponsor" not in str(out[0]["receipt"])

    # one author working alone is aging_wip's territory, not a walk-away
    fresh_db.execute("UPDATE task_worklog SET author = 'sponsor' WHERE task_id = ?", (t["id"],))
    assert insights._r_task_abandoned() == []
