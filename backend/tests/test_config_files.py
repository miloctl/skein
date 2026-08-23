"""The <NAME>_FILE hatch: the four settings that hold a whole document also
read it from a mounted YAML file, and a fault never quotes the document."""

import importlib
import os

import pytest

from app import config

_FILE_KEYS = (
    "SKEIN_MODELS",
    "SKEIN_MODELS_FILE",
    "SKEIN_MODEL_PRICES",
    "SKEIN_MODEL_PRICES_FILE",
    "SKEIN_MODEL_PARAMS",
    "SKEIN_MODEL_PARAMS_FILE",
    "SKEIN_MCP_SERVERS",
    "SKEIN_MCP_SERVERS_FILE",
)


@pytest.fixture(autouse=True)
def _restore_config():
    yield
    # scrub BEFORE reloading, the test_model_registry.py rule: fixture
    # finalization can run while a test's env is still live, and reloading
    # then bakes that test's file into the module for the next test.
    # "" and not pop, the conftest.py rule: config's load_dotenv() re-fills an
    # ABSENT var from backend/.env, so popping hands the next test whatever
    # this dev box happens to mount.
    for key in _FILE_KEYS:
        os.environ[key] = ""
    importlib.reload(config)


def _reload(monkeypatch, tmp_path, name, text):
    path = tmp_path / f"{name.lower()}.yaml"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setenv(f"{name}_FILE", str(path))
    return importlib.reload(config), path


def _reload_inline(monkeypatch, name, text):
    monkeypatch.setenv(f"{name}_FILE", "")
    monkeypatch.setenv(name, text)
    return importlib.reload(config)


def test_component_variables_compose_a_quoted_conninfo(monkeypatch):
    from psycopg.conninfo import conninfo_to_dict

    from app import config

    monkeypatch.delenv("SKEIN_DATABASE_URL", raising=False)
    monkeypatch.setenv("SKEIN_DB_HOST", "db.example")
    monkeypatch.setenv("SKEIN_DB_PORT", "5433")
    monkeypatch.setenv("SKEIN_DB_USER", "app")
    monkeypatch.setenv("SKEIN_DB_PASSWORD", "p@ss:w/o%r?d#!")
    monkeypatch.setenv("SKEIN_DB_NAME", "skein")
    info = conninfo_to_dict(config._database_url())
    assert info["host"] == "db.example"
    assert info["password"] == "p@ss:w/o%r?d#!"
    assert info["dbname"] == "skein"
    # a whole URL wins over the components, and neither set means fail closed
    monkeypatch.setenv("SKEIN_DATABASE_URL", "postgresql://x@y/z")
    assert config._database_url() == "postgresql://x@y/z"
    monkeypatch.delenv("SKEIN_DATABASE_URL", raising=False)
    monkeypatch.delenv("SKEIN_DB_HOST", raising=False)
    assert config._database_url() == ""


