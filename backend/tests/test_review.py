"""The review gate: propose, claim, approve, reject, diff, and the notification lifecycle around a verdict."""

import json

import pytest


def _approve_latest(client):
    from app.services.api_keys import create_key

    headers = {"Authorization": f"Bearer {create_key('tester', 'p')['key']}"}
    pending = client.get("/api/review?status=pending").json()
    assert pending, "expected a pending proposal"
    r = client.post(f"/api/review/{pending[0]['id']}/approve", json={}, headers=headers)
    assert r.json()["status"] == "approved"
    return pending[0]


def _strong(client, name="tester"):
    from app.services.api_keys import create_key

    return {"Authorization": f"Bearer {create_key(name, 'r')['key']}"}


def _delegated_task(fresh_db, title="probe"):
    from app.services import delegation, users, work

    users.ensure_user("mira")
    users.ensure_user("scout", kind="agent")
    t = work.create_task(title=title, actor="mira")
    delegation.delegate_task(t["id"], "scout", "mira", actor="mira")
    return t["id"]


def test_approval_keeps_proposer_as_author(fresh_db):
    from app.services import review, work

    p = review.propose_change("task", "create", {"title": "agent's own idea"}, actor="agent-x")
    review.approve_change(p["id"], actor="hana")
    task = work.list_tasks()[0]
    assert task["created_by"] == "agent-x"  # not the approving human
    assert task["origin"] == "agent_verified"


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


def test_approve_claim_is_single_shot(fresh_db):
    from app.services import review, work

    p = review.propose_change("task", "create", {"title": "once"}, actor="agent")
    review.approve_change(p["id"], actor="alice")
    with pytest.raises(ValueError, match="already approved"):
        review.approve_change(p["id"], actor="bob")
    assert len(work.list_tasks()) == 1  # applied exactly once


def test_approve_bad_payload_returns_to_pending(fresh_db):
    from app.services import review

    p = review.propose_change("task", "create", {"title": "x", "bogus_field": 1}, actor="agent")
    with pytest.raises(ValueError, match="could not apply"):
        review.approve_change(p["id"], actor="alice")
    row = fresh_db.query_one("SELECT * FROM pending_changes WHERE id = ?", (p["id"],))
    assert row["status"] == "pending"
    assert "apply failed" in row["review_note"]


def test_review_gate_covers_all_mutating_tools(fresh_db, monkeypatch):
    from app import config
    from app.services import review
    from app.tools import collab as tc
    from app.tools import platform as tp
    from app.tools import schedule as ts
    from app.tools import work as tw

    monkeypatch.setattr(config, "AGENT_REVIEW", True)

    calls = [
        lambda: tw.create_task(title="t"),
        lambda: tw.create_milestone(title="m"),
        lambda: tc.ask_question(question="q?", asked_by="agent"),
        lambda: tc.record_decision(title="d", decision="do it"),
        lambda: tc.post_standup(author="agent"),
        lambda: tc.save_note(topic="n", content="c"),
        lambda: ts.schedule_event(title="e", starts_at="2030-01-01T10:00"),
        lambda: tp.raise_blocker(title="b"),
        lambda: tp.submit_intake_request(title="i"),
        lambda: tp.record_lesson(lesson="l"),
        lambda: tp.start_engagement_from_playbook(
            playbook_slug="prototype", engagement_name="Gated"
        ),
    ]
    for call in calls:
        out = json.loads(call())
        assert out.get("status") == "pending", out

    assert len(review.list_changes("pending")) == len(calls)
    # nothing actually written
    assert fresh_db.query("SELECT * FROM tasks") == []
    assert fresh_db.query("SELECT * FROM engagements") == []

    # destructive cancel is refused outright under the gate
    out = json.loads(ts.cancel_event(event_id=1))
    assert "error" in out


def test_gated_playbook_approval_applies(fresh_db, monkeypatch):
    from app import config
    from app.services import engagements, review
    from app.tools import platform as tp

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    out = json.loads(
        tp.start_engagement_from_playbook(
            playbook_slug="incident", engagement_name="Sev1 db outage"
        )
    )
    review.approve_change(out["id"], actor="alice")
    assert engagements.list_engagements()[0]["name"] == "Sev1 db outage"


