"""REST error shapes: overflow integers, blank required strings, and the clear sentinels."""

import pytest


def test_client_disconnect_is_not_a_server_fault():
    import asyncio

    from starlette.requests import ClientDisconnect

    from app.main import client_disconnect_handler

    response = asyncio.run(client_disconnect_handler(None, ClientDisconnect()))
    assert response.status_code == 400
    assert (
        response.body == b'{"detail":"The request body was not received. Send the request again."}'
    )


def test_overflow_ints_are_refused_not_crashed(client):
    """An id too large to exist is a 4xx, never a 500.

    404 is in the set because it is the honest answer now: psycopg sends an
    over-range integer as a numeric and the comparison simply matches no row,
    where sqlite3 refused to bind it at all and raised OverflowError, which
    main.py mapped to 400. Both say "your id is not a thing"; neither is a
    server fault, and that is what this pins."""
    huge = 99999999999999999999999
    assert client.patch(f"/api/tasks/{huge}", json={"status": "done"}).status_code in (
        400,
        404,
        422,
    )
    assert client.get("/api/adoption?weeks=999999999").status_code == 200  # clamped
    assert client.get("/api/findings?weeks=99999999999999999999").status_code in (200, 400, 422)


@pytest.mark.parametrize(
    ("path", "field"),
    [
        ("tasks", "title"),
        ("notes", "content"),
        ("engagements", "name"),
        ("promises", "promise"),
        ("questions", "question"),
    ],
)
def test_nul_text_is_refused_without_echo_or_partial_write(client, fresh_db, path, field):
    payload = {"topic": "ops"} if path == "notes" else {}
    payload[field] = "secret-prefix\x00suffix"
    response = client.post(f"/api/{path}", json=payload)
    assert response.status_code == 400, response.text
    assert "secret-prefix" not in response.text
    assert fresh_db.query_one(f"SELECT COUNT(*) AS n FROM {path}")["n"] == 0  # noqa: S608 -- closed parametrized table list


def test_nul_in_a_locked_name_is_an_input_error(client, fresh_db):
    """A crew name is taken under db.name_lock before any other statement
    reads it, and the lock skipped the NUL check every other statement has."""
    from conftest import _strong

    response = client.post(
        "/api/crews", json={"name": "secret-prefix\x00suffix"}, headers=_strong()
    )
    assert response.status_code == 400, response.text
    assert "secret-prefix" not in response.text


def test_nul_search_is_an_input_error(client):
    response = client.get("/api/search", params={"q": "secret-prefix\x00suffix"})
    assert response.status_code == 400, response.text
    assert "secret-prefix" not in response.text


def test_blank_required_strings_rejected(client):
    assert client.post("/api/engagements", json={"name": "  "}).status_code == 400
    assert client.post("/api/milestones", json={"title": ""}).status_code == 400
    assert client.post("/api/tasks", json={"title": " "}).status_code == 400
    assert client.post("/api/lessons", json={"lesson": ""}).status_code == 400
    assert client.post("/api/questions", json={"question": " "}).status_code == 400
    assert (
        client.post("/api/events", json={"title": "x", "starts_at": "garbage"}).status_code == 400
    )


def test_clearable_fields(client, fresh_db):
    t = client.post(
        "/api/tasks", json={"title": "x", "assignee": "ava", "due_date": "2026-08-01"}
    ).json()
    client.patch(f"/api/tasks/{t['id']}", json={"due_date": "-", "assignee": "-"})
    row = fresh_db.query_one("SELECT * FROM tasks WHERE id = ?", (t["id"],))
    assert row["due_date"] is None and row["assignee"] == ""


def test_api_tester_regressions(client):
    # FK violations are clean 400s, not 500s
    assert (
        client.post("/api/tasks", json={"title": "orphan", "milestone_id": 999999}).status_code
        == 400
    )
    assert client.post("/api/engagements/999999/allocate", json={"person": "a"}).status_code == 400
    assert (
        client.post("/api/lessons", json={"lesson": "x", "engagement_id": 999999}).status_code
        == 400
    )

    # playbook slug traversal rejected
    r = client.post(
        "/api/playbooks/instantiate", json={"playbook": "/tmp/pwned", "engagement_name": "t"}
    )
    assert r.status_code == 400
    r = client.post(
        "/api/playbooks/instantiate", json={"playbook": "../secrets", "engagement_name": "t"}
    )
    assert r.status_code == 400

    # 0-row updates are 400s, not silent success
    assert client.patch("/api/tasks/999999", json={"status": "done"}).status_code == 404
    assert client.patch("/api/milestones/999999", json={"status": "done"}).status_code == 404
    assert (
        client.post(
            "/api/intake/999999/score", json={"reach": 3, "impact": 3, "confidence": 3, "effort": 3}
        ).status_code
        == 404
    )

    # disposition is terminal
    req = client.post("/api/intake", json={"title": "once"}).json()
    client.post(
        f"/api/intake/{req['id']}/score",
        json={"reach": 3, "impact": 3, "confidence": 3, "effort": 3},
    )
    client.post(
        f"/api/intake/{req['id']}/disposition", json={"disposition": "accepted", "reason": "yes"}
    )
    r = client.post(
        f"/api/intake/{req['id']}/disposition", json={"disposition": "declined", "reason": "no"}
    )
    assert r.status_code == 400

    # double-resolve is a 400
    b = client.post("/api/blockers", json={"title": "once-only"}).json()
    client.post(f"/api/blockers/{b['id']}/resolve", json={})
    assert client.post(f"/api/blockers/{b['id']}/resolve", json={}).status_code == 400


