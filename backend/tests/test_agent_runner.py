"""The unattended runner and its ceilings.

Nothing here tests that an agent does GOOD work — that is the model's job and
the review gate's. These pin the bounds, because the whole point of the
feature is a turn no human is watching."""

from typing import ClassVar

from app import config, db
from app.services import agent_runner, delegation, usage, work


def _delegated(agent="research-agent", sponsor="tester"):
    from app.services.users import ensure_user

    ensure_user(agent, kind="agent")
    ensure_user(sponsor, kind="human")
    task = work.create_task("chase the vendor", actor=sponsor)
    delegation.delegate_task(task["id"], agent=agent, sponsor=sponsor, actor=sponsor)
    return task["id"]


def test_off_by_default(fresh_db):
    """An operator turns this on deliberately. Shipping it enabled would wake
    agents on every deployment that upgraded."""
    assert config.AGENT_RUNNER == []
    assert agent_runner.run()["ran"] == 0


def test_an_agent_outside_the_allowlist_never_runs(fresh_db, monkeypatch):
    """The allowlist is the fleet. Discovered from open work instead, the
    runner grows one agent every time somebody delegates to a new name."""
    _delegated("research-agent")
    monkeypatch.setattr(config, "AGENT_RUNNER", ["planner-agent"])
    out = agent_runner.run_one("research-agent")
    assert out["ran"] is False
    assert "SKEIN_AGENT_RUNNER" in out["reason"]


def test_the_sweep_runs_with_no_model_at_all(fresh_db, monkeypatch):
    """Keyless-first: the deterministic half is the whole feature on mock,
    and it must not need a provider to tell a sponsor their work is quiet."""
    task_id = _delegated("research-agent")
    monkeypatch.setattr(config, "AGENT_RUNNER", ["research-agent"])
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "mock")

    out = agent_runner.run()
    assert out["sweep"]["swept"] == 1
    # the sponsor, not the team: a delegation has one accountable human
    note = db.query_one(
        "SELECT message FROM notifications WHERE user = 'tester' AND read_at IS NULL"
        " ORDER BY id DESC"
    )
    assert note and f"#{task_id}" in note["message"]
    # and the model half declined, without pretending it ran
    assert out["runs"][0]["ran"] is False
    assert "no model provider" in out["runs"][0]["reason"]


def test_the_daily_ceiling_refuses_the_next_run(fresh_db, monkeypatch):
    _delegated("research-agent")
    monkeypatch.setattr(config, "AGENT_RUNNER", ["research-agent"])
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "AGENT_DAILY_TOKENS", 1000)
    usage.record_chat_usage("t", "research-agent", "m", 900, 200)

    out = agent_runner.run_one("research-agent")
    assert out["ran"] is False
    assert "daily ceiling" in out["reason"]


def test_the_ceiling_counts_only_today_and_only_this_agent(fresh_db, monkeypatch):
    monkeypatch.setattr(config, "AGENT_DAILY_TOKENS", 1000)
    usage.record_chat_usage("t", "other-agent", "m", 5000, 5000)
    # a different agent's spend must not close this one's day
    usage.assert_within_budget("research-agent")

    db.execute(
        "INSERT INTO usage_log (thread_id, agent_name, model_id, input_tokens,"
        " output_tokens, cycles, latency_ms, created_at)"
        " VALUES ('t', 'research-agent', 'm', 5000, 5000, 1, 1, '2020-01-01T00:00:00+00:00')"
    )
    # yesterday's spend must not either — the ceiling is per day
    usage.assert_within_budget("research-agent")


def test_a_forbidden_agent_is_never_woken(fresh_db, monkeypatch):
    """The kill switch has to hold hardest where nobody is watching."""
    _delegated("research-agent")
    monkeypatch.setattr(config, "AGENT_RUNNER", ["research-agent"])
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    delegation.set_authority("research-agent", "task", "forbidden", actor="tester")

    out = agent_runner.run_one("research-agent")
    assert out["ran"] is False
    assert "forbidden" in out["reason"]


def test_an_agent_with_nothing_delegated_is_not_woken(fresh_db, monkeypatch):
    """A turn with no work is a turn that invents some."""
    from app.services.users import ensure_user

    ensure_user("research-agent", kind="agent")
    monkeypatch.setattr(config, "AGENT_RUNNER", ["research-agent"])
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    assert agent_runner.run_one("research-agent")["ran"] is False


