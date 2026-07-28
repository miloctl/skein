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
    from app.services import engagements, portfolio, users

    users.ensure_user("alice")
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
    from app.services import engagements, users

    users.ensure_user("bo")
    users.ensure_user("cy")
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


def test_destructive_verbs_always_propose_even_with_review_off(client, fresh_db, monkeypatch):
    """delete_note / forget_memory must NEVER hard-delete directly from the
    agent path — ALWAYS_REVIEW holds even when the review flag is off."""
    from app import config
    from app.services import collab, memory
    from app.tools.collab import delete_note as delete_note_tool
    from app.tools.memory import forget_memory

    monkeypatch.setattr(config, "AGENT_REVIEW", False)
    n = collab.save_note(topic="keep", content="load-bearing", author="ava", actor="ava")
    m = memory.remember("standing context", topic="ctx", actor="agent")
    out_n = delete_note_tool(note_id=n["id"])
    out_m = forget_memory(memory_id=m["id"])
    assert "pending" in out_n and "pending" in out_m
    assert fresh_db.query_one("SELECT id FROM notes WHERE id = ?", (n["id"],))
    assert fresh_db.query_one("SELECT id FROM memories WHERE id = ?", (m["id"],))
    # and the proposals show the reviewer WHAT would be destroyed
    pending = client.get("/api/review?status=pending").json()
    summaries = " | ".join(p["summary"] for p in pending)
    assert "load-bearing" in summaries and "standing context" in summaries


def test_destructive_diff_shows_doomed_content(client, fresh_db, monkeypatch):
    from app import config
    from app.services import collab
    from app.tools.collab import delete_note as delete_note_tool

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    n = collab.save_note(
        topic="secrets", content="the whole content body", author="ava", actor="ava"
    )
    delete_note_tool(note_id=n["id"])
    pending = client.get("/api/review?status=pending").json()
    d = client.get(f"/api/review/{pending[0]['id']}/diff").json()
    assert d["diff"]["current"]["content"] == "the whole content body"


def test_edit_diff_shows_current_wording(client, fresh_db, monkeypatch):
    from app import config
    from app.services import commitments
    from app.tools.portfolio import edit_commitment

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    c = commitments.add_commitment("shipp the thing to ops", actor="ava")
    edit_commitment(commitment_id=c["id"], promise="ship the thing to ops")
    pending = client.get("/api/review?status=pending").json()
    d = client.get(f"/api/review/{pending[0]['id']}/diff").json()
    assert d["diff"]["current"]["promise"] == "shipp the thing to ops"
    assert d["diff"]["proposed"]["promise"] == "ship the thing to ops"


def test_edit_tools_refuse_empty_and_invalid_before_proposing(fresh_db, monkeypatch):
    from app import config
    from app.services import commitments, engagements
    from app.tools.collab import edit_note
    from app.tools.platform import update_engagement
    from app.tools.portfolio import mark_commitment

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    assert "nothing to change" in edit_note(note_id=1)
    c = commitments.add_commitment("p", actor="ava")
    assert "kept, missed, or withdrawn" in mark_commitment(commitment_id=c["id"], status="done")
    e = engagements.create_engagement("Doomcheck", actor="ava")
    out = update_engagement(engagement_id=e["id"], status="closed")
    assert "conclusion" in out and "pending" not in out


# ---- A1/A2/P2/C1 ------------------------------------------------------------------


def _strong(client, name="tester"):
    from app.services.api_keys import create_key

    return {"Authorization": f"Bearer {create_key(name, 'r')['key']}"}


def test_delegation_work_loop_end_to_end(client, fresh_db, monkeypatch):
    from app import config
    from app.services import delegation, users, work

    monkeypatch.setattr(config, "AGENT_REVIEW", False)  # loop must gate regardless
    users.ensure_user("mira")
    t = work.create_task(title="build the probe", actor="mira")
    delegation.delegate_task(t["id"], "scout", "mira", actor="mira")
    delegation.claim_task(t["id"], actor="scout")
    assert fresh_db.query_one("SELECT status FROM tasks WHERE id = ?", (t["id"],))["status"] == (
        "in_progress"
    )
    delegation.report_progress(t["id"], "probe scaffolded, tests next", actor="scout")
    assert client.get(f"/api/tasks/{t['id']}/worklog").json()[0]["note"].startswith("probe")
    out = delegation.submit_completion(t["id"], "probe built and green", actor="scout")
    # still not done — the sponsor's verdict is the acceptance
    assert fresh_db.query_one("SELECT status FROM tasks WHERE id = ?", (t["id"],))["status"] == (
        "in_progress"
    )
    r = client.post(
        f"/api/review/{out['proposal_id']}/approve", json={}, headers=_strong(client, "mira")
    )
    assert r.json()["status"] == "approved"
    assert fresh_db.query_one("SELECT status FROM tasks WHERE id = ?", (t["id"],))["status"] == (
        "done"
    )
    notes = [w["note"] for w in delegation.list_worklog(t["id"])]
    assert any(n.startswith("[accepted]") for n in notes)


