"""How a long chat is kept inside the context window, and the promise that
choosing wrong degrades instead of taking the API down."""

import importlib
import os

import pytest

from app import config


def _reload(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return importlib.reload(config)


@pytest.fixture(autouse=True)
def _restore_config():
    yield
    # scrub BEFORE reloading: fixture finalization order can run this while
    # a test's monkeypatch.setenv is still live, and reloading with the env
    # set bakes that test's strategy into the module for whichever test the
    # worker runs next — on xdist, a once-in-four flake on the toggle test.
    # monkeypatch undoes its env afterwards either way.
    for key in [k for k in os.environ if k.startswith("SKEIN_CONTEXT_")]:
        os.environ.pop(key)
    importlib.reload(config)


def test_sliding_is_the_default():
    assert config.CONTEXT_STRATEGY == "sliding"
    assert config.CONTEXT_STRATEGY_ERROR == ""


def test_an_unknown_strategy_degrades_to_sliding_and_says_so(monkeypatch):
    cfg = _reload(monkeypatch, SKEIN_CONTEXT_STRATEGY="magic")
    assert cfg.CONTEXT_STRATEGY == "sliding"
    assert "magic" in cfg.CONTEXT_STRATEGY_ERROR
    assert "sliding" in cfg.CONTEXT_STRATEGY_ERROR


@pytest.mark.parametrize(
    ("env", "attr", "default"),
    [
        ({"SKEIN_CONTEXT_WINDOW": "lots"}, "CONTEXT_WINDOW", 40),
        ({"SKEIN_CONTEXT_SUMMARY_RATIO": "abc"}, "CONTEXT_SUMMARY_RATIO", 0.3),
        ({"SKEIN_CONTEXT_PRESERVE_RECENT": "-"}, "CONTEXT_PRESERVE_RECENT", 10),
    ],
)
def test_a_non_numeric_knob_degrades_to_its_default(monkeypatch, env, attr, default):
    """int()/float() on operator input at import time would take the whole
    REST API down — the same trap SKEIN_MAX_TOKENS documents."""
    cfg = _reload(monkeypatch, **env)
    assert getattr(cfg, attr) == default
    assert next(iter(env)) in cfg.CONTEXT_STRATEGY_ERROR


@pytest.mark.parametrize(
    ("env", "attr", "default"),
    [
        # negative window: the SDK RAISES at construction, so every chat turn
        # would fail while /health stayed green
        ({"SKEIN_CONTEXT_WINDOW": "-5"}, "CONTEXT_WINDOW", 40),
        # zero window: clears history on every reduction — a chat with no memory
        ({"SKEIN_CONTEXT_WINDOW": "0"}, "CONTEXT_WINDOW", 40),
        # the SDK silently CLAMPS to 0.1-0.8, so the operator would believe a
        # number that is not in effect
        ({"SKEIN_CONTEXT_SUMMARY_RATIO": "9"}, "CONTEXT_SUMMARY_RATIO", 0.3),
        ({"SKEIN_CONTEXT_SUMMARY_RATIO": "0.01"}, "CONTEXT_SUMMARY_RATIO", 0.3),
        ({"SKEIN_CONTEXT_PRESERVE_RECENT": "-4"}, "CONTEXT_PRESERVE_RECENT", 10),
        ({"SKEIN_CONTEXT_PIN_FIRST": "-1"}, "CONTEXT_PIN_FIRST", 0),
        # no static bound exists (both depend on live message count), but an
        # absurd value makes the SDK raise on every overflow, so the ceiling
        # is a typo guard
        ({"SKEIN_CONTEXT_PRESERVE_RECENT": "5000"}, "CONTEXT_PRESERVE_RECENT", 10),
        ({"SKEIN_CONTEXT_PIN_FIRST": "5000"}, "CONTEXT_PIN_FIRST", 0),
        # NaN fails every comparison, so a bare </> check passes it straight
        # to the SDK's clamp — the exact bug these bounds exist to refuse
        ({"SKEIN_CONTEXT_SUMMARY_RATIO": "nan"}, "CONTEXT_SUMMARY_RATIO", 0.3),
    ],
)
def test_an_out_of_range_knob_degrades_and_says_so(monkeypatch, env, attr, default):
    """A number the SDK would reject or silently clamp is refused up front:
    both are the same bug, a setting that does not mean what it says."""
    cfg = _reload(monkeypatch, **env)
    assert getattr(cfg, attr) == default
    assert next(iter(env)) in cfg.CONTEXT_STRATEGY_ERROR


def test_every_fault_is_reported_not_just_the_first(monkeypatch):
    """These knobs are independent. Reporting one at a time makes an operator
    with three typos restart three times."""
    cfg = _reload(
        monkeypatch,
        SKEIN_CONTEXT_STRATEGY="magic",
        SKEIN_CONTEXT_WINDOW="lots",
        SKEIN_CONTEXT_PRESERVE_RECENT="-4",
    )
    for expected in (
        "SKEIN_CONTEXT_STRATEGY",
        "SKEIN_CONTEXT_WINDOW",
        "SKEIN_CONTEXT_PRESERVE_RECENT",
    ):
        assert expected in cfg.CONTEXT_STRATEGY_ERROR


def test_summarize_is_accepted(monkeypatch):
    cfg = _reload(monkeypatch, SKEIN_CONTEXT_STRATEGY="summarize")
    assert cfg.CONTEXT_STRATEGY == "summarize"
    assert cfg.CONTEXT_STRATEGY_ERROR == ""


@pytest.mark.parametrize(
    ("strategy", "expected"),
    [
        ("sliding", "SlidingWindowConversationManager"),
        ("summarize", "SummarizingConversationManager"),
    ],
)
def test_the_strategy_selects_the_manager(fresh_db, monkeypatch, strategy, expected):
    from app.agents import team_agent

    monkeypatch.setattr(config, "CONTEXT_STRATEGY", strategy)
    assert type(team_agent._conversation_manager()).__name__ == expected


def test_knobs_reach_the_manager(fresh_db, monkeypatch):
    from app.agents import team_agent

    monkeypatch.setattr(config, "CONTEXT_STRATEGY", "sliding")
    monkeypatch.setattr(config, "CONTEXT_WINDOW", 12)
    monkeypatch.setattr(config, "CONTEXT_PIN_FIRST", 4)
    mgr = team_agent._conversation_manager()
    assert mgr.window_size == 12
    assert mgr.pin_first == 4

    monkeypatch.setattr(config, "CONTEXT_STRATEGY", "summarize")
    monkeypatch.setattr(config, "CONTEXT_SUMMARY_RATIO", 0.5)
    monkeypatch.setattr(config, "CONTEXT_PRESERVE_RECENT", 6)
    mgr = team_agent._conversation_manager()
    assert mgr.summary_ratio == 0.5
    assert mgr.preserve_recent_messages == 6
    assert mgr.pin_first == 4


def test_pin_first_zero_means_unpinned_not_pin_zero(fresh_db, monkeypatch):
    """0 must reach the SDK as None. Passed as 0 it would mean "pin nothing"
    explicitly, which is the same outcome today but stops being so the moment
    the SDK distinguishes them."""
    from app.agents import team_agent

    monkeypatch.setattr(config, "CONTEXT_PIN_FIRST", 0)
    assert team_agent._conversation_manager().pin_first is None


def test_mock_never_builds_a_conversation_manager(fresh_db, monkeypatch):
    """Keyless-first: none of these knobs may touch the mock path, which never
    constructs a Strands Agent at all."""
    from app.agents import team_agent
    from app.agents.mock_agent import MockAgent

    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "mock")
    monkeypatch.setattr(config, "CONTEXT_STRATEGY", "summarize")
    assert isinstance(team_agent.build_agent("t1"), MockAgent)