def test_one_run_per_agent_per_day(fresh_db, monkeypatch):
    """A restart must not buy a second turn out of the day's allowance."""
    _delegated("research-agent")
    monkeypatch.setattr(config, "AGENT_RUNNER", ["research-agent"])
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")

    calls = []

    def _fake_build(thread, user="", persona="", stateless=False):
        calls.append(thread)
        return lambda _msg: "did a thing"

    monkeypatch.setattr("app.agents.team_agent.build_agent", _fake_build)
    assert agent_runner.run_one("research-agent")["ran"] is True
    second = agent_runner.run_one("research-agent")
    assert second["ran"] is False
    assert "already ran" in second["reason"]
    assert len(calls) == 1


def test_one_agent_failing_does_not_stop_the_fleet(fresh_db, monkeypatch):
    """run() is a scheduled job. A raise marks the whole sweep failed on
    /health when the other agents ran fine."""
    _delegated("research-agent")
    _delegated("planner-agent")
    monkeypatch.setattr(config, "AGENT_RUNNER", ["research-agent", "planner-agent"])
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")

    def _explode(thread, user="", persona="", stateless=False):
        if "research-agent" in thread:
            raise RuntimeError("provider exploded")
        return lambda _msg: "fine"

    monkeypatch.setattr("app.agents.team_agent.build_agent", _explode)
    out = agent_runner.run()
    reasons = {r["agent"]: r for r in out["runs"]}
    assert reasons["research-agent"]["ran"] is False
    # a build failure spends nothing and says so; either wording is a refusal
    # that leaves the rest of the fleet alone, which is what this pins
    assert "could not build" in reasons["research-agent"]["reason"]
    assert reasons["planner-agent"]["ran"] is True


def test_the_identity_is_restored_after_a_failure(fresh_db, monkeypatch):
    """Left set, the next write on this thread carries the agent's name."""
    from app.agents.identity import agent_identity

    _delegated("research-agent")
    monkeypatch.setattr(config, "AGENT_RUNNER", ["research-agent"])
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")

    def _explode(thread, user="", persona="", stateless=False):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.agents.team_agent.build_agent", _explode)
    before = agent_identity()
    agent_runner.run_one("research-agent")
    assert agent_identity() == before


def test_the_wake_prompt_does_not_ask_for_new_work(fresh_db):
    """An open-ended prompt is how an unwatched agent invents a project
    nobody asked for. The turn resumes work it already holds."""
    assert "Do not create new tasks" in agent_runner._WAKE
    assert "read_worklog" in agent_runner._WAKE  # continuity, not a cold start


def test_an_unattended_write_still_passes_the_gate(fresh_db, monkeypatch):
    """Behavioral, not a source grep: the earlier version asserted that two
    strings were absent from the module, which passes on an empty file.

    The turn runs under the agent's own identity now, so a write it makes is
    evaluated against that agent's authority row — at `review` it must QUEUE,
    not apply."""
    from app.services.users import ensure_user

    task_id = _delegated("research-agent")
    monkeypatch.setattr(config, "AGENT_RUNNER", ["research-agent"])
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    ensure_user("research-agent", kind="agent")
    delegation.set_authority("research-agent", "question", "review", actor="tester")

    def _writes(thread, user="", persona="", stateless=False):
        from app.tools.collab import ask_question

        return lambda _msg: ask_question("what is the vendor SLA?", "research-agent")

    monkeypatch.setattr("app.agents.team_agent.build_agent", _writes)
    assert agent_runner.run_one("research-agent")["ran"] is True

    # queued as a proposal, and NOT written straight to notes
    pending = db.query("SELECT proposed_by FROM pending_changes WHERE status = 'pending'")
    assert [p["proposed_by"] for p in pending] == ["research-agent"]
    assert db.query("SELECT id FROM questions") == []
    assert task_id  # the delegation the run was woken for


def test_the_sweep_notifies_once_per_task_per_week(fresh_db, monkeypatch):
    """The threshold decides WHETHER a task is quiet; it does not bound the
    repeat. The sweep runs daily, so without a weekly claim a task quiet for a
    month sends the same sponsor the same sentence twenty-eight times — which
    is how a team learns to filter the channel."""
    _delegated("research-agent")
    monkeypatch.setattr(config, "AGENT_RUNNER", ["research-agent"])

    assert agent_runner.sweep()["swept"] == 1
    assert agent_runner.sweep()["swept"] == 0  # same day
    # and again tomorrow, still inside the same ISO week
    assert agent_runner.sweep()["swept"] == 0

    sent = db.query(
        "SELECT id FROM notifications WHERE user = 'tester' AND message LIKE '%no progress note%'"
    )
    assert len(sent) == 1


