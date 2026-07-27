"""Engineering-backlog hardening: db.transaction atomicity, engagement_id
joins, job registry outcomes + health, retention pruning, MCP migration
guard, usage service, narrator hook inversion."""

from datetime import datetime, timedelta, timezone

import pytest


def _iso_hours_ago(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")


def test_transaction_rolls_back_all_writes(fresh_db):
    from app import db

    with pytest.raises(RuntimeError), db.transaction():
        db.execute(
            "INSERT INTO notes (topic, content, author, origin, created_by, created_at)"
            " VALUES ('t', 'c', 'a', 'human', 'a', ?)",
            (db.now(),),
        )
        raise RuntimeError("boom")
    assert db.query("SELECT * FROM notes") == []


def test_transaction_commits_and_nests(fresh_db):
    from app import db

    with db.transaction():
        db.execute(
            "INSERT INTO notes (topic, content, author, origin, created_by, created_at)"
            " VALUES ('t', 'c', 'a', 'human', 'a', ?)",
            (db.now(),),
        )
        with db.transaction():  # joins the outer transaction
            assert db.query_row("SELECT COUNT(*) AS n FROM notes")["n"] == 1
    assert db.query_row("SELECT COUNT(*) AS n FROM notes")["n"] == 1


def test_playbook_instantiate_is_atomic(fresh_db, monkeypatch):
    from app.services import engagements, playbooks, schedule

    def explode(**kwargs):
        raise RuntimeError("ritual scheduling failed")

    monkeypatch.setattr(schedule, "schedule_event", explode)
    with pytest.raises(RuntimeError):
        playbooks.instantiate("prototype", "Doomed Launch", lead="ava", actor="tester")
    assert engagements.list_engagements() == []
    assert fresh_db.query("SELECT * FROM milestones") == []
    assert fresh_db.query("SELECT * FROM search_index") == []


def test_create_engagement_adopts_orphan_milestones(fresh_db):
    from app.services import engagements, work
    from app.services.portfolio import _linked_blockers  # noqa: F401 — import sanity

    work.create_milestone(title="Early milestone", project="Comet", actor="tester")
    eng = engagements.create_engagement(name="Comet", actor="tester")
    row = fresh_db.query_row("SELECT engagement_id FROM milestones")
    assert row["engagement_id"] == eng["id"]


def test_ship_it_and_handoff_survive_rename(client, fresh_db):
    from app.services import engagements, handoff, work

    eng = engagements.create_engagement(name="Old Name", actor="tester")
    work.create_milestone(title="M1", project="Old Name", actor="tester")
    fresh_db.execute("UPDATE engagements SET name = 'New Name' WHERE id = ?", (eng["id"],))
    result = handoff.generate_handoff(eng["id"], actor="tester")
    assert "M1" in result["markdown"]  # name join would have lost the milestone


def test_pending_migrations_empty_after_init(fresh_db):
    from app import db

    assert db.pending_migrations() == []
    db.execute("DELETE FROM schema_version WHERE version LIKE '013%'")
    assert db.pending_migrations() == ["013_job_outcomes.sql"]


def test_mcp_main_refuses_pending_migrations(fresh_db, monkeypatch):
    from app import mcp_server

    monkeypatch.setattr(mcp_server.db, "pending_migrations", lambda: ["013_job_outcomes.sql"])
    with pytest.raises(SystemExit):
        mcp_server.main()


def test_run_job_records_outcomes(fresh_db):
    from app.services.jobs import JobSpec, run_job

    run_job(JobSpec("test-ok", lambda: {"n": 1}))
    run_job(JobSpec("test-fail", lambda: 1 / 0))  # must not raise
    ok = fresh_db.query_row("SELECT * FROM job_outcomes WHERE job = 'test-ok'")
    fail = fresh_db.query_row("SELECT * FROM job_outcomes WHERE job = 'test-fail'")
    assert ok["status"] == "ok" and "1" in ok["detail"]
    assert fail["status"] == "error" and "ZeroDivisionError" in fail["detail"]


def test_job_health_flags_stale(fresh_db):
    from app.services.jobs import job_health

    fresh_db.execute(
        "INSERT INTO job_outcomes (job, status, detail, duration_ms, created_at)"
        " VALUES ('daily-digest', 'ok', '', 0, ?)",
        (_iso_hours_ago(100),),
    )
    by_name = {j["job"]: j for j in job_health()}
    assert by_name["daily-digest"]["stale"] is True  # 100h > 2x 24h period
    assert by_name["daily-backup"]["stale"] is False  # never attempted != stale


def test_job_stale_finding_fires(fresh_db, monkeypatch):
    from app import config
    from app.services.insights import run_findings

    monkeypatch.setattr(config, "SCHEDULER_ENABLED", True)
    fresh_db.execute(
        "INSERT INTO job_outcomes (job, status, detail, duration_ms, created_at)"
        " VALUES ('daily-digest', 'ok', '', 0, ?)",
        (_iso_hours_ago(100),),
    )
    result = run_findings(actor="tester")
    stale = [f for f in result["findings"] if f["rule_id"] == "job_stale"]
    assert len(stale) == 1 and stale[0]["subject"] == "daily-digest"


def test_job_stale_finding_suppressed_when_scheduler_off(fresh_db, monkeypatch):
    from app import config
    from app.services.insights import run_findings

    monkeypatch.setattr(config, "SCHEDULER_ENABLED", False)
    fresh_db.execute(
        "INSERT INTO job_outcomes (job, status, detail, duration_ms, created_at)"
        " VALUES ('daily-digest', 'ok', '', 0, ?)",
        (_iso_hours_ago(100),),
    )
    result = run_findings(actor="tester")
    assert not [f for f in result["findings"] if f["rule_id"] == "job_stale"]


def test_health_endpoint_reports_jobs(client):
    body = client.get("/health").json()
    assert {j["job"] for j in body["jobs"]} >= {"daily-digest", "daily-backup", "findings"}


def test_retention_prune(fresh_db):
    from app.services.retention import prune

    old = _iso_hours_ago(24 * 400)
    fresh_db.execute(
        "INSERT INTO forecast_snapshots (day, milestone_id, due_date, forecast_date, created_at)"
        " VALUES ('2025-01-01', 1, '2025-01-01', '2025-01-01', ?)",
        (old,),
    )
    fresh_db.execute(
        "INSERT INTO notifications (user, message, read_at, created_at) VALUES ('a', 'm', ?, ?)",
        (old, old),
    )
    fresh_db.execute(
        "INSERT INTO notifications (user, message, created_at) VALUES ('a', 'unread', ?)",
        (old,),
    )
    fresh_db.execute(
        "INSERT INTO job_runs (job, run_key, created_at) VALUES ('digest', '2025-01-01', ?)",
        (old,),
    )
    removed = prune(actor="tester")
    assert removed["forecast_snapshots"] == 1
    assert removed["notifications"] == 1  # unread rows are never pruned
    assert removed["job_runs"] == 1
    assert prune(actor="tester") == {"skipped": "already pruned this month"}
    assert fresh_db.query_row("SELECT COUNT(*) AS n FROM notifications")["n"] == 1


def test_record_chat_usage(fresh_db):
    from app.services.usage import record_chat_usage

    record_chat_usage("thread-1", "chief-of-staff", "mock", 10, 20, cycles=2, latency_ms=5)
    row = fresh_db.query_row("SELECT * FROM usage_log")
    assert row["input_tokens"] == 10 and row["output_tokens"] == 20


def test_narrator_hook_used_and_fail_safe(fresh_db):
    from app.services import digest

    try:
        digest.set_narrator(lambda md: f"NARRATED\n{md}")
        assert digest.publish_digest(actor="tester")["markdown"].startswith("NARRATED")

        def explode(md):
            raise RuntimeError("model down")

        digest.set_narrator(explode)
        out = digest.publish_digest(actor="tester", force=True)
        assert out["markdown"].startswith("# Daily digest")  # falls back to raw
    finally:
        digest.set_narrator(None)


def test_slas_constants_wired():
    from app.services import digest, insights, portfolio, slas

    assert portfolio.STALE_WIP_DAYS == slas.STALE_WIP_DAYS
    assert insights.AGING_WIP_DAYS == slas.AGING_WIP_DAYS
    assert digest.DIGEST_STALLED_DAYS == slas.DIGEST_STALLED_DAYS
