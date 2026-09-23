"""The SKEIN_MODELS registry: strict parse, whole-list void on any fault, and
the shipped JSON Schema staying true to the code that actually validates."""

import importlib
import json
import os
from pathlib import Path

import jsonschema
import pytest

from app import config

SCHEMA_PATH = Path(config.BASE_DIR) / "schemas" / "skein_models.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text())


def _reload(monkeypatch, models, **env):
    value = models if isinstance(models, str) else json.dumps(models)
    monkeypatch.setenv("SKEIN_MODELS", value)
    for key, setting in env.items():
        monkeypatch.setenv(key, setting)
    return importlib.reload(config)


# The budget check reads the output cap the provider sends, so it needs a real
# provider: a typed-cap one (ollama) and one of the openai family.
TYPED_CAP = {"SKEIN_MODEL_PROVIDER": "ollama", "SKEIN_MODEL_BASE_URL": ""}
OPENAI_FAMILY = {
    "SKEIN_MODEL_PROVIDER": "openai_compatible",
    "SKEIN_MODEL_BASE_URL": "http://gateway.test/v1",
    "SKEIN_MODEL_ID": "m",
}
# restored, not popped: config's load_dotenv() re-fills an absent variable
# from backend/.env, which would bake a developer's provider into the suite
_PROVIDER_ENV = (
    "SKEIN_MODEL_PROVIDER",
    "SKEIN_MODEL_BASE_URL",
    "SKEIN_MODEL_ID",
    "SKEIN_MODEL_PARAMS",
    "SKEIN_MAX_TOKENS",
    "SKEIN_MODELS_FILE",
)


@pytest.fixture(autouse=True)
def _restore_config():
    saved = {key: os.environ.get(key) for key in _PROVIDER_ENV}
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    # scrub BEFORE reloading, the test_context_strategy.py rule: fixture
    # finalization can run while a test's env is still live, and reloading
    # then bakes that test's registry into the module for the next test
    for key in ("SKEIN_MODELS", "SKEIN_MODEL_PRICES"):
        os.environ.pop(key, None)
    importlib.reload(config)


VALID = [
    {
        "id": "claude-opus-4-8",
        "label": "Opus — deep work",
        "detail": "Slow and expensive. Reads a whole engagement in one pass.",
        "max_tokens": 8192,
        "context_tokens": 200_000,
        "price": {"input": 15, "output": 75},
        "params": {"temperature": 0.7},
    },
    # every optional field absent — the minimal legal entry
    {"id": "gpt-oss:120b-cloud"},
    # zero-fraction floats: JSON Schema 2020-12 "integer" admits them, so the
    # code must too, or a green ConfigMap editor produces a red /health
    {"id": "float-tuned", "max_tokens": 4096.0, "context_tokens": 32768.0},
    # reasoning levels: a subset of the vocabulary, each a params object; a
    # level's null removes a key a persona or SKEIN_MODEL_PARAMS would send
    {
        "id": "claude-sonnet-4-6",
        "reasoning": {
            "high": {"thinking": {"type": "adaptive"}, "output_config": {"effort": "high"}},
            "none": {"thinking": {"type": "disabled"}},
            "low": {
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": "low"},
                "temperature": None,
            },
        },
    },
    {
        "id": "claude-haiku-4-5",
        "max_tokens": 16000,
        "reasoning": {"medium": {"thinking": {"type": "enabled", "budget_tokens": 8000}}},
    },
    {
        "id": "safe-escape-hatches",
        "params": {
            "extra_headers": {"X-Tenant": "acme"},
            "extra_body": {"seed": 7},
            "extra_query": {"tenant": "safe"},
            "additional_args": {"custom": "safe"},
        },
    },
]

