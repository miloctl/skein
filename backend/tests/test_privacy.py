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


def test_canary_absent_from_all_egress_surfaces(client, fresh_db, monkeypatch):
    headers = _write_private(client, fresh_db)
    client.post("/api/capture", json={"text": f"fb: dana — {CANARY} extra"}, headers=headers)

    # search + FTS
    assert client.get(f"/api/search?q={CANARY.split('-')[0]}").json() == []
    assert fresh_db.query("SELECT * FROM search_index") == []
    # context pack (DB + artifact file)
    pack = client.post("/api/context-pack/publish", json={}).json()
    assert CANARY not in json.dumps(pack)
    # digest markdown AND the digest's saved note row
    from app.services.digest import publish_digest

    digest = publish_digest(actor="tester", force=True)
    assert CANARY not in digest["markdown"]
    assert CANARY not in json.dumps(fresh_db.query("SELECT * FROM notes"))
    # export files
    from app.services import admin

    result = admin.export()
    assert CANARY not in Path(result["path"]).read_text()
    # activity + notifications
    assert CANARY not in json.dumps(fresh_db.query("SELECT * FROM activity"))
    assert CANARY not in json.dumps(fresh_db.query("SELECT * FROM notifications"))
    # every artifact on disk
    from app import config

    artifacts = Path(config.DATA_DIR) / "artifacts"
    if artifacts.exists():
        for f in artifacts.rglob("*"):
            if f.is_file():
                assert CANARY not in f.read_text()


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


def test_brief_degrades_to_empty(client, fresh_db):
    headers = _setup_key(client, fresh_db)
    r = client.get("/api/private/brief/dana", headers=headers)
    assert r.status_code == 200
    b = r.json()
    assert b["open_blockers"] == [] and b["standups"] == []
    assert "never captured feedback" in b["nudge"]
