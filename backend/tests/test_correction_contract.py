"""Correction-contract coverage for the d6fbfb3..fc91802 audit range:
memory.forget, event cancel deindex, deallocate + window-aware capacity,
work-graph relinking, engagement rename propagation, approve-as-proposer
authorship, scoped due_soon, notes route bounds/filter, merge backfill."""

from datetime import datetime, timedelta, timezone

import pytest


def _utc_today():
    return datetime.now(timezone.utc).date()


# ---- memory.forget ----------------------------------------------------------------


def test_forget_removes_memory_everywhere(client):
    from app.services import memory, search

    m = memory.remember("the staging DB password rotates on tuesdays", topic="ops", user="ava")
    assert any(h["entity"] == "memory" for h in search.search("rotates"))
    assert "rotates" in memory.memory_prompt("ava")

    out = client.delete(f"/api/memories/{m['id']}").json()
    assert out["deleted"] is True
    assert memory.recall(user="ava") == []
    assert memory.memory_prompt("ava") == ""
    assert [h for h in search.search("rotates") if h["entity"] == "memory"] == []


def test_forget_missing_memory_404_and_removal_is_logged(client):
    from app import db
    from app.services import memory

    assert client.delete("/api/memories/9999").status_code == 404
    m = memory.remember("wrong fact", topic="bad")
    client.delete(f"/api/memories/{m['id']}")
    logged = db.query("SELECT * FROM activity WHERE action = 'forget'")
    assert logged and f"#{m['id']}" in logged[0]["detail"]
    assert logged[0]["actor"] == "tester"


# ---- schedule.cancel_event --------------------------------------------------------


def test_cancel_event_deindexes_and_404s_on_missing(client):
    from app.services import schedule, search

    e = schedule.schedule_event("Quarterly offsite kickoff", "2026-08-01T10:00")
    assert any(h["entity"] == "event" for h in search.search("offsite"))

    assert client.delete(f"/api/events/{e['id']}").json()["cancelled"] is True
    assert [h for h in search.search("offsite") if h["entity"] == "event"] == []
    assert client.delete(f"/api/events/{e['id']}").status_code == 404


# ---- deallocate + window-aware capacity -------------------------------------------


def test_capacity_and_conflicts_ignore_out_of_window_allocations(fresh_db):
    from app.services import engagements, portfolio

    a = engagements.create_engagement("Alpha")
    b = engagements.create_engagement("Beta")
    yesterday = (_utc_today() - timedelta(days=1)).isoformat()
    engagements.allocate("alice", a["id"], 80)
    engagements.allocate("alice", b["id"], 40, ends_on=yesterday)  # window closed

    cap = engagements.capacity()
    assert cap[0]["person"] == "alice" and cap[0]["total_percent"] == 80
    assert portfolio.allocation_conflicts() == []  # capacity and conflicts agree

    engagements.allocate("alice", b["id"], 40, starts_on=yesterday)  # covers today
    assert engagements.capacity()[0]["total_percent"] == 120
    assert portfolio.allocation_conflicts()[0]["person"] == "alice"


def test_deallocate_removes_row_and_missing_id_404s(client):
    from app.services import engagements

    e = engagements.create_engagement("Gamma")
    aid = engagements.allocate("bo", e["id"], 50)["id"]
    engagements.allocate("cy", e["id"], 30)
    assert len(engagements.list_allocations(e["id"])) == 2

    out = client.delete(f"/api/allocations/{aid}").json()
    assert out["deleted"] is True
    left = engagements.list_allocations(e["id"])
    assert [r["person"] for r in left] == ["cy"]
    assert client.delete(f"/api/allocations/{aid}").status_code == 404

    with pytest.raises(ValueError, match="no allocation"):
        engagements.deallocate(9999)


# ---- work-graph relinking ---------------------------------------------------------


def test_milestone_relink_unlink_and_bad_id(client):
    from app import db
    from app.services import engagements, work

    e = engagements.create_engagement("Delta")
    m = work.create_milestone("orphaned milestone")  # project=default, no link

    assert (
        client.patch(f"/api/milestones/{m['id']}", json={"engagement_id": 9999}).status_code == 400
    )

    client.patch(f"/api/milestones/{m['id']}", json={"engagement_id": e["id"]})
    row = db.query_one("SELECT engagement_id FROM milestones WHERE id = ?", (m["id"],))
    assert row["engagement_id"] == e["id"]

    client.patch(f"/api/milestones/{m['id']}", json={"engagement_id": -1})
    row = db.query_one("SELECT engagement_id FROM milestones WHERE id = ?", (m["id"],))
    assert row["engagement_id"] is None