def test_status_reports_the_strategy_and_hides_it_on_mock(client, monkeypatch):
    body = client.get("/api/agents/status").json()
    assert body["context_strategy"] == ""  # tests run on mock
    assert body["context_error"] == ""

    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "CONTEXT_STRATEGY", "summarize")
    assert client.get("/api/agents/status").json()["context_strategy"] == "summarize"


def test_health_reports_the_strategy(client):
    assert client.get("/health").json()["context_strategy"] == "sliding"


# ---- the team toggle ---------------------------------------------------------


def _key(name: str = "operator") -> dict:
    from app.services.api_keys import create_key

    return {"Authorization": f"Bearer {create_key(name, 'test')['key']}"}


def test_the_toggle_overrides_the_env_default(client, fresh_db):
    from app.services import settings

    assert settings.effective_context_strategy() == "sliding"
    r = client.post(
        "/api/settings/context-strategy", json={"strategy": "summarize"}, headers=_key()
    )
    assert r.status_code == 200
    assert settings.effective_context_strategy() == "summarize"
    assert client.get("/api/settings/context-strategy").json()["override"] == "summarize"


def test_clearing_the_toggle_returns_to_the_env_default(client, fresh_db):
    from app.services import settings

    client.post("/api/settings/context-strategy", json={"strategy": "summarize"}, headers=_key())
    client.post("/api/settings/context-strategy", json={"strategy": ""}, headers=_key())
    assert settings.context_strategy_override() == ""
    assert settings.effective_context_strategy() == "sliding"