def test_claim_requires_the_delegated_agent(fresh_db):
    from app.services import delegation, users, work

    users.ensure_user("mira")
    t = work.create_task(title="x", actor="mira")
    delegation.delegate_task(t["id"], "scout", "mira", actor="mira")
    try:
        delegation.claim_task(t["id"], actor="other-agent")
        raise AssertionError("claim by a non-delegate succeeded")
    except ValueError:
        pass


def test_authority_review_files_promotion_and_applies(client, fresh_db, monkeypatch):
    from app.services import delegation, review, users

    # 5 strong-verdict approvals for scribe on note -> promotion proposal
    users.ensure_user("scribe", kind="agent")
    headers = _strong(client)
    for i in range(5):
        p = review.propose_change(
            "note", "create", {"topic": f"t{i}", "content": "c"}, actor="scribe"
        )
        client.post(f"/api/review/{p['id']}/approve", json={}, headers=headers)
    out = delegation.review_authority(actor="scheduler")
    assert out["filed"] == 1
    # idempotent while pending
    assert delegation.review_authority(actor="scheduler")["filed"] == 0
    pending = client.get("/api/review?status=pending").json()
    auth = next(p for p in pending if p["entity"] == "authority")
    client.post(f"/api/review/{auth['id']}/approve", json={}, headers=headers)
    assert delegation.authority_level("scribe", "note") == "notify"


def test_absences_shape_capacity_and_week_draft(client, fresh_db):
    from datetime import date, timedelta

    from app.services import absences, engagements, users, weekly, work

    users.ensure_user("dana")
    e = engagements.create_engagement("Staffed", actor="mira")
    engagements.allocate("dana", e["id"], percent=80, actor="mira")
    today = date.today()
    # anchor to the week's Monday: run on a Friday, today-1..today+7 covers
    # too few weekdays of THIS week to trip the >= 3 skip threshold
    monday_anchor = today - timedelta(days=today.weekday())
    absences.add_absence(
        "dana",
        monday_anchor.isoformat(),
        (monday_anchor + timedelta(days=6)).isoformat(),
        actor="mira",
    )
    cap = client.get("/api/capacity").json()
    row = next(c for c in cap if c["person"] == "dana")
    assert row["away"] == "pto"
    # week draft skips someone away most of the week
    work.create_task(title="never plan me", assignee="dana", actor="mira")
    monday = today - timedelta(days=today.weekday())
    week = f"{monday.isocalendar().year}-W{monday.isocalendar().week:02d}"
    draft = weekly.draft_plan(week)
    assert all(i["assignee"] != "dana" for i in draft["items"])
    assert any(s["person"] == "dana" for s in draft["skipped_absent"])


def test_absence_validation_and_delete(client, fresh_db):
    from app.services import users

    users.ensure_user("dana")
    r = client.post(
        "/api/absences",
        json={"person": "dana", "starts_on": "2026-08-10", "ends_on": "2026-08-01"},
    )
    assert r.status_code == 400
    ok = client.post(
        "/api/absences",
        json={"person": "dana", "starts_on": "2026-08-01", "ends_on": "2026-08-10"},
    ).json()
    assert client.delete(f"/api/absences/{ok['id']}").json()["deleted"] is True
    assert client.delete(f"/api/absences/{ok['id']}").status_code == 404


def test_week_rituals_produce_packets_and_notify(client, fresh_db):
    from app.services import commitments, rituals, users

    users.ensure_user("mira")
    commitments.add_commitment("demo to ops", due_date="2020-01-01", actor="mira")
    close = rituals.week_close(actor="mira", force=True)
    assert close["items"] >= 1 and "Promises due or overdue" in close["markdown"]
    opened = rituals.week_open(actor="mira", force=True)
    assert opened["briefed"] >= 1 and "mira" in opened["markdown"]
    # personal notification landed for the obligation owner
    notes = client.get("/api/notifications", headers={"X-User": "mira"}).json()
    assert any("Your week:" in n["message"] for n in notes)


def _delegated_task(fresh_db, title="probe"):
    from app.services import delegation, users, work

    users.ensure_user("mira")
    users.ensure_user("scout", kind="agent")
    t = work.create_task(title=title, actor="mira")
    delegation.delegate_task(t["id"], "scout", "mira", actor="mira")
    return t["id"]


