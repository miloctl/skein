"""Execution ownership lasts for one live acquisition, not a process lifetime."""

import pytest


def _mint(db, name, kind="human"):
    db.execute(
        "INSERT INTO users (name, kind, active, created_at) VALUES (?, ?, 1, ?)",
        (name, kind, db.now()),
    )


def test_a_claim_without_a_live_worker_is_not_renewed(fresh_db):
    from app.services import agent_wakeups, leases

    _mint(fresh_db, "sponsor")
    _mint(fresh_db, "mine", "agent")
    with fresh_db.transaction():
        agent_wakeups.enqueue("mine", 80, requested_by="sponsor")
    assert agent_wakeups.claim_next()
    fresh_db.execute("UPDATE agent_wakeups SET lease_until = '2000-01-01T00:00:00+00:00'")
    assert leases.renew() == 0
    assert agent_wakeups.reclaim_expired() == 1
    assert fresh_db.query_row("SELECT status FROM agent_wakeups") == {
        "status": "completion_unknown"
    }


def test_same_process_reacquisition_has_a_distinct_fence(fresh_db):
    first = fresh_db.claim_job("agent-turn:mine", "turn", lease_seconds=90)
    assert first
    fresh_db.execute("UPDATE job_runs SET lease_until = '2000-01-01T00:00:00+00:00'")
    second = fresh_db.claim_job("agent-turn:mine", "turn", lease_seconds=90)
    assert second and second != first
    assert not fresh_db.release_job("agent-turn:mine", "turn", first)
    assert not fresh_db.claim_job("agent-turn:mine", "turn", lease_seconds=90)
    assert fresh_db.release_job("agent-turn:mine", "turn", second)


def test_live_registration_renews_only_before_expiry(fresh_db):
    from app.services import leases

    token = fresh_db.claim_job("live", "turn", lease_seconds=90)
    assert token
    with leases.held(token):
        assert leases.renew() == 1
        fresh_db.execute("UPDATE job_runs SET lease_until = '2000-01-01T00:00:00+00:00'")
        assert leases.renew() == 0
    assert leases.renew() == 0


def test_fence_covers_ungated_standalone_writes_and_nested_transactions(fresh_db):
    from app.services import leases

    first = fresh_db.claim_job("parent", "turn", lease_seconds=90)
    second = fresh_db.claim_job("child", "turn", lease_seconds=90)
    assert first and second
    with leases.held(first), leases.held(second):
        fresh_db.execute(
            "UPDATE job_runs SET lease_until = '2000-01-01T00:00:00+00:00' WHERE job = 'parent'"
        )
        with pytest.raises(leases.LeaseLost):
            fresh_db.execute(
                "INSERT INTO users (name, created_at) VALUES ('stale', ?)", (fresh_db.now(),)
            )
        with pytest.raises(leases.LeaseLost), fresh_db.transaction(), fresh_db.transaction():
            _mint(fresh_db, "nested")
    assert fresh_db.query("SELECT name FROM users") == []


def test_lifespan_owns_the_heartbeat(fresh_db):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import leases

    with TestClient(app):
        assert leases._thread is not None and leases._thread.is_alive()
    assert leases._thread is None


def test_heartbeat_precedes_startup_catchup_claims(fresh_db, monkeypatch):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from app import main
    from app.services import leases
    from app.services.jobs import JobSpec

    seen = []

    def catch_up():
        seen.append(bool(leases._thread and leases._thread.is_alive()))
        return {"renewed": leases.renew()}

    spec = JobSpec(
        "startup-fence",
        catch_up,
        {"trigger": "interval", "hours": 24},
        catch_up=True,
        retry_safe=True,
    )
    monkeypatch.setattr(main, "_job_specs", lambda *_: (spec,))
    monkeypatch.setattr(
        main.app.state,
        "skein_settings",
        replace(main.app.state.skein_settings, scheduler_enabled=True),
    )
    with TestClient(main.app):
        assert seen == [True]
    assert leases._thread is None


def test_foreign_context_finalization_releases_live_registration(fresh_db):
    from contextvars import Context

    from app.services import leases

    token = fresh_db.claim_job("abandoned-stream", "turn", lease_seconds=90)
    assert token
    original = Context()
    scope = leases.held(token)
    original.run(scope.__enter__)
    assert leases.renew() == 1
    scope.__exit__(None, None, None)
    assert not leases.active()
    assert leases.renew() == 0


