"""Malformed and oversized input is the caller's error, never a 500, and never
an amplifier. Also: a PATCH may not step around a create-time length cap.

Every case here was found by probing the running app, and each one violated a
rule the project already states: main.py's handlers exist so absurd input is a
4xx, and CORRECTIONS.md rule 5 says anything writable is length-capped.
"""

import json


def _deep_body(depth: int) -> str:
    return '{"title":' + "[" * depth + "]" * depth + "}"


def test_deeply_nested_body_is_422_not_500(client):
    """FastAPI's default handler renders the rejected value with
    jsonable_encoder, which recursed and returned a plain-text 500 — to a
    caller with no headers at all."""
    r = client.post(
        "/api/tasks", content=_deep_body(2000), headers={"content-type": "application/json"}
    )
    assert r.status_code == 422
    assert r.json()["detail"]  # structured, parseable by the frontend


def test_validation_error_does_not_echo_the_body_back(client):
    """A 422 used to carry the whole rejected string, so a 50 MB request
    produced a 50 MB response — a 1:1 amplifier on every write endpoint."""
    huge = "D" * 200_000
    r = client.post("/api/tasks", json={"title": "probe", "description": huge})
    assert r.status_code == 422
    assert len(r.content) < 4000
    assert huge not in r.text


def test_patch_honors_the_same_caps_as_create(client, fresh_db):
    created = client.post("/api/tasks", json={"title": "bounds probe"})
    assert created.status_code == 200
    tid = created.json()["id"]
    # the create cap is real
    assert client.post("/api/tasks", json={"title": "x" * 30_000}).status_code == 422
    # and the edit cap matches it, on every field the create bounds
    assert client.patch(f"/api/tasks/{tid}", json={"description": "P" * 60_000}).status_code == 422
    assert client.patch(f"/api/tasks/{tid}", json={"title": "P" * 30_000}).status_code == 422
    row = next(t for t in client.get("/api/tasks").json() if t["id"] == tid)
    assert len(row.get("description") or "") == 0


def test_patch_caps_cover_milestones_and_engagements(client, fresh_db):
    m = client.post("/api/milestones", json={"title": "bounds probe"}).json()
    assert (
        client.patch(f"/api/milestones/{m['id']}", json={"title": "x" * 30_000}).status_code == 422
    )
    e = client.post("/api/engagements", json={"name": "bounds probe"}).json()
    assert (
        client.patch(f"/api/engagements/{e['id']}", json={"summary": "x" * 30_000}).status_code
        == 422
    )


def test_an_empty_note_is_refused_like_every_other_create(client, fresh_db):
    """Every sibling create refuses an empty record. This one accepted it,
    indexed a blank row for search, and spent a hash-chained activity seq."""
    r = client.post("/api/notes", json={"topic": "", "content": ""})
    assert r.status_code == 400
    assert "topic or content" in r.json()["detail"]
    # either field alone is still a real note
    assert (
        client.post("/api/notes", json={"topic": "", "content": "just content"}).status_code == 200
    )
    assert (
        client.post("/api/notes", json={"topic": "just a topic", "content": ""}).status_code == 200
    )


def test_garbage_from_date_is_refused_not_silently_empty(client, fresh_db):
    """A string compare against garbage returned [], which reads as 'no
    events' — a wrong answer rather than an error."""
    r = client.get("/api/events?from_date=garbage")
    assert r.status_code == 400
    assert "YYYY-MM-DD" in r.json()["detail"]
    assert client.get("/api/events?from_date=2026-08-01").status_code == 200
    assert client.get("/api/events").status_code == 200


def test_allocation_writes_are_rate_capped(client, fresh_db):
    """The one create route that never called ratelimit.check, feeding the one
    list service with no LIMIT."""
    from app.services import users

    users.ensure_user("mira")
    e = client.post("/api/engagements", json={"name": "cap probe"}).json()
    codes = [
        client.post(
            f"/api/engagements/{e['id']}/allocate", json={"person": "mira", "percent": 10}
        ).status_code
        for _ in range(40)
    ]
    assert 400 in codes, "no rate cap engaged on allocate"


def test_allocations_list_is_bounded(fresh_db):
    import inspect

    from app.services import engagements

    assert "LIMIT" in inspect.getsource(engagements.list_allocations)


def test_error_bodies_stay_json_and_leak_nothing(client):
    for content in (_deep_body(1500), "{not json", "[]", "null"):
        r = client.post("/api/notes", content=content, headers={"content-type": "application/json"})
        assert r.status_code in (400, 422), content[:20]
        body = json.loads(r.content)  # never a bare text/plain 500
        assert "Traceback" not in r.text and "/home/" not in r.text
        assert body.get("detail")
