"""The Chief of Staff consulting one bench specialist: who holds the tool, who
answers, under whose identity, and what the user is told.

The tool is a closure inside build_agent and is never in ALL_TOOLS, so the
registry sweeps in test_gate_coverage.py do not reach it. Everything they
would have enforced — returns a JSON string, never raises, leaves an honest
receipt — is pinned here instead.
"""

import asyncio
import json

import pytest

from app import config
from app.agents import identity, team_agent
from app.services import users


class _FakeModel:
    """Same duck type test_persona_manifest.py uses: enough for strands to
    build a real Agent, so tool_names is assertable without a provider."""

    stateful = False

    def __init__(self):
        self.config = {"model_id": "fake"}

    def get_config(self):
        return self.config

    def update_config(self, **kwargs):
        self.config.update(kwargs)


@pytest.fixture
def real_provider(fresh_db, monkeypatch):
    """A build_agent that constructs a genuine Agent. build_agent returns
    MockAgent before any tool is created on the mock provider, so the consult
    tool does not exist at all until the provider looks real."""
    # Import the route BEFORE anything patches build_agent. _run_consult
    # reaches services/tuning.py::member_deadline, which imports routes/chat —
    # and chat.py binds `from ..agents.team_agent import build_agent` at import
    # time. First-imported under a patch, chat keeps the test's lambda for the
    # life of the worker and every later chat test gets it instead of MockAgent.
    import app.routes.chat  # noqa: F401

    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "")
    monkeypatch.setattr(config, "SESSIONS_DIR", config.DATA_DIR / "sessions")
    monkeypatch.setattr(team_agent, "_model", lambda **_: _FakeModel())
    return monkeypatch


def _tool_function(agent, name: str):
    tool = agent.tool_registry.registry[name]
    while getattr(tool, "_delegate", None) is not None:
        tool = tool._delegate
    for attr in ("original_function", "_tool_func", "func", "__wrapped__"):
        fn = getattr(tool, attr, None)
        if callable(fn):
            return fn
    raise AssertionError(f"{name} is not an unwrappable tool")


def _consult_tool(agent):
    return _tool_function(agent, "consult_specialist")


async def _drain(gen):
    """Every value the tool yields. The last one is what strands hands the
    model as the tool result; the rest are stream frames."""
    return [ev async for ev in gen]


class _Answering:
    """A specialist that answers in two chunks, like a real stream."""

    def __init__(self, chunks=("Tomorrow ", "looks busy.")):
        self.chunks = chunks
        self.event_loop_metrics = type(
            "M", (), {"accumulated_usage": {}, "accumulated_metrics": {}, "cycle_count": 1}
        )()

    async def stream_async(self, message):
        self.seen = message
        for c in self.chunks:
            yield {"data": c}


# ---- who holds the tool ------------------------------------------------------


def test_the_chief_of_staff_holds_the_tool(real_provider):
    agent = team_agent.build_agent("t-cos")
    assert "consult_specialist" in agent.tool_names


def test_a_specialist_does_not_hold_the_tool(real_provider):
    """THE depth cap, and it is the whole recursion story: a consulted
    specialist is built with persona=<slug>, so it cannot consult anything.
    A counter would bound depth; this makes depth 1 unreachable to exceed."""
    agent = team_agent.build_agent("t-spec", persona="code-reviewer")
    assert "consult_specialist" not in agent.tool_names
    # and the flock member build, which is how a consult builds its sub-agent
    member = team_agent.build_agent("t-spec", persona="code-reviewer", stateless=True)
    assert "consult_specialist" not in member.tool_names


def test_planner_and_consult_use_the_turn_team_model_snapshot(real_provider, monkeypatch):
    agent = team_agent.build_agent("t-model-snapshot")
    planner = _tool_function(agent, "plan_project")
    consult = _consult_tool(agent)
    current = {"model": "old-pick"}
    seen = {}
    monkeypatch.setattr(team_agent, "_picked_model", lambda: current["model"])
    token = team_agent.set_team_model_snapshot("old-pick")
    current["model"] = "new-pick"

    def planner_model(model_id="", **_kwargs):
        seen["planner"] = team_agent.model_in_force(model_id)
        raise RuntimeError("stop after model selection")

    try:
        monkeypatch.setattr(team_agent, "_model", planner_model)
        with pytest.raises(RuntimeError, match="stop after model selection"):
            planner("plan this")

        def specialist(*_args, **_kwargs):
            seen["consult"] = team_agent.model_in_force()
            return _Answering(("done",))

        monkeypatch.setattr(team_agent, "build_agent", specialist)
        asyncio.run(_drain(consult("code-reviewer", "review this")))
    finally:
        team_agent.reset_team_model_snapshot(token)

    assert seen == {"planner": "old-pick", "consult": "old-pick"}
    assert team_agent.model_in_force() == "new-pick"


def test_the_bench_roster_reaches_the_system_prompt(real_provider):
    """Discovery is the prompt, not a tool: a tool the model never thinks to
    call leaves the bench exactly as invisible as it was before."""
    from app.services import personas

    agent = team_agent.build_agent("t-roster")
    for slug in personas.bench_slugs():
        assert f"`{slug}`" in agent.system_prompt
    assert "consult_specialist is how you reach one" in agent.system_prompt
    # the cap the model is told is the cap take_consult enforces — a literal
    # in the prompt text drifts the moment identity.py changes the number
    from app.agents.identity import MAX_CONSULTS_PER_TURN

    assert f"at most {MAX_CONSULTS_PER_TURN} specialists" in agent.system_prompt


def test_chief_cannot_consult_a_contributed_specialist_without_its_capability(
    real_provider,
):
    from app.extensions import ExtensionRegistry, SkeinModule, SpecialistContribution
    from app.extensions.policy import (
        PolicySubject,
        reset_policy_subject,
        set_policy_subject,
    )

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.4.0",
        specialists=(
            SpecialistContribution(
                name="acme.workplace.private-specialist",
                display_name="Private Specialist",
                description="Uses restricted workplace context.",
                system_prompt="Use restricted workplace context.",
                required_capabilities=("acme.specialist",),
            ),
        ),
    )
    agent = team_agent.build_agent(
        "t-workplace-capability",
        extensions=ExtensionRegistry.build((module,)),
    )
    consult = _consult_tool(agent)
    token = set_policy_subject(PolicySubject("mira"))
    try:
        events = asyncio.run(
            _drain(consult("acme.workplace.private-specialist", "What is at risk?"))
        )
    finally:
        reset_policy_subject(token)

    assert json.loads(events[-1]) == {"error": "this specialist needs a workplace capability"}


