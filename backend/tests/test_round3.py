"""Tests for round-3 features: portfolio, weekly line, delegation/authority,
review analytics + eval corpus, decision half-life, commitments, context pack."""

from datetime import datetime, timedelta, timezone


def _ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def _days_ahead(days: int) -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=days)).isoformat()


# ---- flow / completed_at -----------------------------------------------------

def test_completed_at_stamped_and_cleared(client, fresh_db):
    t = client.post("/api/tasks", json={"title": "flow me"}).json()
    client.patch(f"/api/tasks/{t['id']}", json={"status": "done"})
    row = fresh_db.query_one("SELECT * FROM tasks WHERE id = ?", (t["id"],))
    assert row["completed_at"] is not None

    client.patch(f"/api/tasks/{t['id']}", json={"status": "in_progress"})
    row = fresh_db.query_one("SELECT * FROM tasks WHERE id = ?", (t["id"],))
    assert row["completed_at"] is None


def test_flow_metrics_cycle_wip_stale(client, fresh_db):
    done = client.post("/api/tasks", json={"title": "shipped"}).json()
    client.patch(f"/api/tasks/{done['id']}", json={"status": "done"})
    stale = client.post("/api/tasks", json={"title": "stuck", "assignee": "ava"}).json()
    client.patch(f"/api/tasks/{stale['id']}", json={"status": "in_progress"})
    fresh_db.execute("UPDATE tasks SET updated_at = ? WHERE id = ?",
                     (_ago(10), stale["id"]))

    flow = client.get("/api/portfolio/flow").json()
    assert flow["cycle_time"]["tasks_done"] == 1
    assert sum(flow["throughput_by_week"].values()) == 1
    assert flow["wip_by_person"][0]["person"] == "ava"
    assert [s["id"] for s in flow["stale_wip"]] == [stale["id"]]


def test_committed_week_validation(client):
    t = client.post("/api/tasks", json={"title": "x"}).json()
    r = client.patch(f"/api/tasks/{t['id']}", json={"committed_week": "next week"})
    assert r.status_code == 400
    ok = client.patch(f"/api/tasks/{t['id']}", json={"committed_week": "2026-W31"})
    assert ok.status_code == 200


# ---- portfolio health / conflicts / forecast / what-if ------------------------

def _engagement_with_milestone(client, name="Apollo", due=""):
    client.post("/api/engagements", json={"name": name})
    m = client.post("/api/milestones",
                    json={"title": f"{name} m1", "project": name,
                          "due_date": due}).json()
    return m


def test_engagement_health_receipts(client, fresh_db):
    m = _engagement_with_milestone(client, "Apollo", due="2026-01-01")
    health = client.get("/api/portfolio/health").json()
    apollo = next(h for h in health if h["name"] == "Apollo")
    assert apollo["health"] == "yellow"
    assert any("overdue" in r for r in apollo["receipts"])

    # escalated linked blocker turns it red
    t = client.post("/api/tasks", json={"title": "t", "milestone_id": m["id"]}).json()
    b = client.post("/api/blockers", json={"title": "vendor", "task_id": t["id"]}).json()
    fresh_db.execute("UPDATE blockers SET status = 'escalated' WHERE id = ?", (b["id"],))
    health = client.get("/api/portfolio/health").json()
    apollo = next(h for h in health if h["name"] == "Apollo")
    assert apollo["health"] == "red"
    assert any("escalated" in r for r in apollo["receipts"])


def test_health_green_when_clean(client):
    _engagement_with_milestone(client, "Zen", due=_days_ahead(30))
    health = client.get("/api/portfolio/health").json()
    zen = next(h for h in health if h["name"] == "Zen")
    assert zen["health"] == "green" and zen["receipts"] == []


def test_allocation_conflicts(client):
    e1 = client.post("/api/engagements", json={"name": "One"}).json()
    e2 = client.post("/api/engagements", json={"name": "Two"}).json()
    client.post(f"/api/engagements/{e1['id']}/allocate",
                json={"person": "dana", "percent": 80})
    client.post(f"/api/engagements/{e2['id']}/allocate",
                json={"person": "dana", "percent": 50})
    conflicts = client.get("/api/portfolio/conflicts").json()
    assert conflicts[0]["person"] == "dana"
    assert conflicts[0]["total_percent"] == 130


def test_what_if_projection(client):
    e = client.post("/api/engagements", json={"name": "Busy"}).json()
    client.post(f"/api/engagements/{e['id']}/allocate",
                json={"person": "dana", "percent": 80})
    req = client.post("/api/intake", json={"title": "new ask"}).json()
    out = client.post(f"/api/intake/{req['id']}/what-if",
                      json={"people": ["dana", "lee"], "percent": 50}).json()
    dana = next(p for p in out["projection"] if p["person"] == "dana")
    assert dana["projected_percent"] == 130 and dana["overcommitted"]
    lee = next(p for p in out["projection"] if p["person"] == "lee")
    assert lee["projected_percent"] == 50 and not lee["overcommitted"]
    assert client.post("/api/intake/999999/what-if",
                       json={"people": ["x"]}).status_code == 400