# Rejected by BOTH the schema and config.py. Each entry is one distinct fault;
# test_schema_and_code_agree walks them so the shipped schema cannot drift
# looser or stricter than the code.
INVALID = [
    [],
    {"not": "a list"},
    [{"label": "no id"}],
    [{"id": ""}],
    [{"id": "   "}],
    [{"id": "m", "max_tokens": 2048.5}],
    [{"id": "m", "pricee": {"input": 1, "output": 2}}],
    [{"id": "m", "label": ""}],
    [{"id": "m", "label": "x" * 81}],
    [{"id": "m", "detail": "x" * 201}],
    [{"id": "m", "max_tokens": 0}],
    [{"id": "m", "max_tokens": True}],
    [{"id": "m", "context_tokens": 512}],
    [{"id": "m", "price": {"input": 1}}],
    [{"id": "m", "price": {"input": -1, "output": 2}}],
    [{"id": "m", "price": {"input": True, "output": 2}}],
    [{"id": "m", "price": [1, 2]}],
    [{"id": "m", "params": "hot"}],
    [{"id": "m", "params": {"model": "other"}}],
    [{"id": "m", "params": {"model_id": "other"}}],
    [{"id": "m", "params": {"endpoint_url": "https://redirect.invalid"}}],
    [{"id": "m", "params": {"region_name": "other-region"}}],
    [{"id": "m", "params": {"boto_session": "other-session"}}],
    [{"id": "m", "params": {"boto_client_config": {}}}],
    *[
        [{"id": "m", "params": {field: "blocked"}}]
        for field in (
            "messages",
            "tools",
            "system",
            "tool_choice",
            "stream",
            "stream_options",
            "timeout",
            "host",
            "ollama_client_args",
        )
    ],
    [{"id": "m", "params": {"extra_body": {"model": "other"}}}],
    [{"id": "m", "params": {"extra_body": {"metadata": {"model": "other"}}}}],
    [{"id": "m", "params": {"extra_body": {"messages": []}}}],
    [{"id": "m", "params": {"extra_body": {"max_completion_tokens": 1}}}],
    [{"id": "m", "params": {"extra_body": {"provider": {"order": ["other"]}}}}],
    [{"id": "m", "params": {"extra_body": {"models": ["other"]}}}],
    [{"id": "m", "params": {"extra_body": {"route": "fallback"}}}],
    [{"id": "m", "params": {"extra_query": {"model": "other"}}}],
    [{"id": "m", "params": {"extra_query": {"provider": "other"}}}],
    [{"id": "m", "params": {"additional_args": {"model": "other"}}}],
    [{"id": "m", "params": {"additional_args": {"modelId": "other"}}}],
    [{"id": "m", "params": {"additional_args": {"options": {}}}}],
    [
        {
            "id": "m",
            "params": {"additional_args": {"additionalModelRequestFields": {"system": "x"}}},
        }
    ],
    [{"id": "m", "params": {"additional_args": {"inferenceConfig": {}}}}],
    [{"id": "m", "params": {"extra_body": "not-an-object"}}],
    [{"id": "m", "params": {"extra_query": "not-an-object"}}],
    [{"id": "m", "params": {"additional_args": "not-an-object"}}],
    [{"id": "m", "attachments": "image"}],
    [{"id": "m", "attachments": [1]}],
    [{"id": "m", "attachments": ["video"]}],
    [{"id": "m", "reasoning": "high"}],
    [{"id": "m", "reasoning": {}}],
    [{"id": "m", "reasoning": {"extreme": {}}}],
    # the name is `none`: an unquoted `off` is a YAML 1.1 boolean, and a menu
    # file with it fails to load
    [{"id": "m", "reasoning": {"off": {}}}],
    [{"id": "m", "reasoning": {"high": "fast"}}],
    [{"id": "m", "reasoning": {"high": {"model": "other"}}}],
    [{"id": "m", "reasoning": {"high": {"tool_choice": {"type": "any"}}}}],
    [{"id": "m", "reasoning": {"high": {"extra_body": {"provider": {"order": ["x"]}}}}}],
    # removing the output cap leaves each provider on a different fallback,
    # so no budget check could know the limit
    [{"id": "m", "reasoning": {"high": {"max_tokens": None}}}],
    # pins the v1 decision: no cached_input until usage_log carries
    # cache-read tokens — a price nothing multiplies is a believed number
    # not in effect
    [{"id": "m", "price": {"input": 1, "output": 2, "cached_input": 0.1}}],
]