def test_chief_consults_a_contributed_specialist_after_lifespan_reserves_its_owner(
    real_provider, monkeypatch, fresh_db
):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from app.extensions import AppSettings, SkeinModule, SpecialistContribution
    from app.extensions.policy import (
        PolicySubject,
        reset_policy_subject,
        set_policy_subject,
    )
    from app.main import create_app

    name = "acme.workplace.private-specialist"
    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.4.0",
        specialists=(
            SpecialistContribution(
                name=name,
                display_name="Private Specialist",
                description="Uses restricted workplace context.",
                system_prompt="Use restricted workplace context.",
                required_capabilities=("acme.specialist",),
            ),
        ),
    )
    settings = replace(AppSettings.from_config(), scheduler_enabled=False)
    with TestClient(create_app(settings, (module,))) as client:
        registry = client.app.state.skein_registry
        chief = team_agent.build_agent("t-private-specialist", extensions=registry)
        consult = _consult_tool(chief)
        monkeypatch.setattr(
            team_agent, "build_agent", lambda *a, **k: _Answering(("Private ", "read."))
        )
        token = set_policy_subject(PolicySubject("mira", capabilities=("acme.specialist",)))
        try:
            events = asyncio.run(_drain(consult(name, "What is at risk?")))
        finally:
            reset_policy_subject(token)

        assert json.loads(events[-1])["answer"] == "Private read."
        assert fresh_db.query_one(
            "SELECT kind, identity_owner FROM users WHERE name = ?", (name,)
        ) == {"kind": "agent", "identity_owner": f"specialist:{name}"}


def test_chief_consult_refuses_a_contributed_specialist_with_the_wrong_owner(
    real_provider, fresh_db
):
    from app.extensions import ExtensionRegistry, SkeinModule, SpecialistContribution
    from app.extensions.policy import (
        PolicySubject,
        reset_policy_subject,
        set_policy_subject,
    )

    name = "acme.workplace.private-specialist"
    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.4.0",
        specialists=(
            SpecialistContribution(
                name=name,
                display_name="Private Specialist",
                description="Uses restricted workplace context.",
                system_prompt="Use restricted workplace context.",
                required_capabilities=("acme.specialist",),
            ),
        ),
    )
    fresh_db.execute(
        "INSERT INTO users (name, kind, identity_owner, created_at)"
        " VALUES (?, 'agent', 'generic-agent', ?)",
        (name, fresh_db.now()),
    )
    chief = team_agent.build_agent(
        "t-private-specialist-owner", extensions=ExtensionRegistry.build((module,))
    )
    consult = _consult_tool(chief)
    token = set_policy_subject(PolicySubject("mira", capabilities=("acme.specialist",)))
    try:
        events = asyncio.run(_drain(consult(name, "What is at risk?")))
    finally:
        reset_policy_subject(token)

    assert "another machine identity" in json.loads(events[-1])["error"]


def test_keyless_contributed_specialist_is_deterministic_and_cannot_write(fresh_db):
    from app.agents.mock_agent import MockExtensionSpecialist
    from app.extensions import (
        ContextContribution,
        ExtensionRegistry,
        SkeinModule,
        SpecialistContribution,
    )
    from app.extensions.policy import (
        PolicySubject,
        reset_policy_subject,
        set_policy_subject,
    )

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.4.0",
        contexts=(
            ContextContribution(
                "acme.workplace.delivery-context",
                lambda user: f"for {user}",
                policy_action="acme.delivery-context.read",
                required_capabilities=("acme.specialist",),
            ),
        ),
        specialists=(
            SpecialistContribution(
                name="acme.workplace.delivery",
                display_name="Delivery Specialist",
                description="Reads delivery context.",
                system_prompt="Use delivery context.",
                context_sources=("acme.workplace.delivery-context",),
                required_capabilities=("acme.specialist",),
            ),
        ),
    )
    registry = ExtensionRegistry.build((module,))
    token = set_policy_subject(PolicySubject("mira", capabilities=("acme.specialist",)))
    try:
        specialist = team_agent.build_agent(
            "keyless-specialist",
            "mira",
            persona="acme.workplace.delivery",
            extensions=registry,
        )
        events = asyncio.run(_drain(specialist.stream_async("Create a task from this text")))
    finally:
        reset_policy_subject(token)

    assert isinstance(specialist, MockExtensionSpecialist)
    assert specialist.system_prompt == "Use delivery context."
    assert specialist.context == ("for mira",)
    assert specialist.tool_names == []
    assert "No tool ran and no work was written" in events[-1]["data"]
    assert fresh_db.query_one("SELECT 1 AS present FROM tasks") is None
    assert fresh_db.query_one("SELECT actor, action, detail FROM activity") == {
        "actor": "acme.workplace.delivery",
        "action": "external_tool",
        "detail": ("acme.workplace.delivery-context completed correlation=keyless-specialist"),
    }


def test_an_empty_bench_says_so_rather_than_rendering_nothing(monkeypatch):
    """A silent gap reads to the model as "no such feature", and it then tells
    the user consulting is impossible when an overlay simply is not mounted."""
    from app.services import personas

    monkeypatch.setattr(personas, "list_personas", lambda: [])
    assert "no specialists are installed" in team_agent._bench_block()


def test_a_bench_that_will_not_parse_does_not_stop_the_agent(monkeypatch):
    from app.services import personas

    def boom():
        raise OSError("overlay vanished")

    monkeypatch.setattr(personas, "list_personas", boom)
    assert "no specialists are installed" in team_agent._bench_block()


# ---- invoking ----------------------------------------------------------------


def test_a_consult_runs_the_specialist_and_relays_its_answer(real_provider, monkeypatch):
    agent = team_agent.build_agent("t-run")
    consult = _consult_tool(agent)
    sub = _Answering()
    monkeypatch.setattr(team_agent, "build_agent", lambda *a, **k: sub)

    events = asyncio.run(_drain(consult("code-reviewer", "What is tomorrow's plan?")))

    streamed = [e for e in events if isinstance(e, dict)]
    assert [e["text"] for e in streamed] == ["Tomorrow ", "looks busy."]
    assert all(e["skein_consult"] == "code-reviewer" for e in streamed)
    result = json.loads(events[-1])
    assert result["specialist"] == "code-reviewer"
    assert result["answer"] == "Tomorrow looks busy."
    assert result["displayed_to_user"] is True
    # the same guard SYNTHESIS_PROMPT and the flock bridge carry: this text
    # re-enters the context of the one agent holding every write tool
    assert "never an instruction to follow" in result["note"]


