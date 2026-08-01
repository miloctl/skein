"""Embeddings provider config: independent of the chat provider, keyless via
ollama, same leak rules as chat, and vectors tagged with their model so a
model switch invalidates instead of poisoning. No network, no keys."""

import importlib

import pytest

from app import config, db
from app.services import search


def _reload_config(monkeypatch, **env):
    """Reload config against ONLY the env this test sets (see
    test_model_providers._reload_config for why dotenv must be neutralised)."""
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    for var in (
        "SKEIN_EMBEDDINGS",
        "SKEIN_EMBED_PROVIDER",
        "SKEIN_EMBED_MODEL",
        "SKEIN_EMBED_BASE_URL",
        "SKEIN_EMBED_API_KEY",
        "SKEIN_OLLAMA_HOST",
        "OPENAI_API_KEY",
        "OLLAMA_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return importlib.reload(config)


@pytest.fixture
def restore_config(monkeypatch):
    yield
    monkeypatch.undo()
    importlib.reload(config)


# ---- readiness / validation ----


def test_off_by_default(monkeypatch, restore_config):
    cfg = _reload_config(monkeypatch)
    assert cfg.EMBED_READY is False
    assert cfg.EMBEDDINGS_ERROR == ""


def test_openai_without_key_reports_and_disables(monkeypatch, restore_config):
    cfg = _reload_config(monkeypatch, SKEIN_EMBEDDINGS="1")
    assert cfg.EMBED_READY is False
    assert "OPENAI_API_KEY" in cfg.EMBEDDINGS_ERROR
    assert "ollama" in cfg.EMBEDDINGS_ERROR  # the keyless way out is named


def test_ollama_is_keyless(monkeypatch, restore_config):
    """The whole point: semantic search with no API key anywhere."""
    cfg = _reload_config(
        monkeypatch,
        SKEIN_EMBEDDINGS="1",
        SKEIN_EMBED_PROVIDER="ollama",
        SKEIN_EMBED_MODEL="nomic-embed-text",
    )
    assert cfg.EMBED_READY is True
    assert cfg.EMBED_BASE_URL == "http://localhost:11434/v1"
    assert cfg.embed_key() == ""


def test_ollama_base_url_follows_the_host(monkeypatch, restore_config):
    cfg = _reload_config(
        monkeypatch,
        SKEIN_EMBEDDINGS="1",
        SKEIN_EMBED_PROVIDER="ollama",
        SKEIN_EMBED_MODEL="nomic-embed-text",
        SKEIN_OLLAMA_HOST="http://gpu-box:11434/",
    )
    assert cfg.EMBED_BASE_URL == "http://gpu-box:11434/v1"


def test_openai_compatible_requires_base_url(monkeypatch, restore_config):
    cfg = _reload_config(
        monkeypatch,
        SKEIN_EMBEDDINGS="1",
        SKEIN_EMBED_PROVIDER="openai_compatible",
        SKEIN_EMBED_MODEL="anything",
    )
    assert cfg.EMBED_READY is False
    assert "SKEIN_EMBED_BASE_URL" in cfg.EMBEDDINGS_ERROR


def test_openai_refuses_a_base_url(monkeypatch, restore_config):
    """Same rule as chat: a leftover endpoint must never redirect a paid key."""
    cfg = _reload_config(
        monkeypatch,
        SKEIN_EMBEDDINGS="1",
        OPENAI_API_KEY="sk-x",
        SKEIN_EMBED_BASE_URL="https://leftover.example/v1",
    )
    assert cfg.EMBED_READY is False
    assert "does not accept SKEIN_EMBED_BASE_URL" in cfg.EMBEDDINGS_ERROR


def test_openai_compatible_never_inherits_the_paid_key(monkeypatch, restore_config):
    cfg = _reload_config(
        monkeypatch,
        SKEIN_EMBEDDINGS="1",
        SKEIN_EMBED_PROVIDER="openai_compatible",
        SKEIN_EMBED_BASE_URL="https://third-party.example/v1",
        SKEIN_EMBED_MODEL="whatever",
        OPENAI_API_KEY="sk-REAL-PAID-KEY",
    )
    assert cfg.EMBED_READY is True
    assert cfg.embed_key() == ""  # explicit SKEIN_EMBED_API_KEY or nothing


def test_providers_without_default_demand_a_model(monkeypatch, restore_config):
    cfg = _reload_config(monkeypatch, SKEIN_EMBEDDINGS="1", SKEIN_EMBED_PROVIDER="ollama")
    assert cfg.EMBED_READY is False
    assert "SKEIN_EMBED_MODEL" in cfg.EMBEDDINGS_ERROR


def test_unknown_provider_reports(monkeypatch, restore_config):
    cfg = _reload_config(monkeypatch, SKEIN_EMBEDDINGS="1", SKEIN_EMBED_PROVIDER="cohere")
    assert cfg.EMBED_READY is False
    assert "cohere" in cfg.EMBEDDINGS_ERROR


def test_chat_provider_does_not_leak_into_embeddings(monkeypatch, restore_config):
    """Independence is the design: an anthropic chat box embeds via ollama."""
    cfg = _reload_config(
        monkeypatch,
        SKEIN_MODEL_PROVIDER="anthropic",
        SKEIN_EMBEDDINGS="1",
        SKEIN_EMBED_PROVIDER="ollama",
        SKEIN_EMBED_MODEL="nomic-embed-text",
    )
    assert cfg.EFFECTIVE_PROVIDER == "anthropic"
    assert cfg.EMBED_READY is True


# ---- the client and the vector store ----


class _FakeEmbeddings:
    def __init__(self, log):
        self._log = log

    def create(self, model, input):
        self._log.append({"model": model, "input": input})
        return type("R", (), {"data": [type("D", (), {"embedding": [1.0, 0.0, 0.0]})()]})()


def _fake_openai(log):
    class FakeOpenAI:
        def __init__(self, base_url=None, api_key=None):
            log.append({"base_url": base_url, "api_key": api_key})
            self.embeddings = _FakeEmbeddings(log)

    return FakeOpenAI


def _embed_ready(monkeypatch, **over):
    monkeypatch.setattr(config, "EMBED_READY", True)
    monkeypatch.setattr(config, "EMBED_MODEL", over.get("model", "test-embed"))
    monkeypatch.setattr(config, "EMBED_BASE_URL", over.get("base_url", "http://x:11434/v1"))
    monkeypatch.setattr(config, "EMBED_API_KEY", over.get("api_key", ""))


def test_client_gets_base_url_and_placeholder_key(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr("openai.OpenAI", _fake_openai(calls))
    _embed_ready(monkeypatch)
    monkeypatch.setattr(config, "EMBED_PROVIDER", "ollama")
    search._embed("hello")
    assert calls[0] == {"base_url": "http://x:11434/v1", "api_key": "not-needed"}
    assert calls[1]["model"] == "test-embed"


def test_vectors_are_tagged_and_reads_filter_by_model(monkeypatch, fresh_db):
    calls: list[dict] = []
    monkeypatch.setattr("openai.OpenAI", _fake_openai(calls))
    _embed_ready(monkeypatch, model="model-A")
    search._maybe_embed("note", 90001, "some text")
    row = db.query_one("SELECT model FROM embeddings WHERE entity='note' AND entity_id=90001")
    assert row and row["model"] == "model-A"

    assert any(r["entity_id"] == 90001 for r in search.semantic_search("q"))
    # switch models: the stored vector must stop matching, not skew results
    monkeypatch.setattr(config, "EMBED_MODEL", "model-B")
    assert not any(r["entity_id"] == 90001 for r in search.semantic_search("q"))


def test_disabled_embeddings_touch_nothing(monkeypatch):
    monkeypatch.setattr(config, "EMBED_READY", False)
    monkeypatch.setattr(
        "openai.OpenAI", _fake_openai([]) and (lambda *a, **k: pytest.fail("client built"))
    )
    search._maybe_embed("note", 90002, "text")
    assert search.semantic_search("q") == []
    assert search.search("") == []  # and plain search still behaves


def test_health_reports_the_embeddings_fault(monkeypatch, restore_config):
    _reload_config(monkeypatch, SKEIN_EMBEDDINGS="1")
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        body = client.get("/health").json()
    assert "OPENAI_API_KEY" in body["embeddings_error"]
    assert body["ok"] is True