def test_the_toggle_drives_the_manager(client, fresh_db, monkeypatch):
    """The point of the setting: it must change what gets built, not just what
    an endpoint reports."""
    from app.agents import team_agent

    client.post("/api/settings/context-strategy", json={"strategy": "summarize"}, headers=_key())
    assert type(team_agent._conversation_manager()).__name__ == "SummarizingConversationManager"


def test_an_unknown_strategy_is_refused(client, fresh_db):
    r = client.post("/api/settings/context-strategy", json={"strategy": "magic"}, headers=_key())
    assert r.status_code == 400
    assert "magic" in r.json()["detail"]


def test_a_retired_stored_value_falls_back_instead_of_guessing(client, fresh_db):
    """A strategy removed in a later release must not silently select another."""
    from app import db
    from app.services import settings

    db.execute(
        "INSERT INTO app_settings (key, value, updated_at) VALUES ('context_strategy', ?, ?)",
        ("retired-strategy", db.now()),
    )
    assert settings.context_strategy_override() == ""
    assert settings.effective_context_strategy() == "sliding"


def test_writing_the_toggle_needs_a_personal_key(client, fresh_db):
    """X-User is a name typed into a header. This changes what every chat
    costs, so the header alone must not be enough."""
    r = client.post("/api/settings/context-strategy", json={"strategy": "summarize"})
    assert r.status_code == 403


def test_the_change_is_logged_to_the_provenance_ledger(client, fresh_db):
    from app import db

    client.post("/api/settings/context-strategy", json={"strategy": "summarize"}, headers=_key())
    row = db.query_one("SELECT actor, detail FROM activity WHERE action = 'set_context_strategy'")
    assert row["actor"] == "operator"
    assert "summarize" in row["detail"]


# ---- session survival across a strategy change -------------------------------


def _seed_session(thread_id: str, manager_name: str) -> None:
    """Write a session the way a previous turn under `manager_name` would."""
    from strands.types.session import Session, SessionAgent, SessionType

    from app.agents.session_store import SqliteSessionRepository

    repo = SqliteSessionRepository()
    repo.create_session(Session(session_id=thread_id, session_type=SessionType.AGENT))
    repo.create_agent(
        thread_id,
        SessionAgent(
            agent_id="default",
            state={},
            conversation_manager_state={
                "__name__": manager_name,
                "removed_message_count": 3,
            },
        ),
    )


def _stored_state(thread_id: str) -> dict:
    from app.agents.session_store import SqliteSessionRepository

    return SqliteSessionRepository().read_agent(thread_id, "default").conversation_manager_state


def test_the_sdk_really_does_reject_a_foreign_manager_state():
    """The reason _reconcile_session_strategy exists. If this ever stops
    raising, the reconcile can go — but it must not be removed on a hunch."""
    from strands.agent.conversation_manager import (
        SlidingWindowConversationManager,
        SummarizingConversationManager,
    )

    state = SlidingWindowConversationManager().get_state()
    with pytest.raises(ValueError, match="Invalid conversation manager state"):
        SummarizingConversationManager().restore_from_session(state)


def test_changing_the_strategy_rewrites_an_existing_thread(fresh_db, monkeypatch):
    """Without this, flipping the toggle would fail EVERY open thread on its
    next message with an SDK error and no recovery."""
    from app.agents import team_agent

    _seed_session("t-flip", "SlidingWindowConversationManager")
    monkeypatch.setattr(config, "CONTEXT_STRATEGY", "summarize")

    team_agent._reconcile_session_strategy("t-flip", team_agent._conversation_manager())
    state = _stored_state("t-flip")
    assert state["__name__"] == "SummarizingConversationManager"
    # CARRIED, not reset. Resetting replays the whole thread into the next
    # model call, which overflows, and the recovery summarizes a full history
    # in one call and overflows again — several turns fail in a row on exactly
    # the long threads this exists to save.
    assert state["removed_message_count"] == 3


