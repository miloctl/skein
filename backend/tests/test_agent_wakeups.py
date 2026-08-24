"""Durable, coalesced wake requests for explicitly delegated agent work."""

import threading
import time


def _mint(db, name, kind="human"):
    db.execute(
        "INSERT INTO users (name, kind, active, created_at) VALUES (?, ?, 1, ?)",
        (name, kind, db.now()),
    )


def test_enqueue_coalesces_pending_and_running_work(fresh_db):
    from app.services import agent_wakeups

    _mint(fresh_db, "sponsor")
    _mint(fresh_db, "backend-architect", "agent")
    with fresh_db.transaction():
        agent_wakeups.enqueue("backend-architect", 80, requested_by="sponsor")
        agent_wakeups.enqueue("backend-architect", 81, requested_by="sponsor")
    row = fresh_db.query_row("SELECT * FROM agent_wakeups WHERE agent = ?", ("backend-architect",))
    assert row["status"] == "pending"
    assert row["trigger_task_id"] == 81
    assert row["rerun_requested"] == 0

    fresh_db.execute(
        "UPDATE agent_wakeups SET status = 'running', started_at = ?, attempts = 1 WHERE agent = ?",
        (fresh_db.now(), "backend-architect"),
    )
    with fresh_db.transaction():
        agent_wakeups.enqueue("backend-architect", 82, requested_by="sponsor")
    row = fresh_db.query_row("SELECT * FROM agent_wakeups WHERE agent = ?", ("backend-architect",))
    assert row["status"] == "running"
    assert row["trigger_task_id"] == 82
    assert row["rerun_requested"] == 1


def test_worker_claim_and_finish_requeue_one_follow_up(fresh_db):
    from app.services import agent_wakeups

    _mint(fresh_db, "sponsor")
    _mint(fresh_db, "backend-architect", "agent")
    with fresh_db.transaction():
        agent_wakeups.enqueue("backend-architect", 80, requested_by="sponsor")
    claim = agent_wakeups.claim_next()
    assert claim and claim["status"] == "running"
    assert claim["thread_id"] == "wake:backend-architect:1"

    fresh_db.execute(
        "UPDATE agent_wakeups SET rerun_requested = 1 WHERE agent = ?",
        ("backend-architect",),
    )
    agent_wakeups.finish(claim, {"ran": True, "fault": False, "reason": ""})
    row = fresh_db.query_row("SELECT * FROM agent_wakeups WHERE agent = ?", ("backend-architect",))
    assert row["status"] == "pending"
    assert row["rerun_requested"] == 0

    second = agent_wakeups.claim_next()
    assert second and second["attempts"] == 2
    agent_wakeups.finish(second, {"ran": True, "fault": False, "reason": ""})
    assert agent_wakeups.status("backend-architect")["status"] == "completed"


def test_drain_runs_one_explicit_bounded_turn(fresh_db, monkeypatch):
    from app.services import agent_runner, agent_wakeups

    _mint(fresh_db, "sponsor")
    _mint(fresh_db, "backend-architect", "agent")
    with fresh_db.transaction():
        agent_wakeups.enqueue("backend-architect", 80, requested_by="sponsor")
    calls = []

    def run_one(agent, **kwargs):
        calls.append((agent, kwargs))
        return {"agent": agent, "ran": True, "fault": False, "reason": ""}

    registry = type("Registry", (), {"policy_engine": object()})()
    monkeypatch.setattr(agent_wakeups, "_extensions", registry)
    monkeypatch.setattr(agent_runner, "run_one", run_one)
    agent_wakeups._drain()

    assert calls[0][0] == "backend-architect"
    assert calls[0][1]["explicit_key"] == "1"
    assert calls[0][1]["allowed_tools"] == agent_wakeups.WAKE_TOOLS
    # a wake worker is a bare thread with no ambient policy engine, so the
    # composed root must travel explicitly or every wake fails before the
    # provider is reached
    assert calls[0][1]["extensions"] is registry
    assert agent_wakeups.status("backend-architect")["status"] == "completed"


