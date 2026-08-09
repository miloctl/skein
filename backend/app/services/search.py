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
from . import scope


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
        "SELECT s.entity, s.entity_id, s.title, substr(s.body, 1, 120) AS snippet"
        " FROM search_ids i JOIN search_index s ON s.rowid = i.id"
        " WHERE i.entity = ? AND i.entity_id = ?",
        (m.group(2) or "task", int(m.group(1) or m.group(3))),
    )
    return {**row, "rank": None} if row else None


# entity name -> the table it lives in, for the tier lookup below. Only the
# entities index_record is called with; scope.CLASSIFIED holds the tier map.
_ENTITY_TABLE = {
    "blocker": "blockers",
    "decision": "decisions",
    "engagement": "engagements",
    "event": "events",
    "intake": "intake_requests",
    "lesson": "lessons",
    "memory": "memories",
    "milestone": "milestones",
    "note": "notes",
    "promise": "promises",
    "question": "questions",
    "standup": "standups",
    "task": "tasks",
}


def _tier_of(entity: str, entity_id: int) -> tuple[str, int | None] | None:
    """The row's tier, or None when the entity carries none."""
    table = _ENTITY_TABLE.get(entity)
    if table is None or table not in scope.CLASSIFIED:
        return None
    row = db.query_one(
        f"SELECT visibility, crew_id FROM {table} WHERE id = ?",  # noqa: S608 — constant map
        (entity_id,),
    )
    return (row["visibility"], row["crew_id"]) if row else None


def visible_hits(hits: list[dict], viewer: "scope.Viewer") -> list[dict]:
    """Drop the hits this viewer may not read.

    The FTS table has no tier of its own, and it CANNOT get one cheaply:
    `entity` and `entity_id` are UNINDEXED in fts5, so a predicate on them
    scans the whole virtual table, and adding a column means dropping and
    rebuilding the index plus its five shadow tables. So the tier is read off
    the source rows instead.

    Without this every crew row was world-readable through search, /ask, the
    MCP search_workspace tool, and the by-id fetch — which voids every filter
    the list endpoints apply.

    One query per TABLE, not per hit. db.connect() runs four PRAGMAs (db.py),
    so a per-hit read costs a connection — and this filters BEFORE the
    `limit * 4` over-fetch is truncated, so a 20-result page probes 80 rows.
    Measured at 81 connections and 38ms per search; grouped by table it is 3
    and 1.9ms.
    """
    want: dict[str, set[int]] = {}
    for h in hits:
        table = _ENTITY_TABLE.get(h["entity"])
        if table in scope.CLASSIFIED:
            want.setdefault(table, set()).add(h["entity_id"])
    rows: dict[tuple[str, int], tuple[str, int | None, str]] = {}
    for table, ids in want.items():
        marks = ", ".join("?" for _ in ids)
        author = scope.CLASSIFIED[table]
        for r in db.query(
            f"SELECT id, visibility, crew_id, {author} AS author FROM {table}"  # noqa: S608 — table and column from constant maps, ids are bound marks
            f" WHERE id IN ({marks})",
            tuple(ids),
        ):
            rows[table, r["id"]] = (r["visibility"], r["crew_id"], r["author"] or "")
    out = []
    for h in hits:
        table = _ENTITY_TABLE.get(h["entity"])
        if table not in scope.CLASSIFIED:
            out.append(h)  # the entity carries no tier at all
            continue
        row = rows.get((table, h["entity_id"]))
        if row is None:
            # the source row is GONE and this is a stale index entry. Only
            # note, event and memory deindex on delete, so the other ten
            # entities reach here — and a hit whose row cannot be tier-checked
            # must not be served.
            continue
        if scope.can_read(row[0], row[1], viewer, row[2]):
            out.append(h)
    return out


def _is_private(entity: str, entity_id: int) -> bool:
    """Whether this record must stay out of the index.

    Looked up HERE rather than passed in by each of the 20 callers. A
    parameter is a thing a call site can forget, and a forgotten one puts the
    body in the FTS index — where /ask, semantic search, the MCP
    search_workspace tool and the by-id fetch all read it, and where deleting
    the row later does not take back what was already served. One SELECT on a
    primary key — inside the caller's transaction where there is one, and on
    its own connection where there is not (intake.submit_request indexes after
    its transaction closes).
    """
    table = _ENTITY_TABLE.get(entity)
    if table is None or table not in scope.CLASSIFIED:
        return False
    row = db.query_one(f"SELECT visibility FROM {table} WHERE id = ?", (entity_id,))  # noqa: S608 — constant map
    return row is not None and row["visibility"] == scope.PRIVATE