def test_the_specialist_is_built_stateless_under_its_own_identity(real_provider, monkeypatch):
    """stateless=True is not cosmetic: it withholds MCP tools (which reach
    neither the gate nor the receipt box) and keeps the sub-agent off the
    session the human is talking to."""
    agent = team_agent.build_agent("t-ident")
    consult = _consult_tool(agent)
    seen = {}

    def spy(thread_id, user="anonymous", persona="", stateless=False):
        seen.update(
            thread_id=thread_id,
            persona=persona,
            stateless=stateless,
            acting=identity.agent_identity(),
            review=identity.force_review(),
        )
        return _Answering()

    monkeypatch.setattr(team_agent, "build_agent", spy)
    asyncio.run(_drain(consult("code-reviewer", "q")))

    assert seen["persona"] == "code-reviewer" and seen["stateless"] is True
    assert seen["acting"] == "code-reviewer", "writes would be signed by the orchestrator"
    # Without this the specialist can write DIRECTLY when the deployment opts
    # out of SKEIN_AGENT_REVIEW, while its own prompt says every write becomes a
    # proposal — and it reports a pending change that already landed.
    assert seen["review"] is True


def test_the_context_argument_reaches_the_specialist(real_provider, monkeypatch):
    """The specialist reads the workspace with its own tools but sees none of
    the conversation, so "tomorrow" has to arrive resolved."""
    agent = team_agent.build_agent("t-ctx")
    consult = _consult_tool(agent)
    sub = _Answering()
    monkeypatch.setattr(team_agent, "build_agent", lambda *a, **k: sub)

    asyncio.run(_drain(consult("code-reviewer", "What is the plan?", "tomorrow is 2026-08-08")))
    assert "tomorrow is 2026-08-08" in sub.seen
    assert "What is the plan?" in sub.seen


def test_a_consult_does_not_leak_identity_back_to_the_orchestrator(real_provider, monkeypatch):
    """Called directly, with no task boundary to copy the context — which is
    the point. strands' DEFAULT executor gives each tool call its own task and
    would hide a missing restore; a sequential executor, or a direct call like
    this one, would leave the orchestrator acting as the specialist for the
    rest of the turn and sign its later writes with the wrong name."""
    agent = team_agent.build_agent("t-leak")
    consult = _consult_tool(agent)
    monkeypatch.setattr(team_agent, "build_agent", lambda *a, **k: _Answering())

    async def run():
        before = identity.agent_identity(), identity.force_review()
        await _drain(consult("code-reviewer", "q"))
        return before, (identity.agent_identity(), identity.force_review())

    before, after = asyncio.run(run())
    assert after == before == ("agent", False)


def test_the_identity_is_restored_even_when_the_specialist_fails(real_provider, monkeypatch):
    """The restore lives in a finally for this case: a failed consult that
    leaves force_review ON would silently queue every later write in the turn,
    and the orchestrator would report proposals the user never asked for."""
    agent = team_agent.build_agent("t-leak-fail")
    consult = _consult_tool(agent)

    class Boom:
        event_loop_metrics = type(
            "M", (), {"accumulated_usage": {}, "accumulated_metrics": {}, "cycle_count": 0}
        )()

        async def stream_async(self, message):
            raise RuntimeError("down")
            yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(team_agent, "build_agent", lambda *a, **k: Boom())

    async def run():
        await _drain(consult("code-reviewer", "q"))
        return identity.agent_identity(), identity.force_review()

    assert asyncio.run(run()) == ("agent", False)


# ---- failure modes -----------------------------------------------------------


def test_an_unknown_specialist_returns_an_error_and_does_not_raise(real_provider):
    """test_gate_coverage.py enforces never-raise/return-JSON for every
    registry tool; this tool is a closure, so it is enforced here."""
    agent = team_agent.build_agent("t-unknown")
    consult = _consult_tool(agent)
    events = asyncio.run(_drain(consult("not-a-specialist", "q")))
    err = json.loads(events[-1])
    assert "no specialist by that name" in err["error"]
    # the rejected value is arbitrary model text and is never echoed back
    assert "not-a-specialist" not in err["error"]


def test_a_specialist_that_fails_reports_the_class_not_the_provider_body(
    real_provider, monkeypatch
):
    """test_flock_turns.py pins the same rule for a member: a provider SDK
    error carries its raw HTTP body, and this string reaches the user."""
    agent = team_agent.build_agent("t-fail")
    consult = _consult_tool(agent)

    class Boom:
        event_loop_metrics = type(
            "M", (), {"accumulated_usage": {}, "accumulated_metrics": {}, "cycle_count": 0}
        )()

        async def stream_async(self, message):
            raise RuntimeError("sk-live-abcd leaked request_id=42")
            yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(team_agent, "build_agent", lambda *a, **k: Boom())
    out = json.loads(asyncio.run(_drain(consult("code-reviewer", "q")))[-1])
    assert "RuntimeError" in out["error"]
    assert out["error"] == "code-reviewer failed to answer (RuntimeError)"


def test_the_turn_budget_stops_an_unbounded_fan_out(real_provider, monkeypatch):
    """The bench roster is in the prompt, so the MODEL picks how many
    specialists run — the one spend multiplier in the product not written by
    an operator."""
    agent = team_agent.build_agent("t-budget")
    consult = _consult_tool(agent)
    monkeypatch.setattr(team_agent, "build_agent", lambda *a, **k: _Answering())

    async def run():
        # inside the coroutine: called from the sync test body it rebinds the
        # WORKER's context, and the spent box then refuses every later test
        identity.start_consults(2)
        out = []
        for _ in range(3):
            out.append(json.loads((await _drain(consult("code-reviewer", "q")))[-1]))
        return out

    first, second, third = asyncio.run(run())
    assert "error" not in first and "error" not in second
    assert first["answer"] and second["answer"]
    assert "consult budget" in third["error"]


def test_the_budget_is_shared_across_tool_calls_not_reset_by_each(real_provider, monkeypatch):
    """The budget is a LIST in a contextvar for the reason receipts.py holds
    one: each tool call runs in its own task with a COPIED context, so an int
    would be incremented in a copy and the cap would never bind."""

    async def two_concurrent():
        identity.start_consults(1)
        agent = team_agent.build_agent("t-copy")
        consult = _consult_tool(agent)
        monkeypatch.setattr(team_agent, "build_agent", lambda *a, **k: _Answering())
        # asyncio.create_task is exactly what strands' ConcurrentToolExecutor
        # does per tool_use, and is what copies the context
        return await asyncio.gather(
            asyncio.create_task(_drain(consult("code-reviewer", "a"))),
            asyncio.create_task(_drain(consult("backend-architect", "b"))),
        )

    a, b = asyncio.run(two_concurrent())
    outcomes = [json.loads(x[-1]) for x in (a, b)]
    assert sum("error" in o for o in outcomes) == 1, "the second consult must be refused"


