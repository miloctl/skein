"""Meeting-notes ingestion (PLAN.md W1.2): deterministic pass → review
proposals, fb: lines flagged and never stored, batch approve."""

import pytest

NOTES = """
Standup sync 2026-07-24
- todo: update the deploy runbook
- q: who owns the staging cluster?
- decided: we ship on Fridays now
- blocked on the vendor API key
- fb: dana — spoke up well in the design review
- some ambient chatter that matches nothing at all
- promised: revised beta date to ops by Friday
"""


def test_ingest_creates_proposals_not_records(client, fresh_db):
    r = client.post("/api/ingest", json={"text": NOTES}, headers={"X-User": "manager"})
    assert r.status_code == 200
    body = r.json()
    kinds = sorted(p["kind"] for p in body["proposals"])
    assert kinds == ["blocker", "commitment", "decision", "question", "task"]
    assert body["skipped_private"] == 1
    assert any("ambient chatter" in u for u in body["unclassified"])
    # nothing written directly — everything is a pending proposal
    assert fresh_db.query("SELECT * FROM tasks") == []
    assert fresh_db.query("SELECT * FROM questions") == []
    pending = fresh_db.query("SELECT * FROM pending_changes WHERE status = 'pending'")
    assert len(pending) == 5
    assert all(p["origin"] == "human" and p["proposed_by"] == "manager" for p in pending)
    # fb: content never persisted anywhere in the platform db
    import json as j

    assert "dana" not in j.dumps(fresh_db.query("SELECT * FROM pending_changes")).lower()


def test_ingest_one_notification_not_per_line(client, fresh_db):
    client.post("/api/ingest", json={"text": NOTES}, headers={"X-User": "manager"})
    notif = fresh_db.query("SELECT * FROM notifications WHERE message LIKE '%ingested%'")
    review_notifs = fresh_db.query(
        "SELECT * FROM notifications WHERE message LIKE 'Review needed%'"
    )
    assert len(notif) == 1
    assert review_notifs == []


def test_ingest_size_limits(client):
    r = client.post("/api/ingest", json={"text": "x" * (65 * 1024)})
    assert r.status_code == 400
    r = client.post("/api/ingest", json={"text": "todo: a\n" * 501})
    assert r.status_code == 400


def test_batch_approve_applies_and_reports(client, fresh_db):
    client.post("/api/ingest", json={"text": NOTES}, headers={"X-User": "manager"})
    ids = [p["id"] for p in fresh_db.query("SELECT id FROM pending_changes")]
    r = client.post(
        "/api/review/approve-batch", json={"ids": [*ids, 9999]}, headers={"X-User": "reviewer"}
    )
    results = {x["id"]: x["status"] for x in r.json()["results"]}
    assert all(results[i] == "approved" for i in ids)
    assert results[9999] == "error"
    assert len(fresh_db.query("SELECT * FROM tasks")) == 1
    assert len(fresh_db.query("SELECT * FROM questions")) == 1


def test_failed_compound_apply_rolls_back_and_stays_pending(fresh_db, monkeypatch):
    from app.services import review, schedule

    p = review.propose_change(
        "playbook",
        "create",
        {"slug": "prototype", "engagement_name": "Doomed"},
        actor="agent",
    )
    original = schedule.schedule_event

    def explode(**kwargs):
        raise ValueError("ritual failed")

    monkeypatch.setattr(schedule, "schedule_event", explode)
    with pytest.raises(ValueError, match="could not apply"):
        review.approve_change(p["id"], actor="alice")
    row = fresh_db.query_one("SELECT * FROM pending_changes WHERE id = ?", (p["id"],))
    assert row["status"] == "pending"  # atomic rollback → safe to retry
    assert fresh_db.query("SELECT * FROM engagements") == []
    assert fresh_db.query("SELECT * FROM milestones") == []
    # and the same proposal applies cleanly once the failure is gone
    monkeypatch.setattr(schedule, "schedule_event", original)
    review.approve_change(p["id"], actor="alice")
    assert len(fresh_db.query("SELECT * FROM engagements")) == 1


def test_ingest_counts_short_fb_lines(client, fresh_db):
    r = client.post("/api/ingest", json={"text": "todo: real work item\nfb: d—x"})
    assert r.json()["skipped_private"] == 1  # short fb: still counted, never stored