def test_approve_survives_unexpected_exceptions(fresh_db, monkeypatch):
    """ANY apply failure must reset the claim — an approved-but-never-applied
    proposal would vanish from the queue (correctness blocker #1)."""
    from app.services import review, work

    p = review.propose_change("task", "create", {"title": "t"}, actor="agent")

    def explode(**kwargs):
        raise RuntimeError("not a ValueError")

    monkeypatch.setattr(work, "create_task", explode)
    with pytest.raises(ValueError, match="could not apply"):
        review.approve_change(p["id"], actor="alice")
    row = fresh_db.query_row("SELECT * FROM pending_changes WHERE id = ?", (p["id"],))
    assert row["status"] == "pending"


def test_approve_unknown_entity_never_claims(fresh_db):
    from app import db
    from app.services import review

    db.execute(
        "INSERT INTO pending_changes (entity, action, payload, summary, proposed_by,"
        " origin, created_at) VALUES ('ghost', 'create', '{}', 's', 'a', 'agent', ?)",
        (db.now(),),
    )
    row = db.query_row("SELECT id FROM pending_changes WHERE entity = 'ghost'")
    with pytest.raises(ValueError, match="no handler"):
        review.approve_change(row["id"], actor="alice")
    assert (
        fresh_db.query_row("SELECT status FROM pending_changes WHERE id = ?", (row["id"],))[
            "status"
        ]
        == "pending"
    )


def test_review_stats_reports_median_minutes(fresh_db):
    from app.services import review, work

    for title in ("a", "b", "c"):
        p = review.propose_change("task", "create", {"title": title}, actor="agent")
        review.mark_seen([p["id"]], actor="r")
        review.approve_change(p["id"], actor="r")
    stats = review.review_stats()
    assert "median" in stats["active_review_minutes"]
    assert stats["active_review_minutes"]["n"] == 3
    assert len(work.list_tasks()) == 3


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


def test_claim_at_and_active_review_stats(client, fresh_db):
    from app.services import review

    p = review.propose_change("task", "create", {"title": "t"}, actor="agent")
    review.mark_seen([p["id"]], actor="reviewer")
    row = fresh_db.query_row("SELECT claim_at FROM pending_changes WHERE id = ?", (p["id"],))
    assert row["claim_at"] is not None
    first = row["claim_at"]
    review.mark_seen([p["id"]], actor="reviewer")  # idempotent — first-seen wins
    assert (
        fresh_db.query_row("SELECT claim_at FROM pending_changes WHERE id = ?", (p["id"],))[
            "claim_at"
        ]
        == first
    )
    review.approve_change(p["id"], actor="reviewer")
    stats = review.review_stats()
    assert stats["active_review_minutes"]["n"] == 1


def test_review_diff_for_updates(client, fresh_db):
    from app.services import review, work

    t = work.create_task(title="old title", actor="tester")
    p = review.propose_change(
        "task",
        "update",
        {"title": "new title", "status": "in_progress"},
        entity_id=t["id"],
        actor="agent",
    )
    d = client.get(f"/api/review/{p['id']}/diff").json()
    assert d["diff"]["current"]["title"] == "old title"
    assert d["diff"]["proposed"]["title"] == "new title"
    # creates have no diff
    p2 = review.propose_change("task", "create", {"title": "x"}, actor="agent")
    assert client.get(f"/api/review/{p2['id']}/diff").json()["diff"] is None


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


def test_reject_clears_notification_too(client, fresh_db):
    from app.services import review

    p = review.propose_change("task", "create", {"title": "x"}, actor="scout", origin="agent")
    review.reject_change(p["id"], "nope", actor="tester")
    still = fresh_db.query(
        "SELECT * FROM notifications WHERE read_at IS NULL AND message LIKE ?",
        (f"Review needed: #{p['id']}%",),
    )
    assert not still


def test_failed_apply_keeps_notification_unread(client, fresh_db):
    from app.services import review

    # empty title makes create_task raise -> apply fails -> reset to pending
    p = review.propose_change("task", "create", {"title": ""}, actor="scout", origin="agent")
    import contextlib

    with contextlib.suppress(ValueError):
        review.approve_change(p["id"], actor="tester")
    row = fresh_db.query_one("SELECT status FROM pending_changes WHERE id = ?", (p["id"],))
    assert row["status"] == "pending"
    unread = fresh_db.query(
        "SELECT * FROM notifications WHERE read_at IS NULL AND message LIKE ?",
        (f"Review needed: #{p['id']}%",),
    )
    assert unread


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
