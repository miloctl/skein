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
    jsonable_encoder, which recursed and returned a plain-text 500. The live
    instance answered this with no auth headers at all; the suite's client
    always sends X-User, so this pins the status, not the reachability."""
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
    assert client.get("/api/events?from_date=garbage").status_code == 400
    # shape alone is not enough: these match the pattern and are not dates
    for bad in ("9999-99-99", "2026-13-45", "2026-02-30"):
        r = client.get(f"/api/events?from_date={bad}")
        assert r.status_code == 400, bad
        assert "real date" in r.json()["detail"], bad
    assert client.get("/api/events?from_date=2026-08-01").status_code == 200
    assert client.get("/api/events?from_date=2026-08-01T10:00").status_code == 200
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
    # `400 in codes` alone would also pass if EVERY call failed for an
    # unrelated reason, so pin the happy path and the transition too
    assert codes[0] == 200, "allocate is broken, not rate-capped"
    assert codes[-1] == 400
    assert 20 < codes.count(200) < 40


ALLOCATION_ROWS = 620  # the service default LIMIT is 500


def test_allocations_list_is_bounded(client, fresh_db):
    """Every other list service caps its result set. This one returned the
    whole table, so one fat-fingered staffing script made /api/allocations
    (and the capacity page that reads it) grow without limit.

    person == actor short-circuits resolve_teammate, so seeding is one INSERT
    plus one activity row per call."""
    from app.services import engagements, users

    users.ensure_user("loadtest")
    eid = engagements.create_engagement("bounds probe", actor="loadtest")["id"]
    for _ in range(ALLOCATION_ROWS):
        engagements.allocate("loadtest", eid, 1, actor="loadtest")

    total = fresh_db.query_one("SELECT COUNT(*) AS n FROM allocations")["n"]
    assert total == ALLOCATION_ROWS

    rows = engagements.list_allocations()
    assert len(rows) < total, "the unfiltered list returned every row"
    assert len(rows) <= 500
    # bounded must mean a window on the NEWEST rows — a LIMIT that keeps
    # returning the oldest 500 forever is bounded and still wrong
    assert rows[0]["id"] == total

    # the engagement-scoped branch is a SEPARATE query and needs its own cap
    scoped = engagements.list_allocations(eid)
    assert len(scoped) < total
    assert scoped[0]["id"] == total

    # and the route serves the bound rather than re-querying unbounded
    assert len(client.get("/api/allocations").json()) == len(rows)


def test_error_bodies_stay_json_and_leak_nothing(client):
    for content in (_deep_body(1500), "{not json", "[]", "null"):
        r = client.post("/api/notes", content=content, headers={"content-type": "application/json"})
        assert r.status_code in (400, 422), content[:20]
        body = json.loads(r.content)  # never a bare text/plain 500
        assert "Traceback" not in r.text and "/home/" not in r.text
        assert body.get("detail")
