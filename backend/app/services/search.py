"""Programmatic search: FTS5 always, optional embeddings when configured.

`index_record` is called by every service on write, so the index is always
current. Embeddings are a pluggable enhancement gated on config.EMBED_READY
(SKEIN_EMBEDDINGS=1 plus a valid SKEIN_EMBED_PROVIDER setup — openai,
openai_compatible, or ollama): vectors are stored alongside and blended into
results; otherwise hybrid search degrades cleanly to FTS-only.
"""

import json
import logging
import re

from .. import config, db


def _fts_quote(q: str) -> str:
    return '"' + q.replace('"', '""') + '"'


_SHORT_ID = re.compile(r"^(?:#(\d{1,18})|([a-z_]+)(?:\s+#?|#)(\d{1,18}))$", re.ASCII)


def _short_id_hit(q: str) -> dict | None:
    """`#42` / `task 42` / `blocker #3` jump straight to the row — the forms
    /ask citations (`entity #id`) and git trailers (`#12`) put in front of
    people. Bare `#N` means task, matching what the trailers mean by it.
    The index row IS the kind list: an unknown word or missing id falls
    through to FTS, and a new entity is covered the day it is first indexed.
    ASCII + 18-digit cap keep every match inside SQLite's integer range, so
    an oversized id is an FTS miss, never an OverflowError. A separator is
    required ("task 42", never "task42") so a literal token in a body is
    not reinterpreted as a ref."""
    m = _SHORT_ID.match(q.strip().lower())
    if not m:
        return None
    row = db.query_one(
        "SELECT entity, entity_id, title, substr(body, 1, 120) AS snippet"
        " FROM search_index WHERE entity = ? AND entity_id = ?",
        (m.group(2) or "task", int(m.group(1) or m.group(3))),
    )
    return {**row, "rank": None} if row else None


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
    never cite a record that no longer exists. The vector goes with it: an
    orphaned embedding can't leak content (snippets come from search_index),
    but it outranks live records and silently burns a semantic result slot
    per query, forever."""
    db.execute(
        "DELETE FROM search_index WHERE entity = ? AND entity_id = ?",
        (entity, entity_id),
    )
    db.execute(
        "DELETE FROM embeddings WHERE entity = ? AND entity_id = ?",
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
    direct = None if raw else _short_id_hit(q)
    if direct:
        rest = [
            h
            for h in hits
            if (h["entity"], h["entity_id"]) != (direct["entity"], direct["entity_id"])
        ]
        hits = [direct, *rest[: limit - 1]]
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


_embed_client = None
_embed_client_key: tuple = ()
_embed_warned = False


def _maybe_embed(entity: str, entity_id: int, text: str) -> None:
    global _embed_warned
    if not config.EMBED_READY:
        return
    try:
        vec = _embed(text)
        db.execute(
            "INSERT OR REPLACE INTO embeddings (entity, entity_id, model, vector)"
            " VALUES (?, ?, ?, ?)",
            (entity, entity_id, config.EMBED_MODEL, json.dumps(vec)),
        )
        _embed_warned = False
    except Exception as exc:
        # best-effort by design — FTS remains authoritative — but not silent:
        # a dead endpoint with valid config is otherwise invisible (/health
        # only reports CONFIG faults). Once per outage, not per write.
        if not _embed_warned:
            _embed_warned = True
            logging.getLogger("skein").warning(
                "embedding failed for %s#%s (further failures muted until one succeeds): %s",
                entity,
                entity_id,
                exc,
            )


def _embed(text: str) -> list[float]:
    """One cached client, rebuilt when the endpoint or key changes.

    timeout/max_retries are the load-bearing part: index_record runs
    synchronously inside EVERY service write, and the openai default is
    connect=5s read=600s with 2 retries — a hung endpoint would cost ~30
    MINUTES per write and a firewalled one ~17s. Bounded here, the worst
    case is ~5s once, and connection-refused fails in milliseconds.
    """
    global _embed_client, _embed_client_key
    import httpx
    from openai import OpenAI

    key = (config.EMBED_BASE_URL, config.embed_key())
    if _embed_client is None or key != _embed_client_key:
        _embed_client = OpenAI(
            base_url=config.EMBED_BASE_URL or None,
            # local servers ignore it, but the client demands something
            api_key=config.embed_key() or "not-needed",
            timeout=httpx.Timeout(5.0, connect=2.0),
            max_retries=0,
        )
        _embed_client_key = key
    resp = _embed_client.embeddings.create(model=config.EMBED_MODEL, input=text[:8000])
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
