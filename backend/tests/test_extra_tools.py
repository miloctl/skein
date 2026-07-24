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


def test_legacy_tool_spec_style_loads_as_module(monkeypatch):
    # batch is TOOL_SPEC-style: the strands registry needs the module, not the
    # bare function ("unrecognized tool specification" otherwise)
    tools = _load(monkeypatch, ["batch"])
    assert len(tools) == 1
    assert hasattr(tools[0], "TOOL_SPEC")


def test_shell_and_friends_are_refused(monkeypatch, caplog):
    for dangerous in ("shell", "python_repl", "file_write", "editor",
                      "mcp_client", "use_computer", "use_aws",
                      # cut on security review: third write path / SSRF /
                      # model-chosen provider endpoints / path traversal
                      "http_request", "use_agent", "use_llm", "workflow",
                      "diagram"):
        assert _load(monkeypatch, [dangerous]) == ()
    assert _load(monkeypatch, ["shell", "calculator"]) != ()  # good ones still load


def test_unknown_name_skipped_not_fatal(monkeypatch):
    tools = _load(monkeypatch, ["definitely-not-a-tool", "calculator"])
    assert len(tools) == 1
