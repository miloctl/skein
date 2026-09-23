"""The review gate: propose, claim, approve, reject, diff, and the notification lifecycle around a verdict."""

import json

import pytest
from conftest import _delegated_task, _strong

from app import db


@pytest.mark.parametrize("reserved", ("actor", "origin"))
def test_reserved_apply_keys_are_refused_before_proposal_storage(fresh_db, reserved):
    from app.services import review

    payload = {"title": "probe", reserved: "caller-controlled"}
    with pytest.raises(ValueError, match=reserved) as exc:
        review.propose_change("task", "create", payload, actor="scout")
    assert "caller-controlled" not in str(exc.value)
    assert fresh_db.query_one("SELECT id FROM pending_changes") is None


def _approve_latest(client):
    from app.services.api_keys import create_key

    headers = {"Authorization": f"Bearer {create_key('tester', 'p')['key']}"}
    pending = client.get("/api/review?status=pending").json()
    assert pending, "expected a pending proposal"
    r = client.post(f"/api/review/{pending[0]['id']}/approve", json={}, headers=headers)
    assert r.json()["status"] == "approved"
    return pending[0]


def test_season_readout_reads_the_exit_trigger(fresh_db, monkeypatch):
    """The posture note ends the dogfooding season with "a read, not a
    debate" — this is the read: verdicts (split by strong identity, because
    only strong verdicts feed a promotion streak), proposals, authority
    changes, and delegations, all season-scoped. Humans never appear in
    by_agent, the same line review_stats draws."""
    from app import config
    from app.services import delegation, review, users, work

    monkeypatch.setattr(config, "AGENT_REVIEW", False)
    users.ensure_user("hana")
    p1 = review.propose_change("task", "create", {"title": "one"}, actor="agent-x")
    p2 = review.propose_change("task", "create", {"title": "two"}, actor="agent-x")
    review.propose_change(
        "note", "create", {"topic": "t", "content": "c"}, actor="hana", origin="human"
    )
    review.approve_change(p1["id"], actor="hana", strong=True)
    review.reject_change(p2["id"], "not yet", actor="hana")
    t = work.create_task(title="delegated", actor="hana")
    delegation.delegate_task(t["id"], "agent-x", "hana", actor="hana")
    work.update_task(t["id"], status="done", actor="hana")

    out = review.season_readout()
    assert out["verdicts"]["settled"] == 2
    assert out["verdicts"]["approved"] == 1
    assert out["verdicts"]["strong"] == 1
    assert out["delegations"] == {"started": 1, "accepted": 1}
    assert [r["proposed_by"] for r in out["by_agent"]] == ["agent-x"]
    assert out["proposals"] == 3  # the human's proposal counts in the total


def test_approval_keeps_proposer_as_author(fresh_db):
    from app.services import review, work

    p = review.propose_change("task", "create", {"title": "agent's own idea"}, actor="agent-x")
    review.approve_change(p["id"], actor="hana")
    task = work.list_tasks()[0]
    assert task["created_by"] == "agent-x"  # not the approving human
    assert task["origin"] == "agent_verified"


def test_rejection_requires_the_configured_workplace_approver(fresh_db):
    from app.services import review, users

    users.ensure_user("reviewer")
    proposal = review.propose_change(
        "task",
        "create",
        {"title": "manager verdict"},
        actor="agent",
        approver_groups=("delivery-managers",),
    )
    with pytest.raises(PermissionError, match="workplace approver"):
        review.reject_change(proposal["id"], actor="reviewer")
    result = review.reject_change(
        proposal["id"],
        actor="reviewer",
        reviewer_groups=("delivery-managers",),
    )
    assert result["status"] == "rejected"
    row = fresh_db.query_one(
        "SELECT reviewer_qualifications FROM pending_changes WHERE id = ?",
        (proposal["id"],),
    )
    assert json.loads(row["reviewer_qualifications"])["matched_groups"] == ["delivery-managers"]


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
    from app.services import blockers, delegation, users, wording
    from app.tools.platform import edit_blocker

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    users._reserve_core_agent_identity("agent")
    b = blockers.raise_blocker(title="typo'd", owner="ava", actor="ava")
    delegation.set_authority("agent", "blocker_edit", "forbidden", actor="tester")
    out = json.loads(edit_blocker(blocker_id=b["id"], title="nope"))
    assert out == {"error": wording.write_policy_denied()}
    assert (
        fresh_db.query_one("SELECT title FROM blockers WHERE id = ?", (b["id"],))["title"]
        == "typo'd"
    )


