"""An unattended fleet that failed must not report a healthy job.

`run_job` recorded 'ok' for any return value at all, and `_outcome_detail`
reduces a list to its length. So a night where every allowlisted agent failed
to build returned an ordinary dict, `job_outcomes` said ok, /health showed the
`agent-run` job green, and the reasons lived only in the process log.
"""

from app.services import jobs


def _spec(name: str, fn):
    return jobs.JobSpec(name=name, fn=fn)


def test_a_job_that_declares_a_fault_is_not_recorded_ok(fresh_db):
    jobs.run_job(_spec("probe-partial", lambda: {"status": "partial", "faults": "scout: boom"}))
    row = fresh_db.query_one("SELECT status, detail FROM job_outcomes WHERE job = 'probe-partial'")
    assert row["status"] == "error"
    assert "scout" in row["detail"]


def test_an_ordinary_result_is_still_ok(fresh_db):
    jobs.run_job(_spec("probe-ok", lambda: {"swept": 2}))
    assert (
        fresh_db.query_one("SELECT status FROM job_outcomes WHERE job = 'probe-ok'")["status"]
        == "ok"
    )


def test_a_row_carrying_a_status_column_cannot_forge_a_state(fresh_db):
    """Only our own literals are honored. A job that returns a database row
    with a `status` column — a task, a blocker — must not be able to mark
    itself failed, or a sweep's own data would drive the scheduler's health."""
    jobs.run_job(_spec("probe-row", lambda: {"status": "blocked"}))
    assert (
        fresh_db.query_one("SELECT status FROM job_outcomes WHERE job = 'probe-row'")["status"]
        == "ok"
    )


def test_a_quiet_night_is_not_a_fault(fresh_db, monkeypatch):
    """Nothing delegated, already ran today, mock provider, a budget ceiling
    doing its job — the fleet is healthy and the scheduler must say so, or
    every quiet night reads as an incident and the signal stops being read."""
    from conftest import _delegated_task

    from app import config
    from app.services import agent_runner

    _delegated_task(fresh_db)
    monkeypatch.setattr(config, "AGENT_RUNNER", ["scout"])
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "mock")
    out = agent_runner.run()
    assert out["status"] == "ok"
    assert out["runs"][0]["fault"] is False


def test_a_fleet_that_could_not_build_reports_error(fresh_db, monkeypatch):
    from conftest import _delegated_task

    from app import config
    from app.services import agent_runner

    # a REAL delegated task, so sweep() and _due() read the rows they read in
    # production — a faked inbox row has no sponsor and the sweep raises on it
    _delegated_task(fresh_db)
    monkeypatch.setattr(config, "AGENT_RUNNER", ["scout"])
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(
        "app.agents.team_agent.build_agent",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no socket")),
    )
    out = agent_runner.run()
    assert out["status"] == "error"
    assert "scout" in out["faults"] and "no socket" in out["faults"]