def test_task_relink_across_milestones(client):
    from app import db
    from app.services import work

    m1 = work.create_milestone("first home")
    m2 = work.create_milestone("second home")
    t = work.create_task("wandering task", milestone_id=m1["id"])

    assert client.patch(f"/api/tasks/{t['id']}", json={"milestone_id": 9999}).status_code == 400

    client.patch(f"/api/tasks/{t['id']}", json={"milestone_id": m2["id"]})
    row = db.query_one("SELECT milestone_id FROM tasks WHERE id = ?", (t["id"],))
    assert row["milestone_id"] == m2["id"]

    client.patch(f"/api/tasks/{t['id']}", json={"milestone_id": -1})
    row = db.query_one("SELECT milestone_id FROM tasks WHERE id = ?", (t["id"],))
    assert row["milestone_id"] is None


def test_unmatched_project_milestone_files_team_notification(fresh_db):
    from app import db
    from app.services import work

    work.create_milestone("lost milestone", project="ghost-project")
    rows = db.query("SELECT * FROM notifications WHERE user = 'team' AND tier = 'digest'")
    assert any("ghost-project" in r["message"] for r in rows)

    db.execute("DELETE FROM notifications")
    work.create_milestone("plain milestone")  # project=default: no nag
    assert db.query("SELECT * FROM notifications") == []


# ---- engagement rename ------------------------------------------------------------


def test_engagement_rename_propagates_and_reindexes(client):
    from app import db
    from app.services import engagements, search, work

    e = engagements.create_engagement("Aurora Launch")
    m = work.create_milestone("ship v1", project="Aurora Launch")

    client.patch(f"/api/engagements/{e['id']}", json={"name": "Borealis Launch"})
    row = db.query_one("SELECT project, engagement_id FROM milestones WHERE id = ?", (m["id"],))
    assert row["project"] == "Borealis Launch" and row["engagement_id"] == e["id"]
    hits = [h for h in search.search("Borealis") if h["entity"] == "engagement"]
    assert hits and hits[0]["entity_id"] == e["id"]


def test_engagement_rename_collision_rejected(client):
    from app.services import engagements

    engagements.create_engagement("Taken")
    e = engagements.create_engagement("Original")
    with pytest.raises(ValueError, match="already exists"):
        engagements.update_engagement(e["id"], name="Taken")
    assert client.patch(f"/api/engagements/{e['id']}", json={"name": "Taken"}).status_code == 400


def test_failed_rename_close_does_not_orphan_milestone_labels(fresh_db):
    from app import db
    from app.services import engagements, work

    e = engagements.create_engagement("Aurora")
    m = work.create_milestone("ship", project="Aurora")
    with pytest.raises(ValueError, match="conclusion"):
        engagements.update_engagement(e["id"], name="Borealis", status="closed")
    row = db.query_one("SELECT project FROM milestones WHERE id = ?", (m["id"],))
    assert row["project"] == "Aurora"  # rename never landed; the label must not move


# ---- review: authorship stays with the proposer -----------------------------------


def test_approval_keeps_proposer_as_author(fresh_db):
    from app.services import review, work

    p = review.propose_change("task", "create", {"title": "agent's own idea"}, actor="agent-x")
    review.approve_change(p["id"], actor="hana")
    task = work.list_tasks()[0]
    assert task["created_by"] == "agent-x"  # not the approving human
    assert task["origin"] == "agent_verified"


# ---- briefing due_soon scoping ----------------------------------------------------


def test_due_soon_excludes_other_peoples_tasks(fresh_db):
    from app.services import briefing, work

    today = _utc_today().isoformat()
    work.create_task("mine", assignee="ava", due_date=today)
    work.create_task("unassigned", due_date=today)
    work.create_task("bobs", assignee="bob", due_date=today)

    titles = {t["title"] for t in briefing.my_day("ava")["your_work"]["due_soon"]}
    assert titles == {"mine", "unassigned"}


# ---- notes route: bounds + keyword filter -----------------------------------------


def test_notes_keyword_filter_and_patch_bounds(client):
    n = client.post("/api/notes", json={"topic": "infra", "content": "postgres vacuum tips"}).json()
    client.post("/api/notes", json={"topic": "team", "content": "friday demo schedule"})

    hits = client.get("/api/notes", params={"q": "vacuum"}).json()
    assert [h["id"] for h in hits] == [n["id"]]
    assert len(client.get("/api/notes").json()) == 2

    over = client.patch(f"/api/notes/{n['id']}", json={"content": "x" * 20_001})
    assert over.status_code == 422


