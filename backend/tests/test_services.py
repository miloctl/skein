from datetime import datetime, timedelta, timezone

import pytest


def test_milestone_task_lifecycle(fresh_db):
    from app.services import work

    m = work.create_milestone("Ship v1", project="demo", actor="alice")
    t = work.create_task(
        "Write docs", milestone_id=m["id"], assignee="bob", priority="high", actor="alice"
    )
    work.update_task(t["id"], status="in_progress", actor="bob")
    tasks = work.list_tasks(milestone_id=m["id"])
    assert tasks[0]["status"] == "in_progress"
    assert tasks[0]["created_by"] == "alice"
    assert tasks[0]["origin"] == "human"

    with pytest.raises(ValueError):
        work.update_task(t["id"], status="bogus")
    with pytest.raises(ValueError):
        work.create_task("x", priority="nope")


def test_standup_auto_extracts_blocker(fresh_db):
    from app.services import blockers, collab

    collab.post_standup("carol", today="ship auth", blockers="waiting on vendor keys")
    open_blockers = blockers.list_blockers()
    assert len(open_blockers) == 1
    assert open_blockers[0]["owner"] == "carol"
    assert "vendor" in open_blockers[0]["title"]


def test_blocker_escalation_sweep(fresh_db):
    from app.services import blockers

    b = blockers.raise_blocker("stuck on CI", impact="high")  # 8h threshold
    old = (datetime.now(timezone.utc) - timedelta(hours=9)).isoformat(timespec="seconds")
    fresh_db.execute("UPDATE blockers SET created_at = ? WHERE id = ?", (old, b["id"]))

    escalated = blockers.sweep_escalations()
    assert len(escalated) == 1
    assert blockers.list_blockers(status="escalated")[0]["id"] == b["id"]
    assert blockers.sweep_escalations() == []  # idempotent


def test_intake_scoring_and_accept_creates_engagement(fresh_db):
    from app.services import engagements, intake

    r = intake.submit_request("Migrate billing", requester="cto", project_class="migration")
    scored = intake.score_request(r["id"], reach=5, impact=4, confidence=3, effort=2)
    assert scored["score"] == 30.0

    with pytest.raises(ValueError):
        intake.disposition_request(r["id"], "accepted", "")  # reason required
    intake.disposition_request(r["id"], "accepted", "high value, fits Q3")
    assert engagements.list_engagements()[0]["name"] == "Migrate billing"


def test_review_propose_approve_applies_change(fresh_db):
    from app.services import review, work

    p = review.propose_change("task", "create", {"title": "From agent"}, actor="agent-1")
    assert review.list_changes("pending")[0]["id"] == p["id"]

    result = review.approve_change(p["id"], actor="alice")
    task = work.list_tasks()[0]
    assert task["title"] == "From agent"
    assert task["origin"] == "agent_verified"
    assert result["result"]["id"] == task["id"]

    with pytest.raises(ValueError):
        review.approve_change(p["id"], actor="alice")  # already approved


def test_review_reject(fresh_db):
    from app.services import review, work

    p = review.propose_change("task", "create", {"title": "Bad idea"}, actor="agent-1")
    review.reject_change(p["id"], note="not now", actor="alice")
    assert work.list_tasks() == []
    assert review.list_changes("rejected")[0]["review_note"] == "not now"


@pytest.mark.parametrize(
    "text,kind",
    [
        ("todo: ship the API", "task"),
        ("fix the login redirect", "task"),
        ("why is staging down?", "question"),
        ("q: who owns billing?", "question"),
        ("decision: we're using SQLite", "decision"),
        ("we decided to go with FastAPI", "decision"),
        ("blocked on vendor contract", "blocker"),
        ("til: WAL mode needs busy_timeout", "note"),
        ("random musing about architecture", "note"),
    ],
)
def test_capture_classification(fresh_db, text, kind):
    from app.services import capture

    assert capture.capture(text, actor="dana")["kind"] == kind


def test_playbook_instantiate_and_handoff(fresh_db):
    from app.services import engagements, handoff, playbooks, work

    engagements.record_lesson("Always dry-run cutover", project_class="migration")
    created = playbooks.instantiate("migration", "Billing move", lead="alice")
    assert len(created["milestones"]) == 3
    assert len(created["tasks"]) >= 9
    assert len(created["events"]) == 2

    with pytest.raises(ValueError):
        playbooks.instantiate("migration", "Billing move")  # duplicate name

    eng_id = created["engagement"]["id"]
    work.update_task(created["tasks"][0]["id"], status="done")
    pack = handoff.generate_handoff(eng_id, actor="alice")
    assert "Billing move" in pack["markdown"]
    assert "Always dry-run cutover" in pack["markdown"]
    assert handoff.list_artifacts(eng_id)[0]["kind"] == "handoff"


def test_search_indexes_writes(fresh_db):
    from app.services import collab, search, work

    work.create_task("Optimize SQLite queries")
    collab.record_decision("DB choice", "We are using SQLite until 10 agents")
    hits = search.search("sqlite")
    entities = {h["entity"] for h in hits}
    assert {"task", "decision"} <= entities


def test_capacity_overcommit_visible(fresh_db):
    from app.services import engagements, users

    users.ensure_user("alice")
    a = engagements.create_engagement("Alpha")
    b = engagements.create_engagement("Beta")
    engagements.allocate("alice", a["id"], 80)
    engagements.allocate("alice", b["id"], 40)
    cap = engagements.capacity()
    assert cap[0]["person"] == "alice"
    assert cap[0]["total_percent"] == 120


def test_digest_builds_without_model(fresh_db):
    from app.services import digest, work

    work.create_milestone("Due soon", due_date="2020-01-01")
    md = digest.build_digest()
    assert "Daily digest" in md
    result = digest.publish_digest(actor="test")
    assert "Due soon" in result["markdown"]
