"""Embeddings provider config: independent of the chat provider, keyless via
ollama, same leak rules as chat, and vectors tagged with their model so a
model switch invalidates instead of poisoning. No network, no keys."""

import importlib
import sys

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
        SKEIN_MODEL_API_KEY="sk-test",  # anthropic degrades to mock without one
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


def _fake_openai(log, raise_on_create=False):
    class FakeOpenAI:
        def __init__(self, base_url=None, api_key=None, timeout=None, max_retries=None):
            log.append(
                {
                    "base_url": base_url,
                    "api_key": api_key,
                    "timeout": timeout,
                    "max_retries": max_retries,
                }
            )
            if raise_on_create:

                class Boom:
                    def create(self, **kw):
                        raise ConnectionError("endpoint down")

                self.embeddings = Boom()
            else:
                self.embeddings = _FakeEmbeddings(log)

    return FakeOpenAI


def _embed_ready(monkeypatch, **over):
    # the client is cached per (base_url, key); reset so each test gets its own fake
    monkeypatch.setattr(search, "_embed_client", None)
    monkeypatch.setattr(search, "_embed_client_key", ())
    monkeypatch.setattr(search, "_embed_warned", False)
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
    assert calls[0]["base_url"] == "http://x:11434/v1"
    assert calls[0]["api_key"] == "not-needed"
    # bounded, or a hung endpoint costs 30 minutes per service write
    assert calls[0]["timeout"] is not None
    assert calls[0]["max_retries"] == 0
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
    monkeypatch.setattr("openai.OpenAI", lambda *a, **k: pytest.fail("client built"))
    search._maybe_embed("note", 90002, "text")
    assert search.semantic_search("q") == []


def test_health_reports_the_embeddings_fault(monkeypatch, restore_config):
    _reload_config(monkeypatch, SKEIN_EMBEDDINGS="1")
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        body = client.get("/health").json()
    assert "OPENAI_API_KEY" in body["embeddings_error"]
    assert body["ok"] is True


def test_deindex_removes_the_vector_too(monkeypatch, fresh_db):
    """A deleted record's orphaned vector outranks live records and silently
    burns a semantic result slot per query — found live: a deleted note beat
    a live one 0.727 to 0.646 and search() dropped the hit on the missing
    search_index row, shrinking results with no error."""
    calls: list[dict] = []
    monkeypatch.setattr("openai.OpenAI", _fake_openai(calls))
    _embed_ready(monkeypatch, model="model-A")
    search.index_record("note", 90003, "title", "body")
    assert db.query_one("SELECT 1 AS x FROM embeddings WHERE entity_id = 90003")
    search.deindex_record("note", 90003)
    assert db.query_one("SELECT 1 AS x FROM embeddings WHERE entity_id = 90003") is None
    assert search.semantic_search("q") == []


def test_service_write_survives_a_dead_endpoint(monkeypatch, fresh_db, caplog):
    """The production promise behind the except-pass: index_record must land
    the FTS row even when the embeddings endpoint is down — and say so once,
    not once per write, and not never."""
    calls: list[dict] = []
    monkeypatch.setattr("openai.OpenAI", _fake_openai(calls, raise_on_create=True))
    _embed_ready(monkeypatch)
    with caplog.at_level("WARNING", logger="skein"):
        search.index_record("note", 90004, "resilient title", "resilient body")
        search.index_record("note", 90005, "second title", "second body")
    # asserted on search_index, not through search(): these ids have no row in
    # `notes`, and search.visible_hits now refuses a hit whose source row is
    # gone (it cannot be tier-checked). What this test pins is that
    # index_record LANDS the row when embeddings are down.
    indexed = db.query(
        "SELECT entity_id FROM search_index WHERE entity = 'note' ORDER BY entity_id"
    )
    assert [r["entity_id"] for r in indexed] == [90004, 90005]
    assert db.query_one("SELECT 1 AS x FROM embeddings WHERE entity_id = 90004") is None
    warnings = [r for r in caplog.records if "embedding failed" in r.message]
    assert len(warnings) == 1  # once per outage, not per write


def test_embed_api_key_satisfies_and_wins_for_openai(monkeypatch, restore_config):
    cfg = _reload_config(monkeypatch, SKEIN_EMBEDDINGS="1", SKEIN_EMBED_API_KEY="sk-embed-specific")
    assert cfg.EMBED_READY is True  # no OPENAI_API_KEY needed
    monkeypatch.setenv("OPENAI_API_KEY", "sk-ambient")
    assert cfg.embed_key() == "sk-embed-specific"


def test_ollama_refuses_an_embed_base_url(monkeypatch, restore_config):
    """Same rule as everywhere else now: the ollama endpoint derives from
    SKEIN_OLLAMA_HOST alone. A leftover SKEIN_EMBED_BASE_URL would mis-route
    the Ollama Cloud bearer key and double the /v1 suffix."""
    cfg = _reload_config(
        monkeypatch,
        SKEIN_EMBEDDINGS="1",
        SKEIN_EMBED_PROVIDER="ollama",
        SKEIN_EMBED_MODEL="nomic-embed-text",
        SKEIN_EMBED_BASE_URL="http://leftover:8001/v1",
    )
    assert cfg.EMBED_READY is False
    assert "does not accept SKEIN_EMBED_BASE_URL" in cfg.EMBEDDINGS_ERROR