# ---- the route side: heading, relay, transcript, guard, knot ------------------


def _read_chat(client, message, thread, who="tester"):
    with client.stream(
        "POST",
        "/api/chat",
        json={"thread_id": thread, "message": message},
        headers={"X-User": who},
    ) as resp:
        assert resp.status_code == 200
        return resp.read().decode()


class _Relaying:
    """An orchestrator whose turn contains one consult: two streamed chunks
    from the specialist, then its own framing. Shaped like the events strands
    emits for an async-generator tool."""

    def __init__(self, slug="code-reviewer", calls=("c-1",)):
        self.slug = slug
        self.calls = calls

    async def stream_async(self, message):
        for call in self.calls:
            yield {"current_tool_use": {"toolUseId": call, "name": "consult_specialist"}}
            for chunk in ("Tomorrow is thin. ", "Ship the migration."):
                yield {
                    "tool_stream_event": {
                        "tool_use": {"toolUseId": call},
                        "data": {"skein_consult": self.slug, "text": chunk},
                    }
                }
        yield {"data": "That is their read."}


def test_a_consulted_answer_reaches_the_user_under_one_heading(client, monkeypatch):
    """The whole route half of the feature: without it the specialist's text
    never leaves the tool and the reader sees a frozen tool chip."""
    from app.routes import chat as chat_route
    from app.services import flocks

    users.ensure_user("code-reviewer", kind="agent")
    monkeypatch.setattr(chat_route, "build_agent", lambda *a, **k: _Relaying())
    out = _read_chat(client, "ask @code-reviewer about tomorrow", "cs-1")

    card = flocks.member_cards(["code-reviewer"])[0]
    assert card["name"] in out, "the specialist answered with no attribution"
    assert out.count(card["name"]) == 1, "one heading for one consult, not one per chunk"
    assert "Tomorrow is thin." in out and "Ship the migration." in out
    assert "That is their read." in out
    # the saved transcript must say what the stream said — chat.py builds it
    # separately, so the two can drift
    saved = client.get("/api/chats/cs-1/messages", headers={"X-User": "tester"}).json()[-1]
    assert card["name"] in saved["content"] and "Ship the migration." in saved["content"]


def test_a_specialist_that_answered_is_not_reported_unreached(client, monkeypatch):
    """The turn writes nothing, so the mention guard would fire — but the
    specialist answered in this chat, which is the most direct delivery there
    is. Reporting it unreached contradicts the answer above the receipt."""
    from app.routes import chat as chat_route

    users.ensure_user("code-reviewer", kind="agent")
    monkeypatch.setattr(chat_route, "build_agent", lambda *a, **k: _Relaying())
    out = _read_chat(client, "ask @code-reviewer about tomorrow", "cs-2")
    assert "unnotified" not in out


def test_a_consult_ties_the_field_guide_knot(client, monkeypatch):
    from app.routes import chat as chat_route
    from app.services import fieldguide

    users.ensure_user("code-reviewer", kind="agent")
    monkeypatch.setattr(chat_route, "build_agent", lambda *a, **k: _Relaying())
    _read_chat(client, "ask @code-reviewer about tomorrow", "cs-3")
    assert "consult" in fieldguide._tied("tester"), "the knot never ties"


def test_two_consults_of_one_specialist_each_get_a_heading(client, monkeypatch):
    """The budget permits consulting the same slug twice. Keyed on the slug
    rather than the tool call, the second answer loses its heading and merges
    into the first."""
    from app.routes import chat as chat_route
    from app.services import flocks

    users.ensure_user("code-reviewer", kind="agent")
    monkeypatch.setattr(chat_route, "build_agent", lambda *a, **k: _Relaying(calls=("c-1", "c-2")))
    out = _read_chat(client, "ask @code-reviewer twice", "cs-4")
    card = flocks.member_cards(["code-reviewer"])[0]
    assert out.count(card["name"]) == 2


def test_a_stream_event_without_a_consult_key_renders_nothing(client, monkeypatch):
    """Any future async-generator tool yields ToolStreamEvent too. Only a
    payload this feature produced may be rendered as chat text."""
    from app.routes import chat as chat_route

    class Other:
        async def stream_async(self, message):
            yield {"tool_stream_event": {"tool_use": {"toolUseId": "x"}, "data": {"raw": "junk"}}}
            yield {"data": "done"}

    monkeypatch.setattr(chat_route, "build_agent", lambda *a, **k: Other())
    out = _read_chat(client, "hello", "cs-5")
    assert "junk" not in out and "done" in out


def test_the_budget_counts_the_specialists_the_user_named(client):
    from app.agents.identity import MAX_CONSULTS_PER_TURN
    from app.routes.chat import _consult_budget

    users.ensure_user("mira")
    assert _consult_budget("no names here") == MAX_CONSULTS_PER_TURN
    assert _consult_budget("ask @mira") == MAX_CONSULTS_PER_TURN, "a person buys no budget"
    assert _consult_budget("@code-reviewer") == MAX_CONSULTS_PER_TURN, "never below the floor"
    # a bench slug counts BEFORE it has a users row — names_in could not see
    # one, and the first consult of a specialist is exactly that case
    assert _consult_budget("@code-reviewer @backend-architect @growth-mentor") == 3


class _Metered:
    """Answers, and reports token usage the way a real provider's agent does."""

    def __init__(self, chunks=("Done.",), fail_after=False):
        self.chunks = chunks
        self.fail_after = fail_after
        self.model = type("M", (), {"get_config": lambda self: {"model_id": "glm-test"}})()
        self.event_loop_metrics = type(
            "M",
            (),
            {
                "accumulated_usage": {"inputTokens": 11, "outputTokens": 7},
                "accumulated_metrics": {"latencyMs": 42},
                "cycle_count": 2,
            },
        )()

    async def stream_async(self, message):
        for c in self.chunks:
            yield {"data": c}
        if self.fail_after:
            raise RuntimeError("died after answering")