def test_worklog_writes_bound_to_the_loop(fresh_db):
    from app.services import delegation, users

    tid = _delegated_task(fresh_db)
    users.ensure_user("intruder", kind="agent")
    with pytest.raises(ValueError, match="delegate or sponsor"):
        delegation.report_progress(tid, "[accepted] looks done to me", actor="intruder")
    with pytest.raises(ValueError, match="2000"):
        delegation.report_progress(tid, "x" * 2001, actor="scout")
    # the sponsor may annotate; a done task's worklog is frozen
    delegation.report_progress(tid, "sponsor context", actor="mira")
    delegation.accept_completion(tid, "fine", actor="scout")
    with pytest.raises(ValueError, match="history"):
        delegation.report_progress(tid, "late addendum", actor="scout")


def test_agent_cannot_self_complete_delegated_task(client, fresh_db):
    from app.services import work

    tid = _delegated_task(fresh_db)
    with pytest.raises(ValueError, match="sponsor's verdict"):
        work.update_task(tid, status="done", actor="scout", origin="agent")
    # a human closing it directly stays allowed (sponsor override)
    r = client.patch(f"/api/tasks/{tid}", json={"status": "done"})
    assert r.status_code == 200


def test_submit_completion_dedupes_pending(fresh_db):
    from app.services import delegation

    tid = _delegated_task(fresh_db)
    delegation.claim_task(tid, actor="scout")
    delegation.submit_completion(tid, "round one", actor="scout")
    with pytest.raises(ValueError, match="already awaits acceptance"):
        delegation.submit_completion(tid, "round one again", actor="scout")


def test_rejection_keeps_task_open_and_feeds_trust(client, fresh_db):
    from app.services import delegation

    tid = _delegated_task(fresh_db)
    delegation.claim_task(tid, actor="scout")
    out = delegation.submit_completion(tid, "done, trust me", actor="scout")
    r = client.post(
        f"/api/review/{out['proposal_id']}/reject",
        json={"note": "tests are red"},
        headers=_strong(client, "mira"),
    )
    assert r.json()["status"] == "rejected"
    assert fresh_db.query_one("SELECT status FROM tasks WHERE id = ?", (tid,))["status"] == (
        "in_progress"
    )
    row = next(
        s
        for s in delegation.trust_scores()
        if s["agent"] == "scout" and s["entity"] == "task_completion"
    )
    assert row["rejection_streak"] == 1
    # resubmission after the fix is open
    delegation.submit_completion(tid, "fixed and green", actor="scout")


def test_authority_verdicts_need_strong_human_identity(client, fresh_db):
    from app.services import review, users

    users.ensure_user("scribe", kind="agent")
    p = review.propose_change(
        "authority",
        "create",
        {"agent": "scribe", "entity": "note", "level": "notify"},
        actor="scheduler",
    )
    weak = client.post(f"/api/review/{p['id']}/approve", json={})
    assert weak.status_code == 400 and "strong identity" in weak.json()["detail"]
    # the dep now refuses the weak agent header outright (403) — the service
    # guard behind it ("judged by humans") stays as defense in depth
    as_agent = client.post(f"/api/review/{p['id']}/approve", json={}, headers={"X-User": "scribe"})
    assert as_agent.status_code == 403 and "agent identity" in as_agent.json()["detail"]
    ok = client.post(f"/api/review/{p['id']}/approve", json={}, headers=_strong(client))
    assert ok.json()["status"] == "approved"


def test_stale_authority_proposal_never_lifts_forbidden(client, fresh_db):
    from app.services import delegation, review, users

    users.ensure_user("scribe", kind="agent")
    p = review.propose_change(
        "authority",
        "create",
        {"agent": "scribe", "entity": "note", "level": "notify", "expected_current": "review"},
        actor="scheduler",
    )
    delegation.set_authority("scribe", "note", "forbidden", actor="mira")
    r = client.post(f"/api/review/{p['id']}/approve", json={}, headers=_strong(client))
    assert r.status_code == 400 and "stale" in r.json()["detail"]
    assert delegation.authority_level("scribe", "note") == "forbidden"


def test_authority_demotion_end_to_end(client, fresh_db):
    from app.services import delegation, review, users

    users.ensure_user("scribe", kind="agent")
    delegation.set_authority("scribe", "note", "notify", actor="mira")
    headers = _strong(client)
    for i in range(3):
        p = review.propose_change(
            "note", "create", {"topic": f"bad{i}", "content": "c"}, actor="scribe"
        )
        client.post(f"/api/review/{p['id']}/reject", json={"note": "off"}, headers=headers)
    out = delegation.review_authority(actor="scheduler")
    assert out["filed"] == 1
    pending = client.get("/api/review?status=pending").json()
    auth = next(c for c in pending if c["entity"] == "authority")
    assert "notify -> review" in auth["summary"]
    client.post(f"/api/review/{auth['id']}/approve", json={}, headers=headers)
    assert delegation.authority_level("scribe", "note") == "review"