def test_commit_starts_the_worker_when_background_jobs_are_enabled(fresh_db, monkeypatch):
    from app import config
    from app.services import agent_runner, agent_wakeups

    _mint(fresh_db, "sponsor")
    _mint(fresh_db, "backend-architect", "agent")
    called = threading.Event()

    def run_one(agent, **_kwargs):
        called.set()
        return {"agent": agent, "ran": True, "fault": False, "reason": ""}

    monkeypatch.setattr(config, "SCHEDULER_ENABLED", True)
    monkeypatch.setattr(agent_runner, "run_one", run_one)
    with fresh_db.transaction():
        agent_wakeups.enqueue("backend-architect", 80, requested_by="sponsor")

    assert called.wait(timeout=3)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if agent_wakeups.status("backend-architect")["status"] == "completed":
            break
        time.sleep(0.01)
    assert agent_wakeups.status("backend-architect")["status"] == "completed"


def test_startup_never_retries_unknown_completion(fresh_db):
    from app.services import agent_wakeups

    _mint(fresh_db, "sponsor")
    _mint(fresh_db, "backend-architect", "agent")
    with fresh_db.transaction():
        agent_wakeups.enqueue("backend-architect", 80, requested_by="sponsor")
    claim = agent_wakeups.claim_next()
    assert claim

    assert agent_wakeups.recover_startup() == 1
    row = agent_wakeups.status("backend-architect")
    assert row["status"] == "completion_unknown"
    assert row["reason"] == "process_restarted"
    assert agent_wakeups.claim_next() is None

    # a NEW human delegation is a new explicit trigger, so it re-arms the
    # agent instead of leaving it unwakeable forever
    with fresh_db.transaction():
        agent_wakeups.enqueue("backend-architect", 81, requested_by="sponsor")
    assert agent_wakeups.status("backend-architect")["status"] == "pending"


def test_human_delegation_queues_but_agent_delegation_does_not(fresh_db):
    from app.services import agent_wakeups, delegation, work

    for name, kind in (
        ("sponsor", "human"),
        ("planner-agent", "agent"),
        ("scout-agent", "agent"),
    ):
        _mint(fresh_db, name, kind)

    human_task = work.create_task(title="human handoff", actor="sponsor")
    delegation.delegate_task(
        human_task["id"],
        "scout-agent",
        "sponsor",
        actor="sponsor",
        origin="human",
    )
    assert agent_wakeups.status("scout-agent")["status"] == "pending"

    fresh_db.execute("DELETE FROM agent_wakeups WHERE agent = ?", ("scout-agent",))
    agent_task = work.create_task(title="agent fanout", actor="planner-agent", origin="agent")
    delegation.delegate_task(
        agent_task["id"],
        "scout-agent",
        "sponsor",
        actor="planner-agent",
        origin="agent",
    )
    assert agent_wakeups.status("scout-agent") is None


def test_readable_task_projection_carries_safe_wake_status(client, fresh_db):
    from app.services import delegation, work

    _mint(fresh_db, "tester")
    _mint(fresh_db, "scout-agent", "agent")
    task = work.create_task(title="wake projection", actor="tester")
    delegation.delegate_task(
        task["id"],
        "scout-agent",
        "tester",
        actor="tester",
        origin="human",
    )

    projected = client.get(f"/api/tasks/{task['id']}").json()["agent_wakeup"]
    assert projected["status"] == "pending"
    assert projected["automation_enabled"] is False
    assert "requested_by" not in projected
    assert "thread_id" not in projected

    # the wake row is keyed per agent, so a task that did not trigger the
    # current wake must not read another delegation's timing and outcome
    earlier = work.create_task(title="earlier delegation", actor="tester")
    fresh_db.execute(
        "UPDATE tasks SET delegated_agent = 'scout-agent', sponsor = 'tester' WHERE id = ?",
        (earlier["id"],),
    )
    assert "agent_wakeup" not in client.get(f"/api/tasks/{earlier['id']}").json()


