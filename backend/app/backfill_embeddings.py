"""Embed every indexed record that lacks a current-model vector:
`python -m app.backfill_embeddings [--dry-run]`.

Exists because "records re-embed on their next write" is NEVER for the
write-once entities semantic search is for — notes, decisions, lessons.
Without this, enabling embeddings mid-life (or changing SKEIN_EMBED_MODEL,
which invalidates all vectors by design) leaves the semantic index
permanently biased toward recently-edited records.

Idempotent and resumable: each row is independent, already-covered rows are
skipped, and a failed row is reported and skipped rather than aborting."""

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
    missing = search.missing_embeddings_count()
    covered = db.query_row(
        "SELECT COUNT(*) AS n FROM embeddings WHERE model = ?", (config.EMBED_MODEL,)
    )
    print(f"model {config.EMBED_MODEL}: {covered['n']} covered, {missing} to embed")
    if dry or not missing:
        return

    done, failed = search.embed_missing(
        on_error=lambda entity, eid, exc: print(f"  FAILED {entity}#{eid}: {exc}", file=sys.stderr)
    )
    print(f"embedded {done}, failed {failed}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