def test_agent_history_guard_survives_approval(client, fresh_db, monkeypatch):
    """Approving an edit of a since-settled record must fail the apply and
    reset the proposal — never falsify history."""
    from app import config
    from app.services import promises
    from app.tools.portfolio import edit_promise

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    c = promises.add_promise("shipp it", actor="ava")
    out = edit_promise(promise_id=c["id"], promise="ship it")
    assert "pending" in out
    promises.update_promise(c["id"], "kept", actor="ava")  # settles first
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
        fresh_db.query_one("SELECT promise FROM promises WHERE id = ?", (c["id"],))["promise"]
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
    from app.services import promises
    from app.tools.portfolio import edit_promise

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    c = promises.add_promise("shipp the thing to ops", actor="ava")
    edit_promise(promise_id=c["id"], promise="ship the thing to ops")
    pending = client.get("/api/review?status=pending").json()
    d = client.get(f"/api/review/{pending[0]['id']}/diff").json()
    assert d["diff"]["current"]["promise"] == "shipp the thing to ops"
    assert d["diff"]["proposed"]["promise"] == "ship the thing to ops"


def test_edit_tools_refuse_empty_and_invalid_before_proposing(fresh_db, monkeypatch):
    from app import config
    from app.services import engagements, promises
    from app.tools.collab import edit_note
    from app.tools.platform import update_engagement
    from app.tools.portfolio import mark_promise

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    assert "nothing to change" in edit_note(note_id=1)
    c = promises.add_promise("p", actor="ava")
    assert "kept, missed, or withdrawn" in mark_promise(promise_id=c["id"], status="done")
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


@pytest.mark.parametrize(
    ("action", "payload"),
    [
        ("create", {"title": "Task", "priority": "critical"}),
        ("create", {"title": "Task", "due_date": "-"}),
        ("create", {"title": "   "}),
        ("update", {"priority": "critical"}),
        ("update", {"status": "finished"}),
        ("update", {"committed_week": "2026-W99"}),
        ("update", {"waiting_on": "unknown:1"}),
    ],
)
def test_invalid_task_fields_are_refused_by_services_and_proposals(fresh_db, action, payload):
    from app.services import review, work

    task = work.create_task("Existing")
    with pytest.raises(ValueError) as direct:
        if action == "create":
            work.create_task(**payload)
        else:
            work.update_task(task["id"], **payload)
    with pytest.raises(ValueError) as proposal:
        review.propose_change(
            "task", action, payload, entity_id=task["id"] if action == "update" else 0
        )
    assert str(proposal.value) == str(direct.value)
    assert fresh_db.query("SELECT id FROM pending_changes") == []


@pytest.mark.parametrize(
    ("action", "self_wait"), [("create", False), ("update", False), ("update", True)]
)
def test_legacy_invalid_task_proposals_are_terminally_rejected(
    fresh_db, monkeypatch, action, self_wait
):
    from app.services import review, work

    task = work.create_task("Existing")
    payload = {"waiting_on": f"task:#{task['id']}"} if self_wait else {"priority": "critical"}
    if action == "create":
        payload["title"] = "Legacy task"
    # The old proposal service accepted this payload. Keep its real notice and ledger rows.
    with monkeypatch.context() as legacy:
        legacy.setattr(review, "unappliable", lambda *_args, **_kwargs: "")
        proposal = review.propose_change(
            "task",
            action,
            payload,
            entity_id=task["id"] if action == "update" else 0,
            actor="agent",
        )
    with pytest.raises(ValueError, match="auto-rejected"):
        review.approve_change(proposal["id"], actor="alice", strong=True)
    row = fresh_db.query_one("SELECT * FROM pending_changes WHERE id = ?", (proposal["id"],))
    assert row["status"] == "rejected"
    assert row["reviewed_strong"] == 0
    assert (
        fresh_db.query_one("SELECT priority FROM tasks WHERE id = ?", (task["id"],))["priority"]
        == "medium"
    )
    assert (
        fresh_db.query_one(
            "SELECT 1 FROM notifications WHERE pending_change_id = ? AND read_at IS NULL",
            (proposal["id"],),
        )
        is None
    )