def test_a_valid_registry_parses(monkeypatch):
    cfg = _reload(monkeypatch, VALID)
    assert cfg.MODELS_ERROR == ""
    assert set(cfg.MODELS) == {
        "claude-opus-4-8",
        "gpt-oss:120b-cloud",
        "float-tuned",
        "safe-escape-hatches",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
    }
    full = cfg.MODELS["claude-opus-4-8"]
    assert full["price"] == (15.0, 75.0)
    assert full["context_tokens"] == 200_000
    minimal = cfg.MODELS["gpt-oss:120b-cloud"]
    assert minimal["label"] == "gpt-oss:120b-cloud"  # label falls back to id
    assert minimal["price"] is None
    assert minimal["params"] == {}
    # normalized to real ints, so the SDK never sees a float where a token
    # count belongs
    floaty = cfg.MODELS["float-tuned"]
    assert floaty["max_tokens"] == 4096 and isinstance(floaty["max_tokens"], int)
    assert floaty["context_tokens"] == 32768 and isinstance(floaty["context_tokens"], int)
    assert cfg.MODELS["safe-escape-hatches"]["params"]["extra_body"] == {"seed": 7}
    assert cfg.MODELS["safe-escape-hatches"]["params"]["extra_query"] == {"tenant": "safe"}


def test_attachments_is_declared_per_model_and_absence_differs_from_empty(monkeypatch):
    """The provider knows what its formatter can express; only the operator
    knows what the endpoint they pointed at is serving. Absent must stay
    distinguishable from an empty list, or "use the provider default" and
    "this model takes nothing" become the same entry."""
    cfg = _reload(
        monkeypatch,
        [
            {"id": "vision", "attachments": ["image"]},
            {"id": "text-only", "attachments": []},
            {"id": "unstated"},
        ],
    )
    assert cfg.MODELS_ERROR == ""
    assert cfg.MODELS["vision"]["attachments"] == ("image",)
    assert cfg.MODELS["text-only"]["attachments"] == ()
    assert cfg.MODELS["unstated"]["attachments"] is None


def test_no_registry_means_no_menu_and_no_error(monkeypatch):
    # "" and not delenv, for the reason conftest.py records: the reload below
    # runs load_dotenv(), which re-fills an ABSENT variable from backend/.env,
    # so delenv passes only on a box whose operator curated no menu.
    monkeypatch.setenv("SKEIN_MODELS", "")
    cfg = importlib.reload(config)
    assert cfg.MODELS == {}
    assert cfg.MODELS_ERROR == ""


def test_one_bad_entry_voids_the_whole_list(monkeypatch):
    """A partial menu looks complete — an admin picks from whatever renders,
    so the menu is all-or-nothing."""
    cfg = _reload(monkeypatch, [VALID[0], {"id": "m", "max_tokens": 0}])
    assert cfg.MODELS == {}
    assert "entry 2 (m)" in cfg.MODELS_ERROR
    assert "max_tokens" in cfg.MODELS_ERROR


def test_every_fault_is_reported_not_just_the_first(monkeypatch):
    """One at a time makes an operator with two typos restart twice."""
    cfg = _reload(
        monkeypatch,
        [{"id": "a", "max_tokens": 0}, {"id": "b", "context_tokens": 1}],
    )
    assert "entry 1 (a)" in cfg.MODELS_ERROR
    assert "entry 2 (b)" in cfg.MODELS_ERROR


def test_an_entry_with_no_id_still_reports_its_field_faults(monkeypatch):
    """Both faults in one restart, even when the id itself is the problem —
    and no import-time raise (the KeyError trap on entry["id"])."""
    cfg = _reload(monkeypatch, [{"max_tokens": 0}])
    assert "entry 1 has no usable id" in cfg.MODELS_ERROR
    assert "max_tokens" in cfg.MODELS_ERROR


def test_a_fault_names_fields_never_values(monkeypatch):
    """MODELS_ERROR reaches every signed-in user via agents/status, and a
    params or price value is a plausible place an operator put a credential."""
    cfg = _reload(
        monkeypatch,
        [{"id": "m", "price": {"input": "sk-skein-oops-a-secret", "output": 2}}],
    )
    assert cfg.MODELS == {}
    assert "price.input" in cfg.MODELS_ERROR
    assert "sk-skein-oops-a-secret" not in cfg.MODELS_ERROR


def test_duplicate_ids_are_refused(monkeypatch):
    """Which entry wins is otherwise silent — the menu must not guess."""
    cfg = _reload(monkeypatch, [{"id": "m"}, {"id": "m"}])
    assert cfg.MODELS == {}
    assert "repeats an earlier id" in cfg.MODELS_ERROR


def test_a_bare_infinity_is_refused(monkeypatch):
    """Infinity is not JSON and breaks every cost sum it touches."""
    cfg = _reload(monkeypatch, '[{"id": "m", "price": {"input": Infinity, "output": 2}}]')
    assert cfg.MODELS == {}
    assert "not finite" in cfg.MODELS_ERROR