def test_reconcile_leaves_a_matching_thread_alone(fresh_db, monkeypatch):
    from app.agents import team_agent

    _seed_session("t-same", "SlidingWindowConversationManager")
    monkeypatch.setattr(config, "CONTEXT_STRATEGY", "sliding")

    team_agent._reconcile_session_strategy("t-same", team_agent._conversation_manager())
    assert _stored_state("t-same")["removed_message_count"] == 3  # untouched


def test_reconcile_never_raises_into_a_chat_turn(fresh_db, monkeypatch):
    """Bookkeeping must not be able to kill a conversation."""
    from app.agents import team_agent

    team_agent._reconcile_session_strategy("t-missing", team_agent._conversation_manager())


def test_the_session_bridge_seeds_the_configured_manager(fresh_db, monkeypatch):
    """A command-first thread on a summarize deployment used to be seeded with
    sliding state, killing it the moment the agent first replied."""
    from app.agents import session_log

    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "")
    monkeypatch.setattr(config, "CONTEXT_STRATEGY", "summarize")

    session_log.log_exchange("t-bridge", "/help", "here is help")
    assert _stored_state("t-bridge")["__name__"] == "SummarizingConversationManager"


def test_concurrent_bridge_writes_keep_every_exchange(fresh_db, monkeypatch):
    """next_id came from the LAST message on disk, with no lock — two commands
    on one thread read the same id and wrote message_<n>.json over each other,
    and both raced to create the session. Measured at 34 of 180 messages
    surviving before the per-thread lock."""
    import threading

    from app.agents import session_log

    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "")

    workers, per_worker = 6, 10
    failures: list[BaseException] = []

    def hammer(w: int) -> None:
        try:
            for i in range(per_worker):
                session_log.log_exchange("t-race", f"/cmd {w}-{i}", f"reply {w}-{i}")
        except BaseException as exc:
            failures.append(exc)  # reported by the assert below, never swallowed

    threads = [threading.Thread(target=hammer, args=(w,)) for w in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)

    assert not failures, f"bridge raised under contention: {failures[:3]}"
    rows = fresh_db.query("SELECT message_id FROM session_messages WHERE session_id = 't-race'")
    # each exchange writes a user message and an assistant message, except
    # where one folds into a stranded user turn — so the floor is the exchange
    # count, and the ceiling is twice it. Losing writes lands far below both.
    assert len(rows) >= workers * per_worker, (
        f"{len(rows)} messages stored for {workers * per_worker} exchanges — writes were lost"
    )


def test_build_agent_reconciles_a_mismatched_session(fresh_db, monkeypatch):
    """The CALL SITE, not just the function. Without the reconcile call in
    build_agent, this raises ValueError('Invalid conversation manager state.')
    — the original bricking bug, which every other test here would miss."""
    from app.agents import team_agent

    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "")
    monkeypatch.setattr(config, "CONTEXT_STRATEGY", "summarize")
    monkeypatch.setattr(team_agent, "_model", lambda **_: _FakeModel())
    _seed_session("t-brick", "SlidingWindowConversationManager")

    agent = team_agent.build_agent("t-brick")
    assert type(agent.conversation_manager).__name__ == "SummarizingConversationManager"


def test_a_failed_reconcile_is_logged_not_swallowed(fresh_db, monkeypatch, caplog):
    """Silence here means the next turn dies with the SDK's opaque error and
    nothing in the log explains why recovery never ran."""
    import logging

    from app.agents import team_agent
    from app.agents.session_store import SqliteSessionRepository

    _seed_session("t-boom", "SlidingWindowConversationManager")
    monkeypatch.setattr(config, "CONTEXT_STRATEGY", "summarize")

    def boom(*a, **k):
        raise OSError("database is locked")

    monkeypatch.setattr(SqliteSessionRepository, "update_agent", boom)
    with caplog.at_level(logging.WARNING):
        team_agent._reconcile_session_strategy("t-boom", team_agent._conversation_manager())
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