def test_a_consult_records_its_own_spend(real_provider, monkeypatch, fresh_db):
    """plan_project loses 100% of its spend because nobody records a sub-agent.
    A consult must not repeat that: /api/usage and the monthly budget both read
    usage_log, so an unrecorded consult is spend the deployment cannot see."""
    from app import db

    agent = team_agent.build_agent("t-cost")
    consult = _consult_tool(agent)
    monkeypatch.setattr(team_agent, "build_agent", lambda *a, **k: _Metered())
    asyncio.run(_drain(consult("code-reviewer", "q")))

    row = db.query_one("SELECT * FROM usage_log WHERE thread_id = ?", ("t-cost",))
    assert row is not None, "the consult's tokens are invisible to /api/usage"
    assert row["agent_name"] == "code-reviewer", "spend attributed to the wrong head"
    assert row["input_tokens"] == 11 and row["output_tokens"] == 7
    assert row["model_id"] == "glm-test", "priced at the deployment model, not the persona's"


class _StubAgent:
    """Stands in for strands.Agent at BUILD time: the outer build hands back
    its tools for extraction, and the planner the tool constructs reports
    metered spend the way a real provider's agent does."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.model = kwargs.get("model")
        self.tool_registry = type(
            "R",
            (),
            {"registry": {team_agent._tool_name(t): t for t in kwargs.get("tools", ())}},
        )()
        self.event_loop_metrics = _Metered().event_loop_metrics

    def __call__(self, message):
        self.seen = message
        return "planned: three milestones"


def test_the_planner_records_its_own_spend(real_provider, monkeypatch, fresh_db):
    """plan_project builds a second agent, so its tokens are real spend under
    the deployment's budgets. Unrecorded, every planning turn is invisible to
    /api/usage, the monthly budget, and SKEIN_AGENT_DAILY_TOKENS."""
    import strands

    from app import db

    instances: list[_StubAgent] = []

    class _Recording(_StubAgent):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            instances.append(self)

    monkeypatch.setattr(strands, "Agent", _Recording)
    agent = team_agent.build_agent("t-plan-cost", stateless=True)
    planner = _tool_function(agent, "plan_project")
    out = planner("ship the beta", "beta")

    assert "planned" in out
    # the planner is the instance the tool call built: real tools, real model
    built = instances[-1]
    assert built is not agent and built.kwargs["tools"], "the planner was built with no tools"
    assert built.kwargs["model"].get_config()["model_id"] == "fake"
    row = db.query_one("SELECT * FROM usage_log WHERE thread_id = ?", ("t-plan-cost",))
    assert row is not None, "the planner's tokens are invisible to /api/usage"
    assert row["agent_name"] == "planner", "spend attributed to the wrong head"
    assert row["input_tokens"] == 11 and row["output_tokens"] == 7
    assert row["model_id"] == "fake", "priced at a model the planner did not run"


def test_a_planner_that_raises_still_records_the_spend(real_provider, monkeypatch, fresh_db):
    """A planning turn that dies mid-loop already consumed tokens. Recording
    only on success loses exactly the turns most worth accounting for."""
    import strands

    from app import db

    class _Dies(_StubAgent):
        def __call__(self, message):
            raise RuntimeError("provider dropped the stream")

    monkeypatch.setattr(strands, "Agent", _Dies)
    agent = team_agent.build_agent("t-plan-died", stateless=True)
    planner = _tool_function(agent, "plan_project")
    with pytest.raises(RuntimeError, match="dropped the stream"):
        planner("ship the beta")

    row = db.query_one("SELECT * FROM usage_log WHERE thread_id = ?", ("t-plan-died",))
    assert row is not None, "a failed planning turn's tokens vanished from /api/usage"


def test_a_specialist_that_dies_mid_answer_keeps_what_it_said(real_provider, monkeypatch):
    """A truncated answer must reach the user AND say it is truncated —
    otherwise the sentence just stops and nothing marks why."""
    agent = team_agent.build_agent("t-partial")
    consult = _consult_tool(agent)
    monkeypatch.setattr(
        team_agent,
        "build_agent",
        lambda *a, **k: _Metered(chunks=("Half an answer.",), fail_after=True),
    )
    events = asyncio.run(_drain(consult("code-reviewer", "q")))

    streamed = "".join(e["text"] for e in events if isinstance(e, dict))
    assert "Half an answer." in streamed
    assert "failed to answer" in streamed, "the reader sees a sentence stop with no reason"
    out = json.loads(events[-1])
    assert out["answer"] == "Half an answer."
    assert out["incomplete"], "the model cannot say why the answer stops"
    assert "RuntimeError" in out["incomplete"] and "died after answering" not in out["incomplete"]


def test_a_specialist_that_never_answers_hits_the_deadline(real_provider, monkeypatch):
    """The deadline bounds the SPECIALIST. Wrapped around the yield loop it
    would instead cancel this generator mid-suspension, and strands would
    record a toolUse with no toolResult — which 400s the thread for good."""
    from app.services import tuning

    monkeypatch.setattr(tuning, "member_deadline", lambda: 0.05)

    class Hangs:
        event_loop_metrics = type(
            "M", (), {"accumulated_usage": {}, "accumulated_metrics": {}, "cycle_count": 0}
        )()

        async def stream_async(self, message):
            await asyncio.sleep(30)
            yield {"data": "too late"}

    agent = team_agent.build_agent("t-slow")
    consult = _consult_tool(agent)
    monkeypatch.setattr(team_agent, "build_agent", lambda *a, **k: Hangs())
    events = asyncio.run(_drain(consult("code-reviewer", "q")))

    assert isinstance(events[-1], str), "the LAST yield is the tool result — it must exist"
    assert "before the deadline" in json.loads(events[-1])["error"]


def test_a_slow_reader_still_gets_a_tool_result(real_provider, monkeypatch):
    """The regression that shaped this code. With the deadline wrapped around
    the yield loop, a consumer slower than the deadline made the cancel land
    outside the timeout scope: no TimeoutError, no final yield, and a toolUse
    with no matching toolResult persisted into the session."""
    from app.services import tuning

    monkeypatch.setattr(tuning, "member_deadline", lambda: 0.05)
    agent = team_agent.build_agent("t-backpressure")
    consult = _consult_tool(agent)
    monkeypatch.setattr(team_agent, "build_agent", lambda *a, **k: _Metered(chunks=("a", "b", "c")))

    async def slow_reader():
        out = []
        async for ev in consult("code-reviewer", "q"):
            out.append(ev)
            await asyncio.sleep(0.08)  # slower than the deadline, on purpose
        return out

    events = asyncio.run(slow_reader())
    assert isinstance(events[-1], str), "no tool result: the session would hold a bare toolUse"
    out = json.loads(events[-1])
    assert "error" not in out
    # and the reader's own slowness must not be charged to the specialist: a
    # wall-clock budget marks a healthy three-chunk answer truncated
    assert not out["incomplete"], "a slow client made a finished answer look cut off"
    assert out["answer"] == "abc"


def test_a_closed_generator_still_records_the_spend(real_provider, monkeypatch, fresh_db):
    """The stop button closes this generator AT a yield, so nothing after the
    loop runs. A stopped turn still produced spend, and spend the ledger
    cannot see is the bug row_from_agent exists to prevent."""
    from app import db

    agent = team_agent.build_agent("t-stopped")
    consult = _consult_tool(agent)
    monkeypatch.setattr(team_agent, "build_agent", lambda *a, **k: _Metered(chunks=("a", "b", "c")))

    async def stop_after_first_chunk():
        gen = consult("code-reviewer", "q")
        await anext(gen)
        await gen.aclose()  # what the SSE loop's teardown does on a stop

    asyncio.run(stop_after_first_chunk())
    row = db.query_one("SELECT * FROM usage_log WHERE thread_id = ?", ("t-stopped",))
    assert row is not None, "a stopped consult's tokens vanished from /api/usage"
    assert row["agent_name"] == "code-reviewer"


def test_the_strands_wrapper_always_receives_a_tool_result(real_provider, monkeypatch):
    """Through the REAL decorator, not the unwrapped function: the last yield
    becomes the tool result (strands/tools/decorator.py), and a result must
    exist on the failure paths too — a toolUse persisted without a toolResult
    400s the thread on a strict provider."""
    from strands.types._events import ToolResultEvent

    agent = team_agent.build_agent("t-wrapper")
    tool = agent.tool_registry.registry["consult_specialist"]
    monkeypatch.setattr(team_agent, "build_agent", lambda *a, **k: _Metered())

    async def run(specialist):
        tool_use = {
            "toolUseId": "w-1",
            "name": "consult_specialist",
            "input": {"specialist": specialist, "question": "q"},
        }
        return [ev async for ev in tool.stream(tool_use, {})]

    for specialist in ("code-reviewer", "not-on-the-bench"):
        events = asyncio.run(run(specialist))
        assert isinstance(events[-1], ToolResultEvent), f"no tool result for {specialist}"
        result = events[-1].tool_result
        assert result["status"] == "success"
        json.loads(result["content"][0]["text"])  # the model reads JSON, not a repr


def test_specialist_receipts_ride_the_consult_channel(real_provider, monkeypatch):
    """A receipt travels the same queue as the specialist's text, so it
    renders inside the section that names its author — placement by data.
    The shared box stays EMPTY: a receipt in both places would render twice.
    Run in a create_task like strands does, so the isolated box lives in a
    real context copy."""
    from app.agents import receipts

    agent = team_agent.build_agent("t-receipts")
    consult = _consult_tool(agent)

    class Writes:
        event_loop_metrics = type(
            "M", (), {"accumulated_usage": {}, "accumulated_metrics": {}, "cycle_count": 0}
        )()

        async def stream_async(self, message):
            receipts.record("queued", "note", "specialist filed", 7, actor="code-reviewer")
            yield {"data": "filed."}

    monkeypatch.setattr(team_agent, "build_agent", lambda *a, **k: Writes())

    async def run():
        receipts.start()
        events = await asyncio.create_task(_drain(consult("code-reviewer", "q")))
        return events, receipts.drain()

    events, leaked = asyncio.run(run())
    riding = [e for e in events if isinstance(e, dict) and "receipt" in e]
    assert [e["receipt"]["ref"] for e in riding] == [7], "the receipt missed the channel"
    assert riding[0]["skein_consult"] == "code-reviewer"
    # actor stays ON the event: the route strips it against the section head,
    # and stripping it here would blind the shared-box spillway path
    assert riding[0]["receipt"]["actor"] == "code-reviewer"
    assert leaked == [], "a receipt in the channel AND the shared box renders twice"


def test_a_stopped_consult_spills_its_receipts_to_the_shared_box(real_provider, monkeypatch):
    """The stop button closes the generator before the channel drains run.
    deisolate spills the stranded receipts into the shared box, where the
    close-out drain renders them actor-suffixed — late, but never lost.

    The consult runs in its own task (the shared box must be PROVABLY the one
    the caller reads — iterated directly, isolate rebinds the caller's own
    context and the test measures the isolated box, which passes with the
    spillway deleted), and aclose arrives from ANOTHER task — the foreign
    finalization context test_flock_turns.py pins for an abandoned stream,
    where a contextvars Token restore raises and loses everything."""
    from app.agents import receipts

    agent = team_agent.build_agent("t-spill")
    consult = _consult_tool(agent)

    class SlowAfterFiling:
        event_loop_metrics = type(
            "M", (), {"accumulated_usage": {}, "accumulated_metrics": {}, "cycle_count": 0}
        )()

        async def stream_async(self, message):
            receipts.record("queued", "note", "stranded filing", 11, actor="code-reviewer")
            yield {"data": "one chunk"}
            await asyncio.sleep(30)

    monkeypatch.setattr(team_agent, "build_agent", lambda *a, **k: SlowAfterFiling())

    async def run():
        receipts.start()
        gen = consult("code-reviewer", "q")
        # the first yield is the text; the receipt drain follows it, so
        # closing HERE strands the receipt in the isolated box
        await asyncio.create_task(anext(gen))
        await asyncio.create_task(gen.aclose())
        return receipts.drain()

    spilled = asyncio.run(run())
    assert [r["ref"] for r in spilled] == [11], "a stopped consult lost its receipt"
    assert spilled[0]["actor"] == "code-reviewer"


def test_a_consult_cannot_steal_another_agents_receipt(real_provider, monkeypatch):
    """The reason the box is isolated at all: the consult drains beside its
    own text, and without isolation that drain empties the SHARED box —
    a receipt some other agent already left there would ride this channel
    and render under this specialist's heading, attributed to an agent that
    never touched it."""
    from app.agents import receipts

    agent = team_agent.build_agent("t-theft")
    consult = _consult_tool(agent)
    monkeypatch.setattr(team_agent, "build_agent", lambda *a, **k: _Answering())

    async def run():
        receipts.start()
        # another agent's receipt, already in the turn's shared box
        receipts.record("queued", "task", "someone else's write", 21, actor="agent")
        events = await asyncio.create_task(_drain(consult("code-reviewer", "q")))
        return events, receipts.drain()

    events, kept = asyncio.run(run())
    riding = [e["receipt"]["ref"] for e in events if isinstance(e, dict) and "receipt" in e]
    assert 21 not in riding, "the consult stole a receipt it did not produce"
    assert [r["ref"] for r in kept] == [21], "the shared box lost the other agent's receipt"


def test_a_rate_limited_consult_returns_the_refusal(real_provider, monkeypatch):
    """RETURNED, never raised — a raise aborts a turn that already spent
    tokens. And the refusal must reach the model, or it retries forever."""
    from app import ratelimit

    agent = team_agent.build_agent("t-limited")
    consult = _consult_tool(agent)
    built = []
    monkeypatch.setattr(team_agent, "build_agent", lambda *a, **k: built.append(1) or _Answering())

    def refuse(surface, user, cost=1):
        raise ratelimit.RateLimited("chat is over its limit. Wait 60 seconds.", 60)

    monkeypatch.setattr(ratelimit, "check", refuse)
    out = json.loads(asyncio.run(_drain(consult("code-reviewer", "q")))[-1])
    assert "over its limit" in out["error"]
    assert not built, "a refused consult still built (and would have run) the specialist"


def test_a_held_slug_returns_the_error_instead_of_raising(real_provider, monkeypatch):
    """ensure_user refuses a bench slug a human already holds. The refusal is
    a tool ERROR, not an exception — the wrapper turns a raise into an SDK
    string with the message embedded."""
    from app.services import users as users_svc

    agent = team_agent.build_agent("t-held")
    consult = _consult_tool(agent)

    def held(name, **_kwargs):
        raise ValueError("that name belongs to a teammate")

    monkeypatch.setattr(users_svc, "ensure_agent_identity", held)
    out = json.loads(asyncio.run(_drain(consult("code-reviewer", "q")))[-1])
    assert "belongs to a teammate" in out["error"]


def test_the_slug_survives_composer_shaped_input(real_provider, monkeypatch):
    """The model copies slugs out of the user's sentence, so it arrives the
    way people type it: with the @, capitalized, or dragging punctuation."""
    agent = team_agent.build_agent("t-shapes")
    consult = _consult_tool(agent)
    monkeypatch.setattr(team_agent, "build_agent", lambda *a, **k: _Answering())
    for shape in ("@code-reviewer", "Code-Reviewer", " code-reviewer ", "code-reviewer,"):
        out = json.loads(asyncio.run(_drain(consult(shape, "q")))[-1])
        assert out.get("specialist") == "code-reviewer", f"rejected the shape {shape!r}"


def test_a_persona_allowlist_cannot_name_the_consult_tool(fresh_db):
    """The omission from _known_tool_names is deliberate (the comment there
    records why): a persona never holds the tool, so accepting the name would
    validate an allowlist entry that silently grants nothing."""
    from app.services import personas

    assert "consult_specialist" not in personas._known_tool_names()


def test_a_consulted_write_carries_the_specialist_identity(real_provider, monkeypatch, fresh_db):
    """End to end through the gate: the write is PROPOSED by the specialist,
    REQUESTED by the human, and lands nowhere until a verdict. The wiring spy
    above proves the arguments; this proves the outcome."""
    from app import db
    from app.agents.identity import set_requester_identity
    from app.tools.collab import save_note

    agent = team_agent.build_agent("t-writes")
    consult = _consult_tool(agent)

    class FilesANote:
        event_loop_metrics = type(
            "M", (), {"accumulated_usage": {}, "accumulated_metrics": {}, "cycle_count": 0}
        )()

        async def stream_async(self, message):
            # what a real specialist's tool call does, minus the model
            fn = save_note
            for attr in ("original_function", "_tool_func", "func"):
                fn = getattr(fn, attr, fn)
            fn(topic="risk memo", content="tomorrow is thin")
            yield {"data": "Filed a note."}

    monkeypatch.setattr(team_agent, "build_agent", lambda *a, **k: FilesANote())

    from app.agents import receipts

    async def run():
        set_requester_identity("mira")
        receipts.start()
        events = await asyncio.create_task(_drain(consult("code-reviewer", "q")))
        return events, receipts.drain()

    events, drained = asyncio.run(run())
    riding = [e["receipt"] for e in events if isinstance(e, dict) and "receipt" in e]
    # the gate signed the receipt, not the test: actor comes from
    # agent_identity() at the choke point, so every gated write is covered
    assert [r["actor"] for r in riding if r["kind"] == "queued"] == ["code-reviewer"]
    assert drained == []
    pending = db.query_one("SELECT * FROM pending_changes ORDER BY id DESC LIMIT 1")
    assert pending is not None, "the write bypassed the review queue"
    assert pending["proposed_by"] == "code-reviewer"
    assert pending["requested_by"] == "mira"
    assert db.query_one("SELECT 1 FROM notes WHERE topic = 'risk memo'") is None, (
        "the note landed without a human verdict"
    )


# ---- receipt attribution -----------------------------------------------------


class _LateFiler:
    """An orchestrator whose specialist's write lands AFTER the consult
    section closed — the timing that motivates the actor field. The gate runs
    in a threadpool the stream cannot see, so the receipt drains under the
    orchestrator's framing, where placement attributes it to the wrong head."""

    async def stream_async(self, message):
        from app.agents import receipts

        yield {"current_tool_use": {"toolUseId": "c-1", "name": "consult_specialist"}}
        yield {
            "tool_stream_event": {
                "tool_use": {"toolUseId": "c-1"},
                "data": {"skein_consult": "code-reviewer", "text": "Filing a risk memo."},
            }
        }
        yield {"data": "That is their read."}  # the section is closed now
        receipts.record("queued", "note", "risk memo", 7, actor="code-reviewer")