def test_a_task_with_a_recent_note_is_not_quiet(fresh_db, monkeypatch):
    """QUIET_DAYS is the threshold. A note inside the window means the work is
    moving, and a sponsor pinged about moving work stops reading."""
    task_id = _delegated("research-agent")
    monkeypatch.setattr(config, "AGENT_RUNNER", ["research-agent"])
    delegation.claim_task(task_id, actor="research-agent")
    delegation.report_progress(task_id, "vendor replied", actor="research-agent")

    assert agent_runner.sweep()["swept"] == 0


def test_the_turn_runs_as_the_agent_it_woke(fresh_db, monkeypatch):
    """A ContextVar does NOT cross a bare threading.Thread.

    Without copy_context the turn ran as "agent" — the chat identity — so its
    inbox came back empty, every report_progress was refused, and the gate
    resolved authority against a row that is often promoted to autonomous.
    Every other stub in this file returns a lambda that reads no identity,
    which is exactly why none of them caught it.
    """
    from app.agents.identity import agent_identity

    _delegated("research-agent")
    monkeypatch.setattr(config, "AGENT_RUNNER", ["research-agent"])
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")

    seen = {}

    def _capture(thread, user="", persona="", stateless=False):
        # read INSIDE the turn, which is what runs in the worker thread
        return lambda _msg: seen.setdefault("identity", agent_identity())

    monkeypatch.setattr("app.agents.team_agent.build_agent", _capture)
    assert agent_runner.run_one("research-agent")["ran"] is True
    assert seen["identity"] == "research-agent"


def test_the_runs_own_spend_reaches_usage_log(fresh_db, monkeypatch):
    """Both bounds written for the runner read usage_log, and build_agent
    returns a bare Agent that records nothing — so an unrecorded turn leaves
    the daily ceiling and the runaway rule reading zero forever."""
    _delegated("research-agent")
    monkeypatch.setattr(config, "AGENT_RUNNER", ["research-agent"])
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")

    class _Metrics:
        # ClassVar, because ruff refuses a mutable class attribute — these
        # stand in for the strands metrics object usage.row_from_agent reads
        accumulated_usage: ClassVar[dict] = {"inputTokens": 1234, "outputTokens": 56}
        accumulated_metrics: ClassVar[dict] = {"latencyMs": 10}
        cycle_count = 3

    class _Agent:
        event_loop_metrics = _Metrics()
        model = None

        def __call__(self, _msg):
            return "done"

    monkeypatch.setattr(
        "app.agents.team_agent.build_agent",
        lambda thread, user="", persona="", stateless=False: _Agent(),
    )
    agent_runner.run_one("research-agent")

    row = db.query_one(
        "SELECT input_tokens, output_tokens, cycles FROM usage_log WHERE agent_name = ?",
        ("research-agent",),
    )
    assert row and row["input_tokens"] == 1234
    assert row["cycles"] == 3
    # and the ceiling can now see it
    assert usage.spent_today("research-agent")["tokens"] == 1290


def test_a_hanging_turn_is_abandoned_not_awaited_forever(fresh_db, monkeypatch):
    """The job runs at 05:30 and nobody looks until morning. A turn that never
    returns must not take the rest of the fleet with it."""
    import time

    _delegated("research-agent")
    monkeypatch.setattr(config, "AGENT_RUNNER", ["research-agent"])
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "AGENT_RUN_SECONDS", 1)

    def _hang(thread, user="", persona="", stateless=False):
        return lambda _msg: time.sleep(30)

    monkeypatch.setattr("app.agents.team_agent.build_agent", _hang)
    started = time.monotonic()
    out = agent_runner.run_one("research-agent")
    # the bound is the point: a join() that waits out the hang is the bug
    assert time.monotonic() - started < 10
    assert out["ran"] is False
    assert "abandoned" in out["reason"]


def test_a_failure_that_spent_nothing_does_not_eat_the_day(fresh_db, monkeypatch):
    """catch_up is False and the cron runs once, so a 30-second provider blip
    at 05:30 would otherwise cost every agent its whole day."""
    _delegated("research-agent")
    monkeypatch.setattr(config, "AGENT_RUNNER", ["research-agent"])
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")

    def _blip(thread, user="", persona="", stateless=False):
        raise RuntimeError("provider unreachable")

    monkeypatch.setattr("app.agents.team_agent.build_agent", _blip)
    assert agent_runner.run_one("research-agent")["ran"] is False

    # the claim is back, so a retry can still run today
    monkeypatch.setattr(
        "app.agents.team_agent.build_agent",
        lambda thread, user="", persona="", stateless=False: lambda _m: "ok",
    )
    assert agent_runner.run_one("research-agent")["ran"] is True