def test_a_transaction_that_outlives_its_lease_rolls_back(fresh_db):
    from app.services import leases

    token = fresh_db.claim_job("slow-write", "turn", lease_seconds=1)
    assert token
    with leases.held(token), pytest.raises(leases.LeaseLost), fresh_db.transaction():
        _mint(fresh_db, "uncommitted")
        fresh_db.query("SELECT pg_sleep(1.1)")
    assert fresh_db.query("SELECT name FROM users") == []


def test_shutdown_revokes_copied_context_after_registration_ends(fresh_db):
    from contextvars import copy_context

    from app.services import leases

    token = fresh_db.claim_job("abandoned", "turn", lease_seconds=90)
    assert token
    with leases.held(token):
        abandoned = copy_context()
    assert leases.renew() == 0
    leases.stop()
    with pytest.raises(leases.LeaseLost):
        abandoned.run(_mint, fresh_db, "late")
    with pytest.raises(leases.LeaseLost):
        abandoned.run(fresh_db.log_activity, "agent", "agent_run", "late")
    assert fresh_db.query("SELECT name FROM users") == []
    assert fresh_db.query("SELECT id FROM activity") == []


def test_shutdown_prevents_commit_when_database_revocation_fails(fresh_db, monkeypatch):
    from app.services import leases

    token = fresh_db.claim_job("late-commit", "turn", lease_seconds=90)
    assert token
    execute = fresh_db.execute

    def fail_revocation(sql, params=()):
        if sql.startswith("UPDATE agent_wakeups SET lease_until"):
            raise RuntimeError("database unavailable")
        return execute(sql, params)

    monkeypatch.setattr(fresh_db, "execute", fail_revocation)
    with leases.held(token), pytest.raises(leases.LeaseLost), fresh_db.transaction():
        _mint(fresh_db, "uncommitted")
        with pytest.raises(RuntimeError, match="database unavailable"):
            leases.stop()
    assert fresh_db.query("SELECT name FROM users") == []
    with pytest.raises(leases.LeaseLost), leases.held(token):
        pass


def test_slow_maintenance_does_not_block_the_heartbeat(fresh_db, monkeypatch):
    import threading

    from app.services import leases, mcp_servers

    entered, release, renewed = threading.Event(), threading.Event(), threading.Event()
    original_renew = leases.renew

    def cleanup():
        entered.set()
        assert release.wait(3)

    def renew():
        result = original_renew()
        if entered.is_set() and threading.current_thread().name == "execution-leases":
            renewed.set()
        return result

    monkeypatch.setattr(leases, "HEARTBEAT_SECONDS", 0.02)
    monkeypatch.setattr(leases, "renew", renew)
    monkeypatch.setattr(mcp_servers, "prune_oauth_flows", cleanup)
    token = fresh_db.claim_job("healthy", "turn", lease_seconds=90)
    assert token
    with leases.held(token):
        leases.start(wake_kicks=False, recover=False)
        try:
            assert entered.wait(2)
            assert renewed.wait(2)
        finally:
            release.set()
            leases.stop()


def test_concurrent_command_bridges_preserve_every_exchange(fresh_db, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from app import config
    from app.agents import session_log
    from app.agents.session_store import DatabaseSessionRepository

    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    barrier = threading.Barrier(8)

    def append(index):
        barrier.wait(3)
        session_log.log_exchange("command-session", f"question {index}", f"answer {index}")

    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(append, range(8)))
    messages = DatabaseSessionRepository().list_messages("command-session", "default")
    assert len(messages) == 16


def test_solo_model_session_has_one_acquisition_across_callers(fresh_db):
    from app.services.chat_threads import finish_model_turn, start_model_turn

    first = start_model_turn("same-session")
    with pytest.raises(fresh_db.ResourceBusy):
        start_model_turn("same-session")
    fresh_db.execute("UPDATE job_runs SET lease_until = '2000-01-01T00:00:00+00:00'")
    second = start_model_turn("same-session")
    finish_model_turn("same-session", first)
    with pytest.raises(fresh_db.ResourceBusy):
        start_model_turn("same-session")
    finish_model_turn("same-session", second)