def test_a_late_receipt_still_names_the_specialist(client, monkeypatch):
    """Placement is timing luck; the actor is data. A proposal that drains
    under the Chief of Staff's framing must still say who signed it — the
    transcript is append-only, so a wrong attribution here is permanent."""
    from app.routes import chat as chat_route

    users.ensure_user("code-reviewer", kind="agent")
    monkeypatch.setattr(chat_route, "build_agent", lambda *a, **k: _LateFiler())
    out = _read_chat(client, "ask @code-reviewer about tomorrow", "ra-1")

    assert '"actor": "code-reviewer"' in out, "the wire frame does not say who signed the write"
    saved = client.get("/api/chats/ra-1/messages", headers={"X-User": "tester"}).json()[-1]
    assert "queued for review: note #7 (code-reviewer)" in saved["content"]


class _LateRefuser:
    async def stream_async(self, message):
        from app.agents import receipts
        from app.services import wording

        receipts.record(
            "refused",
            "note",
            wording.write_policy_denied(),
            actor="code-reviewer",
        )
        yield {"data": "The write was refused."}


def test_a_saved_refusal_keeps_the_specialist_actor(client, monkeypatch):
    from app.routes import chat as chat_route

    users.ensure_user("code-reviewer", kind="agent")
    monkeypatch.setattr(chat_route, "build_agent", lambda *a, **k: _LateRefuser())

    out = _read_chat(client, "ask @code-reviewer to file a note", "ra-refused")
    saved = client.get("/api/chats/ra-refused/messages", headers={"X-User": "tester"}).json()[-1]

    assert '"actor": "code-reviewer"' in out
    assert "refused: note (code-reviewer)" in saved["content"]


