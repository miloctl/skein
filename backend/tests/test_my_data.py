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
from app.services.projection_policy import ProjectionPolicy


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
    # private records only: a shared one is the team's, and its readers change
    assert [row["today"] for row in body["records"]["standups"]] == ["private plan"]
    assert [f["title"] for f in body["files"]] == ["cv.md"]
    assert "resume bytes" not in response.text and "bo's plan" not in response.text
    assert [c["id"] for c in body["chats"]] == ["ava-chat"] and body["chats"][0]["messages"]
    assert [n["body"] for n in body["journal_notes"]] == ["bo wants to lead"]
    assert db.query_one("SELECT 1 FROM private.audit WHERE author = 'ava' AND action = 'export'")
    ledger = db.query_row("SELECT detail FROM activity WHERE action = 'export_my_data'")
    assert "plan" not in json.dumps(ledger)


class _DenyRegulated(ProjectionPolicy):
    """A workplace rule that denies regulated projects on REST reads."""

    def permits(self, entity, entity_id, attributes):
        return attributes.get("project_type") != "regulated"


def _policy(person: str) -> ProjectionPolicy:
    return _DenyRegulated(None, None, "skein.rest.get.my-data", "rest", scope.Viewer(person, True))


def test_the_list_and_the_export_pass_the_workplace_policy(client):
    """Every other read of a regulated project's rows passes the workplace
    policy. The export asked only who wrote them."""
    ava = _strong(client, "ava")
    regulated = client.post(
        "/api/engagements",
        json={"name": "Audit prep", "project_class": "regulated", "visibility": "private"},
        headers=ava,
    ).json()["id"]
    client.post(
        "/api/engagements",
        json={"name": "Side project", "visibility": "private"},
        headers=ava,
    )
    client.post(
        "/api/tasks",
        json={"title": "regulated draft", "visibility": "private", "engagement_id": regulated},
        headers=ava,
    )
    listed = my_data.list_private("engagements", "ava", _policy("ava"))
    assert [row["label"] for row in listed] == ["Side project"]
    body = my_data.export("ava", _policy("ava"))
    assert [row["name"] for row in body["records"]["engagements"]] == ["Side project"]
    assert body["records"]["tasks"] == []


def test_a_delete_refuses_a_record_others_point_at(client):
    """An allocation cascades with its engagement and names another person's
    time, and a superseded decision names its successor: deleting under them
    took the first and stranded the second."""
    from app.services import collab, engagements

    ava = _strong(client, "ava")
    engagement = client.post(
        "/api/engagements", json={"name": "Side project", "visibility": "private"}, headers=ava
    ).json()["id"]
    engagements.allocate("ava", engagement, 50, actor="ava")
    assert client.delete(f"/api/my-data/engagements/{engagement}", headers=ava).status_code == 400
    assert db.query_one("SELECT 1 FROM allocations WHERE engagement_id = ?", (engagement,))

    first = collab.record_decision("Ship weekly", "yes", actor="ava", visibility="private")["id"]
    second = collab.supersede_decision(first, "Ship daily", "yes", actor="ava")["id"]
    db.execute("UPDATE decisions SET visibility = 'private' WHERE id = ?", (second,))
    assert client.delete(f"/api/my-data/decisions/{second}", headers=ava).status_code == 400


def test_a_private_handoff_is_listed_deleted_and_counted_apart_from_files(client):
    """A handoff made for a private engagement is private, and it was counted
    under Attached files, where nothing lists or deletes it."""
    ava = _strong(client, "ava")
    engagement = client.post(
        "/api/engagements", json={"name": "Side project", "visibility": "private"}, headers=ava
    ).json()["id"]
    handoff = client.post(f"/api/engagements/{engagement}/handoff", headers=ava).json()
    client.post(
        "/api/files",
        files={"file": ("cv.md", io.BytesIO(b"resume"), "text/plain")},
        headers=ava,
    )
    counts = client.get("/api/my-data", headers=ava).json()["counts"]
    assert (counts["uploads"], counts["artifacts"]) == (1, 1)
    listed = client.get("/api/my-data/artifacts", headers=ava).json()
    assert [row["id"] for row in listed] == [handoff["artifact_id"]]
    path = db.query_row("SELECT path FROM artifacts WHERE id = ?", (handoff["artifact_id"],))[
        "path"
    ]
    assert (
        client.delete(f"/api/my-data/artifacts/{handoff['artifact_id']}", headers=ava).status_code
        == 200
    )
    from pathlib import Path

    assert not Path(path).exists()
    upload = db.query_row("SELECT id FROM artifacts WHERE kind = 'upload'")["id"]
    assert client.delete(f"/api/my-data/artifacts/{upload}", headers=ava).status_code == 404
