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
    # the delegation is aged past the quiet window: a fresh delegation is
    # deliberately not quiet (see _delegated_at), and this test is about
    # the nag itself
    monkeypatch.setattr(agent_runner, "_delegated_at", lambda _tid: "2000-01-01T00:00:00+00:00")
    """Keyless-first: the deterministic half is the whole feature on mock,
    and it must not need a provider to tell a sponsor their work is quiet."""
    task_id = _delegated("research-agent")
    monkeypatch.setattr(config, "AGENT_RUNNER", ["research-agent"])
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "mock")

    out = agent_runner.run()
    assert out["sweep"]["swept"] == 1
    # the sponsor, not the team: a delegation has one accountable human
    note = db.query_one(
        "SELECT message FROM notifications WHERE \"user\" = 'tester' AND read_at IS NULL"
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
    # the delegation is aged past the quiet window: a fresh delegation is
    # deliberately not quiet (see _delegated_at), and this test is about
    # the nag itself
    monkeypatch.setattr(agent_runner, "_delegated_at", lambda _tid: "2000-01-01T00:00:00+00:00")
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
        "SELECT id FROM notifications WHERE \"user\" = 'tester' AND message LIKE '%no progress note%'"
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


def test_a_missed_check_in_nags_the_sponsor_once_per_date(fresh_db, monkeypatch):
    """The contract's second promise: a check-in date that passed with the
    task still open reaches the sponsor — even when the work is NOT quiet,
    because notes every day with no verdict sought is its own failure. Once
    per (task, date): moving the date re-arms the nag."""
    from app.services.users import ensure_user

    ensure_user("research-agent", kind="agent")
    ensure_user("tester", kind="human")
    task = work.create_task("chase the vendor", actor="tester")
    delegation.delegate_task(
        task["id"],
        agent="research-agent",
        sponsor="tester",
        acceptance_criteria="vendor confirms in writing",
        check_in_at="2020-01-01",
        actor="tester",
    )
    monkeypatch.setattr(config, "AGENT_RUNNER", ["research-agent"])
    # a note today keeps the task out of the QUIET nag — the check-in nag
    # must fire anyway
    delegation.claim_task(task["id"], actor="research-agent")
    delegation.report_progress(task["id"], "still chasing", actor="research-agent")

    assert agent_runner.sweep()["swept"] == 1
    assert agent_runner.sweep()["swept"] == 0  # same date, claimed

    sent = db.query(
        "SELECT message FROM notifications WHERE \"user\" = 'tester'"
        " AND message LIKE '%past its check-in date%'"
    )
    assert len(sent) == 1
    assert "2020-01-01" in sent[0]["message"]


def test_a_fresh_delegation_is_not_quiet(fresh_db, monkeypatch):
    """Delegated minutes ago with no note yet, the sweep claimed "no progress
    note for 2 days" — false as stated, and the first thing a sponsor read
    about their own fresh delegation was a nag. The quiet clock starts at the
    delegation, read from the ledger's own delegate_task row."""
    _delegated("research-agent")
    monkeypatch.setattr(config, "AGENT_RUNNER", ["research-agent"])
    assert agent_runner.sweep()["swept"] == 0


def test_past_check_in_nags_once_not_twice(fresh_db, monkeypatch):
    """A task past its check-in AND quiet earned the sponsor two
    notifications in one sweep. The check-in nag already sends them to look;
    the quiet nag stands down for that task."""
    from app.services.users import ensure_user

    ensure_user("research-agent", kind="agent")
    ensure_user("tester", kind="human")
    task = work.create_task("chase the vendor", actor="tester")
    delegation.delegate_task(
        task["id"], agent="research-agent", sponsor="tester",
        check_in_at="2020-01-01", actor="tester",
    )
    monkeypatch.setattr(config, "AGENT_RUNNER", ["research-agent"])
    monkeypatch.setattr(
        agent_runner, "_delegated_at", lambda _tid: "2000-01-01T00:00:00+00:00"
    )
    assert agent_runner.sweep()["swept"] == 1
    sent = db.query("SELECT message FROM notifications WHERE \"user\" = 'tester'")
    nags = [r["message"] for r in sent if "task #%d" % task["id"] in r["message"]]
    assert len([m for m in nags if "past its check-in" in m]) == 1
    assert not [m for m in nags if "no progress note" in m]


def test_a_future_check_in_stays_silent(fresh_db, monkeypatch):
    from app.services.users import ensure_user

    ensure_user("research-agent", kind="agent")
    ensure_user("tester", kind="human")
    task = work.create_task("chase the vendor", actor="tester")
    delegation.delegate_task(
        task["id"],
        agent="research-agent",
        sponsor="tester",
        check_in_at="2999-01-01",
        actor="tester",
    )
    monkeypatch.setattr(config, "AGENT_RUNNER", ["research-agent"])
    delegation.claim_task(task["id"], actor="research-agent")
    delegation.report_progress(task["id"], "on it", actor="research-agent")
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


def test_an_unattended_turn_keeps_one_team_model_snapshot(fresh_db, monkeypatch):
    from app.agents import team_agent

    _delegated("research-agent")
    monkeypatch.setattr(config, "AGENT_RUNNER", ["research-agent"])
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    current = {"model": "old-pick"}
    seen = {}
    monkeypatch.setattr(team_agent, "_picked_model", lambda: current["model"])

    def build(thread, user="", persona="", stateless=False):
        seen["outer"] = team_agent.model_in_force()

        def turn(_message):
            current["model"] = "new-pick"
            seen["nested"] = team_agent.model_in_force()
            return "done"

        return turn

    monkeypatch.setattr(team_agent, "build_agent", build)
    assert agent_runner.run_one("research-agent")["ran"] is True
    assert seen == {"outer": "old-pick", "nested": "old-pick"}
    assert team_agent.model_in_force() == "new-pick"


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
    returns must not take the rest of the fleet with it.

    The hang is an Event this test RELEASES, never a bare sleep. run_one
    deliberately abandons the worker, so a sleep leaves a live thread running
    for the rest of its duration — and db.DB_PATH is read per connection
    (db.py::connect) while fresh_db repoints it per test, so a late write
    lands in whichever test's database is installed at that moment. Nothing
    in the CODE prevents that write: it is prevented only by this fake having
    no event_loop_metrics, and the fake twelve tests below has them. That is
    the shape of the reload(db) flake this suite already paid for once.
    """
    import threading
    import time

    _delegated("research-agent")
    monkeypatch.setattr(config, "AGENT_RUNNER", ["research-agent"])
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "AGENT_RUN_SECONDS", 1)

    released = threading.Event()

    def _hang(thread, user="", persona="", stateless=False):
        return lambda _msg: released.wait(30)

    monkeypatch.setattr("app.agents.team_agent.build_agent", _hang)
    try:
        started = time.monotonic()
        out = agent_runner.run_one("research-agent")
        # the bound is the point: a join() that waits out the hang is the bug
        assert time.monotonic() - started < 10
        assert out["ran"] is False
        assert "abandoned" in out["reason"]

        # the thread is still alive, which is what "abandoned" MEANS — a
        # future join() with no timeout would make this vanish and the test
        # above would still pass
        worker = next(
            (t for t in threading.enumerate() if t.name == "agent-run-research-agent"),
            None,
        )
        assert worker is not None and worker.is_alive()
    finally:
        # hand the thread back before the next test installs its database
        released.set()
        if worker := next(
            (t for t in threading.enumerate() if t.name == "agent-run-research-agent"),
            None,
        ):
            worker.join(timeout=5)
            assert not worker.is_alive()


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


def test_the_run_is_recorded_under_the_scheduler_not_the_agent(fresh_db, monkeypatch):
    """The actor on this row decides who can SEE it.

    `scheduler` is in activity.SYSTEM_ACTORS, and visible_actor_filter shows a
    system actor's rows to EVERY viewer. An edit that "improves" this row by
    passing the agent name instead would put one agent's turn in front of the
    whole team under that exemption — and the anti-surveillance rule is what
    buys the team's honest data entry. Nothing else enforces the choice.

    The row is also the only feed entry saying an unattended turn happened at
    all, so a rename that drops it out of the verb registry silently demotes
    it to the generic unregistered sentence.
    """
    from app.services import activity

    _delegated("research-agent")
    monkeypatch.setattr(config, "AGENT_RUNNER", ["research-agent"])
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(
        "app.agents.team_agent.build_agent",
        lambda thread, user="", persona="", stateless=False: lambda _m: "done",
    )
    assert agent_runner.run_one("research-agent", actor="scheduler")["ran"] is True

    rows = db.query("SELECT actor, action, detail FROM activity WHERE action = 'agent_run'")
    assert len(rows) == 1
    assert rows[0]["actor"] == "scheduler"
    assert rows[0]["actor"] != "research-agent"
    assert "research-agent" in rows[0]["detail"]
    # registered, so the feed renders its own sentence rather than the
    # honest-but-generic fallback for an unknown action
    assert rows[0]["action"] in activity.VERBS


def test_unattended_turn_uses_composed_policy_and_registry(fresh_db, monkeypatch):
    """An allowed scheduler job must not replace per-tool workplace policy."""
    from app.extensions import (
        AppSettings,
        ExtensionRegistry,
        PolicyContribution,
        PolicyDecision,
        PolicyEffect,
        PolicyInput,
        PolicyResource,
        PolicySubject,
        SkeinModule,
    )
    from app.extensions.core import core_module
    from app.extensions.policy import current_policy_engine, current_policy_subject
    from app.main import _job_specs
    from app.tools._gate import gated_write

    _delegated("research-agent")
    monkeypatch.setattr(config, "AGENT_RUNNER", ["research-agent"])
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")

    def deny_agent_create(request):
        if request.action == "task.create" and request.subject.name == "research-agent":
            return PolicyDecision(PolicyEffect.DENY, ("unattended create denied",))
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.4.0",
        policies=(PolicyContribution("acme.workplace.runner-policy", deny_agent_create),),
    )
    registry = ExtensionRegistry.build((core_module(), module))
    observed = {}

    def build(_thread, user="", **options):
        observed["extensions"] = options.get("extensions")
        observed["subject"] = options.get("policy_subject")
        observed["engine"] = current_policy_engine()

        def turn(_message):
            observed["worker_subject"] = current_policy_subject()
            return gated_write(
                "task",
                "create",
                {"title": "must stay denied"},
                lambda: work.create_task("must stay denied", actor=user, origin="agent"),
            )

        return turn

    monkeypatch.setattr("app.agents.team_agent.build_agent", build)
    spec = next(
        item for item in _job_specs(registry, AppSettings.from_config()) if item.name == "agent-run"
    )
    result = spec.fn()

    assert result["runs"][0]["ran"] is True
    assert observed["extensions"] is registry
    assert (
        observed["engine"]
        .decide(
            PolicyInput(
                PolicySubject("research-agent", kind="agent"),
                "task.create",
                PolicyResource("task"),
                "agent_tool",
            )
        )
        .effect
        == PolicyEffect.DENY
    )
    assert observed["subject"] == PolicySubject(
        "research-agent", kind="agent", strong=True, source="agent-runner"
    )
    assert observed["worker_subject"] == observed["subject"]
    assert db.query_one("SELECT id FROM tasks WHERE title = 'must stay denied'") is None


def test_unattended_runner_does_not_wake_for_a_denied_delegated_project(
    fresh_db,
    monkeypatch,
):
    from app.extensions import PolicyDecision, PolicyEffect, PolicyEngine
    from app.services import crews, engagements, users

    users.ensure_user("sponsor")
    users.ensure_agent_identity("research-agent")
    crew_id = crews.create_crew("Runner policy crew", actor="sponsor")["id"]
    engagement_id = engagements.create_engagement(
        "Runner regulated project",
        project_class="regulated",
        actor="sponsor",
        visibility="crew",
        crew_id=crew_id,
    )["id"]
    task_id = work.create_task(
        "RUNNER REGULATED CANARY",
        engagement_id=engagement_id,
        actor="sponsor",
        visibility="crew",
        crew_id=crew_id,
    )["id"]
    delegation.delegate_task(task_id, "research-agent", "sponsor", actor="sponsor")
    notifications_before = db.query_one(
        "SELECT COUNT(*) AS n FROM notifications WHERE \"user\" = 'sponsor'"
    )["n"]
    monkeypatch.setattr(config, "AGENT_RUNNER", ["research-agent"])
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(
        "app.agents.team_agent.build_agent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("runner woke")),
    )

    def deny_regulated_runner(request):
        if request.action == "skein.job.agent-run" and request.resource.project_type == "regulated":
            return PolicyDecision(PolicyEffect.DENY, ("Regulated runner work is closed.",))
        return None

    result = agent_runner.run(policy=PolicyEngine((deny_regulated_runner,)))

    assert result["sweep"]["swept"] == 0
    assert result["runs"][0]["ran"] is False
    assert result["runs"][0]["reason"] == "nothing delegated"
    assert (
        db.query_one("SELECT COUNT(*) AS n FROM notifications WHERE \"user\" = 'sponsor'")["n"]
        == notifications_before
    )


def test_runner_sweep_serializes_policy_and_notification(fresh_db, monkeypatch):
    # the delegation is aged past the quiet window: a fresh delegation is
    # deliberately not quiet (see _delegated_at), and this test is about
    # the nag itself
    monkeypatch.setattr(agent_runner, "_delegated_at", lambda _tid: "2000-01-01T00:00:00+00:00")
    from threading import Event, Thread
    from time import sleep

    from app.extensions import PolicyDecision, PolicyEffect, PolicyEngine
    from app.services import engagements, notifications

    standard = engagements.create_engagement("Runner standard", project_class="standard")["id"]
    regulated = engagements.create_engagement("Runner regulated", project_class="regulated")["id"]
    task_id = _delegated("research-agent", "sponsor")
    work.update_task(task_id, engagement_id=standard, actor="sponsor")
    monkeypatch.setattr(config, "AGENT_RUNNER", ["research-agent"])
    policy_entered = Event()
    writer_attempted = Event()
    writer_done = Event()
    paused = {"value": False}

    def deny_regulated(request):
        if request.action != "skein.job.agent-run":
            return None
        if request.resource.project_type == "regulated":
            return PolicyDecision(PolicyEffect.DENY, ("Regulated runner work is closed.",))
        if request.resource.type == "task" and not paused["value"]:
            paused["value"] = True
            policy_entered.set()
            assert writer_attempted.wait(5)
            # The relink is allowed to COMMIT here. What the sweep guarantees
            # is that the decision and the work it authorizes come from ONE
            # read: agent_runner._due resolves each task's policy attributes
            # once, at the top, and the run acts on those. (Not a read
            # snapshot — sweep() wraps _due in db.transaction(), and
            # read_transaction joins an ambient one rather than raising its
            # isolation.) `swept == 1` at the bottom is that guarantee; a
            # blocked writer was SQLite's mechanism for it, not the promise.
            sleep(0.05)
        return None

    def relink() -> None:
        assert policy_entered.wait(5)
        writer_attempted.set()
        fresh_db.execute(
            "UPDATE tasks SET engagement_id = ? WHERE id = ?",
            (regulated, task_id),
        )
        writer_done.set()

    original_notify = notifications.notify

    def observed_notify(*args, **kwargs):
        return original_notify(*args, **kwargs)

    monkeypatch.setattr(notifications, "notify", observed_notify)
    writer = Thread(target=relink)
    writer.start()
    result = agent_runner.sweep(PolicyEngine((deny_regulated,)))
    writer.join(5)

    assert result["swept"] == 1
    assert writer_done.is_set()
    assert fresh_db.query_one("SELECT engagement_id FROM tasks WHERE id = ?", (task_id,)) == {
        "engagement_id": regulated
    }


def test_runner_final_policy_check_and_daily_claim_share_one_transaction(fresh_db, monkeypatch):
    from threading import Event, Thread
    from time import sleep

    from app.extensions import PolicyDecision, PolicyEffect, PolicyEngine
    from app.services import engagements

    standard = engagements.create_engagement("Run claim standard", project_class="standard")["id"]
    regulated = engagements.create_engagement("Run claim regulated", project_class="regulated")[
        "id"
    ]
    task_id = _delegated("research-agent", "sponsor")
    work.update_task(task_id, engagement_id=standard, actor="sponsor")
    monkeypatch.setattr(config, "AGENT_RUNNER", ["research-agent"])
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    policy_entered = Event()
    writer_attempted = Event()
    writer_done = Event()
    build_called = Event()

    def deny_regulated(request):
        if request.action != "skein.job.agent-run":
            return None
        if request.resource.project_type == "regulated":
            return PolicyDecision(PolicyEffect.DENY, ("Regulated runner work is closed.",))
        if request.resource.type == "task" and not policy_entered.is_set():
            policy_entered.set()
            assert writer_attempted.wait(5)
            # As above: the relink may commit here. The run's consistency
            # comes from resolving the attributes once, not from holding the
            # writer off, and `ran is True` plus the daily claim below is what
            # that means.
            sleep(0.05)
        return None

    def relink() -> None:
        assert policy_entered.wait(5)
        writer_attempted.set()
        fresh_db.execute(
            "UPDATE tasks SET engagement_id = ? WHERE id = ?",
            (regulated, task_id),
        )
        writer_done.set()

    def build(_thread, **_options):
        assert writer_done.wait(5)
        build_called.set()
        return lambda _message: "current inbox policy will filter the relinked task"

    monkeypatch.setattr("app.agents.team_agent.build_agent", build)
    writer = Thread(target=relink)
    writer.start()
    result = agent_runner.run_one(
        "research-agent",
        policy=PolicyEngine((deny_regulated,)),
    )
    writer.join(5)

    assert result["ran"] is True
    assert build_called.is_set()
    assert writer_done.is_set()
    assert fresh_db.query_one(
        "SELECT 1 AS claimed FROM job_runs WHERE job = ?",
        ("agent-run:research-agent",),
    ) == {"claimed": 1}
