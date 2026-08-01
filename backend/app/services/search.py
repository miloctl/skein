"""Programmatic search: FTS5 always, optional embeddings when configured.

`index_record` is called by every service on write, so the index is always
current. Embeddings are a pluggable enhancement gated on config.EMBED_READY
(SKEIN_EMBEDDINGS=1 plus a valid SKEIN_EMBED_PROVIDER setup — openai,
openai_compatible, or ollama): vectors are stored alongside and blended into
results; otherwise hybrid search degrades cleanly to FTS-only.
"""

import json

from .. import config, db


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


def deindex_record(entity: str, entity_id: int) -> None:
    """Hard-deleted rows must leave the index too — search and /ask must
    never cite a record that no longer exists."""
    db.execute(
        "DELETE FROM search_index WHERE entity = ? AND entity_id = ?",
        (entity, entity_id),
    )


def ask(q: str, limit: int = 5) -> dict:
    """Q&A with receipts: deterministic FTS answer where every snippet cites
    its row (entity #id), findings-style. Degrades honestly keyless — an LLM
    synthesis can be layered on top later, but the citations ARE the answer.
    NOTE for any future UI: snippets contain literal <b> markup from FTS —
    render as text or strip it; never innerHTML indexed user content."""
    hits = search(q, limit)
    note = ""
    if not hits:
        # natural phrasing rarely matches as a phrase — fall back to OR of
        # the meaningful words, bm25-ranked, and say so
        words = [w for w in q.split() if len(w) > 2]
        if len(words) > 1:
            hits = search(" OR ".join(_fts_quote(w) for w in words), limit, raw=True)
            if hits:
                note = "no exact match — loosely related results (word overlap)"
    if not hits:
        note = "nothing indexed matches — try different words"
    return {
        "question": q,
        "citations": [
            {
                "ref": f"{h['entity']} #{h['entity_id']}",
                "title": h["title"],
                "snippet": h["snippet"],
            }
            for h in hits
        ],
        "note": note,
    }


def search(q: str, limit: int = 20, raw: bool = False) -> list[dict]:
    """raw=True passes q as a pre-built FTS expression (callers must quote
    each term themselves — ask()'s OR fallback does)."""
    if not q.strip():
        return []
    hits = db.query(
        "SELECT entity, entity_id, title,"
        " snippet(search_index, 3, '<b>', '</b>', '…', 12) AS snippet,"
        " bm25(search_index) AS rank"
        " FROM search_index WHERE search_index MATCH ?"
        " ORDER BY rank LIMIT ?",
        (q if raw else _fts_quote(q), limit),
    )
    if len(hits) < limit:
        seen = {(h["entity"], h["entity_id"]) for h in hits}
        for s in semantic_search(q, limit - len(hits)):
            if (s["entity"], s["entity_id"]) in seen:
                continue
            row = db.query_one(
                "SELECT title, substr(body, 1, 120) AS snippet FROM search_index"
                " WHERE entity = ? AND entity_id = ?",
                (s["entity"], s["entity_id"]),
            )
            if row:
                hits.append(
                    {
                        "entity": s["entity"],
                        "entity_id": s["entity_id"],
                        "title": row["title"],
                        "snippet": row["snippet"],
                        "rank": None,
                    }
                )
    return hits


# --- optional embedding layer (activates when configured) -------------------
# Provider-aware via config.EMBED_*: openai, openai_compatible, or ollama —
# all speak the OpenAI /v1/embeddings shape, so one client covers them.
# Gates read config at CALL time, not import: an import-time value copy is the
# stale-binding bug chat.py had, and it breaks config reloads in tests.


def _maybe_embed(entity: str, entity_id: int, text: str) -> None:
    if not config.EMBED_READY:
        return
    try:
        vec = _embed(text)
        db.execute(
            "INSERT OR REPLACE INTO embeddings (entity, entity_id, model, vector)"
            " VALUES (?, ?, ?, ?)",
            (entity, entity_id, config.EMBED_MODEL, json.dumps(vec)),
        )
    except Exception:
        pass  # embeddings are best-effort; FTS remains authoritative


def _embed(text: str) -> list[float]:
    from openai import OpenAI

    client = OpenAI(
        base_url=config.EMBED_BASE_URL or None,
        # local servers ignore it, but the client demands something
        api_key=config.embed_key() or "not-needed",
    )
    resp = client.embeddings.create(model=config.EMBED_MODEL, input=text[:8000])
    return resp.data[0].embedding


def semantic_search(q: str, limit: int = 10) -> list[dict]:
    """Cosine-similarity search over stored vectors; empty without embeddings.
    Only vectors from the CURRENT model are compared — similarity across two
    embedding spaces is noise, so a model change invalidates rather than
    poisons (stale rows are re-embedded on the record's next write)."""
    if not config.EMBED_READY:
        return []
    try:
        qv = _embed(q)
        rows = db.query(
            "SELECT entity, entity_id, vector FROM embeddings WHERE model = ?",
            (config.EMBED_MODEL,),
        )
    except Exception:
        return []

    def cos(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        return dot / (na * nb) if na and nb else 0.0

    scored = [
        {
            "entity": r["entity"],
            "entity_id": r["entity_id"],
            "score": cos(qv, json.loads(r["vector"])),
        }
        for r in rows
    ]
    return sorted(scored, key=lambda r: -r["score"])[:limit]