def test_authority_review_skips_humans_and_meta_entities(client, fresh_db):
    from app.services import delegation, review

    headers = _strong(client)
    for i in range(5):  # human proposer with a perfect streak: no proposal
        p = review.propose_change(
            "note", "create", {"topic": f"h{i}", "content": "c"}, actor="tester"
        )
        client.post(f"/api/review/{p['id']}/approve", json={}, headers=headers)
    assert delegation.review_authority(actor="scheduler")["filed"] == 0


def test_manual_ritual_run_consumes_the_weekly_claim(fresh_db):
    from app.services import rituals, users

    users.ensure_user("mira")
    manual = rituals.week_open(actor="mira", force=True)
    assert "markdown" in manual
    scheduled = rituals.week_open(actor="scheduler")
    assert scheduled.get("skipped") == "already ran this week"


def test_agent_cannot_delegate_to_itself(fresh_db):
    from app.services import users, work

    users.ensure_user("mira")
    users.ensure_user("scout", kind="agent")
    t = work.create_task(title="mine now", actor="mira")
    from app.services import delegation

    with pytest.raises(ValueError, match="itself"):
        delegation.delegate_task(t["id"], "scout", "mira", actor="scout", origin="agent")


def test_acceptance_verdicts_bind_to_the_sponsor(client, fresh_db):
    from app.services import delegation

    tid = _delegated_task(fresh_db)
    delegation.claim_task(tid, actor="scout")
    pid = delegation.submit_completion(tid, "ready", actor="scout")["proposal_id"]
    row = next(p for p in client.get("/api/review?status=pending").json() if p["id"] == pid)
    assert row["sponsor"] == "mira"
    # a non-sponsor without a reason is refused
    bare = client.post(f"/api/review/{pid}/approve", json={}, headers=_strong(client))
    assert bare.status_code == 400 and "sponsored by mira" in bare.json()["detail"]
    # with a reason it lands — marked override, reason on the record
    ok = client.post(
        f"/api/review/{pid}/approve",
        json={"note": "mira is on PTO and asked me to close it"},
        headers=_strong(client),
    )
    assert ok.json()["status"] == "approved"
    ch = fresh_db.query_one("SELECT reviewed_override FROM pending_changes WHERE id = ?", (pid,))
    assert ch["reviewed_override"] == 1
    acts = fresh_db.query("SELECT detail FROM activity WHERE action = 'approve_change'")
    assert any("accepted for mira" in a["detail"] for a in acts)
    # override verdicts are provenance, not trust — the streak ignores them
    score = next(
        s
        for s in delegation.trust_scores()
        if s["agent"] == "scout" and s["entity"] == "task_completion"
    )
    assert score["recent_streak"] == 0


def test_sponsor_verdict_needs_no_note_and_feeds_trust(client, fresh_db):
    from app.services import delegation

    tid = _delegated_task(fresh_db)
    delegation.claim_task(tid, actor="scout")
    pid = delegation.submit_completion(tid, "ready", actor="scout")["proposal_id"]
    r = client.post(f"/api/review/{pid}/approve", json={}, headers=_strong(client, "mira"))
    assert r.json()["status"] == "approved"
    ch = fresh_db.query_one(
        "SELECT reviewed_override, reviewed_strong FROM pending_changes WHERE id = ?", (pid,)
    )
    assert ch["reviewed_override"] == 0 and ch["reviewed_strong"] == 1
    score = next(
        s
        for s in delegation.trust_scores()
        if s["agent"] == "scout" and s["entity"] == "task_completion"
    )
    assert score["recent_streak"] == 1


def test_non_sponsor_reject_needs_a_reason_too(client, fresh_db):
    from app.services import delegation

    tid = _delegated_task(fresh_db)
    delegation.claim_task(tid, actor="scout")
    pid = delegation.submit_completion(tid, "ready", actor="scout")["proposal_id"]
    bare = client.post(f"/api/review/{pid}/reject", json={}, headers=_strong(client))
    assert bare.status_code == 400 and "sponsored by mira" in bare.json()["detail"]
    ok = client.post(
        f"/api/review/{pid}/reject",
        json={"note": "covering for mira — the output is wrong"},
        headers=_strong(client),
    )
    assert ok.json()["status"] == "rejected"
    # an override rejection must not push the agent toward demotion
    score = next(
        s
        for s in delegation.trust_scores()
        if s["agent"] == "scout" and s["entity"] == "task_completion"
    )
    assert score["rejection_streak"] == 0


