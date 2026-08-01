"""How a long chat is kept inside the context window, and the promise that
choosing wrong degrades instead of taking the API down."""

import importlib

import pytest

from app import config


def _reload(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return importlib.reload(config)


@pytest.fixture(autouse=True)
def _restore_config():
    yield
    importlib.reload(config)


def test_sliding_is_the_default():
    assert config.CONTEXT_STRATEGY == "sliding"
    assert config.CONTEXT_STRATEGY_ERROR == ""


def test_an_unknown_strategy_degrades_to_sliding_and_says_so(monkeypatch):
    cfg = _reload(monkeypatch, SKEIN_CONTEXT_STRATEGY="magic")
    assert cfg.CONTEXT_STRATEGY == "sliding"
    assert "magic" in cfg.CONTEXT_STRATEGY_ERROR
    assert "sliding" in cfg.CONTEXT_STRATEGY_ERROR


def test_a_non_numeric_knob_degrades_to_its_default(monkeypatch):
    """int() on operator input at import time would take the whole REST API
    down — the same trap SKEIN_MAX_TOKENS documents."""
    cfg = _reload(monkeypatch, SKEIN_CONTEXT_WINDOW="lots")
    assert cfg.CONTEXT_WINDOW == 40
    assert "SKEIN_CONTEXT_WINDOW" in cfg.CONTEXT_STRATEGY_ERROR


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
def test_the_strategy_selects_the_manager(monkeypatch, strategy, expected):
    from app.agents import team_agent

    monkeypatch.setattr(config, "CONTEXT_STRATEGY", strategy)
    assert type(team_agent._conversation_manager()).__name__ == expected


def test_knobs_reach_the_manager(monkeypatch):
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


def test_pin_first_zero_means_unpinned_not_pin_zero(monkeypatch):
    """0 must reach the SDK as None. Passed as 0 it would mean "pin nothing"
    explicitly, which is the same outcome today but stops being so the moment
    the SDK distinguishes them."""
    from app.agents import team_agent

    monkeypatch.setattr(config, "CONTEXT_PIN_FIRST", 0)
    assert team_agent._conversation_manager().pin_first is None


def test_mock_never_builds_a_conversation_manager(monkeypatch):
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