def test_build_agent_actually_attaches_the_manager(fresh_db, monkeypatch):
    """The wiring itself, not just the factory: deleting the
    conversation_manager argument from Agent(...) must fail a test."""
    from app.agents import team_agent

    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "")
    monkeypatch.setattr(config, "CONTEXT_STRATEGY", "summarize")
    monkeypatch.setattr(team_agent, "_model", lambda **_: _FakeModel())

    agent = team_agent.build_agent("t-wired")
    assert type(agent.conversation_manager).__name__ == "SummarizingConversationManager"


class _FakeModel:
    """Enough of a strands model for Agent construction."""

    stateful = False

    def __init__(self):
        self.config = {"model_id": "fake"}

    def get_config(self):
        return self.config

    def update_config(self, **kwargs):
        self.config.update(kwargs)


def test_the_summarizer_prompt_refuses_to_launder_pasted_instructions():
    """The summarizer runs with no tools and outside the platform system
    prompt, and its output is persisted as a `user` message — the one place
    pasted third-party text can come back looking like a standing order."""
    from app.agents.team_agent import SUMMARIZER_PROMPT

    flat = " ".join(SUMMARIZER_PROMPT.lower().split())  # the prompt is wrapped
    assert "as reported content" in flat
    assert "never as directives" in flat
    assert "permissions, authority, or approvals" in flat


def test_summarize_carries_the_hardened_prompt(fresh_db, monkeypatch):
    from app.agents import team_agent

    monkeypatch.setattr(config, "CONTEXT_STRATEGY", "summarize")
    mgr = team_agent._conversation_manager()
    assert mgr.summarization_system_prompt == team_agent.SUMMARIZER_PROMPT


def test_health_follows_the_toggle(client, fresh_db):
    """/health and /api/agents/status must not disagree about one fact."""
    client.post("/api/settings/context-strategy", json={"strategy": "summarize"}, headers=_key())
    assert client.get("/health").json()["context_strategy"] == "summarize"


def test_the_toggle_is_rate_capped(client, fresh_db):
    """Each call appends to the activity ledger, which is never pruned — an
    uncapped write permanently inflates the chain the integrity check walks.
    Skein surfaces a spent cap as 400 (ratelimit.check raises ValueError)."""
    headers = _key()
    codes = [
        client.post(
            "/api/settings/context-strategy", json={"strategy": "sliding"}, headers=headers
        ).status_code
        for _ in range(40)
    ]
    assert codes[0] == 200
    assert 400 in codes


def _seed_messages(thread_id: str, roles: list[str]) -> None:
    from strands.types.session import SessionMessage

    from app.agents.session_store import SqliteSessionRepository

    repo = SqliteSessionRepository()
    for i, role in enumerate(roles):
        repo.create_message(
            thread_id,
            "default",
            SessionMessage.from_message({"role": role, "content": [{"text": f"m{i}"}]}, i),
        )


def test_leaving_summarize_never_restores_an_assistant_first_history(fresh_db, monkeypatch):
    """Under summarize the prepended summary is a user message and is what
    keeps the restored list legal. Dropping it while carrying the offset can
    start the history on an assistant turn, which anthropic and bedrock reject
    outright — the thread then fails every turn until it outgrows the window."""
    from app.agents import team_agent
    from app.agents.session_store import SqliteSessionRepository

    _seed_session("t-roles", "SummarizingConversationManager")
    # offset 3 lands on an assistant turn
    _seed_messages("t-roles", ["user", "assistant", "user", "assistant", "user", "assistant"])
    monkeypatch.setattr(config, "CONTEXT_STRATEGY", "sliding")

    team_agent._reconcile_session_strategy("t-roles", team_agent._conversation_manager())
    offset = _stored_state("t-roles")["removed_message_count"]

    restored = SqliteSessionRepository().list_messages("t-roles", "default", offset=offset)
    assert restored[0].to_message()["role"] == "user"
    assert offset == 2  # walked BACK, so nothing on disk was dropped


def test_an_already_aligned_offset_is_left_alone(fresh_db, monkeypatch):
    from app.agents import team_agent

    _seed_session("t-aligned", "SummarizingConversationManager")
    _seed_messages("t-aligned", ["user", "assistant", "user", "assistant"])
    monkeypatch.setattr(config, "CONTEXT_STRATEGY", "sliding")

    team_agent._reconcile_session_strategy("t-aligned", team_agent._conversation_manager())
    assert _stored_state("t-aligned")["removed_message_count"] == 2


