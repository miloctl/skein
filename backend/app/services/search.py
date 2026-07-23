"""Programmatic search: FTS5 today, optional embeddings when an API key exists.

`index_record` is called by every service on write, so the index is always
current. Embeddings are a pluggable enhancement: with OPENAI_API_KEY set and
STRANDS_EMBEDDINGS=1, vectors are stored alongside and blended into results;
without it, hybrid search degrades cleanly to FTS-only.
"""

import json
import os

from .. import db


def _fts_quote(q: str) -> str:
    return '"' + q.replace('"', '""') + '"'


def index_record(entity: str, entity_id: int, title: str, body: str) -> None:
    db.execute(
        "DELETE FROM search_index WHERE entity = ? AND entity_id = ?",
        (entity, entity_id),
    )
    db.execute(
        "INSERT INTO search_index (entity, entity_id, title, body) VALUES (?, ?, ?, ?)",
        (entity, entity_id, title, body),
    )
    _maybe_embed(entity, entity_id, f"{title}\n{body}")


def search(q: str, limit: int = 20) -> list[dict]:
    if not q.strip():
        return []
    return db.query(
        "SELECT entity, entity_id, title,"
        " snippet(search_index, 3, '<b>', '</b>', '…', 12) AS snippet,"
        " bm25(search_index) AS rank"
        " FROM search_index WHERE search_index MATCH ?"
        " ORDER BY rank LIMIT ?",
        (_fts_quote(q), limit),
    )


# --- optional embedding layer (activates when keys are connected) -----------

EMBEDDINGS_ENABLED = os.getenv("STRANDS_EMBEDDINGS", "0") == "1"
_EMBED_MODEL = os.getenv("STRANDS_EMBED_MODEL", "text-embedding-3-small")


def _maybe_embed(entity: str, entity_id: int, text: str) -> None:
    if not EMBEDDINGS_ENABLED or not os.getenv("OPENAI_API_KEY"):
        return
    try:
        vec = _embed(text)
        db.execute(
            "CREATE TABLE IF NOT EXISTS embeddings"
            " (entity TEXT, entity_id INTEGER, vector TEXT,"
            " PRIMARY KEY (entity, entity_id))"
        )
        db.execute(
            "INSERT OR REPLACE INTO embeddings (entity, entity_id, vector) VALUES (?, ?, ?)",
            (entity, entity_id, json.dumps(vec)),
        )
    except Exception:
        pass  # embeddings are best-effort; FTS remains authoritative


def _embed(text: str) -> list[float]:
    from openai import OpenAI

    client = OpenAI()
    resp = client.embeddings.create(model=_EMBED_MODEL, input=text[:8000])
    return resp.data[0].embedding


def semantic_search(q: str, limit: int = 10) -> list[dict]:
    """Cosine-similarity search over stored vectors; empty without embeddings."""
    if not EMBEDDINGS_ENABLED or not os.getenv("OPENAI_API_KEY"):
        return []
    try:
        qv = _embed(q)
        rows = db.query("SELECT entity, entity_id, vector FROM embeddings")
    except Exception:
        return []

    def cos(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        return dot / (na * nb) if na and nb else 0.0

    scored = [
        {"entity": r["entity"], "entity_id": r["entity_id"],
         "score": cos(qv, json.loads(r["vector"]))}
        for r in rows
    ]
    return sorted(scored, key=lambda r: -r["score"])[:limit]
