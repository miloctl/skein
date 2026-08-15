"""The JOBS registry: per-run outcomes, staleness on /health, and the job_stale findings rule."""

from datetime import UTC, datetime, timedelta


def _iso_hours_ago(hours: int) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat(timespec="seconds")


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
    body = client.get("/api/health").json()
    assert {j["job"] for j in body["jobs"]} >= {"daily-digest", "daily-backup", "findings"}