def index_record(entity: str, entity_id: int, title: str, body: str) -> None:
    # by rowid via search_ids, never WHERE entity = ?: entity/entity_id are
    # UNINDEXED in FTS5, so that predicate scans the whole virtual table —
    # under the write lock, on every service write (migration 043).
    # INVARIANT: every search_index row has a search_ids twin. A row written
    # to search_index directly occupies a rowid that INSERT OR IGNORE can
    # mint next, and the DELETE below then destroys the bystander silently.
    # One transaction: two writers of the same record interleaving these
    # statements would collide on the explicit rowid insert (IntegrityError);
    # BEGIN IMMEDIATE serializes them instead.
    with db.transaction():
        if _is_private(entity, entity_id):
            # and remove any row a previous, non-private version left behind:
            # a record demoted to private must not stay searchable
            deindex_record(entity, entity_id)
            return
        db.execute(
            "INSERT OR IGNORE INTO search_ids (entity, entity_id) VALUES (?, ?)",
            (entity, entity_id),
        )
        sid = db.query_row(
            "SELECT id FROM search_ids WHERE entity = ? AND entity_id = ?",
            (entity, entity_id),
        )["id"]
        db.execute("DELETE FROM search_index WHERE rowid = ?", (sid,))
        db.execute(
            "INSERT INTO search_index (rowid, entity, entity_id, title, body)"
            " VALUES (?, ?, ?, ?, ?)",
            (sid, entity, entity_id, title, body),
        )
    # The embed is an HTTP round-trip of up to ~5s. Callers run index_record
    # inside db.transaction() (review.approve_change, playbooks.instantiate,
    # intake.disposition) — inline, the round-trip would hold SQLite's single
    # write lock and stall every concurrent write for its duration. Deferred
    # to after commit it holds nothing, and a rollback drops the embed along
    # with the row it would have described. The FTS write above stays inside
    # the transaction — it is the authoritative index.
    text = f"{title}\n{body}"
    if not db.on_commit(lambda: _maybe_embed(entity, entity_id, text)):
        _maybe_embed(entity, entity_id, text)


def deindex_record(entity: str, entity_id: int) -> None:
    """Hard-deleted rows must leave the index too — search and /ask must
    never cite a record that no longer exists. The vector goes with it: an
    orphaned embedding can't leak content (snippets come from search_index),
    but it outranks live records and silently burns a semantic result slot
    per query, forever."""
    # One transaction, matching index_record: unwrapped, a concurrent
    # index_record commits between the lookup and the DELETEs and re-inserts
    # the row, leaving the full body of a deleted record queryable through
    # search forever — nothing reaps it. It also holds the search_ids twin
    # invariant that index_record's comment above depends on: a half-applied
    # delete leaves a search_index row whose freed rowid INSERT OR IGNORE
    # mints next, and the next index_record destroys that bystander.
    with db.transaction():
        row = db.query_one(
            "SELECT id FROM search_ids WHERE entity = ? AND entity_id = ?",
            (entity, entity_id),
        )
        if row:
            db.execute("DELETE FROM search_index WHERE rowid = ?", (row["id"],))
            db.execute("DELETE FROM search_ids WHERE id = ?", (row["id"],))
        db.execute(
            "DELETE FROM embeddings WHERE entity = ? AND entity_id = ?",
            (entity, entity_id),
        )


# Function words the OR fallback drops. Without this, "why is the vendor
# contract blocked" ORs in `the`, which matches most rows in the database:
# the one relevant blocker came back ranked beside four unrelated tasks whose
# only tie to the question was that word. Words of 1-2 characters never reach
# here (the length filter below takes them), so this holds 3+ only.
#
# Closed-class words ONLY. A status or a domain noun has to survive, because
# those ARE what people search for -- `done`, `open`, `out` (out-of-scope),
# `off` (off-call), `new`, `own`, `need`, `want` are all absent on purpose.
# `will` and `may` are absent for a second reason: one is a person's name and
# the other is a month, and this list cannot tell either from the auxiliary.
# fmt: off
_STOPWORDS = frozenset({
    # articles, conjunctions, prepositions
    "the", "and", "but", "for", "nor", "yet", "with", "from",
    "into", "onto", "over", "under", "about", "after", "before", "between",
    "during", "than", "upon", "via", "per",
    # pronouns and determiners
    "you", "your", "yours", "our", "ours", "their", "theirs", "its",
    "his", "her", "hers", "they", "them", "this", "that", "these",
    "those", "there", "here",
    # question words
    "what", "which", "who", "whom", "whose", "why", "when", "where",
    "how",
    # quantifiers
    "any", "all", "some", "each", "every", "other", "another", "such",
    "both", "same",
    # auxiliaries
    "are", "was", "were", "been", "being", "has", "have", "had",
    "does", "did", "doing", "can", "could", "should", "shall", "might",
    "must",
    # degree and negation
    "not", "only", "just", "also", "very", "too", "more", "most",
    "less", "least", "then",
})
# fmt: on