def test_a_huge_integer_price_degrades_instead_of_killing_the_import(monkeypatch):
    """math.isfinite converts to a C double first, so a 309-digit JSON int
    raises OverflowError — and an uncaught raise in config takes down every
    route, the ICS feed, and backups with it (the _ctx_num trap, in the
    registry's price path)."""
    huge = "1" + "0" * 400
    cfg = _reload(monkeypatch, f'[{{"id": "m", "price": {{"input": {huge}, "output": 2}}}}]')
    assert cfg.MODELS == {}
    assert "price.input" in cfg.MODELS_ERROR


def test_a_huge_integer_in_the_price_table_degrades_too(monkeypatch):
    """The same OverflowError trap in the SKEIN_MODEL_PRICES sibling — one
    guard covers both tables."""
    huge = "1" + "0" * 400
    monkeypatch.setenv("SKEIN_MODEL_PRICES", f'{{"m": [{huge}, 2]}}')
    import importlib

    cfg = importlib.reload(config)
    assert cfg.MODEL_PRICES == {}
    assert "SKEIN_MODEL_PRICES is unusable" in cfg.MODEL_PRICES_ERROR


def test_unparseable_json_degrades_and_says_so(monkeypatch):
    cfg = _reload(monkeypatch, "{nope")
    assert cfg.MODELS == {}
    assert "not valid JSON" in cfg.MODELS_ERROR


def test_the_registry_price_wins_over_the_price_table(monkeypatch):
    monkeypatch.setenv("SKEIN_MODEL_PRICES", json.dumps({"m": [1, 2], "other": [3, 4]}))
    _reload(monkeypatch, [{"id": "m", "price": {"input": 10, "output": 20}}])
    from app.services import usage

    # 1M in + 1M out: registry says 10+20, the table's 1+2 must lose
    assert usage.cost_for("m", 1_000_000, 1_000_000) == 30.0
    assert usage.model_price("m")[1] == "model_menu"
    # a model outside the registry still prices from the table
    assert usage.cost_for("other", 1_000_000, 1_000_000) == 7.0
    assert usage.model_price("other")[1] == "inline"
    # no price anywhere = None — honest, not zero
    assert usage.cost_for("unknown", 1_000_000, 1_000_000) is None
    assert usage.model_price("unknown") == (None, "unset")


def test_an_unpriced_registry_model_falls_back_to_the_price_table(monkeypatch):
    monkeypatch.setenv("SKEIN_MODEL_PRICES", json.dumps({"m": [1, 2]}))
    _reload(monkeypatch, [{"id": "m"}])
    from app.services import usage

    assert usage.cost_for("m", 1_000_000, 1_000_000) == 3.0


def test_schema_and_code_agree(monkeypatch):
    """The shipped schema is the ConfigMap editor's contract. It must accept
    what the code accepts and reject what the code rejects, or an operator's
    green editor produces a red /health."""
    jsonschema.validate(VALID, SCHEMA)
    assert _reload(monkeypatch, VALID).MODELS_ERROR == ""
    for bad in INVALID:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, SCHEMA)
        cfg = _reload(monkeypatch, bad)
        assert cfg.MODELS == {}, f"code accepted what the schema rejects: {bad}"
        assert cfg.MODELS_ERROR != ""


def test_reasoning_levels_parse_in_vocabulary_order(monkeypatch):
    cfg = _reload(monkeypatch, VALID)
    assert cfg.MODELS_ERROR == ""
    levels = cfg.MODELS["claude-sonnet-4-6"]["reasoning"]
    assert list(levels) == ["none", "low", "high"]
    assert levels["low"]["temperature"] is None
    assert cfg.MODELS["gpt-oss:120b-cloud"]["reasoning"] == {}