@pytest.mark.parametrize(
    "missing",
    ("SKEIN_DB_HOST", "SKEIN_DB_USER", "SKEIN_DB_PASSWORD", "SKEIN_DB_NAME"),
)
def test_partial_database_components_fail_closed(missing, monkeypatch):
    from app import config

    monkeypatch.delenv("SKEIN_DATABASE_URL", raising=False)
    for name, value in {
        "SKEIN_DB_HOST": "db.example",
        "SKEIN_DB_USER": "skein-app",
        "SKEIN_DB_PASSWORD": "secret",
        "SKEIN_DB_NAME": "skein",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(missing, raising=False)
    # Ambient libpq credentials must never complete a partial Skein contract.
    monkeypatch.setenv("PGUSER", "ambient-admin")
    monkeypatch.setenv("PGPASSWORD", "ambient-secret")
    assert config._database_url() == ""


def test_a_yaml_file_supplies_the_model_menu(monkeypatch, tmp_path):
    """The point of the hatch: nesting and comments, which a JSON string in a
    ConfigMap literal cannot carry."""
    cfg, _ = _reload(
        monkeypatch,
        tmp_path,
        "SKEIN_MODELS",
        """
# the deep-work model
- id: claude-opus-4-8
  label: Opus
  price:
    input: 15
    output: 75
- id: gpt-oss:120b-cloud
""",
    )
    assert cfg.MODELS_ERROR == ""
    assert set(cfg.MODELS) == {"claude-opus-4-8", "gpt-oss:120b-cloud"}
    assert cfg.MODELS["claude-opus-4-8"]["price"] == (15.0, 75.0)


def test_every_structured_setting_takes_a_file(monkeypatch, tmp_path):
    """One hatch, four settings. A setting that kept only the inline form
    would be the one an operator finds at deploy time."""
    cfg, _ = _reload(monkeypatch, tmp_path, "SKEIN_MODEL_PRICES", "claude-opus-4-8: [15, 75]\n")
    assert cfg.MODEL_PRICES_ERROR == ""
    assert cfg.MODEL_PRICES == {"claude-opus-4-8": (15.0, 75.0)}

    cfg, _ = _reload(monkeypatch, tmp_path, "SKEIN_MODEL_PARAMS", "temperature: 0.2\n")
    assert cfg.MODEL_PROVIDER_ERROR == ""
    assert cfg.MODEL_PARAMS == {"temperature": 0.2}

    cfg, _ = _reload(
        monkeypatch,
        tmp_path,
        "SKEIN_MCP_SERVERS",
        "- name: github\n  url: https://a.invalid/mcp/\n",
    )
    assert cfg.MCP_SERVERS_ERROR == ""
    assert "github" in cfg.MCP_SERVERS


def test_structured_settings_report_the_source_not_the_path(monkeypatch, tmp_path):
    cfg, path = _reload(monkeypatch, tmp_path, "SKEIN_MODELS", "- id: from-file\n")
    assert cfg.MODELS_SOURCE == "file"
    assert str(path) not in cfg.MODELS_SOURCE

    monkeypatch.setenv("SKEIN_MODELS_FILE", "")
    monkeypatch.setenv("SKEIN_MODELS", '[{"id": "inline"}]')
    cfg = importlib.reload(config)
    assert cfg.MODELS_SOURCE == "inline"

    monkeypatch.setenv("SKEIN_MODELS", "")
    cfg = importlib.reload(config)
    assert cfg.MODELS_SOURCE == "unset"

    cfg, path = _reload(
        monkeypatch,
        tmp_path,
        "SKEIN_MCP_SERVERS",
        "- name: remote\n  url: https://a.invalid/mcp/\n",
    )
    assert cfg.MCP_SERVERS_SOURCE == "file"
    assert str(path) not in cfg.MCP_SERVERS_SOURCE


def test_both_forms_set_is_a_fault(monkeypatch, tmp_path):
    """Never a silent winner: the operator edited one of the two and is
    watching that one."""
    monkeypatch.setenv("SKEIN_MODELS", '[{"id": "inline"}]')
    cfg, _ = _reload(monkeypatch, tmp_path, "SKEIN_MODELS", "- id: from-file\n")
    assert cfg.MODELS == {}
    assert cfg.MODELS_SOURCE == "both"
    assert "SKEIN_MODELS and SKEIN_MODELS_FILE are both set" in cfg.MODELS_ERROR


def test_a_missing_file_faults_without_naming_the_path(monkeypatch, tmp_path):
    """MODELS_ERROR reaches every signed-in user through /api/agents/status."""
    missing = tmp_path / "absent.yaml"
    monkeypatch.setenv("SKEIN_MODELS_FILE", str(missing))
    cfg = importlib.reload(config)
    assert cfg.MODELS == {}
    assert cfg.MODELS_SOURCE == "file"
    assert "SKEIN_MODELS_FILE cannot be read" in cfg.MODELS_ERROR
    assert str(missing) not in cfg.MODELS_ERROR


def test_a_broken_file_faults_without_quoting_it(monkeypatch, tmp_path):
    """A YAML error quotes the line it failed on, and a params document is a
    plausible place an operator put a credential. The line NUMBER is the whole
    debugging value."""
    cfg, _ = _reload(
        monkeypatch,
        tmp_path,
        "SKEIN_MODEL_PARAMS",
        'api_key: "sk-not-a-real-key\ntemperature: 0.2\n',
    )
    assert cfg.MODEL_PARAMS == {}
    assert "SKEIN_MODEL_PARAMS_FILE is not valid YAML" in cfg.MODEL_PROVIDER_ERROR
    assert "sk-not-a-real-key" not in cfg.MODEL_PROVIDER_ERROR


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("temperature: 0.1\ntemperature: 0.2\n", "duplicate"),
        ("1: value\n", "string"),
        ("temperature: .nan\n", "finite"),
        ("base: &base [1, 2]\nexpanded: [*base, *base]\n", "alias"),
    ],
)
def test_yaml_files_obey_the_strict_json_contract(monkeypatch, tmp_path, text, reason):
    cfg, path = _reload(monkeypatch, tmp_path, "SKEIN_MODEL_PARAMS", text)
    assert cfg.MODEL_PARAMS == {}
    assert reason in cfg.MODEL_PROVIDER_ERROR.lower()
    assert str(path) not in cfg.MODEL_PROVIDER_ERROR