def ask(q: str, limit: int = 5, viewer: "scope.Viewer | None" = None) -> dict:
    """Q&A with receipts: deterministic FTS answer where every snippet cites
    its row (entity #id), findings-style. Degrades honestly keyless — an LLM
    synthesis can be layered on top later, but the citations ARE the answer.
    NOTE for any future UI: snippets contain literal <b> markup from FTS —
    render as text or strip it; never innerHTML indexed user content."""
    # viewer forwarded to BOTH searches: taking the parameter and dropping it
    # left /ask serving every crew and private row through the one surface
    # whose whole job is to quote them back
    hits = search(q, limit, viewer=viewer)
    note = ""
    if not hits:
        # natural phrasing rarely matches as a phrase — fall back to OR of
        # the meaningful words, bm25-ranked, and say so
        tokens = q.split()
        words = [w for w in tokens if len(w) > 2 and w.strip(".,;:!?").lower() not in _STOPWORDS]
        # One meaningful word is worth trying when the question carried more
        # than that ("what is skein" -> skein). It is NOT worth trying when the
        # question was already that one word: search() just ran it and missed,
        # so re-running it costs a second scan for the same nothing.
        if words and (len(words) > 1 or len(words) != len(tokens)):
            hits = search(" OR ".join(_fts_quote(w) for w in words), limit, raw=True, viewer=viewer)
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


def search(
    q: str, limit: int = 20, raw: bool = False, viewer: "scope.Viewer | None" = None
) -> list[dict]:
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
        # over-fetch, because the tier is checked AFTER the match: a page of
        # hits that are all scoped would otherwise come back empty
        (q if raw else _fts_quote(q), limit * 4),
    )
    hits = visible_hits(hits, viewer or scope.NOBODY)[:limit]
    # the by-id fetch is its own door: `note 4` resolves a row without
    # matching anything, so the tier has to be checked here too
    direct = None if raw else _short_id_hit(q)
    if direct and not visible_hits([direct], viewer or scope.NOBODY):
        direct = None
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
                "SELECT s.title, substr(s.body, 1, 120) AS snippet"
                " FROM search_ids i JOIN search_index s ON s.rowid = i.id"
                " WHERE i.entity = ? AND i.entity_id = ?",
                (s["entity"], s["entity_id"]),
            )
            if row and not visible_hits(
                [{"entity": s["entity"], "entity_id": s["entity_id"]}], viewer or scope.NOBODY
            ):
                continue
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
        # the whole table for this model, JSON-parsed and cosine-scored in
        # Python on every request. Known and DEFERRED, not overlooked:
        # SQLite has no vector index, so the real fix is a cached matrix
        # with write invalidation or a bounded candidate set — its own
        # change. Gated behind EMBED_READY (off by default), so the keyless
        # deployment never pays it.
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

    # json.loads INSIDE the guard, not below it. A corrupt embeddings.vector is
    # our own state, and JSONDecodeError subclasses ValueError, which main.py
    # maps to 400 — telling the caller their query was invalid when it was not,
    # with a raw parser message as the fix. Skip the bad row: one unreadable
    # vector must not take down search and /ask for every query.
    scored = []
    for r in rows:
        try:
            vector = json.loads(r["vector"])
        except (json.JSONDecodeError, TypeError):
            logging.getLogger("skein").warning(
                "embeddings: unreadable vector for %s #%s", r["entity"], r["entity_id"]
            )
            continue
        score = cos(qv, vector)
        # the floor is what makes "no semantic match" expressible. Sorting
        # alone always yields `limit` rows, so without this a query sharing
        # nothing with the corpus still fills the page and search can never
        # answer "nothing matches" once embeddings are on (config.py records
        # how to retune it for a different embedding model).
        if score < config.EMBED_MIN_SCORE:
            continue
        scored.append({"entity": r["entity"], "entity_id": r["entity_id"], "score": score})
    return sorted(scored, key=lambda r: -r["score"])[:limit]
