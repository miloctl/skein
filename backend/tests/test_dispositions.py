"""Finding dispositions: suppression keys on (rule_id,
subject) because findings re-fire weekly as new rows."""

from datetime import date, timedelta

import pytest
from conftest import _strong


def _plant_finding(
    fresh_db,
    rule_id="aging_wip",
    subject="aging_wip",
    week=None,
    severity="medium",
):
    from app import db
    from app.services.insights import _week

    fid = db.execute(
        'INSERT INTO findings (rule_id, subject, severity, message, n, "window",'
        " receipt, week, created_at) VALUES (?, ?, ?, 'msg', 1, 'w', '{}', ?, ?)"
        " RETURNING id",
        (rule_id, subject, severity, week or _week(), db.now()),
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
    fid = _plant_finding(fresh_db, severity="high")
    r = client.post(
        f"/api/findings/{fid}/convert",
        json={"kind": "task", "title": "chase the aging WIP"},
        headers={"X-User": "manager"},
    )
    assert r.status_code == 200
    task = fresh_db.query_row("SELECT * FROM tasks")
    assert r.json()["task_id"] == task["id"]
    assert task["source_finding_id"] == fid
    assert task["priority"] == "high"
    assert task["assignee"] == "manager"
    d = fresh_db.query_row("SELECT * FROM finding_dispositions")
    assert d["disposition"] == "converted" and f"#{task['id']}" in d["reason"]


def test_positive_finding_conversion_uses_a_valid_task_priority(client, fresh_db):
    fid = _plant_finding(fresh_db, subject="positive", severity="positive")
    r = client.post(
        f"/api/findings/{fid}/convert",
        json={"kind": "task"},
        headers={"X-User": "manager"},
    )

    assert r.status_code == 200
    task = fresh_db.query_one(
        "SELECT assignee, priority FROM tasks WHERE id = ?",
        (r.json()["task_id"],),
    )
    assert task == {"assignee": "manager", "priority": "medium"}


def test_chain_finding_conversion_needs_strong_identity(client, fresh_db):
    from app.services import wording

    fid = _plant_finding(
        fresh_db,
        rule_id="activity_chain_broken",
        subject="seq:58",
        severity="high",
    )

    weak = client.post(f"/api/findings/{fid}/convert", json={"kind": "task"})
    assert weak.status_code == 403
    assert weak.json()["detail"] == wording.strong_identity_required()
    assert fresh_db.query_one("SELECT COUNT(*) AS count FROM tasks") == {"count": 0}

    strong = client.post(
        f"/api/findings/{fid}/convert",
        json={"kind": "task"},
        headers=_strong(client),
    )
    assert strong.status_code == 200
    assert fresh_db.query_one(
        "SELECT assignee, priority FROM tasks WHERE id = ?",
        (strong.json()["task_id"],),
    ) == {"assignee": "tester", "priority": "high"}


def test_finding_conversion_rolls_back_if_disposition_fails(fresh_db, monkeypatch):
    from app.services import insights

    fid = _plant_finding(fresh_db)

    def fail(*_args, **_kwargs):
        raise RuntimeError("disposition failed")

    monkeypatch.setattr(insights, "disposition_finding", fail)
    with pytest.raises(RuntimeError, match="disposition failed"):
        insights.convert_finding(fid, "task", actor="manager")

    assert fresh_db.query_one("SELECT COUNT(*) AS count FROM tasks") == {"count": 0}
    assert fresh_db.query_one("SELECT COUNT(*) AS count FROM activity") == {"count": 0}


def test_concurrent_finding_conversion_returns_one_task(fresh_db, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from time import sleep

    from app.services import insights

    fid = _plant_finding(fresh_db)
    query_one = insights.db.query_one

    def slow_finding_read(sql, params=()):
        row = query_one(sql, params)
        if sql.startswith("SELECT * FROM findings WHERE id = ?"):
            sleep(0.05)
        return row

    monkeypatch.setattr(insights.db, "query_one", slow_finding_read)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _index: insights.convert_finding(fid, "task", actor="manager"),
                range(2),
            )
        )

    assert {result["task_id"] for result in results} == {results[0]["task_id"]}
    assert fresh_db.query_one("SELECT COUNT(*) AS count FROM tasks") == {"count": 1}
    assert fresh_db.query_one(
        "SELECT COUNT(*) AS count FROM finding_dispositions WHERE finding_id = ?",
        (fid,),
    ) == {"count": 1}


def test_rule_stats_counts(fresh_db):
    from app.services.insights import disposition_finding, rule_stats

    f1 = _plant_finding(fresh_db, subject="a")
    _plant_finding(fresh_db, subject="b")
    disposition_finding(f1, "dismissed", actor="m")
    stats = {s["rule_id"]: s for s in rule_stats()}
    assert stats["aging_wip"]["fired"] == 2
    assert stats["aging_wip"]["dispositioned"] == 1
    assert stats["aging_wip"]["dismissed"] == 1


def test_findings_feed_carries_disposition(client, fresh_db):
    from app.services import insights

    fresh_db.execute(
        "INSERT INTO findings (rule_id, subject, severity, message, receipt, week, created_at)"
        " VALUES ('question_aging', 'question-9', 'low', 'm', '{}', ?, ?)",
        (insights._week(), fresh_db.now()),
    )
    fid = fresh_db.query("SELECT id FROM findings")[0]["id"]
    rows = insights.list_findings()
    assert rows[0]["disposition"] == ""
    insights.disposition_finding(fid, "dismissed", reason="test", actor="tester")
    rows = insights.list_findings()
    assert rows[0]["disposition"] == "dismissed"


def test_deferred_until_must_be_a_date(fresh_db):
    from app import db
    from app.services.insights import disposition_finding

    fid = db.execute(
        'INSERT INTO findings (rule_id, subject, severity, message, n, "window",'
        " receipt, week, created_at) VALUES ('r', 's', 'low', 'm', 1, 'w', '{}', '2026-W30', ?)"
        " RETURNING id",
        (db.now(),),
    )
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        disposition_finding(fid, "deferred", deferred_until="banana", actor="m")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        disposition_finding(fid, "deferred", deferred_until="2026-7-1", actor="m")


def test_rule_stats_median_days(fresh_db):
    from app import db
    from app.services.insights import disposition_finding, rule_stats

    fid = db.execute(
        'INSERT INTO findings (rule_id, subject, severity, message, n, "window",'
        " receipt, week, created_at) VALUES ('r1', 's', 'low', 'm', 1, 'w', '{}', '2026-W30', ?)"
        " RETURNING id",
        ("2026-07-20T00:00:00+00:00",),
    )
    disposition_finding(fid, "resolved", actor="m")
    stats = {s["rule_id"]: s for s in rule_stats()}
    assert stats["r1"]["median_days_to_disposition"] is not None
    assert stats["r1"]["median_days_to_disposition"] >= 3  # planted 4 days ago