def test_backfill_embeds_only_missing_rows(monkeypatch, fresh_db, capsys):
    from app import backfill_embeddings

    calls: list[dict] = []
    monkeypatch.setattr("openai.OpenAI", _fake_openai(calls))
    _embed_ready(monkeypatch, model="model-A")
    monkeypatch.setattr(config, "EMBEDDINGS_ENABLED", True)
    monkeypatch.setattr(config, "EMBEDDINGS_ERROR", "")
    monkeypatch.setattr(sys, "argv", ["backfill_embeddings"])

    # one covered row, one stale-model row, one bare row. The bare/stale rows
    # go in directly (index_record would embed them, defeating the setup) but
    # must keep the search_ids twin — search.index_record's invariant: an FTS
    # row with no twin gets its rowid re-minted and silently clobbered.
    search.index_record("note", 90010, "already covered", "body")  # embeds as model-A
    for eid, title, body in ((90011, "bare", "no vector"), (90012, "stale", "old model")):
        db.execute("INSERT INTO search_ids (entity, entity_id) VALUES (?, ?)", ("note", eid))
        sid = db.query_row(
            "SELECT id FROM search_ids WHERE entity = 'note' AND entity_id = ?", (eid,)
        )["id"]
        db.execute(
            "INSERT INTO search_index (rowid, entity, entity_id, title, body)"
            " VALUES (?, ?, ?, ?, ?)",
            (sid, "note", eid, title, body),
        )
    db.execute(
        "INSERT OR REPLACE INTO embeddings (entity, entity_id, model, vector) VALUES (?, ?, ?, ?)",
        ("note", 90012, "model-OLD", "[1,0,0]"),
    )

    embed_calls_before = len([c for c in calls if "input" in c])
    backfill_embeddings.main()
    out = capsys.readouterr().out
    assert "2 to embed" in out and "embedded 2, failed 0" in out
    # exactly two new embed calls — the covered row was not re-embedded
    assert len([c for c in calls if "input" in c]) == embed_calls_before + 2
    for eid in (90010, 90011, 90012):
        row = db.query_one(
            "SELECT 1 AS x FROM embeddings WHERE entity_id = ? AND model = 'model-A'", (eid,)
        )
        assert row, f"{eid} missing a current-model vector"


def test_backfill_refuses_when_misconfigured(monkeypatch):
    from app import backfill_embeddings

    monkeypatch.setattr(sys, "argv", ["backfill_embeddings"])
    monkeypatch.setattr(config, "EMBEDDINGS_ENABLED", True)
    monkeypatch.setattr(config, "EMBEDDINGS_ERROR", "broken on purpose")
    with pytest.raises(SystemExit) as e:
        backfill_embeddings.main()
    assert e.value.code == 2


def test_a_corrupt_vector_costs_one_result_not_the_search(monkeypatch, fresh_db, caplog):
    """JSONDecodeError subclasses ValueError, which main.py maps to 400 — an
    unguarded json.loads here once answered every /api/search query with
    "your input is invalid" over a row only we could have corrupted."""
    calls: list[dict] = []
    monkeypatch.setattr("openai.OpenAI", _fake_openai(calls))
    _embed_ready(monkeypatch, model="model-A")
    search.index_record("note", 90020, "healthy", "body")
    db.execute(
        "INSERT OR REPLACE INTO embeddings (entity, entity_id, model, vector)"
        " VALUES ('note', 90021, 'model-A', 'NOT JSON')"
    )
    with caplog.at_level("WARNING", logger="skein"):
        results = search.semantic_search("q")
    assert [r["entity_id"] for r in results] == [90020]
    assert any("unreadable vector" in r.message for r in caplog.records)


def test_a_semantic_hit_must_clear_the_similarity_floor(monkeypatch, fresh_db):
    """Sorting alone always returns `limit` rows, however unrelated they are.

    Enabling embeddings without a floor made every nonsense query come back
    with a full page of records it shares nothing with, and took the "nothing
    matches those words" answer off the table entirely -- measured on a real
    corpus, a junk string scored 0.49 against rows with no word in common.
    """
    import json as _json

    from app import db

    # two stored vectors: one identical to the query (cosine 1.0), one
    # orthogonal-ish (cosine well under any sane floor)
    db.execute(
        "INSERT INTO embeddings (entity, entity_id, model, vector) VALUES (?,?,?,?)",
        ("task", 1, "test-embed", _json.dumps([1.0, 0.0, 0.0])),
    )
    db.execute(
        "INSERT INTO embeddings (entity, entity_id, model, vector) VALUES (?,?,?,?)",
        ("task", 2, "test-embed", _json.dumps([0.0, 1.0, 0.0])),
    )
    monkeypatch.setattr("openai.OpenAI", _fake_openai([]))  # embeds to [1,0,0]
    _embed_ready(monkeypatch)
    monkeypatch.setattr(config, "EMBED_PROVIDER", "ollama")

    monkeypatch.setattr(config, "EMBED_MIN_SCORE", 0.5)
    hits = search.semantic_search("anything", limit=10)
    assert [(h["entity"], h["entity_id"]) for h in hits] == [("task", 1)]

    # a floor of 0 is the old behavior, and returns the unrelated row too
    monkeypatch.setattr(config, "EMBED_MIN_SCORE", 0.0)
    assert len(search.semantic_search("anything", limit=10)) == 2
