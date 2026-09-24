"""A person's own data: what Skein holds, the delete of a private record,
and the export.

Standups, questions, decisions, promises, blockers, requests, tasks and
lessons had no delete at all, so a private one stayed until an erasure, and
a person could see what Skein held about them only one surface at a time."""

import io
import json

from conftest import _strong

from app import db
from app.services import my_data, private_notes, scope


def test_the_label_map_and_the_own_surfaces_cover_every_scoped_table():
    """A scoped kind in neither map has no delete here and no other surface."""
    assert set(my_data.LABELS) | set(my_data.OWN_SURFACE) == set(scope.CLASSIFIED)
    assert not set(my_data.LABELS) & set(my_data.OWN_SURFACE)


def test_a_person_sees_counts_and_deletes_a_private_record(client):
    ava = _strong(client, "ava")
    standup = client.post(
        "/api/standups", json={"yesterday": "y", "today": "interview at noon"}, headers=ava
    ).json()["id"]
    shared = client.post(
        "/api/standups",
        json={"yesterday": "y", "today": "shipped", "visibility": "workspace"},
        headers=ava,
    ).json()["id"]
    summary = client.get("/api/my-data", headers=ava).json()
    assert summary["counts"]["standups"] == 1

    listed = client.get("/api/my-data/standups", headers=ava).json()
    assert [(r["id"], r["label"]) for r in listed] == [(standup, "interview at noon")]
    assert client.delete(f"/api/my-data/standups/{standup}", headers=ava).status_code == 200
    assert not db.query_one("SELECT 1 FROM standups WHERE id = ?", (standup,))
    ledger = db.query_row("SELECT detail FROM activity WHERE action = 'delete_private_record'")
    assert "interview" not in ledger["detail"]

    # a shared record and another person's private one read as absent, with
    # the same sentence an id nobody holds gets
    bo = _strong(client, "bo")
    theirs = client.post("/api/standups", json={"yesterday": "y", "today": "t"}, headers=bo).json()[
        "id"
    ]
    absent = client.delete("/api/my-data/standups/999999", headers=ava)
    for row_id in (shared, theirs):
        refused = client.delete(f"/api/my-data/standups/{row_id}", headers=ava)
        assert refused.status_code == absent.status_code == 404
        assert refused.json()["detail"] == absent.json()["detail"].replace("999999", str(row_id))
    assert db.query_one("SELECT 1 FROM standups WHERE id = ?", (shared,))


def test_a_self_asserted_name_reads_nobody_s_data(client):
    """In trusted-header mode a name is whatever the caller typed."""
    assert client.get("/api/my-data", headers={"X-User": "ava"}).status_code == 403
    assert client.get("/api/my-data/export", headers={"X-User": "ava"}).status_code == 403


def test_a_record_others_point_at_is_not_deleted_under_them(client):
    ava = _strong(client, "ava")
    engagement = client.post(
        "/api/engagements", json={"name": "Side project", "visibility": "private"}, headers=ava
    ).json()["id"]
    task = client.post(
        "/api/tasks",
        json={"title": "draft", "visibility": "private", "engagement_id": engagement},
        headers=ava,
    ).json()["id"]
    refused = client.delete(f"/api/my-data/engagements/{engagement}", headers=ava)
    assert refused.status_code == 400
    assert client.delete(f"/api/my-data/tasks/{task}", headers=ava).status_code == 200
    assert client.delete(f"/api/my-data/engagements/{engagement}", headers=ava).status_code == 200


def test_the_export_holds_the_person_s_own_data_and_no_file_contents(client):
    ava = _strong(client, "ava")
    client.post("/api/standups", json={"yesterday": "y", "today": "private plan"}, headers=ava)
    client.post(
        "/api/standups",
        json={"yesterday": "y", "today": "team update", "visibility": "workspace"},
        headers=ava,
    )
    bo = _strong(client, "bo")
    client.post("/api/standups", json={"yesterday": "y", "today": "bo's plan"}, headers=bo)
    client.post(
        "/api/files",
        files={"file": ("cv.md", io.BytesIO(b"resume bytes"), "text/plain")},
        headers=ava,
    )
    client.post("/api/chat", json={"thread_id": "ava-chat", "message": "/help"}, headers=ava)
    private_notes.add_note("ava", "bo", "bo wants to lead")

    response = client.get("/api/my-data/export", headers=ava)
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment;")
    body = response.json()
    todays = {row["today"] for row in body["records"]["standups"]}
    assert todays == {"private plan", "team update"}
    assert [f["title"] for f in body["files"]] == ["cv.md"]
    assert "resume bytes" not in response.text and "bo's plan" not in response.text
    assert [c["id"] for c in body["chats"]] == ["ava-chat"] and body["chats"][0]["messages"]
    assert [n["body"] for n in body["journal_notes"]] == ["bo wants to lead"]
    assert db.query_one("SELECT 1 FROM private.audit WHERE author = 'ava' AND action = 'export'")
    ledger = db.query_row("SELECT detail FROM activity WHERE action = 'export_my_data'")
    assert "plan" not in json.dumps(ledger)
