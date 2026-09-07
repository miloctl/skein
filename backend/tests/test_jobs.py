"""The JOBS registry: per-run outcomes, staleness on /health, and the job_stale findings rule."""

import threading
from datetime import UTC, datetime, timedelta

import pytest


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
    spec = JobSpec(
        "test-fire", lambda: runs.append(1), {"trigger": "cron", "hour": 3}, 24, retry_safe=True
    )
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

    spec = JobSpec("test-retry", flaky, {"trigger": "cron", "hour": 3}, 24, retry_safe=True)
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
    token = db.claim_job("leased", "run", lease_seconds=60)
    assert token
    assert not db.claim_job("leased", "run", lease_seconds=60)  # live lease
    assert db.settle_job("leased", "run", token)
    assert not db.settle_job("leased", "run", token)  # already permanent


@pytest.mark.parametrize("retry_safe", [False, True])
def test_job_heartbeat_renews_only_its_live_acquisitions(fresh_db, retry_safe):
    from app.services import jobs, leases

    renewed = []
    jobs.run_job(
        jobs.JobSpec("live-work", lambda: renewed.append(leases.renew()), retry_safe=retry_safe)
    )
    assert renewed == [2 if retry_safe else 1]
    assert leases.renew() == 0
    assert fresh_db.query("SELECT 1 FROM job_runs WHERE job = 'active:live-work'") == []


def test_an_unknown_job_failure_does_not_repeat_committed_effects(fresh_db):
    from app.services import collab, jobs

    def interrupted():
        collab.save_note("Committed effect", "The external job stopped later", actor="system")
        raise RuntimeError("after commit")

    spec = jobs.JobSpec("unsafe-write", interrupted)
    jobs.run_job(spec)
    jobs.run_job(spec)
    assert fresh_db.query_row("SELECT COUNT(*) AS n FROM notes")["n"] == 1
    outcome = fresh_db.query_row(
        "SELECT status, detail FROM job_outcomes WHERE job = 'unsafe-write'"
    )
    assert outcome["status"] == "error" and "unknown" in outcome["detail"].lower()


def test_an_unsafe_job_records_unknown_completion_before_its_body(fresh_db):
    from app.services import jobs

    def stopped():
        raise SystemExit(1)

    with pytest.raises(SystemExit):
        jobs.run_job(jobs.JobSpec("interrupted-body", stopped))
    claim = fresh_db.query_row(
        "SELECT lease_until FROM job_runs WHERE job = 'fire:interrupted-body'"
    )
    assert claim["lease_until"] == ""
    outcome = fresh_db.query_row(
        "SELECT status, detail FROM job_outcomes WHERE job = 'interrupted-body'"
    )
    assert outcome["status"] == "error" and "unknown" in outcome["detail"].lower()
    jobs.run_job(jobs.JobSpec("interrupted-body", lambda: pytest.fail("unsafe replay")))


@pytest.mark.parametrize("retry_safe", [False, True])
def test_a_successful_body_keeps_its_receipt_when_outcome_logging_fails(
    fresh_db, monkeypatch, retry_safe
):
    from app.services import jobs

    runs = []

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("outcome unavailable")

    monkeypatch.setattr(jobs, "record_outcome", unavailable)
    spec = jobs.JobSpec("finished-body", lambda: runs.append(1), retry_safe=retry_safe)
    jobs.run_job(spec)
    jobs.run_job(spec)
    assert runs == [1]


@pytest.mark.parametrize("failure", ["claim", "key"])
def test_job_acquisition_failure_does_not_escape_startup(fresh_db, monkeypatch, failure):
    from psycopg.errors import LockNotAvailable

    from app import db
    from app.services import jobs

    def unavailable(*_args, **_kwargs):
        raise LockNotAvailable("held")

    if failure == "claim":
        monkeypatch.setattr(db, "claim_job", unavailable)
    else:
        monkeypatch.setattr(jobs, "fire_key", unavailable)
    runs = []
    jobs.run_job(jobs.JobSpec("acquisition-fault", lambda: runs.append(1)))
    assert runs == []
    outcome = db.query_row(
        "SELECT status, detail FROM job_outcomes WHERE job = 'acquisition-fault'"
    )
    assert outcome["status"] == "error" and "LockNotAvailable" in outcome["detail"]


