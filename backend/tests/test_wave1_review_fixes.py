"""Regression tests for the Wave 1 three-agent review findings."""

import pytest


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


def test_deferred_until_must_be_a_date(fresh_db):
    from app import db
    from app.services.insights import disposition_finding

    fid = db.execute(
        "INSERT INTO findings (rule_id, subject, severity, message, n, window,"
        " receipt, week, created_at) VALUES ('r', 's', 'low', 'm', 1, 'w', '{}', '2026-W30', ?)",
        (db.now(),),
    )
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        disposition_finding(fid, "deferred", deferred_until="banana", actor="m")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        disposition_finding(fid, "deferred", deferred_until="2026-7-1", actor="m")


def test_team_commitments_dont_fire_external_rule(fresh_db):
    from app.services.commitments import add_commitment
    from app.services.insights import run_findings

    add_commitment("promise to the team", due_date="2020-01-01", audience="team", actor="m")
    result = run_findings(actor="t")
    assert not any(f["rule_id"] == "commitment_due" for f in result["findings"])
    add_commitment("promise to ops", due_date="2020-01-01", audience="external", actor="m")
    result = run_findings(actor="t")
    assert any(f["rule_id"] == "commitment_due" for f in result["findings"])


def test_intake_accept_as_experiment(client, fresh_db):
    req = client.post("/api/intake", json={"title": "RAG spike"}).json()
    client.post(
        f"/api/intake/{req['id']}/score",
        json={"reach": 3, "impact": 3, "confidence": 3, "effort": 2},
    )
    r = client.post(
        f"/api/intake/{req['id']}/disposition",
        json={
            "disposition": "accepted",
            "reason": "worth two weeks",
            "kind": "experiment",
            "timebox_end": "2026-08-15",
            "outcome": "median lookup under 8 minutes",
        },
    )
    assert r.json()["engagement_created"] is True
    eng = fresh_db.query_row("SELECT * FROM engagements WHERE name = 'RAG spike'")
    assert eng["kind"] == "experiment"
    assert eng["timebox_end"] == "2026-08-15"
    assert eng["outcome"] == "median lookup under 8 minutes"


def test_timebox_can_be_extended(client, fresh_db):
    from app.services.engagements import create_engagement

    e = create_engagement("Spike", kind="experiment", timebox_end="2026-08-01", actor="m")
    r = client.patch(f"/api/engagements/{e['id']}", json={"timebox_end": "2026-09-01"})
    assert r.status_code == 200
    assert (
        fresh_db.query_row("SELECT timebox_end FROM engagements WHERE id = ?", (e["id"],))[
            "timebox_end"
        ]
        == "2026-09-01"
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


def test_ingest_counts_short_fb_lines(client, fresh_db):
    r = client.post("/api/ingest", json={"text": "todo: real work item\nfb: d—x"})
    assert r.json()["skipped_private"] == 1  # short fb: still counted, never stored