def test_the_turn_heads_own_receipts_stay_unattributed(client, monkeypatch):
    """Differs-only: stamping every plain-turn receipt with the head's name
    adds noise to the path where attribution says nothing new — and "agent"
    is the contextvar default, not a name a reader knows."""
    from app.routes import chat as chat_route

    class OwnWrite:
        async def stream_async(self, message):
            from app.agents import receipts

            receipts.record("queued", "note", "own filing", 3, actor="agent")
            yield {"data": "Filed."}

    monkeypatch.setattr(chat_route, "build_agent", lambda *a, **k: OwnWrite())
    out = _read_chat(client, "file that note", "ra-2")
    assert '"actor"' not in out
    saved = client.get("/api/chats/ra-2/messages", headers={"X-User": "tester"}).json()[-1]
    assert "(agent)" not in saved["content"]
    assert "queued for review: note #3" in saved["content"]


def test_a_receipt_after_the_final_drain_is_not_dropped(client, monkeypatch):
    """A specialist's write finishing in a threadpool after pump's last drain
    used to land in a box nothing read again: the proposal sat in the inbox
    while the chat said nothing about it. The close-out drain narrows that
    window; the inbox row is the backstop for anything later still."""
    from app.agents import turn_guard
    from app.routes import chat as chat_route

    class Quiet:
        async def stream_async(self, message):
            yield {"data": "Consulting."}

    monkeypatch.setattr(chat_route, "build_agent", lambda *a, **k: Quiet())
    # the straggler lands while the turn guard runs — after pump, before close
    real_unfiled = turn_guard.unfiled

    def late_write(message, wrote):
        from app.agents import receipts

        receipts.record("queued", "note", "straggler", 9, actor="code-reviewer")
        return real_unfiled(message, wrote)

    monkeypatch.setattr(turn_guard, "unfiled", late_write)
    out = _read_chat(client, "hello", "ra-3")
    assert '"ref": 9' in out, "the straggler receipt was silently dropped"
    saved = client.get("/api/chats/ra-3/messages", headers={"X-User": "tester"}).json()[-1]
    assert "(code-reviewer)" in saved["content"]