def test_composed_jobs_keep_core_retry_safety_and_the_app_timezone(monkeypatch):
    from dataclasses import replace

    from app import config, main
    from app.services.jobs import JOBS, fire_key

    monkeypatch.setattr(config, "TZ_NAME", "UTC")
    settings = replace(main.app.state.skein_settings, timezone="America/New_York")
    composed = {
        spec.name: spec for spec in main._job_specs(main.app.state.skein_registry, settings)
    }
    for core in JOBS:
        assert composed[core.name].retry_safe == core.retry_safe
    assert fire_key(composed["daily-digest"], datetime(2026, 9, 7, 12, tzinfo=UTC)) == (
        "2026-09-07T07:00-04:00"
    )


def test_an_explicit_cron_timezone_overrides_the_app_timezone():
    from app.services.jobs import JobSpec, fire_key

    spec = JobSpec(
        "own-zone",
        lambda: None,
        {"trigger": "cron", "hour": 7, "timezone": "Europe/Helsinki"},
        timezone="America/New_York",
    )
    assert fire_key(spec, datetime(2026, 9, 7, 12, tzinfo=UTC)) == "2026-09-07T07:00+03:00"


def test_fire_key_advances_through_the_repeated_dst_hour(monkeypatch):
    from apscheduler.triggers.cron import CronTrigger

    from app import config
    from app.services.jobs import JOBS, fire_key

    monkeypatch.setattr(config, "TZ_NAME", "Europe/Helsinki")
    original = CronTrigger.get_next_fire_time
    cursors = []

    def next_fire(trigger, previous, cursor):
        assert not cursors or cursor.timestamp() > cursors[-1], "cron cursor went backward"
        cursors.append(cursor.timestamp())
        return original(trigger, previous, cursor)

    monkeypatch.setattr(CronTrigger, "get_next_fire_time", next_fire)
    spec = next(job for job in JOBS if job.name == "activity-verify")
    assert fire_key(spec, datetime(2026, 10, 25, 2, 0, tzinfo=UTC)) == "2026-10-25T03:30+02:00"


def test_reconciliation_does_not_overlap_the_next_period(fresh_db, monkeypatch):
    from app import config, db
    from app.services import collab, jobs, search

    collab.save_note("Boundary probe", "One indexed note", actor="system")
    assert search.missing_embeddings_count() == 1
    started, release = threading.Event(), threading.Event()
    calls = []

    class Clock:
        @staticmethod
        def now(_zone):
            if threading.current_thread().name == "prior-window":
                return datetime(2026, 9, 7, 13, 59, 59, tzinfo=UTC)
            return datetime(2026, 9, 7, 14, 0, 0, tzinfo=UTC)

    def embed(text):
        calls.append(text)
        if threading.current_thread().name == "prior-window":
            started.set()
            assert release.wait(10)
        return [0.5]

    monkeypatch.setattr(jobs, "datetime", Clock)
    monkeypatch.setattr(config, "EMBED_READY", True)
    monkeypatch.setattr(search, "_embed", embed)
    spec = next(job for job in jobs.JOBS if job.name == "embed-reconcile")
    first = threading.Thread(name="prior-window", target=jobs.run_job, args=(spec,))
    first.start()
    try:
        assert started.wait(5)
        jobs.run_job(spec)
        assert calls == ["Boundary probe\nOne indexed note"]
    finally:
        release.set()
        first.join(10)
    assert not first.is_alive()
    assert db.query_row("SELECT COUNT(*) AS n FROM embeddings")["n"] == 1
    jobs.run_job(spec)
    assert len(calls) == 1


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