def test_several_faults_read_as_sentences(monkeypatch):
    """Each fault terminates itself, so the join must not add punctuation.
    The agent strip renders this string straight into a sentence."""
    cfg = _reload(
        monkeypatch,
        SKEIN_CONTEXT_STRATEGY="magic",
        SKEIN_CONTEXT_WINDOW="lots",
        SKEIN_CONTEXT_PIN_FIRST="5000",
    )
    err = cfg.CONTEXT_STRATEGY_ERROR
    assert ";" not in err  # user-visible functional text carries no semicolons
    assert ".;" not in err
    assert err.endswith(".")


def _seed_tool_messages(thread_id: str) -> None:
    """[user, assistant(toolUse), user(toolResult), assistant, user, assistant]
    — the ordinary shape for a tool-driven agent, and a legal summarize split
    at index 3 because the tool pair sits wholly inside the summarized range."""
    from strands.types.session import SessionMessage

    from app.agents.session_store import SqliteSessionRepository

    repo = SqliteSessionRepository()
    msgs = [
        {"role": "user", "content": [{"text": "do it"}]},
        {
            "role": "assistant",
            "content": [{"toolUse": {"toolUseId": "tu1", "name": "t", "input": {}}}],
        },
        {
            "role": "user",
            "content": [{"toolResult": {"toolUseId": "tu1", "content": [{"text": "ok"}]}}],
        },
        {"role": "assistant", "content": [{"text": "done"}]},
        {"role": "user", "content": [{"text": "next"}]},
        {"role": "assistant", "content": [{"text": "sure"}]},
    ]
    for i, m in enumerate(msgs):
        repo.create_message(thread_id, "default", SessionMessage.from_message(m, i))


def test_alignment_skips_an_orphaned_toolresult(fresh_db, monkeypatch):
    """A lone toolResult IS a user message, so a role-only check lands on it —
    and the SDK then deletes it as an orphan on restore, putting the assistant
    turn first again. The role check alone is not the test."""
    from app.agents import team_agent
    from app.agents.session_store import SqliteSessionRepository

    _seed_session("t-tools", "SummarizingConversationManager")
    _seed_tool_messages("t-tools")
    monkeypatch.setattr(config, "CONTEXT_STRATEGY", "sliding")

    team_agent._reconcile_session_strategy("t-tools", team_agent._conversation_manager())
    offset = _stored_state("t-tools")["removed_message_count"]
    assert offset == 0  # index 2 is the orphaned toolResult, so it walks past it

    restored = [
        m.to_message()
        for m in SqliteSessionRepository().list_messages("t-tools", "default", offset=offset)
    ]
    assert restored[0]["role"] == "user"
    assert not any("toolResult" in c for c in restored[0]["content"])


def test_a_mistyped_field_is_refused_not_treated_as_clear(client, fresh_db):
    """Empty string is the CLEAR sentinel, so a typo'd field name falling
    through to the default would silently revert the whole team and answer
    200 as if that were deliberate."""
    from app.services import settings

    client.post("/api/settings/context-strategy", json={"strategy": "summarize"}, headers=_key())
    r = client.post("/api/settings/context-strategy", json={"stratgy": "sliding"}, headers=_key())
    assert r.status_code == 422
    assert settings.effective_context_strategy() == "summarize"  # untouched

    r = client.post("/api/settings/context-strategy", json={}, headers=_key())
    assert r.status_code == 422
    assert settings.effective_context_strategy() == "summarize"


def test_the_strategy_fault_does_not_assert_which_strategy_runs(monkeypatch):
    """The toggle overrides the env value, so a fault claiming "Using sliding."
    would keep saying that while every chat summarizes."""
    cfg = _reload(monkeypatch, SKEIN_CONTEXT_STRATEGY="summarise")
    assert "Using" not in cfg.CONTEXT_STRATEGY_ERROR
    assert "summarise" in cfg.CONTEXT_STRATEGY_ERROR


def test_an_absurdly_long_number_does_not_kill_the_import(monkeypatch):
    """math.isfinite converts to a C double first, so a 309-digit int raises
    OverflowError — and an uncaught raise in config takes down every route,
    the ICS feed, and backups with it."""
    cfg = _reload(monkeypatch, SKEIN_CONTEXT_WINDOW="9" * 400)
    assert cfg.CONTEXT_WINDOW == 40
    assert "SKEIN_CONTEXT_WINDOW" in cfg.CONTEXT_STRATEGY_ERROR