def test_batch_approve_skips_sponsor_bound_rows_with_a_clear_error(client, fresh_db):
    from app.services import delegation, review

    tid = _delegated_task(fresh_db)
    delegation.claim_task(tid, actor="scout")
    bound = delegation.submit_completion(tid, "ready", actor="scout")["proposal_id"]
    plain = review.propose_change("note", "create", {"topic": "t", "content": "c"}, actor="scout")[
        "id"
    ]
    r = client.post(
        "/api/review/approve-batch",
        json={"ids": [bound, plain]},
        headers=_strong(client),
    ).json()
    by_id = {x["id"]: x for x in r["results"]}
    assert by_id[plain]["status"] == "approved"
    assert by_id[bound]["status"] == "error" and "sponsored by mira" in by_id[bound]["detail"]
    assert fresh_db.query_one("SELECT status FROM tasks WHERE id = ?", (tid,))["status"] == (
        "in_progress"
    )
    assert (
        fresh_db.query_one("SELECT status FROM pending_changes WHERE id = ?", (bound,))["status"]
        == "pending"
    )


def test_verdict_follows_the_sponsor_after_re_delegation(client, fresh_db):
    from app.services import delegation, users

    tid = _delegated_task(fresh_db)
    delegation.claim_task(tid, actor="scout")
    pid = delegation.submit_completion(tid, "ready", actor="scout")["proposal_id"]
    users.ensure_user("jon")
    delegation.delegate_task(tid, "scout", "jon", actor="jon")
    # the OLD sponsor is now an override like anyone else
    bare = client.post(f"/api/review/{pid}/approve", json={}, headers=_strong(client, "mira"))
    assert bare.status_code == 400 and "sponsored by jon" in bare.json()["detail"]
    ok = client.post(
        f"/api/review/{pid}/approve",
        json={"note": "I commissioned this before the handover"},
        headers=_strong(client, "mira"),
    )
    assert ok.json()["status"] == "approved"
    assert (
        fresh_db.query_one("SELECT reviewed_override FROM pending_changes WHERE id = ?", (pid,))[
            "reviewed_override"
        ]
        == 1
    )


def test_orphaned_acceptance_requires_a_reason_and_feeds_no_streak(client, fresh_db):
    from app.services import delegation, work

    tid = _delegated_task(fresh_db)
    delegation.claim_task(tid, actor="scout")
    pid = delegation.submit_completion(tid, "ready", actor="scout")["proposal_id"]
    # reassigning to a human clears the delegation AND the sponsor
    work.update_task(tid, assignee="tester", actor="mira")
    bare = client.post(f"/api/review/{pid}/reject", json={}, headers=_strong(client))
    assert bare.status_code == 400 and "orphaned" in bare.json()["detail"]
    ok = client.post(
        f"/api/review/{pid}/reject",
        json={"note": "task was reassigned — closing the stale submission"},
        headers=_strong(client),
    )
    assert ok.json()["status"] == "rejected"
    score = next(
        s
        for s in delegation.trust_scores()
        if s["agent"] == "scout" and s["entity"] == "task_completion"
    )
    assert score["rejection_streak"] == 0


def test_override_verdicts_are_invisible_to_streaks_by_design(client, fresh_db):
    """Overrides are provenance, not trust: they neither count toward nor
    interrupt a streak. A promotion needs 5 SPONSOR approvals; a non-sponsor
    rejection in the middle doesn't reset that count (and symmetrically a
    buddy's override approval can't shield a demotion streak)."""
    from app.services import delegation, users, work

    users.ensure_user("mira")
    users.ensure_user("scout", kind="agent")
    verdicts = []
    for i in range(3):
        t = work.create_task(title=f"loop {i}", actor="mira")
        delegation.delegate_task(t["id"], "scout", "mira", actor="mira")
        delegation.claim_task(t["id"], actor="scout")
        verdicts.append(delegation.submit_completion(t["id"], "done", actor="scout"))
    a, b, c = (v["proposal_id"] for v in verdicts)
    client.post(f"/api/review/{a}/approve", json={}, headers=_strong(client, "mira"))
    # a non-sponsor override rejection lands between two sponsor approvals
    client.post(
        f"/api/review/{b}/reject",
        json={"note": "covering — looked off to me"},
        headers=_strong(client),
    )
    client.post(f"/api/review/{c}/approve", json={}, headers=_strong(client, "mira"))
    score = next(
        s
        for s in delegation.trust_scores()
        if s["agent"] == "scout" and s["entity"] == "task_completion"
    )
    assert score["recent_streak"] == 2  # both sponsor approvals, unbroken
    assert score["rejection_streak"] == 0


# ---- fresh-eyes audit №2, phase 1 -------------------------------------------------