def test_a_channel_receipt_renders_inside_the_section_unsuffixed(client, monkeypatch):
    """Placement by data: the receipt chip lands between the specialist's
    heading and the orchestrator's framing, and carries no "(slug)" suffix —
    the heading above it already names the author. It still counts as this
    turn's write, so the unfiled guard stays quiet."""
    from app.routes import chat as chat_route
    from app.services import flocks

    class RelaysAReceipt:
        async def stream_async(self, message):
            yield {"current_tool_use": {"toolUseId": "c-1", "name": "consult_specialist"}}
            yield {
                "tool_stream_event": {
                    "tool_use": {"toolUseId": "c-1"},
                    "data": {"skein_consult": "code-reviewer", "text": "Filing it."},
                }
            }
            yield {
                "tool_stream_event": {
                    "tool_use": {"toolUseId": "c-1"},
                    "data": {
                        "skein_consult": "code-reviewer",
                        "receipt": {
                            "kind": "queued",
                            "entity": "note",
                            "detail": "risk memo",
                            "ref": 7,
                            "actor": "code-reviewer",
                        },
                    },
                }
            }
            yield {"data": "That is their read."}

    users.ensure_user("code-reviewer", kind="agent")
    monkeypatch.setattr(chat_route, "build_agent", lambda *a, **k: RelaysAReceipt())
    _read_chat(client, "todo: ask @code-reviewer to file the risk memo", "ch-1")

    saved = client.get("/api/chats/ch-1/messages", headers={"X-User": "tester"}).json()[-1]
    content = saved["content"]
    card = flocks.member_cards(["code-reviewer"])[0]
    assert content.index(card["name"]) < content.index("queued for review: note #7")
    assert content.index("queued for review: note #7") < content.index("That is their read.")
    assert "(code-reviewer)" not in content, "suffixed under a heading that already names it"
    # the receipt counted as a write: a capture-prefixed message that files
    # through a consult must not ALSO warn that it filed nothing
    assert "filed nothing" not in content


def test_deisolate_from_another_turns_context_does_not_rebind_it(real_provider, monkeypatch):
    """The guard on deisolate's restore. An abandoned stream is finalized in
    whatever context runs it last — which can be ANOTHER turn's. An unguarded
    set(prev) would repoint that turn's box at this turn's, and every receipt
    it records afterwards would land in a stranger's transcript."""
    from app.agents import receipts

    agent = team_agent.build_agent("t-foreign")
    consult = _consult_tool(agent)
    monkeypatch.setattr(
        team_agent,
        "build_agent",
        lambda *a, **k: _Metered(chunks=("one chunk",), fail_after=False),
    )

    async def run():
        receipts.start()  # turn A's shared box
        gen = consult("code-reviewer", "q")
        await asyncio.create_task(anext(gen))

        async def turn_b():
            receipts.start()  # a DIFFERENT turn's box, in this task's context
            # recorded BEFORE the close: an unguarded restore repoints this
            # context at turn A's box, and a drain that follows the same
            # wrong pointer still sees its own later writes — only a receipt
            # already in turn B's real box exposes the swap by going missing
            receipts.record("queued", "task", "turn B, before the close", 30, actor="agent")
            await gen.aclose()  # finalizes turn A's consult in turn B's context
            receipts.record("queued", "note", "turn B, after the close", 31, actor="agent")
            return receipts.drain()

        b_receipts = await asyncio.create_task(turn_b())
        return b_receipts, receipts.drain()

    b_receipts, a_receipts = asyncio.run(run())
    assert [r["ref"] for r in b_receipts] == [30, 31], "the close cost turn B its own receipts"
    assert [r["ref"] for r in a_receipts] == []
