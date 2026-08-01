"""Canary tests for the private-record boundary (PLAN.md W1.1).

A canary string is written into private notes; every egress surface is then
asserted canary-free. If any of these fail, private data is leaking."""

import json
from pathlib import Path

CANARY = "CANARY-zx9q-private-feedback"


def _setup_key(client, fresh_db):
    from app.services.api_keys import create_key

    key = create_key("manager", "test")["key"]
    return {"Authorization": f"Bearer {key}"}


def _write_private(client, fresh_db):
    headers = _setup_key(client, fresh_db)
    r = client.post(
        "/api/private/notes",
        json={"person": "dana", "body": f"{CANARY} handled the outage well", "kind": "feedback"},
        headers=headers,
    )
    assert r.status_code == 200
    return headers


def test_private_requires_strong_identity(client, fresh_db):
    r = client.get("/api/private/notes", headers={"X-User": "sneaky"})
    assert r.status_code == 403
    r = client.post(
        "/api/private/notes",
        json={"person": "dana", "body": "spoofed"},
        headers={"X-User": "manager"},
    )
    assert r.status_code == 403


def test_private_notes_are_author_scoped(client, fresh_db):
    _write_private(client, fresh_db)
    from app.services.api_keys import create_key

    other = create_key("other-person", "test")["key"]
    r = client.get("/api/private/notes", headers={"Authorization": f"Bearer {other}"})
    assert r.status_code == 200
    assert r.json() == []


