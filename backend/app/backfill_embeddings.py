"""Embed every indexed record that lacks a current-model vector:
`python -m app.backfill_embeddings [--dry-run]`.

Exists because "records re-embed on their next write" is NEVER for the
write-once entities semantic search is for — notes, decisions, lessons.
Without this, enabling embeddings mid-life (or changing SKEIN_EMBED_MODEL,
which invalidates all vectors by design) leaves the semantic index
permanently biased toward recently-edited records.

Idempotent and resumable: each row is independent, already-covered rows are
skipped, and a failed row is reported and skipped rather than aborting."""

import json
import sys

from . import config, db
from .services import search


def main() -> None:
    dry = "--dry-run" in sys.argv
    if not config.EMBEDDINGS_ENABLED:
        print("SKEIN_EMBEDDINGS is off — nothing to backfill.", file=sys.stderr)
        raise SystemExit(2)
    if config.EMBEDDINGS_ERROR:
        print(f"embeddings misconfigured: {config.EMBEDDINGS_ERROR}", file=sys.stderr)
        raise SystemExit(2)

    db.init_db()
    rows = db.query(
        "SELECT s.entity, s.entity_id, s.title, s.body FROM search_index s"
        " WHERE NOT EXISTS (SELECT 1 FROM embeddings e"
        "   WHERE e.entity = s.entity AND e.entity_id = s.entity_id AND e.model = ?)"
        " ORDER BY s.entity, s.entity_id",
        (config.EMBED_MODEL,),
    )
    covered = db.query_row(
        "SELECT COUNT(*) AS n FROM embeddings WHERE model = ?", (config.EMBED_MODEL,)
    )
    print(f"model {config.EMBED_MODEL}: {covered['n']} covered, {len(rows)} to embed")
    if dry or not rows:
        return

    done = failed = 0
    for r in rows:
        try:
            vec = search._embed(f"{r['title']}\n{r['body']}")
            db.execute(
                "INSERT OR REPLACE INTO embeddings (entity, entity_id, model, vector)"
                " VALUES (?, ?, ?, ?)",
                (r["entity"], r["entity_id"], config.EMBED_MODEL, json.dumps(vec)),
            )
            done += 1
        except Exception as exc:
            failed += 1
            print(f"  FAILED {r['entity']}#{r['entity_id']}: {exc}", file=sys.stderr)
    print(f"embedded {done}, failed {failed}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