def test_task_validation_preserves_following_valid_proposals_and_clear_sentinels(fresh_db):
    from app.services import review, work

    with pytest.raises(ValueError, match="priority"):
        review.propose_change(
            "task", "create", {"title": "Invalid task", "priority": "critical"}, actor="agent"
        )
    proposal = review.propose_change("task", "create", {"title": "Proposed task"}, actor="agent")
    task_id = review.approve_change(proposal["id"], actor="alice")["result"]["id"]
    task = fresh_db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    assert (task["priority"], task["origin"], task["created_by"]) == (
        "medium",
        "agent_verified",
        "agent",
    )
    work.update_task(task_id, due_date="2026-09-07", committed_week="2026-W37", actor="alice")
    for payload in (
        {"priority": "high"},
        {"due_date": "-", "committed_week": "-", "waiting_on": "-"},
    ):
        proposal = review.propose_change(
            "task", "update", payload, entity_id=task_id, actor="agent"
        )
        review.approve_change(proposal["id"], actor="alice")
    task = fresh_db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    assert task["priority"] == "high"
    assert task["due_date"] is None and task["committed_week"] is None


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

    # counted in the table, not through list_changes: an agent's standup is
    # private by default, and its proposal is its author's to read
    pending = db.query("SELECT id FROM pending_changes WHERE status = 'pending'")
    assert len(pending) == len(calls)
    # nothing actually written
    assert fresh_db.query("SELECT * FROM tasks") == []
    assert fresh_db.query("SELECT * FROM engagements") == []

    # destructive cancel is refused outright under the gate
    out = json.loads(ts.cancel_event(event_id=1))
    assert "error" in out


def test_gated_playbook_approval_applies(fresh_db, monkeypatch):
    from app import config
    from app.extensions import ExtensionRegistry
    from app.extensions.core import core_module
    from app.services import engagements, review
    from app.tools import platform as tp

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    out = json.loads(
        tp.start_engagement_from_playbook(
            playbook_slug="incident", engagement_name="Sev1 db outage"
        )
    )
    review.approve_change(
        out["id"],
        actor="alice",
        policy_registry=ExtensionRegistry.build((core_module(),)),
    )
    assert engagements.list_engagements()[0]["name"] == "Sev1 db outage"


def test_approve_survives_unexpected_exceptions(fresh_db, monkeypatch):
    """ANY apply failure must reset the claim — an approved-but-never-applied
    proposal would vanish from the queue."""
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


def test_review_stats_never_groups_human_proposers(client):
    from app.services import review, users

    users.ensure_user("mira")
    users.ensure_user("scout", kind="agent")
    review.propose_change("note", "create", {"topic": "human", "content": "x"}, actor="mira")
    review.propose_change("note", "create", {"topic": "agent", "content": "x"}, actor="scout")

    # `scheduler` owns no users row: review_authority files every promotion
    # under it, so testing for is_agent drops the whole authority entity from
    # this table while by_entity still counts it
    review.propose_change("note", "create", {"topic": "job", "content": "x"}, actor="scheduler")

    names = {row["proposed_by"] for row in client.get("/api/review/stats").json()["by_proposer"]}
    assert names == {"scout", "scheduler"}


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


def test_review_resolution_clears_notification(client, fresh_db):
    from app.services import review

    p = review.propose_change(
        "task", "create", {"title": "from an agent"}, actor="scout", origin="agent"
    )
    unread = fresh_db.query(
        "SELECT pending_change_id FROM notifications"
        " WHERE read_at IS NULL AND pending_change_id = ?",
        (p["id"],),
    )
    assert unread == [{"pending_change_id": p["id"]}]
    review.approve_change(p["id"], actor="tester")
    still = fresh_db.query(
        "SELECT id FROM notifications WHERE read_at IS NULL AND pending_change_id = ?",
        (p["id"],),
    )
    assert not still


