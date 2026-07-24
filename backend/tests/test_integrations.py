"""Tests for the keyless integration layer: notifications, Slack, memory,
MCP gating, and the optional API token."""

import hashlib
import hmac
import time

import pytest


def test_notification_tiers(fresh_db, monkeypatch):
    from app.services import notifications

    posts = []
    monkeypatch.setattr(notifications, "_post_slack", posts.append)

    notifications.notify("ava", "urgent thing", tier="immediate")
    notifications.notify("ava", "later thing", tier="digest")
    assert notifications.notify("ava", "quiet thing", tier="passive") is None
    with pytest.raises(ValueError):
        notifications.notify("ava", "x", tier="loud")

    unread = notifications.list_notifications("ava")
    assert len(unread) == 2
    assert len(posts) == 1  # only immediate posted right away

    flushed = notifications.flush_digest_tier()
    assert flushed["flushed"] == 1
    assert len(posts) == 2  # digest batch posted
    assert "later thing" in posts[1]

    notifications.mark_read("ava")
    assert notifications.list_notifications("ava") == []


def test_escalation_notifies_owner(fresh_db, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from app.services import blockers, notifications

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)
    b = blockers.raise_blocker("aging", owner="marcus", impact="critical")
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(timespec="seconds")
    fresh_db.execute("UPDATE blockers SET created_at = ? WHERE id = ?", (old, b["id"]))
    blockers.sweep_escalations()
    msgs = [n["message"] for n in notifications.list_notifications("marcus")]
    assert any("escalated" in m for m in msgs)


def test_briefing_includes_team_notifications(client, monkeypatch):
    from app.services import notifications

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)
    notifications.notify("team", "review the thing", tier="digest")
    b = client.get("/api/briefing").json()
    assert any("review the thing" in n["message"] for n in b["needs_you"]["notifications"])
    assert client.get("/api/attention").json()["count"] >= 1
    client.post("/api/notifications/read", json={"notification_id": 0})


def test_memory_remember_recall_prompt(fresh_db):
    from app.services import memory

    memory.remember("Mario prefers uv over pip", topic="tooling", user="mario")
    memory.remember("Deploys happen Fridays", topic="process")
    assert len(memory.recall()) == 2
    hits = memory.recall("deploys")
    assert hits and "Fridays" in hits[0]["content"]
    prompt = memory.memory_prompt("mario")
    assert "uv over pip" in prompt and "Team memory" in prompt
    assert memory.memory_prompt("nobody-with-no-memories") != ""  # shared memories included

    with pytest.raises(ValueError):
        memory.remember("   ")


def test_mock_agent_remember_command(client):
    with client.stream("POST", "/api/chat",
                       json={"thread_id": "m", "message": "/remember standups at 10am"}) as r:
        assert "Remembered" in r.read().decode()
    assert client.get("/api/memories").json()[0]["content"] == "standups at 10am"


def test_mcp_disabled_without_config(fresh_db):
    from app.agents import mcp_tools

    mcp_tools._tools = None
    assert mcp_tools.mcp_tools() == []


def _slack_headers(secret: str, body: bytes) -> dict:
    ts = str(int(time.time()))
    base = f"v0:{ts}:{body.decode()}"
    sig = "v0=" + hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig,
            "Content-Type": "application/x-www-form-urlencoded"}


def test_slack_command_roundtrip(client, monkeypatch):
    from app import config

    r = client.post("/api/slack/command", content=b"text=help")
    assert r.status_code == 404  # unconfigured

    monkeypatch.setattr(config, "SLACK_SIGNING_SECRET", "shhh")
    body = b"text=blocked%20on%20dns&user_name=ava"
    r = client.post("/api/slack/command", content=body,
                    headers=_slack_headers("shhh", body))
    assert r.status_code == 200
    assert "blocker" in r.json()["text"].lower()

    r = client.post("/api/slack/command", content=body,
                    headers=_slack_headers("wrong-secret", body))
    assert r.status_code == 401


def test_api_token_gate(client, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "API_TOKEN", "sekrit")
    assert client.get("/api/tasks").status_code == 401
    assert client.get("/health").status_code == 200
    ok = client.get("/api/tasks", headers={"Authorization": "Bearer sekrit"})
    assert ok.status_code == 200


def test_telemetry_noop_without_endpoint(fresh_db):
    from app.telemetry import setup_telemetry

    assert setup_telemetry() is False


def test_api_token_allows_cors_preflight(client, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "API_TOKEN", "sekrit")
    r = client.options("/api/tasks", headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization,x-user,content-type",
    })
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_slack_garbage_timestamp_is_401(client, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "SLACK_SIGNING_SECRET", "shhh")
    r = client.post("/api/slack/command", content=b"text=hi", headers={
        "X-Slack-Request-Timestamp": "not-a-number",
        "X-Slack-Signature": "v0=deadbeef",
        "Content-Type": "application/x-www-form-urlencoded",
    })
    assert r.status_code == 401
