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


def _reload(monkeypatch, models):
    value = models if isinstance(models, str) else json.dumps(models)
    monkeypatch.setenv("SKEIN_MODELS", value)
    return importlib.reload(config)


@pytest.fixture(autouse=True)
def _restore_config():
    yield
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