def test_reject_clears_notification_too(client, fresh_db):
    from app.services import review

    p = review.propose_change("task", "create", {"title": "x"}, actor="scout", origin="agent")
    fresh_db.execute(
        "UPDATE notifications SET pending_change_id = NULL WHERE pending_change_id = ?",
        (p["id"],),
    )
    review.reject_change(p["id"], "nope", actor="tester")
    still = fresh_db.query(
        "SELECT * FROM notifications WHERE read_at IS NULL AND message LIKE ?",
        (f"Review needed: #{p['id']}%",),
    )
    assert not still


def test_failed_apply_keeps_notification_unread(client, fresh_db):
    from app.services import engagements, review, work

    original = engagements.create_engagement("Original")["id"]
    other = engagements.create_engagement("Other")["id"]
    milestone = work.create_milestone("Movable milestone", project="Original")["id"]
    p = review.propose_change(
        "task",
        "create",
        {"title": "Proposed task", "milestone_id": milestone, "engagement_id": original},
        actor="scout",
        origin="agent",
    )
    work.update_milestone(milestone, engagement_id=other)
    with pytest.raises(ValueError, match="same engagement"):
        review.approve_change(p["id"], actor="tester")
    row = fresh_db.query_one("SELECT status FROM pending_changes WHERE id = ?", (p["id"],))
    assert row["status"] == "pending"
    unread = fresh_db.query(
        "SELECT * FROM notifications WHERE read_at IS NULL AND message LIKE ?",
        (f"Review needed: #{p['id']}%",),
    )
    assert unread
    work.update_milestone(milestone, engagement_id=original)
    assert review.approve_change(p["id"], actor="tester")["status"] == "approved"


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


def test_mark_seen_stamps_only_pending_unseen_rows(fresh_db):
    from app.services import review, users

    users.ensure_user("hana")
    approved = review.propose_change("task", "create", {"title": "done deal"}, actor="agent")
    review.approve_change(approved["id"], actor="hana")
    pending = review.propose_change("task", "create", {"title": "still open"}, actor="agent")

    assert review.mark_seen([approved["id"], pending["id"]]) == {"seen": 1}
    row = fresh_db.query_row("SELECT claim_at FROM pending_changes WHERE id = ?", (approved["id"],))
    assert row["claim_at"] is None  # a verdict already landed — the clock stays honest


def test_batch_approve_returns_one_result_per_id(client, fresh_db):
    """The model accepts 200 ids (the pending-list LIMIT, so 'select all' on
    a full queue validates) while the route looped over only the first 100 —
    so 150 selections produced 100 result rows and 50 proposals were dropped
    with nothing said. A caller must be able to count the answers."""
    from app.services import review

    ids = []
    for i in range(120):
        c = review.propose_change(
            "task",
            "create",
            {"title": f"batch probe {i}"},
            summary=f"probe {i}",
            actor="planner-agent",
        )
        ids.append(c["id"])

    r = client.post("/api/review/approve-batch", json={"ids": ids})
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) == len(ids), "a selection got no answer"
    assert {x["id"] for x in results} == set(ids)
    assert all(x["status"] == "approved" for x in results)


def test_batch_approve_rejects_more_ids_than_the_model_allows(client, fresh_db):
    """max_length=200 on BatchApproveIn is the only cap on a batch — the
    loop trusts it (routes/api.py). If validation loosens, ids beyond the
    pending-list LIMIT reach the loop unannounced."""
    r = client.post("/api/review/approve-batch", json={"ids": list(range(1, 202))})
    assert r.status_code == 422


def test_batch_approve_answers_a_duplicated_id_twice(client, fresh_db):
    """One answer per submitted id, even when two of them are the same id:
    the first approves, the second reports the error. Collapsing duplicates
    would break the caller's count of answers against selections."""
    from app.services import review

    c = review.propose_change(
        "task", "create", {"title": "dup probe"}, summary="dup", actor="planner-agent"
    )
    r = client.post("/api/review/approve-batch", json={"ids": [c["id"], c["id"]]})
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) == 2
    assert results[0]["status"] == "approved"
    assert results[1]["status"] == "error"