def test_fb_capture_routes_private_and_requires_key(client, fresh_db):
    headers = _write_private(client, fresh_db)
    # without a key: refused, nothing stored anywhere
    r = client.post("/api/capture", json={"text": f"fb: dana — {CANARY}"}, headers={"X-User": "m"})
    assert r.status_code == 400
    assert "API key" in r.json()["detail"]
    # with a key: lands as private feedback, not a note
    r = client.post("/api/capture", json={"text": f"fb: dana — {CANARY} two"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["kind"] == "feedback"
    assert fresh_db.query("SELECT * FROM notes") == []


def test_fb_capture_refuses_agents(fresh_db):
    import pytest

    from app.services.capture import capture

    with pytest.raises(ValueError, match="human-only"):
        capture("fb: dana — sneaky agent note", actor="agent-x", origin="agent", strong_auth=True)


def _spray_canary(client, headers):
    """Every write path an fb: line could plausibly transit."""
    client.post("/api/capture", json={"text": f"fb: dana — {CANARY} extra"}, headers=headers)
    # multi-line capture containing an fb: line must be REFUSED whole
    r = client.post(
        "/api/capture",
        json={"text": f"todo: ship the thing\nfb: dana — {CANARY} sneaky"},
        headers=headers,
    )
    assert r.status_code == 400 and "alone" in r.json()["detail"]
    # ingest skips fb: lines
    ing = client.post(
        "/api/ingest",
        json={"text": f"todo: real work\nfb: dana — {CANARY} in transcript"},
        headers={"X-User": "m"},
    ).json()
    assert ing["skipped_private"] == 1
    # chat refuses fb: before the agent ever sees it
    with client.stream(
        "POST", "/api/chat", json={"thread_id": "t", "message": f"fb: dana — {CANARY} via chat"}
    ) as resp:
        chat_out = resp.read().decode()
    assert "private" in chat_out and CANARY not in json.dumps(
        [r["title"] for r in client.get("/api/tasks").json()]
    )
    # a slash prefix must not smuggle fb: past the gate — the transcript and
    # the session bridge are both downstream of it
    with client.stream(
        "POST",
        "/api/chat",
        json={"thread_id": "t", "message": f"/remember fb: dana — {CANARY} wrapped"},
    ) as resp:
        wrapped_out = resp.read().decode()
    assert "private" in wrapped_out


def test_canary_absent_from_every_platform_table(client, fresh_db):
    """Exhaustive: scan EVERY table in platform.db, not an enumerated list —
    a leak into any new table fails this without anyone remembering to add it."""
    headers = _write_private(client, fresh_db)
    _spray_canary(client, headers)
    for t in fresh_db.query("SELECT name FROM sqlite_master WHERE type = 'table'"):
        rows = fresh_db.query(f"SELECT * FROM {t['name']}")  # noqa: S608 — names from sqlite_master
        assert CANARY not in json.dumps(rows, default=str), f"canary leaked into {t['name']}"


def test_canary_absent_from_every_disk_file(client, fresh_db):
    """Exhaustive: after exercising every artifact-producing surface, no file
    under DATA_DIR except private.db itself may contain the canary."""
    from app import config
    from app.services import admin
    from app.services.digest import publish_digest

    headers = _write_private(client, fresh_db)
    _spray_canary(client, headers)
    client.post("/api/context-pack/publish", json={})
    publish_digest(actor="tester", force=True)
    admin.backup()
    admin.export()
    assert "BEGIN:VCALENDAR" in client.get("/api/calendar.ics").text  # exercise the feed
    assert CANARY not in client.get("/api/calendar.ics").text
    # /ask reads the same FTS: the echo of the question is fine, citations must be empty
    assert client.get(f"/api/ask?q={CANARY}").json()["citations"] == []
    # the on-demand engagement pack is the one context surface never written
    # to disk — scan its output directly
    client.post("/api/engagements", json={"name": "Canary Pack Probe"})
    eng = client.get("/api/engagements").json()[0]
    assert CANARY not in client.get(f"/api/context-pack?engagement={eng['id']}").text
    for f in Path(config.DATA_DIR).rglob("*"):
        if f.is_file() and "private.db" not in f.name:
            assert CANARY.encode() not in f.read_bytes(), f"canary leaked into {f}"


def test_private_db_not_in_platform_tables_or_backups(client, fresh_db):
    _write_private(client, fresh_db)
    from app.services import admin

    assert not any("private" in t for t in admin.TABLES)
    # platform DB contains no private tables
    tables = [
        r["name"] for r in fresh_db.query("SELECT name FROM sqlite_master WHERE type = 'table'")
    ]
    assert not any("private" in t for t in tables)
    # backups copy platform.db only — never private.db
    result = admin.backup()
    backup_dir = Path(result["path"]).parent
    assert not any("private" in f.name for f in backup_dir.glob("*"))


def test_no_agent_or_review_surface_over_private_entities(fresh_db):
    from app.services.review import _registry
    from app.tools import ALL_TOOLS

    assert not any("private" in name or "feedback_note" in name for name in _registry())
    tool_names = [getattr(t, "__name__", str(t)) for t in ALL_TOOLS]
    assert not any("private" in n or "fb_" in n for n in tool_names)
    # MCP module source must never reference the private service
    import inspect

    from app import mcp_server

    assert "private_notes" not in inspect.getsource(mcp_server)


def test_key_minting_requires_strong_identity(client, fresh_db):
    """The escalation that defeats the whole boundary: X-User must NOT be
    able to mint a usable key for any identity."""
    r = client.post("/api/keys", json={"label": "evil"}, headers={"X-User": "manager"})
    assert r.status_code == 403
    # and therefore private notes stay unreachable for header-only callers
    assert client.get("/api/private/notes", headers={"X-User": "manager"}).status_code == 403


def test_private_db_file_permissions(client, fresh_db):
    import stat

    from app import config

    _write_private(client, fresh_db)
    mode = stat.S_IMODE(Path(config.PRIVATE_DB_PATH).stat().st_mode)
    assert mode == 0o600, f"private.db is {oct(mode)}, expected 0600"


def test_feedback_parses_hyphenated_names(fresh_db):
    from app.services.private_notes import parse_feedback

    assert parse_feedback("fb: mary-jane — crushed the demo") == ("mary-jane", "crushed the demo")
    assert parse_feedback("fb: dana - solid week") == ("dana", "solid week")
    assert parse_feedback("fb: chen: good pushback") == ("chen", "good pushback")


def test_brief_degrades_to_empty(client, fresh_db):
    headers = _setup_key(client, fresh_db)
    r = client.get("/api/private/brief/dana", headers=headers)
    assert r.status_code == 200
    b = r.json()
    assert b["open_blockers"] == [] and b["standups"] == []
    assert "never captured feedback" in b["nudge"]