def test_clear_sentinel_rejected_on_create_paths(fresh_db):
    from app.services import engagements, promises, users, work

    with pytest.raises(ValueError, match="only clears"):
        work.create_task(title="t", due_date="-")
    with pytest.raises(ValueError, match="only clears"):
        promises.add_promise("p", due_date="-")
    users.ensure_user("mira")
    e = engagements.create_engagement("SentinelCheck")
    with pytest.raises(ValueError, match="only clears"):
        engagements.allocate("mira", e["id"], 50, starts_on="-")


def test_storage_permission_failure_is_a_safe_server_error(client, fresh_db, tmp_path):
    from app.services import api_keys

    token = api_keys.create_key("tester", "storage-test")["key"]
    uploads = tmp_path / "artifacts" / "uploads"
    uploads.mkdir(parents=True)
    uploads.chmod(0o500)
    try:
        response = client.post(
            "/api/files",
            files={"file": ("blocked.txt", b"test bytes", "text/plain")},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        uploads.chmod(0o700)
    assert response.status_code == 500, response.text
    assert "storage" in response.json()["detail"]
    assert str(tmp_path) not in response.text
    assert fresh_db.query_one("SELECT COUNT(*) AS n FROM artifacts")["n"] == 0


@pytest.mark.parametrize("perimeter", [False, True])
@pytest.mark.parametrize(
    "error_name",
    [
        "OperationalError",
        "AdminShutdown",
        "CrashShutdown",
        "CannotConnectNow",
        "ConnectionFailure",
        "TooManyConnections",
    ],
)
def test_database_disconnect_is_retryable_without_exposing_connection_details(
    client, monkeypatch, perimeter, error_name
):
    import psycopg

    from app import config
    from app.services import api_keys, users

    token = api_keys.create_key("tester", "connection-test")["key"]

    def disconnected(*_args, **_kwargs):
        error = (
            psycopg.OperationalError
            if error_name == "OperationalError"
            else getattr(psycopg.errors, error_name)
        )
        raise error("connection lost to private-db.example password=hidden")

    if perimeter:
        monkeypatch.setattr(config, "AUTH_MODE", "api-key")
        monkeypatch.setattr(api_keys, "verify_key", disconnected)
    else:
        monkeypatch.setattr(users, "public_users", disconnected)
    response = client.get("/api/users", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 503, response.text
    assert response.headers["Retry-After"] == "5"
    assert "private-db" not in response.text and "hidden" not in response.text
    assert "outcome" in response.json()["detail"]


def test_database_authentication_configuration_fault_stays_a_server_error(client, monkeypatch):
    import psycopg

    from app.services import users

    def invalid_configuration(*_args, **_kwargs):
        raise psycopg.errors.InvalidPassword("private configuration details")

    monkeypatch.setattr(users, "public_users", invalid_configuration)
    response = client.get("/api/users")
    assert response.status_code == 500, response.text
    assert "private configuration" not in response.text


def test_semantic_permission_refusal_stays_forbidden():
    import asyncio

    from app.main import permission_error_handler

    response = asyncio.run(
        permission_error_handler(None, PermissionError("Only a steward can do this."))
    )
    assert response.status_code == 403
    assert response.body == b'{"detail":"Only a steward can do this."}'


def test_a_refusal_does_not_quote_the_rejected_value(client, fresh_db):
    """An error response never echoes what the caller sent (CLAUDE.md). Each
    of these quoted it: a path segment, a date string, or a duplicate name."""
    from conftest import _strong

    strong = _strong()
    marker = "zq-echo-probe"
    refusals = [
        client.get(f"/api/provenance/{marker}/1"),
        client.get(f"/api/personas/{marker} x"),
        client.post(f"/api/users/{marker}/rename", json={"new_name": "someone"}, headers=strong),
        client.post(
            "/api/playbooks/instantiate",
            json={"playbook": "prototype", "engagement_name": "e1", "start_date": "zq-echo-pr"},
        ),
    ]
    for path, body in (("/api/crews", {"name": marker}), ("/api/engagements", {"name": marker})):
        assert client.post(path, json=body, headers=strong).status_code == 200
        refusals.append(client.post(path, json=body, headers=strong))
    for response in refusals:
        assert 400 <= response.status_code < 500, response.text
        assert "zq-echo-pr" not in response.text, response.text
