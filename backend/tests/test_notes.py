"""Notes: keyword filter, patch bounds, edit, delete, and deindex."""


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
