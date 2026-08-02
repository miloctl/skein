"""Persona behavior fields (model / temperature / tools), pack defaults, and
the strict validator lint.sh runs. The runtime stays lenient — a malformed
persona drops off the bench instead of failing chat — so the validator is the
only thing standing between a typo and a silent disappearance."""

import pytest

from app.services import personas


@pytest.fixture()
def bench(tmp_path, monkeypatch):
    monkeypatch.setattr(personas, "PERSONAS_DIR", tmp_path)
    monkeypatch.setattr(personas, "PACK_FILE", tmp_path / "pack.json")
    return tmp_path


def _write(bench, slug: str, front: str = "", body: str = "You are a probe.") -> None:
    (bench / f"{slug}.md").write_text(
        f"---\nname: Probe\ndescription: probes\n{front}---\n{body}", encoding="utf-8"
    )


def test_behavior_fields_parse(bench):
    _write(bench, "probe", "model: small-model\ntemperature: 0.3\ntools: save_note, ask_question\n")
    b = personas.behavior("probe")
    assert b == {"model": "small-model", "temperature": 0.3, "tools": ["save_note", "ask_question"]}


def test_absent_fields_mean_no_override_and_no_restriction(bench):
    _write(bench, "probe")
    assert personas.behavior("probe") == {"model": "", "temperature": None, "tools": None}


def test_pack_defaults_fill_gaps_and_persona_wins(bench):
    (bench / "pack.json").write_text(
        '{"defaults": {"model": "pack-model", "temperature": "0.7", "tools": "save_note"}}'
    )
    _write(bench, "probe", "temperature: 0.2\n")
    b = personas.behavior("probe")
    assert b["model"] == "pack-model"  # pack fills the gap
    assert b["temperature"] == 0.2  # persona wins field-by-field
    assert b["tools"] == ["save_note"]


def test_a_bad_temperature_degrades_to_none_at_runtime(bench):
    _write(bench, "probe", "temperature: warm\n")
    assert personas.behavior("probe")["temperature"] is None
    _write(bench, "probe2", "temperature: 9\n")
    assert personas.behavior("probe2")["temperature"] is None


def test_a_bad_pack_file_degrades_to_no_defaults(bench):
    (bench / "pack.json").write_text("{not json")
    _write(bench, "probe")
    assert personas.behavior("probe")["model"] == ""


def test_the_validator_is_strict_where_the_runtime_is_lenient(bench):
    _write(bench, "probe", "temperature: warm\ntools: save_note, not_a_tool\n")
    (bench / "no-front.md").write_text("no frontmatter at all")
    (bench / "pack.json").write_text('{"defaults": {"colour": "red"}}')
    errors = personas.validate_all()
    joined = "\n".join(errors)
    assert "temperature 'warm' is not a number" in joined
    assert "not_a_tool" in joined
    assert "no-front.md" in joined
    assert "colour" in joined
    assert len(errors) == 4


def test_the_shipped_bench_validates(fresh_db):
    """The real personas/ directory must always pass — this is the same check
    lint.sh runs, pinned here so pytest alone catches a bad edit."""
    assert personas.validate_all() == []


def test_known_tool_names_come_from_the_registry():
    names = personas._known_tool_names()
    assert "save_note" in names
    assert "remember" in names  # the registry, not a stale hand list
    assert "plan_project" in names


# ---- wiring into the agent ---------------------------------------------------


class _FakeModel:
    stateful = False

    def __init__(self):
        self.config = {"model_id": "fake"}

    def get_config(self):
        return self.config

    def update_config(self, **kwargs):
        self.config.update(kwargs)


def test_persona_allowlist_filters_the_agent_tools(bench, fresh_db, monkeypatch):
    from app import config
    from app.agents import team_agent

    _write(bench, "lens", "tools: save_note, ask_question\n")
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "")
    monkeypatch.setattr(config, "SESSIONS_DIR", config.DATA_DIR / "sessions")
    monkeypatch.setattr(team_agent, "_model", lambda **_: _FakeModel())

    agent = team_agent.build_agent("t-lens", persona="lens")
    assert sorted(agent.tool_names) == ["ask_question", "save_note"]


def test_no_allowlist_keeps_the_full_registry(bench, fresh_db, monkeypatch):
    from app import config
    from app.agents import team_agent
    from app.tools import ALL_TOOLS

    _write(bench, "open")
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "")
    monkeypatch.setattr(config, "SESSIONS_DIR", config.DATA_DIR / "sessions")
    monkeypatch.setattr(team_agent, "_model", lambda **_: _FakeModel())

    agent = team_agent.build_agent("t-open", persona="open")
    assert len(agent.tool_names) >= len(ALL_TOOLS)


