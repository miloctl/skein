"""Notes: keyword filter, patch bounds, edit, delete, and deindex."""

import pytest


def test_notes_keyword_filter_and_patch_bounds(client):
    n = client.post("/api/notes", json={"topic": "infra", "content": "postgres vacuum tips"}).json()
    client.post("/api/notes", json={"topic": "team", "content": "friday demo schedule"})

    hits = client.get("/api/notes", params={"q": "vacuum"}).json()
    assert [h["id"] for h in hits] == [n["id"]]
    assert len(client.get("/api/notes").json()) == 2

    over = client.patch(f"/api/notes/{n['id']}", json={"content": "x" * 20_001})
    assert over.status_code == 422


def test_note_edit_delete_and_deindex(client):
    n = client.post("/api/notes", json={"topic": "conv", "content": "old text zebra"}).json()
    client.patch(f"/api/notes/{n['id']}", json={"content": "new text giraffe"})
    assert client.get("/api/search", params={"q": "giraffe"}).json()
    client.delete(f"/api/notes/{n['id']}")
    assert client.get("/api/search", params={"q": "giraffe"}).json() == []
    assert client.delete(f"/api/notes/{n['id']}").status_code == 404


def test_a_delete_that_cannot_deindex_keeps_the_note(client, fresh_db, monkeypatch):
    """All or nothing. Split across transactions, the row delete commits and
    the index delete does not, so the note is gone from `notes` while its FULL
    body stays queryable through /api/search — unreachable by any delete, and
    unbounded, where the ledger snapshot delete_note keeps is capped at 300
    chars. Concurrency produces the same split; this reaches it deterministically."""
    from app.services import collab, search

    n = client.post("/api/notes", json={"topic": "conv", "content": "ghost zebra"}).json()
    assert client.get("/api/search", params={"q": "zebra"}).json()

    def boom(*_a, **_k):
        raise RuntimeError("index backend down")

    monkeypatch.setattr(search, "deindex_record", boom)
    with pytest.raises(RuntimeError):
        collab.delete_note(n["id"], actor="tester")
    assert fresh_db.query_one("SELECT * FROM notes WHERE id = ?", (n["id"],)) is not None
    assert client.get("/api/search", params={"q": "zebra"}).json()