def test_agent_remember_is_gated_and_carries_provenance(client, fresh_db, monkeypatch):
    import json as j

    from app import config
    from app.agents.identity import reset_agent_identity, set_agent_identity
    from app.services import users
    from app.tools.memory import remember as remember_tool

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    users.ensure_user("scribe", kind="agent")
    token = set_agent_identity("scribe")
    try:
        out = j.loads(remember_tool(content="the deploy window is Fridays", topic="ops"))
        assert out.get("note") == "queued for human review"
        pid = out["id"]
    finally:
        reset_agent_identity(token)
    r = client.post(f"/api/review/{pid}/approve", json={}, headers=_strong(client))
    row = fresh_db.query_one(
        "SELECT origin, created_by FROM memories WHERE id = ?", (r.json()["result"]["id"],)
    )
    assert row["origin"] == "agent_verified" and row["created_by"] == "scribe"


def test_agent_remember_respects_forbidden_and_caps(fresh_db):
    import json as j

    from app.agents.identity import reset_agent_identity, set_agent_identity
    from app.services import delegation, memory, users
    from app.tools.memory import remember as remember_tool

    users.ensure_user("scribe", kind="agent")
    users.ensure_user("mira")
    delegation.set_authority("scribe", "memory", "forbidden", actor="mira")
    token = set_agent_identity("scribe")
    try:
        out = j.loads(remember_tool(content="steering text"))
        assert "forbidden" in out["error"]
    finally:
        reset_agent_identity(token)
    with pytest.raises(ValueError, match="2000"):
        memory.remember("x" * 2001, actor="mira")


def test_weak_header_cannot_claim_agent_identity(client, fresh_db):
    from app.services import users

    users.ensure_user("scout", kind="agent")
    # /notifications carries CurrentUser on GET; /tasks GET is identity-free
    r = client.get("/api/notifications", headers={"X-User": "scout"})
    assert r.status_code == 403 and "agent identity" in r.json()["detail"]
    w = client.post("/api/tasks", json={"title": "as scout"}, headers={"X-User": "scout"})
    assert w.status_code == 403


def test_reads_do_not_mint_roster_rows(client, fresh_db):
    client.get("/api/notifications", headers={"X-User": "drive-by-reader"})
    assert not fresh_db.query_one("SELECT id FROM users WHERE name = ?", ("drive-by-reader",))
    client.post("/api/tasks", json={"title": "t"}, headers={"X-User": "drive-by-writer"})
    assert fresh_db.query_one("SELECT id FROM users WHERE name = ?", ("drive-by-writer",))


def test_dispositioned_intake_cannot_be_rescored(client, fresh_db):
    from app.services import intake

    req = intake.submit_request("old idea", requester="mira", actor="mira")
    intake.score_request(req["id"], 2, 2, 2, 2, actor="mira")
    intake.disposition_request(req["id"], "declined", "not now", actor="mira")
    r = client.post(
        f"/api/intake/{req['id']}/score",
        json={"reach": 5, "impact": 5, "confidence": 5, "effort": 1},
    )
    assert r.status_code == 400 and "stay put" in r.json()["detail"]
    row = fresh_db.query_one("SELECT status FROM intake_requests WHERE id = ?", (req["id"],))
    assert row["status"] == "declined"


def test_dates_are_validated_and_ics_survives_bad_legacy_rows(client, fresh_db):
    from app.services import commitments, engagements, users, work

    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        work.create_task(title="t", due_date="soon")
    with pytest.raises(ValueError, match="real date"):
        work.create_milestone("m", due_date="2026-02-31")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        commitments.add_commitment("p", due_date="07/30/2026")
    users.ensure_user("mira")
    e = engagements.create_engagement("Dated")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        engagements.allocate("mira", e["id"], 50, starts_on="tomorrow")
    # clear sentinel still passes
    t = work.create_task(title="ok", due_date="2026-08-01")
    work.update_task(t["id"], due_date="-", actor="mira")
    # a bad date already in the DB (pre-validation rows) must not sink the feed
    fresh_db.execute(
        "INSERT INTO commitments (promise, due_date, status, audience, created_by,"
        " created_at, updated_at) VALUES ('legacy', 'soon', 'open', 'external', 'mira', ?, ?)",
        (fresh_db.now(), fresh_db.now()),
    )
    feed = client.get("/api/calendar.ics").text
    assert "soon" not in feed and feed.rstrip().endswith("END:VCALENDAR")


def test_export_covers_the_newer_tables(fresh_db):
    from app.services import absences, admin, users

    users.ensure_user("mira")
    absences.add_absence("mira", "2026-08-03", "2026-08-04", actor="mira")
    out = admin.export()
    assert out["tables"]["absences"] == 1
    for table in ("task_worklog", "finding_dispositions", "app_settings", "job_outcomes"):
        assert table in out["tables"]