@pytest.mark.parametrize(
    "entry",
    [
        # at the 4096 default output limit, a 4096 budget leaves nothing to answer with
        {
            "id": "m",
            "reasoning": {"high": {"thinking": {"type": "enabled", "budget_tokens": 4096}}},
        },
        # Bedrock nests it one level deeper, and fails the same way
        {
            "id": "m",
            "reasoning": {
                "high": {"additional_request_fields": {"thinking": {"budget_tokens": 8192}}}
            },
        },
        # the entry's own cap is the limit when the level sets none
        {
            "id": "m",
            "max_tokens": 8000,
            "reasoning": {"high": {"thinking": {"type": "enabled", "budget_tokens": 8000}}},
        },
    ],
)
def test_a_thinking_budget_at_or_above_the_output_limit_is_refused_at_startup(monkeypatch, entry):
    """Anthropic refuses budget_tokens >= max_tokens on every request. Unchecked,
    the level loads and every chat turn that uses it fails."""
    cfg = _reload(monkeypatch, [entry], **TYPED_CAP)
    assert cfg.MODELS == {}
    assert "budget_tokens" in cfg.MODELS_ERROR and "high" in cfg.MODELS_ERROR
    assert "4096" not in cfg.MODELS_ERROR and "8000" not in cfg.MODELS_ERROR


def test_a_level_can_raise_its_own_output_limit_above_its_budget(monkeypatch):
    cfg = _reload(
        monkeypatch,
        [
            {
                "id": "m",
                "reasoning": {
                    "high": {
                        "max_tokens": 20000,
                        "thinking": {"type": "enabled", "budget_tokens": 16000},
                    }
                },
            }
        ],
    )
    assert cfg.MODELS_ERROR == ""
    assert cfg.MODELS["m"]["reasoning"]["high"]["max_tokens"] == 20000


@pytest.mark.parametrize(
    "env, entry",
    [
        # a budget in the entry params meets the smaller cap a level sets
        (
            {},
            {
                "id": "m",
                "max_tokens": 16000,
                "params": {"thinking": {"type": "enabled", "budget_tokens": 8000}},
                "reasoning": {"high": {"max_tokens": 4000}},
            },
        ),
        # the entry params cap outranks the entry's typed cap
        (
            {},
            {
                "id": "m",
                "max_tokens": 32000,
                "params": {"max_tokens": 4000},
                "reasoning": {"high": {"thinking": {"type": "enabled", "budget_tokens": 8000}}},
            },
        ),
        # SKEIN_MODEL_PARAMS outranks SKEIN_MAX_TOKENS
        (
            {"SKEIN_MODEL_PARAMS": '{"max_tokens": 2000}', "SKEIN_MAX_TOKENS": "16000"},
            {
                "id": "m",
                "reasoning": {"high": {"thinking": {"type": "enabled", "budget_tokens": 8000}}},
            },
        ),
    ],
)
def test_the_budget_check_reads_the_request_the_level_builds(monkeypatch, env, entry):
    """The check must see what reaches the wire: every layer merged under the
    level, and the cap that wins among them."""
    cfg = _reload(monkeypatch, [entry], **TYPED_CAP, **env)
    assert cfg.MODELS == {}
    assert "budget_tokens" in cfg.MODELS_ERROR


def test_the_openai_family_checks_the_cap_it_sends(monkeypatch):
    """The openai family sends no typed cap: its limit is max_completion_tokens
    or max_tokens in params. Checked against SKEIN_MAX_TOKENS instead, a valid
    level voids the whole menu, and the fault advises a key gpt-5 refuses."""
    level = {"max_completion_tokens": 20000, "extra_body": {"thinking": {"budget_tokens": 10000}}}
    cfg = _reload(monkeypatch, [{"id": "m", "reasoning": {"high": level}}], **OPENAI_FAMILY)
    assert cfg.MODELS_ERROR == ""
    small = {"max_completion_tokens": 4000, "extra_body": {"thinking": {"budget_tokens": 8000}}}
    cfg = _reload(monkeypatch, [{"id": "m", "reasoning": {"high": small}}], **OPENAI_FAMILY)
    assert "budget_tokens" in cfg.MODELS_ERROR


def test_the_readme_recipes_load_from_a_menu_file(monkeypatch, tmp_path):
    """Operators copy these into SKEIN_MODELS_FILE. A recipe that fails to
    load voids the whole menu."""
    readme = (Path(config.BASE_DIR).parent / "README.md").read_text()
    section = readme.split("**Reasoning levels.**", 1)[1]
    recipes = section.split("```yaml\n", 1)[1].split("```", 1)[0]
    menu = tmp_path / "models.yaml"
    menu.write_text(recipes)
    cfg = _reload(monkeypatch, "", SKEIN_MODELS_FILE=str(menu), **TYPED_CAP)
    assert cfg.MODELS_ERROR == ""
    assert all(entry["reasoning"] for entry in cfg.MODELS.values())
    assert len(cfg.MODELS) >= 5