def test_persona_model_and_temperature_reach_the_provider(bench, fresh_db, monkeypatch):
    """Through the REAL _model() on the keyless provider — the override must
    land in the model config, not just be accepted and dropped."""
    from app import config
    from app.agents import team_agent

    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "")
    model = team_agent._model(model_id="tiny-model", temperature=0.1)
    cfg = model.get_config()
    assert cfg["model_id"] == "tiny-model"
    assert cfg["temperature"] == 0.1


def test_persona_temperature_beats_global_model_params(bench, fresh_db, monkeypatch):
    """The persona is the more specific operator intent."""
    from app import config
    from app.agents import team_agent

    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "")
    monkeypatch.setattr(config, "MODEL_PARAMS", {"temperature": 0.9})
    model = team_agent._model(temperature=0.1)
    assert model.get_config()["temperature"] == 0.1


def test_a_persona_cannot_change_the_provider(bench):
    """behavior() exposes a model ID, never a provider or base URL — a persona
    file must not be able to redirect traffic to a different endpoint."""
    _write(bench, "probe", "model: anything\n")
    assert set(personas.behavior("probe")) == {"model", "temperature", "tools"}


def test_pack_json_native_types_are_accepted(bench):
    """A JSON list is the natural way to write a tool list in a JSON file —
    str() on it produced a repr matching no tool, silently building every
    persona with ZERO tools."""
    (bench / "pack.json").write_text(
        '{"defaults": {"tools": ["save_note", "ask_question"], "temperature": 0.4}}'
    )
    _write(bench, "probe")
    b = personas.behavior("probe")
    assert b["tools"] == ["save_note", "ask_question"]
    assert b["temperature"] == 0.4


def test_the_validator_checks_pack_default_values_not_just_names(bench):
    """The pack must not smuggle what a persona cannot: a bad default here
    strips tools from EVERY persona at once."""
    (bench / "pack.json").write_text('{"defaults": {"tools": "not_a_tool", "temperature": "warm"}}')
    errors = personas.validate_all()
    joined = "\n".join(errors)
    assert "not_a_tool" in joined
    assert "'warm' is not a number" in joined


def test_the_validator_rejects_unusable_pack_value_types(bench):
    (bench / "pack.json").write_text('{"defaults": {"tools": {"a": 1}}}')
    errors = personas.validate_all()
    assert any("list of strings" in e for e in errors)


def test_the_planner_inherits_the_persona_allowlist():
    """plan_project spawns a sub-agent under the SAME persona identity — an
    allowlist that stopped at the outer agent handed a read-only persona three
    write tools through this one door."""
    from app.agents import team_agent

    names = [team_agent._tool_name(t) for t in team_agent._planner_tools(None)]
    assert "create_task" in names and len(names) == 6

    narrowed = team_agent._planner_tools(["list_tasks", "plan_project", "list_playbooks"])
    assert sorted(team_agent._tool_name(t) for t in narrowed) == ["list_playbooks", "list_tasks"]


def test_build_agent_wires_the_planner_filter():
    """The closure must call _planner_tools with the persona allowlist — the
    helper being correct means nothing if build_agent ignores it."""
    import inspect

    from app.agents import team_agent

    src = inspect.getsource(team_agent.build_agent)
    assert '_planner_tools(beh["tools"])' in src


def test_extra_tools_cannot_be_granted_by_allowlist_name(bench, fresh_db, monkeypatch):
    """The docs claim extra/MCP tools cannot be allowlisted by name. The
    validator refuses such names in CI — but a persona file dropped on the box
    never meets CI, so the guarantee must hold at construction: the allowlist
    is intersected with the REGISTRY names before filtering the pool."""
    from strands import tool

    from app import config
    from app.agents import team_agent

    @tool
    def calculator(expression: str) -> str:
        """Fake extra tool.

        Args:
            expression: expression.
        """
        return expression

    _write(bench, "sneaky", "tools: save_note, calculator\n")
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "")
    monkeypatch.setattr(config, "SESSIONS_DIR", config.DATA_DIR / "sessions")
    monkeypatch.setattr(team_agent, "_model", lambda **_: _FakeModel())
    monkeypatch.setattr("app.agents.extra_tools.extra_tools", lambda: [calculator])

    agent = team_agent.build_agent("t-sneaky", persona="sneaky")
    assert agent.tool_names == ["save_note"]  # calculator filtered despite the name match