def test_slip_forecast_uses_history(client, fresh_db):
    _engagement_with_milestone(client, "Hist", due=_days_ahead(10))
    done = client.post("/api/milestones",
                       json={"title": "old", "project": "Hist",
                             "due_date": "2026-06-01"}).json()
    client.patch(f"/api/milestones/{done['id']}", json={"status": "done"})
    fresh_db.execute("UPDATE milestones SET updated_at = '2026-06-08T00:00:00' WHERE id = ?",
                     (done["id"],))
    out = client.get("/api/portfolio/forecast").json()
    assert out["basis"]["milestones_measured"] == 1
    assert out["basis"]["avg_slip_days"] == 7.0
    f = out["forecasts"][0]
    assert f["forecast_date"] > f["due_date"]


def test_exec_readout_artifact(client):
    _engagement_with_milestone(client, "Read Me", due="2026-01-01")
    client.post("/api/commitments",
                json={"promise": "demo to CEO", "to_whom": "CEO",
                      "due_date": _days_ahead(3)})
    out = client.post("/api/portfolio/readout").json()
    assert "Exec readout" in out["markdown"]
    assert "Read Me" in out["markdown"]
    assert "demo to CEO" in out["markdown"]
    assert any(a["kind"] == "readout" for a in client.get("/api/artifacts").json())


# ---- weekly commitment line ---------------------------------------------------

def test_weekly_draft_and_plan_via_review_inbox(client, fresh_db):
    from app.services import weekly

    client.post("/api/standups", json={"today": "here"})  # tester becomes active human
    t1 = client.post("/api/tasks", json={"title": "a", "assignee": "tester",
                                         "priority": "urgent"}).json()
    t2 = client.post("/api/tasks", json={"title": "b", "assignee": "tester"}).json()

    draft = client.get("/api/week/draft").json()
    ids = [i["task_id"] for i in draft["items"]]
    assert ids[0] == t1["id"] and t2["id"] in ids  # urgent first

    out = weekly.propose_weekly_plan(actor="scheduler")
    assert out["status"] == "pending"
    approved = client.post(f"/api/review/{out['id']}/approve", json={}).json()
    assert approved["status"] == "approved"

    week = client.get("/api/week").json()
    assert week["committed"] == 2
    client.patch(f"/api/tasks/{t1['id']}", json={"status": "done"})
    week = client.get("/api/week").json()
    assert week["done"] == 1 and week["kept_percent"] == 50

    # claim: second propose in the same week is a no-op
    assert "skipped" in weekly.propose_weekly_plan(actor="scheduler")


# ---- delegation / authority / trust --------------------------------------------

def test_delegate_task_and_inbox(client, fresh_db):
    t = client.post("/api/tasks", json={"title": "agent work"}).json()
    out = client.post(f"/api/tasks/{t['id']}/delegate",
                      json={"agent": "scribe", "sponsor": "tester"}).json()
    assert out["delegated_agent"] == "scribe"
    users = {u["name"]: u for u in client.get("/api/users").json()}
    assert users["scribe"]["kind"] == "agent"

    inbox = client.get("/api/agents/scribe/inbox").json()
    assert [x["id"] for x in inbox["delegated_tasks"]] == [t["id"]]

    mc = client.get("/api/agents").json()
    scribe = next(a for a in mc if a["agent"] == "scribe")
    assert scribe["open_tasks"] == 1


def test_authority_matrix_gate(client, fresh_db, monkeypatch):
    import json as j

    from app import config
    from app.tools.portfolio import add_commitment

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    # default 'review' → proposal
    out = j.loads(add_commitment(promise="p1"))
    assert out.get("note") == "queued for human review"

    # autonomous → direct write even with review mode on
    client.post("/api/agents/authority",
                json={"agent": "agent", "entity": "commitment", "level": "autonomous"})
    out = j.loads(add_commitment(promise="p2"))
    assert out.get("status") == "open"

    # forbidden → refused
    client.post("/api/agents/authority",
                json={"agent": "agent", "entity": "commitment", "level": "forbidden"})
    out = j.loads(add_commitment(promise="p3"))
    assert "forbidden" in out["error"]


def test_trust_scores_streak_suggestion(client, fresh_db):
    from app.services import review

    for i in range(5):
        p = review.propose_change("note", "create",
                                  {"topic": f"t{i}", "content": "c"}, actor="scribe")
        client.post(f"/api/review/{p['id']}/approve", json={})
    trust = client.get("/api/agents/trust").json()
    row = next(r for r in trust if r["agent"] == "scribe")
    assert row["approved"] == 5 and row["recent_streak"] == 5
    assert "autonomous" in row["suggestion"]


# ---- review analytics + eval corpus --------------------------------------------