def test_every_registry_entity_maps_to_a_target_table_or_is_named_untargeted(fresh_db):
    """_readable decides whether a proposal may be shown or judged by looking
    up the row it targets. An entity in neither map has no row to look up, so
    it is kept for every reader with its payload — which for a create is the
    whole body of the row it would make. That is how `note`, `standup`,
    `event`, `memory`, `lesson`, `intake` and `absence` creates went out
    unfiltered while the update entities beside them were checked."""
    from app.services import review, scope

    entities = set(review._registry())
    mapped = set(review._TARGET_TABLE)
    untargeted = set(review._UNTARGETED)
    missing = entities - mapped - untargeted
    assert not missing, (
        f"registry entities with no target table and no written reason: {sorted(missing)}."
        " Add the table to review._TARGET_TABLE, or name it in _UNTARGETED with why."
    )
    assert not (mapped & untargeted), sorted(mapped & untargeted)
    ghosts = (mapped | untargeted) - entities
    assert not ghosts, f"mapped entities the registry no longer has: {sorted(ghosts)}"
    unknown = {t for t in review._TARGET_TABLE.values() if t not in scope.CLASSIFIED}
    assert not unknown, f"target tables that carry no tier: {sorted(unknown)}"


def test_a_create_proposal_is_judged_by_the_tier_of_the_row_it_names(fresh_db):
    """A proposal that is invisible must not be approvable. `_readable` and
    `_assert_judgeable` each grew their own tier lookup and disagreed: a
    `delegation` create names its task in the PAYLOAD, not in entity_id, so
    both read nothing and a non-member approved a delegation of a crew task
    they cannot see. One resolver now answers for both."""
    from app.services import crews, review, scope, users, work

    for n in ("ava", "mallory"):
        users.ensure_user(n)
    users.ensure_user("scout", kind="agent")
    cid = crews.create_crew("Alpha", actor="ava")["id"]
    crew_task = work.create_task(title="rotate keys", actor="ava", visibility="crew", crew_id=cid)
    open_task = work.create_task(title="open work", actor="ava")

    scoped = review.propose_change(
        "delegation",
        "create",
        {"task_id": crew_task["id"], "agent": "scout", "sponsor": "ava"},
        summary="delegate",
        actor="scout",
    )["id"]
    declared = review.propose_change(
        "note",
        "create",
        {"topic": "t", "content": "SECRET", "author": "ava", "visibility": "crew", "crew_id": cid},
        summary="save note",
        actor="scout",
    )["id"]
    workspace = review.propose_change(
        "delegation",
        "create",
        {"task_id": open_task["id"], "agent": "scout", "sponsor": "ava"},
        summary="delegate",
        actor="scout",
    )["id"]

    mal = scope.Viewer("mallory", True)
    assert [c["id"] for c in review.list_changes(viewer=mal)] == [workspace]
    for pid in (scoped, declared):
        with pytest.raises(db.NotFound):
            review.approve_change(pid, actor="mallory", strong=True, viewer=mal)
        with pytest.raises(db.NotFound):
            review.reject_change(pid, note="no", actor="mallory", strong=True, viewer=mal)
    # and the guard does not swallow the ordinary case
    assert (
        review.approve_change(workspace, actor="mallory", strong=True, viewer=mal)["status"]
        == "approved"
    )


def test_batch_approve_answers_an_unqualified_approver_per_row(client, fresh_db):
    """A workplace approver requirement raises PermissionError inside the
    loop. As a batch-level 403 it hid the ids already committed before it and
    silently skipped every id after it."""
    from app.services import review

    first = review.propose_change(
        "note", "create", {"topic": "a", "content": "a"}, actor="planner-agent"
    )["id"]
    guarded = review.propose_change(
        "note",
        "create",
        {"topic": "b", "content": "b"},
        actor="planner-agent",
        approver_capabilities=("acme.approve",),
    )["id"]
    last = review.propose_change(
        "note", "create", {"topic": "c", "content": "c"}, actor="planner-agent"
    )["id"]

    r = client.post("/api/review/approve-batch", json={"ids": [first, guarded, last]})
    assert r.status_code == 200
    by_id = {row["id"]: row for row in r.json()["results"]}
    assert by_id[first]["status"] == "approved"
    assert by_id[guarded]["status"] == "forbidden"
    assert "approver" in by_id[guarded]["detail"]
    assert by_id[last]["status"] == "approved"
    assert (
        fresh_db.query_one("SELECT status FROM pending_changes WHERE id = ?", (guarded,))["status"]
        == "pending"
    )