def test_a_malformed_yaml_timestamp_degrades_instead_of_breaking_import(monkeypatch, tmp_path):
    cfg, path = _reload(
        monkeypatch,
        tmp_path,
        "SKEIN_MODEL_PARAMS",
        "expires: 2026-13-01\nsecret: sk-not-a-real-key\n",
    )
    assert cfg.MODEL_PARAMS == {}
    assert "SKEIN_MODEL_PARAMS_FILE" in cfg.MODEL_PROVIDER_ERROR
    assert "sk-not-a-real-key" not in cfg.MODEL_PROVIDER_ERROR
    assert str(path) not in cfg.MODEL_PROVIDER_ERROR


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ('{"temperature": 0.1, "temperature": 0.2}', "duplicate"),
        ('{"temperature": NaN}', "finite"),
    ],
)
def test_inline_documents_obey_the_strict_json_contract(monkeypatch, text, reason):
    cfg = _reload_inline(monkeypatch, "SKEIN_MODEL_PARAMS", text)
    assert cfg.MODEL_PARAMS == {}
    assert reason in cfg.MODEL_PROVIDER_ERROR.lower()


def test_an_oversized_inline_integer_degrades_instead_of_breaking_import(monkeypatch):
    text = '{"temperature":' + "1" * 5000 + "}"
    cfg = _reload_inline(monkeypatch, "SKEIN_MODEL_PARAMS", text)
    assert cfg.MODEL_PARAMS == {}
    assert "not valid JSON" in cfg.MODEL_PROVIDER_ERROR
    assert "1" * 100 not in cfg.MODEL_PROVIDER_ERROR


def test_a_deeply_nested_inline_document_degrades_instead_of_breaking_import(monkeypatch):
    nested = "[" * 1200 + "0" + "]" * 1200
    cfg = _reload_inline(monkeypatch, "SKEIN_MODEL_PARAMS", nested)
    assert cfg.MODEL_PARAMS == {}
    assert "nested too deeply" in cfg.MODEL_PROVIDER_ERROR


def test_a_yaml_only_type_is_refused(monkeypatch, tmp_path):
    """YAML reads a bare date as a date object. Passing it on would fail at
    the first model call instead of at boot."""
    cfg, _ = _reload(monkeypatch, tmp_path, "SKEIN_MODEL_PRICES", "expires: 2026-01-01\n")
    assert cfg.MODEL_PRICES == {}
    assert "is not JSON" in cfg.MODEL_PRICES_ERROR


def test_an_empty_file_is_refused_by_shape(monkeypatch, tmp_path):
    """An empty ConfigMap key must not read as "no menu configured" — the
    operator mounted it on purpose."""
    cfg, _ = _reload(monkeypatch, tmp_path, "SKEIN_MODELS", "")
    assert cfg.MODELS == {}
    assert "not a JSON array" in cfg.MODELS_ERROR


def test_a_deeply_nested_file_degrades_instead_of_breaking_import(monkeypatch, tmp_path):
    nested = "[" * 1200 + "0" + "]" * 1200
    cfg, path = _reload(monkeypatch, tmp_path, "SKEIN_MODEL_PARAMS", nested)
    assert cfg.MODEL_PARAMS == {}
    assert "nested too deeply" in cfg.MODEL_PROVIDER_ERROR
    assert str(path) not in cfg.MODEL_PROVIDER_ERROR
