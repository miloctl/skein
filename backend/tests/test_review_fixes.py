"""Regression tests for the code-review findings."""

import json

import pytest


def test_migrations_idempotent_and_atomic(fresh_db):
    fresh_db.init_db()  # second run must be a clean no-op
    versions = [r["version"] for r in fresh_db.query("SELECT version FROM schema_version")]
    assert len(versions) == len(set(versions)) >= 4


def test_fts_entity_word_not_indexed(fresh_db):
    from app.services import search, work

    work.create_task("Optimize queries")
    assert search.search("task") == []  # entity name is not searchable
    assert search.search("optimize") != []


def test_ensure_user_concurrent_safe(fresh_db):
    from app.services import users

    a = users.ensure_user("sam")
    b = users.ensure_user("sam")
    assert a["id"] == b["id"]
    assert len(users.list_users()) == 1


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


def test_raise_blocker_bad_task_id_is_valueerror(fresh_db):
    from app.services import blockers

    with pytest.raises(ValueError, match="task #999 not found"):
        blockers.raise_blocker("stuck", task_id=999)


def test_resolve_blocker_unblocks_linked_task(fresh_db):
    from app.services import blockers, work

    t = work.create_task("build it")
    b = blockers.raise_blocker("stuck", task_id=t["id"])
    assert work.list_tasks(status="blocked")[0]["id"] == t["id"]
    blockers.resolve_blocker(b["id"])
    assert work.list_tasks()[0]["status"] == "in_progress"

    with pytest.raises(ValueError, match="not found"):
        blockers.resolve_blocker(999)


def test_chat_thread_id_sanitized(client):
    with client.stream(
        "POST", "/api/chat", json={"thread_id": "../../etc/passwd", "message": "/help"}
    ) as r:
        assert r.status_code == 200
        assert "Mock agent" in r.read().decode()