def test_the_requester_approves_their_own_proposal_by_default(fresh_db):
    """At team scale the person who asked an agent to act is usually the only
    one who can judge the result, so separation is opt-in."""
    from app.services import review

    proposal = review.propose_change(
        "task", "create", {"title": "routine write"}, actor="agent", requested_by="mira"
    )

    assert review.approve_change(proposal["id"], actor="mira")["status"] == "approved"


def test_separated_duties_refuse_the_person_the_proposal_came_from(fresh_db, monkeypatch):
    from app import config
    from app.services import review

    proposal = review.propose_change(
        "task", "create", {"title": "regulated write"}, actor="agent", requested_by="mira"
    )
    monkeypatch.setattr(config, "REVIEW_SEPARATION", True)

    with pytest.raises(PermissionError, match="different qualified person"):
        review.approve_change(proposal["id"], actor="mira")
    # a different case is the same person
    with pytest.raises(PermissionError, match="different qualified person"):
        review.approve_change(proposal["id"], actor="MIRA")

    assert review.approve_change(proposal["id"], actor="hana")["status"] == "approved"


def test_separated_duties_still_let_the_requester_reject(fresh_db, monkeypatch):
    """A rule that traps a proposal in the queue is worse than one person
    declining it, so separation gates approval alone."""
    from app import config
    from app.services import review

    proposal = review.propose_change(
        "task", "create", {"title": "withdrawn write"}, actor="agent", requested_by="mira"
    )
    monkeypatch.setattr(config, "REVIEW_SEPARATION", True)

    assert review.reject_change(proposal["id"], actor="mira")["status"] == "rejected"


def test_approving_a_change_holds_the_row_it_is_about(fresh_db, monkeypatch):
    """The hold read a source_id column pending_changes does not have, so it
    never ran: a relink committed between the policy re-check and the apply
    settled the proposal under the old project's rule."""
    from app.services import policy_context, review, users, work

    users.ensure_user("scout", kind="agent")
    tid = work.create_task(title="probe", actor="tester")["id"]
    held: list[tuple[str, int]] = []
    real = policy_context.hold_resource
    monkeypatch.setattr(
        policy_context, "hold_resource", lambda e, i: (held.append((e, i)), real(e, i))[1]
    )
    p = review.propose_change("task", "update", {"title": "renamed"}, entity_id=tid, actor="scout")
    review.approve_change(p["id"], actor="tester")
    assert ("task", tid) in held


def test_an_approved_create_follows_the_row_it_made(client, fresh_db):
    """Resolved by its payload alone, an approved create kept the body of a
    note readable in the approved list after the note was deleted."""
    from app.services import collab, review, users

    users.ensure_user("scribe", kind="agent")
    users.ensure_user("ava")
    proposal = review.propose_change(
        "note", "create", {"topic": "t", "content": "ZZGONEBODYZZ"}, "note", actor="scribe"
    )
    note = review.approve_change(proposal["id"], actor="ava", strong=True)["result"]
    collab.delete_note(note["id"], actor="ava")
    settled = client.get("/api/review", params={"status": "approved"}, headers=_strong(client))
    assert "ZZGONEBODYZZ" not in settled.text


def test_a_proposal_for_an_addressed_memory_is_the_addressees_alone(client, fresh_db):
    """The create branch read payload["author"], and a memory's author is its
    `user`: a proposal addressed to ava was readable and approvable by all."""
    from app.services import review, users

    users.ensure_user("scribe", kind="agent")
    users.ensure_user("ava")
    proposal = review.propose_change(
        "memory", "create", {"content": "ZZAVAONLYZZ", "user": "ava"}, "memory", actor="scribe"
    )
    queue = client.get("/api/review", headers=_strong(client)).text
    assert "ZZAVAONLYZZ" not in queue
    refused = client.post(f"/api/review/{proposal['id']}/approve", json={}, headers=_strong(client))
    assert refused.status_code == 404
    assert "ZZAVAONLYZZ" in client.get("/api/review", headers=_strong(client, "ava")).text