# ---- users merge backfill ---------------------------------------------------------


def test_merge_backfills_profile_fields_target_never_set(fresh_db):
    from app.services import users

    users.ensure_user("mira")  # target: no theme, no growth interests
    users.ensure_user("Mira K")
    users.set_theme("Mira K", '{"pack":"atelier"}')
    users.set_growth_interests("Mira K", "rust, distributed systems")

    out = users.rename_user("Mira K", "mira")
    assert out["merged"] is True
    assert users.get_theme("mira") == '{"pack":"atelier"}'
    row = users.list_users()
    me = next(u for u in row if u["name"] == "mira")
    assert me["growth_interests"] == "rust, distributed systems"


# ---- agent-tool parity ------------------------------------------------------------


def _approve_latest(client):
    from app.services.api_keys import create_key

    headers = {"Authorization": f"Bearer {create_key('tester', 'p')['key']}"}
    pending = client.get("/api/review?status=pending").json()
    assert pending, "expected a pending proposal"
    r = client.post(f"/api/review/{pending[0]['id']}/approve", json={}, headers=headers)
    assert r.json()["status"] == "approved"
    return pending[0]


def test_agent_note_edit_flows_through_review(client, fresh_db, monkeypatch):
    from app import config
    from app.services import collab
    from app.tools.collab import edit_note

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    n = collab.save_note(topic="conv", content="original", author="ava", actor="ava")
    out = edit_note(note_id=n["id"], content="corrected by the bench")
    assert "pending" in out or "queued" in out  # proposal, not a direct write
    assert (
        fresh_db.query_one("SELECT content FROM notes WHERE id = ?", (n["id"],))["content"]
        == "original"
    )
    change = _approve_latest(client)
    assert change["entity"] == "note_edit"
    assert (
        fresh_db.query_one("SELECT content FROM notes WHERE id = ?", (n["id"],))["content"]
        == "corrected by the bench"
    )


def test_agent_forget_memory_gated_and_applies(client, fresh_db, monkeypatch):
    from app import config
    from app.services import memory
    from app.tools.memory import forget_memory

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    m = memory.remember("the cluster password rotates on Fridays", topic="ops", actor="agent")
    out = forget_memory(memory_id=m["id"])
    assert "pending" in out
    assert fresh_db.query_one("SELECT id FROM memories WHERE id = ?", (m["id"],))
    _approve_latest(client)
    assert not fresh_db.query_one("SELECT id FROM memories WHERE id = ?", (m["id"],))


def test_agent_edit_respects_forbidden_authority(fresh_db, monkeypatch):
    from app import config
    from app.services import blockers, delegation
    from app.tools.platform import edit_blocker

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    b = blockers.raise_blocker(title="typo'd", owner="ava", actor="ava")
    delegation.set_authority("agent", "blocker_edit", "forbidden", actor="tester")
    out = edit_blocker(blocker_id=b["id"], title="nope")
    assert "forbidden" in out
    assert (
        fresh_db.query_one("SELECT title FROM blockers WHERE id = ?", (b["id"],))["title"]
        == "typo'd"
    )


def test_agent_history_guard_survives_approval(client, fresh_db, monkeypatch):
    """Approving an edit of a since-settled record must fail the apply and
    reset the proposal — never falsify history."""
    from app import config
    from app.services import commitments
    from app.tools.portfolio import edit_commitment

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    c = commitments.add_commitment("shipp it", actor="ava")
    out = edit_commitment(commitment_id=c["id"], promise="ship it")
    assert "pending" in out
    commitments.update_commitment(c["id"], "kept", actor="ava")  # settles first
    from app.services.api_keys import create_key

    headers = {"Authorization": f"Bearer {create_key('tester', 'p2')['key']}"}
    pending = client.get("/api/review?status=pending").json()
    resp = client.post(f"/api/review/{pending[0]['id']}/approve", json={}, headers=headers)
    assert resp.status_code == 400  # apply failed loudly
    row = fresh_db.query_one(
        "SELECT status, review_note FROM pending_changes WHERE id = ?", (pending[0]["id"],)
    )
    assert row["status"] == "pending" and "apply failed" in row["review_note"]
    assert (
        fresh_db.query_one("SELECT promise FROM commitments WHERE id = ?", (c["id"],))["promise"]
        == "shipp it"
    )
