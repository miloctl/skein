"""Tests for the opt-in strands-agents-tools loader."""


def _load(monkeypatch, names):
    from app import config
    from app.agents import extra_tools as mod

    monkeypatch.setattr(config, "EXTRA_TOOLS", tuple(names))
    mod.extra_tools.cache_clear()
    tools = mod.extra_tools()
    mod.extra_tools.cache_clear()
    return tools


def test_default_is_empty(monkeypatch):
    assert _load(monkeypatch, []) == ()


def test_allowlisted_tools_load(monkeypatch):
    tools = _load(monkeypatch, ["calculator", "current_time"])
    names = {getattr(t, "tool_name", getattr(t, "__name__", "")) for t in tools}
    assert len(tools) == 2
    assert {"calculator", "current_time"} <= names


def test_rss_loads_with_its_optional_dependencies(monkeypatch):
    import pytest

    pytest.importorskip("feedparser", reason="strands-agents-tools[rss] is not installed")
    pytest.importorskip("html2text", reason="strands-agents-tools[rss] is not installed")
    tools = _load(monkeypatch, ["rss"])
    assert len(tools) == 1
    assert tools[0].tool_name == "rss"


def test_supported_extra_tool_inventory():
    from app.agents.extra_tools import ALLOWED

    assert set(ALLOWED) == {"calculator", "current_time", "batch", "sleep", "rss"}


def test_unlisted_research_tools_are_not_imported(monkeypatch):
    import builtins

    original = builtins.__import__
    attempted = []

    def guarded_import(name, *args, **kwargs):
        if name in {"strands_tools.tavily", "strands_tools.exa"}:
            attempted.append(name)
            raise ImportError("A non-allowlisted tool must not be imported.")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    tools = _load(
        monkeypatch,
        ["tavily_search", "tavily_extract", "exa_search", "exa_get_contents", "calculator"],
    )
    assert attempted == []
    assert len(tools) == 1
    assert tools[0].tool_name == "calculator"


def test_legacy_tool_spec_style_loads_as_module(monkeypatch):
    # batch is TOOL_SPEC-style: the Strands registry needs the module, not the
    # bare function ("unrecognized tool specification" otherwise)
    tools = _load(monkeypatch, ["batch"])
    assert len(tools) == 1
    assert hasattr(tools[0], "TOOL_SPEC")


def test_shell_and_friends_are_refused(monkeypatch, caplog):
    for dangerous in (
        "shell",
        "python_repl",
        "file_write",
        "editor",
        "mcp_client",
        "use_computer",
        "use_aws",
        # http_request is SSRF plus a third write path into our own keyless
        # API (forged X-User skips the gate); use_agent/use_llm take a
        # model-chosen provider endpoint; workflow/diagram reach subprocess
        # and path traversal
        "http_request",
        "use_agent",
        "use_llm",
        "workflow",
        "diagram",
    ):
        assert _load(monkeypatch, [dangerous]) == ()
    assert _load(monkeypatch, ["shell", "calculator"]) != ()  # good ones still load


def test_unknown_name_skipped_not_fatal(monkeypatch):
    tools = _load(monkeypatch, ["definitely-not-a-tool", "calculator"])
    assert len(tools) == 1


def test_extra_tools_security_cuts(monkeypatch):
    from app import config
    from app.agents import extra_tools as mod

    monkeypatch.setattr(
        config, "EXTRA_TOOLS", ("http_request", "use_agent", "use_llm", "workflow", "diagram")
    )
    mod.extra_tools.cache_clear()
    assert mod.extra_tools() == ()
    mod.extra_tools.cache_clear()


def test_the_calculator_refuses_a_sandbox_escape(monkeypatch, tmp_path):
    """Before strands-agents-tools 0.8.7, symbols("...", cls=N) handed the
    string to sympify with full builtins. This payload would create a file."""
    marker = tmp_path / "escaped"
    (calculator,) = _load(monkeypatch, ["calculator"])
    escape = f"symbols(\"__import__('pathlib').Path('{marker}').touch()\", cls=N)"
    result = calculator(expression=escape)
    assert not marker.exists()
    assert result["status"] == "error"
    assert "14" in str(calculator(expression="2 + 3*4")["content"])


def test_think_is_refused_because_the_model_picks_its_provider(monkeypatch):
    """think takes model_provider and model_settings from the model. That routes
    a turn around agents/team_agent.py::_model(), the one place allowed to
    choose a provider, as use_llm and use_agent would."""
    assert _load(monkeypatch, ["think"]) == ()
    assert _load(monkeypatch, ["think", "calculator"]) != ()


def test_the_package_floor_excludes_the_calculator_sandbox_escape():
    """A floor below 0.8.7 lets a workplace resolve the escapable calculator."""
    import tomllib
    from pathlib import Path

    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())["project"]
    (tools,) = [
        Requirement(value)
        for value in project["dependencies"]
        if canonicalize_name(Requirement(value).name) == "strands-agents-tools"
    ]
    assert "0.8.6" not in tools.specifier
    assert "0.8.9" in tools.specifier