def test_review_stats(client):
    from app.services import review

    p1 = review.propose_change("note", "create", {"topic": "a", "content": "b"})
    p2 = review.propose_change("task", "create", {"title": "x"})
    client.post(f"/api/review/{p1['id']}/approve", json={})
    client.post(f"/api/review/{p2['id']}/reject", json={"note": "not needed"})
    stats = client.get("/api/review/stats").json()
    entities = {r["entity"]: r for r in stats["by_entity"]}
    assert entities["note"]["approved"] == 1
    assert entities["task"]["rejected"] == 1
    assert stats["recent_rejections"][0]["review_note"] == "not needed"


def test_feedback_and_eval_capture(client):
    # correct classification, thumbs up
    client.post("/api/feedback", json={"kind": "capture", "input_text": "todo: ship it",
                                       "output": "task", "verdict": "up"})
    # a case the rules get wrong today
    client.post("/api/feedback", json={"kind": "capture",
                                       "input_text": "remember we owe legal a summary",
                                       "output": "note", "verdict": "corrected",
                                       "correction": "commitment"})
    out = client.get("/api/eval/capture").json()
    assert out["cases"] == 2 and out["passed"] == 1
    assert out["mismatches"][0]["expected"] == "commitment"

    r = client.post("/api/feedback", json={"kind": "capture", "input_text": "x",
                                           "verdict": "corrected"})
    assert r.status_code == 400  # corrected needs the correction


# ---- decision half-life ---------------------------------------------------------

def test_decision_half_life_sweep_and_supersede(client, fresh_db):
    from app.services import collab

    d = client.post("/api/decisions",
                    json={"title": "Use SQLite", "decision": "keep it simple",
                          "review_by": "2026-01-01"}).json()
    stale = collab.sweep_stale_decisions()
    assert [s["id"] for s in stale] == [d["id"]]
    assert collab.sweep_stale_decisions() == []  # status flip is the claim
    row = fresh_db.query_one("SELECT * FROM decisions WHERE id = ?", (d["id"],))
    assert row["status"] == "stale"

    new = client.post(f"/api/decisions/{d['id']}/supersede",
                      json={"title": "Use SQLite + Litestream",
                            "decision": "replicate off-box"}).json()
    old = fresh_db.query_one("SELECT * FROM decisions WHERE id = ?", (d["id"],))
    assert old["status"] == "superseded" and old["superseded_by"] == new["id"]
    r = client.post(f"/api/decisions/{d['id']}/supersede",
                    json={"title": "again", "decision": "no"})
    assert r.status_code == 400


def test_decision_reconfirm(client, fresh_db):
    d = client.post("/api/decisions",
                    json={"title": "T", "decision": "D",
                          "review_by": "2026-01-01"}).json()
    from app.services import collab

    collab.sweep_stale_decisions()
    out = client.post(f"/api/decisions/{d['id']}/reconfirm",
                      json={"review_by": _days_ahead(90)}).json()
    assert out["status"] == "active"


# ---- commitments -----------------------------------------------------------------

def test_commitment_lifecycle_and_capture(client):
    c = client.post("/api/commitments",
                    json={"promise": "ship v1 to ops", "to_whom": "ops"}).json()
    assert c["status"] == "open"
    client.post(f"/api/commitments/{c['id']}/status", json={"status": "kept"})
    r = client.post(f"/api/commitments/{c['id']}/status", json={"status": "missed"})
    assert r.status_code == 400  # terminal

    cap = client.post("/api/capture",
                      json={"text": "promised: security review to legal by Friday"}).json()
    assert cap["kind"] == "commitment"
    assert any("security review" in x["promise"]
               for x in client.get("/api/commitments").json())


# ---- context pack -----------------------------------------------------------------

def test_context_pack_versions_only_on_change(client):
    client.post("/api/decisions", json={"title": "Ship weekly", "decision": "always"})
    pack = client.get("/api/context-pack").json()
    assert pack["version"] == 1
    assert "Ship weekly" in pack["content"]

    again = client.post("/api/context-pack/publish").json()
    assert again["changed"] is False and again["version"] == 1

    client.post("/api/notes", json={"topic": "convention: PR size",
                                    "content": "keep diffs under 400 lines"})
    bumped = client.post("/api/context-pack/publish").json()
    assert bumped["changed"] is True and bumped["version"] == 2
    pack = client.get("/api/context-pack").json()
    assert "PR size" in pack["content"]


def test_stale_wip_nudge_claims_week(client, fresh_db, monkeypatch):
    from app.services import notifications, portfolio

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)
    t = client.post("/api/tasks", json={"title": "old", "assignee": "ava"}).json()
    client.patch(f"/api/tasks/{t['id']}", json={"status": "in_progress"})
    fresh_db.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (_ago(10), t["id"]))
    assert portfolio.nudge_stale_wip()["nudged"] == 1
    assert portfolio.nudge_stale_wip()["nudged"] == 0  # claimed for this week
    msgs = [n["message"] for n in notifications.list_notifications("ava", unread_only=False)]
    assert any("in progress" in m for m in msgs)