def test_a_verdict_on_a_persons_proposal_stays_between_proposer_and_reviewer(
    client, fresh_db, monkeypatch
):
    """Settled lists rebuilt a teammate's rejection record with the reviewer's
    notes, the person-level judgment trust_scores withholds."""
    from app import config
    from app.services import insights, review, scope, users

    monkeypatch.setattr(config, "ADMINS", frozenset({"ops"}))
    for name in ("alice", "bob", "carol"):
        users.ensure_user(name)
    change = review.propose_change(
        "note", "create", {"topic": "t", "content": "c"}, "note", actor="alice", origin="human"
    )
    review.reject_change(change["id"], "ZZHARSHZZ", actor="bob", viewer=scope.Viewer("bob", True))
    for who, sees in (("alice", True), ("bob", True), ("carol", False)):
        for path in ("/api/review?status=rejected", "/api/review/stats"):
            text = client.get(path, headers=_strong(client, who)).text
            assert ("ZZHARSHZZ" in text) is sees, (who, path)
    # nine more rejections so the spike rule fires; its receipt quotes notes
    users.ensure_user("scribe", kind="agent")
    for i in range(9):
        other = review.propose_change(
            "note", "create", {"topic": "t", "content": f"c{i}"}, "note", actor="scribe"
        )
        review.reject_change(
            other["id"], "agent note", actor="bob", viewer=scope.Viewer("bob", True)
        )
    fired = insights._r_rejection_spike()
    assert fired and "agent note" in str(fired)
    assert "ZZHARSHZZ" not in str(fired)


def test_an_agents_proposal_is_the_requesters_to_judge_first(client, fresh_db, monkeypatch):
    """A proposal from a person's chat went to the team queue: every teammate
    read its payload, the words of that chat, and got a notice quoting it."""
    from app import config
    from app.agents import identity
    from app.extensions import PolicyDecision, PolicyEffect
    from app.extensions.policy import PolicyEngine, reset_policy_engine, set_policy_engine
    from app.services import scope, users
    from app.tools._gate import gated_write

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    for name in ("ava", "bob"):
        users.ensure_user(name)

    def filed(text, *, strong=True):
        requester = identity.set_requester_identity("ava")
        viewer = identity.set_requester_viewer(scope.Viewer("ava", strong))
        try:
            payload = {"question": text, "asked_by": "ava"}
            out = gated_write("question", "create", payload, lambda: {"id": 0})
        finally:
            identity.reset_requester_viewer(viewer)
            identity.reset_requester_identity(requester)
        return fresh_db.query_one(
            "SELECT id, review_visibility, review_owner FROM pending_changes WHERE id = ?",
            (json.loads(out)["id"],),
        )

    mine = filed("ZZMINEZZ")
    assert (mine["review_visibility"], mine["review_owner"]) == ("private", "ava")
    assert "ZZMINEZZ" not in json.dumps(fresh_db.query("SELECT message FROM notifications"))
    seen_by = {
        name: [row["id"] for row in client.get("/api/review", headers=_strong(client, name)).json()]
        for name in ("ava", "bob")
    }
    assert mine["id"] in seen_by["ava"] and mine["id"] not in seen_by["bob"]
    approved = client.post(
        f"/api/review/{mine['id']}/approve", json={}, headers=_strong(client, "ava")
    )
    assert approved.status_code == 200
    # a trusted-header name with no key reads no private row
    assert filed("weak", strong=False)["review_visibility"] == "workspace"
    token = set_policy_engine(
        PolicyEngine(
            (lambda request: PolicyDecision(PolicyEffect.REVIEW, approver_groups=("leads",)),)
        )
    )
    try:
        assert filed("grouped")["review_visibility"] == "workspace"
    finally:
        reset_policy_engine(token)
    monkeypatch.setattr(config, "REVIEW_SEPARATION", True)
    assert filed("separated")["review_visibility"] == "workspace"
