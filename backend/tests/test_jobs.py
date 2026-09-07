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


def test_job_health_reports_the_latest_failure_immediately(fresh_db):
    from app.services.jobs import job_health

    fresh_db.execute(
        "INSERT INTO job_outcomes (job, status, detail, duration_ms, created_at)"
        " VALUES ('daily-digest', 'ok', '', 0, ?),"
        " ('daily-digest', 'error', 'fault', 0, ?)",
        (_iso_hours_ago(1), _iso_hours_ago(0)),
    )
    status = {item["job"]: item for item in job_health()}["daily-digest"]
    assert status["stale"] is False
    assert status["last_status"] == "error"
    assert status["last_attempt"] is not None
    assert status["last_success"] is not None


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


def test_one_process_runs_each_firing_and_a_lapsed_claim_is_retaken(fresh_db):
    from app.services.jobs import JobSpec, run_job

    runs = []
    spec = JobSpec("test-fire", lambda: runs.append(1), {"trigger": "cron", "hour": 3}, 24)
    run_job(spec)
    run_job(spec)  # the same firing, claimed by the first call
    assert runs == [1]
    claim = fresh_db.query_row(
        "SELECT lease_owner, lease_until FROM job_runs WHERE job = 'fire:test-fire'"
    )
    assert claim == {"lease_owner": "", "lease_until": ""}  # settled, so permanent
    # a process that died mid-run left a lapsed lease, which the next run retakes
    fresh_db.execute(
        "UPDATE job_runs SET lease_owner = 'dead', lease_until = '2000-01-01T00:00:00+00:00'"
        " WHERE job = 'fire:test-fire'"
    )
    run_job(spec)
    assert runs == [1, 1]


def test_a_failed_firing_releases_its_claim_for_a_retry(fresh_db):
    from app.services.jobs import JobSpec, run_job

    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("first attempt fails")
        return {"status": "partial"} if len(attempts) == 2 else {"n": 1}

    spec = JobSpec("test-retry", flaky, {"trigger": "cron", "hour": 3}, 24)
    run_job(spec)  # raises: released
    run_job(spec)  # partial: released
    run_job(spec)  # ok: settled
    run_job(spec)  # settled, so skipped
    assert len(attempts) == 3


def test_a_permanent_claim_is_never_retaken(fresh_db):
    from app import db

    assert db.claim_job("receipt", "once")
    assert not db.claim_job("receipt", "once")
    assert not db.claim_job("receipt", "once", lease_seconds=1)
    assert db.claim_job("leased", "run", lease_seconds=60)
    assert not db.claim_job("leased", "run", lease_seconds=60)  # live lease
    assert db.settle_job("leased", "run")
    assert not db.settle_job("leased", "run")  # already permanent


def test_fire_key_names_the_latest_scheduled_time(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app import config
    from app.services.jobs import JobSpec, fire_key

    monkeypatch.setattr(config, "TZ_NAME", "America/New_York")
    zone = ZoneInfo("America/New_York")
    flush = JobSpec("flush", lambda: None, {"trigger": "cron", "hour": "7,15", "minute": 5}, 12)
    at = lambda hour: datetime(2026, 9, 7, hour, 0, tzinfo=zone)  # noqa: E731
    assert fire_key(flush, at(14)) == "2026-09-07T07:05-04:00"
    assert fire_key(flush, at(16)) == "2026-09-07T15:05-04:00"
    assert fire_key(flush, at(6)) == "2026-09-06T15:05-04:00"
    hourly = JobSpec("sweep", lambda: None, {"trigger": "interval", "hours": 1}, 1)
    assert fire_key(hourly, at(14)) == fire_key(hourly, at(14).replace(minute=59))
    assert fire_key(hourly, at(14)) != fire_key(hourly, at(15))