def test_mcp_remember_routes_through_the_gate(client, fresh_db, monkeypatch):
    import json as j

    from app import config, mcp_server
    from app.services import users

    users.ensure_user("mcp-agent", kind="agent")
    monkeypatch.setattr(mcp_server, "ACTOR", "mcp-agent")
    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    out = j.loads(mcp_server.remember("the deploy window is Fridays", topic="ops"))
    assert out.get("note") == "queued for human review"
    pending = client.get("/api/review?status=pending").json()
    assert any(p["entity"] == "memory" and p["proposed_by"] == "mcp-agent" for p in pending)
    big = j.loads(mcp_server.remember("x" * 2001))
    assert "2000" in big["error"]


def test_memory_rate_cap_and_human_provenance(fresh_db):
    from app.services import memory

    for i in range(10):
        memory.remember(f"fact {i}", actor="mira")
    with pytest.raises(ValueError, match="capped at 10/minute"):
        memory.remember("fact 11", actor="mira")
    row = fresh_db.query_one("SELECT origin, created_by FROM memories WHERE id = 1")
    assert row["origin"] == "human" and row["created_by"] == "mira"


def test_agent_owned_key_is_refused_on_rest(client, fresh_db):
    from app.services import users
    from app.services.api_keys import create_key

    users.ensure_user("scout", kind="agent")
    key = create_key("scout", "probe")["key"]
    r = client.get("/api/notifications", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 403 and "gated tool surface" in r.json()["detail"]


def test_reserved_agent_identities_minted_at_startup(client, fresh_db):
    # the client fixture runs the lifespan — 'agent' (default chat identity)
    # must exist as kind=agent so a weak header can never shadow it
    row = fresh_db.query_one("SELECT kind FROM users WHERE name = 'agent'")
    assert row and row["kind"] == "agent"
    assert (
        client.post("/api/tasks", json={"title": "t"}, headers={"X-User": "agent"}).status_code
        == 403
    )


def test_clear_sentinel_rejected_on_create_paths(fresh_db):
    from app.services import commitments, engagements, users, work

    with pytest.raises(ValueError, match="only clears"):
        work.create_task(title="t", due_date="-")
    with pytest.raises(ValueError, match="only clears"):
        commitments.add_commitment("p", due_date="-")
    users.ensure_user("mira")
    e = engagements.create_engagement("SentinelCheck")
    with pytest.raises(ValueError, match="only clears"):
        engagements.allocate("mira", e["id"], 50, starts_on="-")


def test_oversized_memory_fails_on_the_agent_not_the_reviewer(fresh_db, monkeypatch):
    import json as j

    from app import config
    from app.agents.identity import reset_agent_identity, set_agent_identity
    from app.services import users
    from app.tools.memory import remember as remember_tool

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    users.ensure_user("scribe", kind="agent")
    token = set_agent_identity("scribe")
    try:
        out = j.loads(remember_tool(content="x" * 2001))
    finally:
        reset_agent_identity(token)
    assert "2000" in out["error"]
    assert not fresh_db.query_one(
        "SELECT id FROM pending_changes WHERE entity = 'memory' AND status = 'pending'"
    )


# ---- fresh-eyes audit №2, phase 2 -------------------------------------------------


def test_event_cancel_is_always_a_proposal(client, fresh_db, monkeypatch):
    import json as j

    from app import config
    from app.agents.identity import reset_agent_identity, set_agent_identity
    from app.services import schedule, users
    from app.tools.schedule import cancel_event as cancel_tool

    monkeypatch.setattr(config, "AGENT_REVIEW", False)  # ALWAYS_REVIEW must not care
    users.ensure_user("scout", kind="agent")
    e = schedule.schedule_event("standup sync", "2026-08-10T10:00")
    token = set_agent_identity("scout")
    try:
        out = j.loads(cancel_tool(event_id=e["id"]))
    finally:
        reset_agent_identity(token)
    assert out.get("note") == "queued for human review"
    assert fresh_db.query_one("SELECT id FROM events WHERE id = ?", (e["id"],))
    # the reviewer sees what would be destroyed
    diff = client.get(f"/api/review/{out['id']}/diff").json()["diff"]
    assert diff["current"]["title"] == "standup sync"
    r = client.post(f"/api/review/{out['id']}/approve", json={}, headers=_strong(client))
    assert r.json()["status"] == "approved"
    assert not fresh_db.query_one("SELECT id FROM events WHERE id = ?", (e["id"],))


def test_agent_absence_is_always_a_proposal(fresh_db, monkeypatch):
    import json as j

    from app import config
    from app.agents.identity import reset_agent_identity, set_agent_identity
    from app.services import users
    from app.tools.portfolio import add_absence as absence_tool

    monkeypatch.setattr(config, "AGENT_REVIEW", False)
    users.ensure_user("scout", kind="agent")
    token = set_agent_identity("scout")
    try:
        out = j.loads(absence_tool(person="mira", starts_on="2026-08-10", ends_on="2026-08-12"))
    finally:
        reset_agent_identity(token)
    assert out.get("note") == "queued for human review"
    assert not fresh_db.query_one("SELECT id FROM absences")


def test_rename_honors_identity_walls(fresh_db):
    from app.services import personas, users

    users.ensure_user("bob")
    users.ensure_user("scout", kind="agent")
    with pytest.raises(ValueError, match="human/agent boundary"):
        users.rename_user("bob", "scout", actor="mira")
    slug = personas.list_personas()[0]["slug"]
    with pytest.raises(ValueError, match="reserved for a bench persona"):
        users.rename_user("bob", slug, actor="mira")


def test_create_bodies_are_capped(client, fresh_db):
    r = client.post("/api/notes", json={"topic": "big", "content": "x" * 50_000})
    assert r.status_code == 422
    r = client.post("/api/chat", json={"message": "x" * 50_000})
    assert r.status_code == 422


def test_empty_update_proposal_bounced_on_the_agent(fresh_db, monkeypatch):
    import json as j

    from app import config
    from app.agents.identity import reset_agent_identity, set_agent_identity
    from app.services import users, work
    from app.tools.work import update_task as update_tool

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    users.ensure_user("scout", kind="agent")
    t = work.create_task(title="t", actor="mira")
    token = set_agent_identity("scout")
    try:
        out = j.loads(update_tool(task_id=t["id"]))
    finally:
        reset_agent_identity(token)
    assert "nothing to change" in out["error"]
    assert not fresh_db.query_one("SELECT id FROM pending_changes")


def test_write_rate_cap_enforced_on_create_routes(client, fresh_db):
    for i in range(30):
        assert client.post("/api/tasks", json={"title": f"t{i}"}).status_code == 200
    r = client.post("/api/tasks", json={"title": "t31"})
    assert r.status_code == 400 and "slow down" in r.json()["detail"]


def test_pending_reviews_limited_with_honest_total(client, fresh_db):
    from app.services import briefing, review, users

    users.ensure_user("scribe", kind="agent")
    for i in range(60):
        review.propose_change(
            "note",
            "create",
            {"topic": f"t{i}", "content": "c"},
            actor="scribe",
            notify_team=False,
        )
    day = briefing.my_day("tester")
    assert len(day["needs_you"]["pending_reviews"]) == 50
    assert day["pending_reviews_total"] == 60


def test_allocation_and_absence_refuse_team_and_ghosts(fresh_db):
    from app.services import absences, engagements, users

    users.ensure_user("mira")
    e = engagements.create_engagement("Ghosts")
    with pytest.raises(ValueError, match="not an active teammate"):
        engagements.allocate("team", e["id"], 50, actor="mira")
    with pytest.raises(ValueError, match="not an active teammate"):
        absences.add_absence("gohst", "2026-08-10", "2026-08-11", actor="mira")
    absences.add_absence("MIRA", "2026-08-10", "2026-08-11", actor="tester")
    row = fresh_db.query_one("SELECT person FROM absences")
    assert row["person"] == "mira"  # canonicalized


def test_doomed_event_cancel_proposal_auto_rejects(client, fresh_db, monkeypatch):
    import json as j

    from app import config
    from app.agents.identity import reset_agent_identity, set_agent_identity
    from app.services import schedule, users
    from app.tools.schedule import cancel_event as cancel_tool

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    users.ensure_user("scout", kind="agent")
    e = schedule.schedule_event("doomed", "2026-08-11T10:00")
    token = set_agent_identity("scout")
    try:
        out = j.loads(cancel_tool(event_id=e["id"]))
    finally:
        reset_agent_identity(token)
    schedule.cancel_event(e["id"], actor="mira")  # REST got there first
    r = client.post(f"/api/review/{out['id']}/approve", json={}, headers=_strong(client))
    assert r.status_code == 400 and "auto-rejected" in r.json()["detail"]
    row = fresh_db.query_one(
        "SELECT status, review_note FROM pending_changes WHERE id = ?", (out["id"],)
    )
    assert row["status"] == "rejected" and "target vanished" in row["review_note"]


def test_agent_recorded_promises_surface_in_week_open(fresh_db):
    from app.services import commitments, rituals, users

    users.ensure_user("mira")
    users.ensure_user("scribe", kind="agent")
    commitments.add_commitment("send the SOW", due_date="2020-01-02", actor="scribe")
    opened = rituals.week_open(actor="mira", force=True)
    assert "Recorded by agents" in opened["markdown"]
    assert "send the SOW" in opened["markdown"]
