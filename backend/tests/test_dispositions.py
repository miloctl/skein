"""Finding dispositions (PLAN.md W1.4): suppression keys on (rule_id,
subject) because findings re-fire weekly as new rows."""

from datetime import date, timedelta

import pytest


def _plant_finding(fresh_db, rule_id="aging_wip", subject="aging_wip", week=None):
    from app import db
    from app.services.insights import _week

    fid = db.execute(
        "INSERT INTO findings (rule_id, subject, severity, message, n, window,"
        " receipt, week, created_at) VALUES (?, ?, 'medium', 'msg', 1, 'w', '{}', ?, ?)",
        (rule_id, subject, week or _week(), db.now()),
    )
    return fid


def test_disposition_validates(fresh_db):
    from app.services.insights import disposition_finding

    fid = _plant_finding(fresh_db)
    with pytest.raises(ValueError, match="deferred_until"):
        disposition_finding(fid, "deferred", actor="m")
    with pytest.raises(ValueError, match="not found"):
        disposition_finding(9999, "dismissed", actor="m")
    with pytest.raises(ValueError, match="human"):
        disposition_finding(fid, "dismissed", actor="agent-x", origin="agent")


def test_dismissed_suppresses_refire_across_weeks(fresh_db):
    from app.services.insights import _suppressed, disposition_finding

    fid = _plant_finding(fresh_db)
    assert not _suppressed("aging_wip", "aging_wip")
    disposition_finding(fid, "dismissed", reason="known", actor="m")
    # next week's run consults (rule_id, subject) history — still suppressed
    assert _suppressed("aging_wip", "aging_wip")
    # a different subject of the same rule is untouched
    assert not _suppressed("aging_wip", "other-subject")


def test_deferred_suppresses_until_date(fresh_db):
    from app.services.insights import _suppressed, disposition_finding

    fid = _plant_finding(fresh_db, subject="s1")
    future = (date.today() + timedelta(days=7)).isoformat()
    disposition_finding(fid, "deferred", deferred_until=future, actor="m")
    assert _suppressed("aging_wip", "s1")
    fid2 = _plant_finding(fresh_db, subject="s2")
    past = (date.today() - timedelta(days=1)).isoformat()
    disposition_finding(fid2, "deferred", deferred_until=past, actor="m")
    assert not _suppressed("aging_wip", "s2")


def test_resolved_does_not_suppress(fresh_db):
    from app.services.insights import _suppressed, disposition_finding

    fid = _plant_finding(fresh_db)
    disposition_finding(fid, "resolved", actor="m")
    assert not _suppressed("aging_wip", "aging_wip")  # a re-fire after a fix is signal


def test_run_findings_skips_suppressed(fresh_db):
    from app.services.engagements import create_engagement
    from app.services.insights import disposition_finding, run_findings

    create_engagement("Old spike", kind="experiment", timebox_end="2020-01-01", actor="t")
    first = run_findings(actor="t")
    hits = [f for f in first["findings"] if f["rule_id"] == "experiment_overdue"]
    assert len(hits) == 1
    disposition_finding(hits[0]["id"], "dismissed", reason="extending on purpose", actor="m")
    # move the fired row to a past week to simulate next week's fresh run
    fresh_db.execute("UPDATE findings SET week = '2020-W01' WHERE id = ?", (hits[0]["id"],))
    again = run_findings(actor="t")
    assert not any(f["rule_id"] == "experiment_overdue" for f in again["findings"])


def test_digest_excludes_dispositioned(fresh_db):
    from app.services.insights import digest_findings, disposition_finding

    fid = _plant_finding(fresh_db)
    assert len(digest_findings()) == 1
    disposition_finding(fid, "resolved", actor="m")
    assert digest_findings() == []


def test_convert_links_back_and_dispositions(client, fresh_db):
    fid = _plant_finding(fresh_db)
    r = client.post(
        f"/api/findings/{fid}/convert",
        json={"kind": "task", "title": "chase the aging WIP"},
        headers={"X-User": "manager"},
    )
    assert r.status_code == 200
    task = fresh_db.query_row("SELECT * FROM tasks")
    assert task["source_finding_id"] == fid
    d = fresh_db.query_row("SELECT * FROM finding_dispositions")
    assert d["disposition"] == "converted" and f"#{task['id']}" in d["reason"]


def test_rule_stats_counts(fresh_db):
    from app.services.insights import disposition_finding, rule_stats

    f1 = _plant_finding(fresh_db, subject="a")
    _plant_finding(fresh_db, subject="b")
    disposition_finding(f1, "dismissed", actor="m")
    stats = {s["rule_id"]: s for s in rule_stats()}
    assert stats["aging_wip"]["fired"] == 2
    assert stats["aging_wip"]["dispositioned"] == 1
    assert stats["aging_wip"]["dismissed"] == 1
