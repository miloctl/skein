"""Experiments + close conclusions (PLAN.md W1.3)."""

import pytest


def _experiment(name="Retrieval spike", timebox="2026-07-01"):
    from app.services.engagements import create_engagement

    return create_engagement(
        name,
        kind="experiment",
        timebox_end=timebox,
        kill_criteria="no quality lift after 2 weeks",
        outcome="reduce median lookup time below 8 minutes",
        actor="tester",
    )


def test_experiment_requires_timebox(fresh_db):
    from app.services.engagements import create_engagement

    with pytest.raises(ValueError, match="timebox"):
        create_engagement("No box", kind="experiment", actor="tester")


def test_close_requires_conclusion(fresh_db):
    from app.services.engagements import update_engagement

    e = _experiment()
    with pytest.raises(ValueError, match="conclusion"):
        update_engagement(e["id"], status="closed", actor="tester")
    update_engagement(e["id"], status="closed", conclusion="invalidated", actor="tester")
    row = fresh_db.query_row("SELECT * FROM engagements WHERE id = ?", (e["id"],))
    assert row["conclusion"] == "invalidated" and row["status"] == "closed"


def test_experiment_close_drafts_lesson_and_honest_recap(fresh_db, monkeypatch):
    from app.services import notifications
    from app.services.engagements import update_engagement

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)
    e = _experiment()
    update_engagement(e["id"], status="closed", conclusion="invalidated", actor="tester")
    lessons = fresh_db.query("SELECT * FROM lessons")
    assert len(lessons) == 1 and "invalidated" in lessons[0]["lesson"]
    note = fresh_db.query_row("SELECT content FROM notes WHERE topic LIKE 'shipped-%'")
    assert "Experiment concluded" in note["content"]  # not framed as "Shipped"


def test_slip_forecast_skips_experiments(fresh_db):
    from app.services import work
    from app.services.portfolio import slip_forecast

    e = _experiment()
    work.create_milestone(
        title="spike milestone", project="Retrieval spike", due_date="2020-01-01", actor="t"
    )
    assert e["id"]  # milestone adopted via engagement link at create time
    assert slip_forecast()["forecasts"] == []


def test_experiment_overdue_rule_fires(fresh_db):
    from app.services.insights import run_findings

    _experiment(timebox="2020-01-01")
    result = run_findings(actor="tester")
    hits = [f for f in result["findings"] if f["rule_id"] == "experiment_overdue"]
    assert len(hits) == 1
    assert "past its timebox" in hits[0]["message"]


def test_delivery_engagements_unchanged(fresh_db):
    from app.services.engagements import create_engagement, update_engagement

    e = create_engagement("Normal delivery", actor="tester")
    with pytest.raises(ValueError, match="conclusion"):
        update_engagement(e["id"], status="closed", actor="tester")
    update_engagement(e["id"], status="closed", conclusion="achieved", actor="tester")


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