def test_daily_wake_cap_refuses_without_a_model_call(fresh_db, monkeypatch):
    from app import config
    from app.services import agent_runner, agent_wakeups, usage

    _mint(fresh_db, "sponsor")
    _mint(fresh_db, "backend-architect", "agent")
    monkeypatch.setattr(config, "AGENT_WAKES_PER_DAY", 1)
    usage.record_chat_usage("wake:other-agent:1", "other-agent", "m", 10, 10)
    monkeypatch.setattr(
        agent_runner,
        "run_one",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("capped wake reached the runner")),
    )
    with fresh_db.transaction():
        agent_wakeups.enqueue("backend-architect", 80, requested_by="sponsor")
    agent_wakeups._drain()

    row = agent_wakeups.status("backend-architect")
    assert row["status"] == "refused"
    assert row["reason"] == "wake_cap"


def test_wake_session_ids_cannot_be_claimed_or_forged_from_chat(fresh_db, monkeypatch):
    import pytest

    from app import config
    from app.services import agent_wakeups, chat_threads, usage

    _mint(fresh_db, "sponsor")
    _mint(fresh_db, "backend-architect", "agent")

    # the runner's session id carries ':', which the chat thread-id charset
    # refuses — so no caller can claim it and restore the agent's unattended
    # conversation
    with pytest.raises(ValueError):
        chat_threads.claim_thread("wake:backend-architect:1", "mallory")

    # and a user-NAMED 'wake-...' chat thread must not spend the workspace's
    # daily wake allowance
    monkeypatch.setattr(config, "AGENT_WAKES_PER_DAY", 1)
    usage.record_chat_usage("wake-me", "agent", "m", 10, 10)
    assert agent_wakeups._wake_cap_reached() is False


def test_merging_two_agents_folds_their_wake_rows(fresh_db):
    from app.services import agent_wakeups
    from app.services.users import rename_user

    _mint(fresh_db, "sponsor")
    _mint(fresh_db, "old-agent", "agent")
    _mint(fresh_db, "kept-agent", "agent")
    with fresh_db.transaction():
        agent_wakeups.enqueue("old-agent", 80, requested_by="sponsor")
        agent_wakeups.enqueue("kept-agent", 81, requested_by="sponsor")

    rename_user("old-agent", "kept-agent", actor="sponsor", expected_merge=True)
    assert agent_wakeups.status("old-agent") is None
    assert agent_wakeups.status("kept-agent", task_id=81) is not None


def test_a_fault_still_honors_a_delegation_queued_mid_run(fresh_db):
    from app.services import agent_wakeups

    _mint(fresh_db, "sponsor")
    _mint(fresh_db, "backend-architect", "agent")
    with fresh_db.transaction():
        agent_wakeups.enqueue("backend-architect", 80, requested_by="sponsor")
    claim = agent_wakeups.claim_next()
    fresh_db.execute(
        "UPDATE agent_wakeups SET rerun_requested = 1 WHERE agent = ?",
        ("backend-architect",),
    )
    agent_wakeups.finish(claim, {"ran": False, "fault": True, "reason": "could not build"})
    assert agent_wakeups.status("backend-architect")["status"] == "pending"


def test_crash_recovery_keeps_a_delegation_queued_mid_run(fresh_db):
    from app.services import agent_wakeups

    _mint(fresh_db, "sponsor")
    _mint(fresh_db, "backend-architect", "agent")
    with fresh_db.transaction():
        agent_wakeups.enqueue("backend-architect", 80, requested_by="sponsor")
    agent_wakeups.claim_next()
    fresh_db.execute(
        "UPDATE agent_wakeups SET rerun_requested = 1 WHERE agent = ?",
        ("backend-architect",),
    )

    assert agent_wakeups.recover_startup() == 1
    assert agent_wakeups.status("backend-architect")["status"] == "pending"


def test_wake_status_exposes_only_safe_fields(fresh_db):
    from app.services import agent_wakeups

    _mint(fresh_db, "sponsor")
    _mint(fresh_db, "backend-architect", "agent")
    with fresh_db.transaction():
        agent_wakeups.enqueue("backend-architect", 80, requested_by="sponsor")
    projected = agent_wakeups.status("backend-architect")
    assert set(projected) == {
        "status",
        "requested_at",
        "started_at",
        "finished_at",
        "reason",
        "automation_enabled",
    }
    assert "sponsor" not in str(projected)
