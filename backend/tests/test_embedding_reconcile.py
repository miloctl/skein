"""embed-reconcile: the hourly job that heals _maybe_embed's best-effort gaps.

_maybe_embed fails open on a provider outage (FTS stays authoritative), which
used to strand the row out of semantic search until an operator ran the
backfill by hand. The missing embeddings row is the queue; embed_missing
drains it."""

import pytest

from app.services import collab, jobs, search


@pytest.fixture()
def indexed_note(fresh_db):
    """A note indexed while the embed path is unavailable — the exact state a
    provider outage leaves behind."""
    collab.save_note("conventions", "branch names use task ids", author="mira")
    return fresh_db


def test_an_outage_leaves_the_row_queued_and_the_reconciler_heals_it(indexed_note, monkeypatch):
    assert search.missing_embeddings_count() == 1
    monkeypatch.setattr(search, "_embed", lambda text: [1.0, 0.0])
    assert search.embed_missing() == (1, 0)
    assert search.missing_embeddings_count() == 0


def test_a_failing_provider_keeps_the_row_queued(indexed_note, monkeypatch):
    def down(text):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(search, "_embed", down)
    assert search.embed_missing() == (0, 1)
    assert search.missing_embeddings_count() == 1


def test_the_job_noops_keyless(fresh_db):
    assert jobs._embed_reconcile() == "embeddings off"
