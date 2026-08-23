"""Tests for the keyless integration layer: notifications, Slack, memory,
MCP gating, and the optional API token."""

import hashlib
import hmac
import time
from datetime import UTC

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
    # a COUNT, never the message: the batch goes to ONE shared channel, so a
    # body addressed to one person lands in front of everybody
    assert "later thing" not in posts[1]
    assert posts[1] == "Skein digest — 1 notification for ava. Open Skein to read it."
    # the immediate post carries no body either — same channel, same reason
    assert "urgent thing" not in posts[0]
    assert posts[0] == "Skein — 1 notification for ava. Open Skein to read it."

    notifications.mark_read("ava")
    assert notifications.list_notifications("ava") == []


def test_escalation_notifies_owner(fresh_db, monkeypatch):
    from datetime import datetime, timedelta

    from app.services import blockers, notifications, users

    users.ensure_user("marcus")
    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)
    b = blockers.raise_blocker("aging", owner="marcus", impact="critical")
    old = (datetime.now(UTC) - timedelta(hours=3)).isoformat(timespec="seconds")
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
    # notifications are notice-tier: visible in the briefing, silent on the badge
    assert any(a["group"] == "notice" for a in b["attention"])
    assert client.get("/api/attention").json()["count"] == 0
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
    with client.stream(
        "POST", "/api/chat", json={"thread_id": "m", "message": "/remember standups at 10am"}
    ) as r:
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
    return {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": sig,
        "Content-Type": "application/x-www-form-urlencoded",
    }


def test_slack_command_roundtrip(client, monkeypatch):
    from app import config

    r = client.post("/api/slack/command", content=b"text=help")
    assert r.status_code == 404  # unconfigured

    monkeypatch.setattr(config, "SLACK_SIGNING_SECRET", "shhh")
    body = b"text=blocked%20on%20dns&user_name=ava"
    r = client.post("/api/slack/command", content=body, headers=_slack_headers("shhh", body))
    assert r.status_code == 200
    assert "blocker" in r.json()["text"].lower()

    r = client.post(
        "/api/slack/command", content=body, headers=_slack_headers("wrong-secret", body)
    )
    assert r.status_code == 401


def test_workplace_policy_can_deny_signed_slack_writes(fresh_db, monkeypatch):
    from fastapi.testclient import TestClient

    from app import config
    from app.extensions import (
        PolicyContribution,
        PolicyDecision,
        PolicyEffect,
        SkeinModule,
    )
    from app.main import create_app

    def deny_slack(request):
        if request.action == "skein.integration.slack":
            return PolicyDecision(PolicyEffect.DENY, ("Slack is disabled",))
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.4.0",
        policies=(PolicyContribution("acme.workplace.slack", deny_slack),),
    )
    monkeypatch.setattr(config, "SLACK_SIGNING_SECRET", "shhh")
    body = b"text=blocked%20on%20dns&user_name=ava"
    with TestClient(create_app(modules=(module,))) as client:
        response = client.post(
            "/api/slack/command",
            content=body,
            headers=_slack_headers("shhh", body),
        )
    assert response.status_code == 403
    assert fresh_db.query_one("SELECT id FROM blockers") is None


@pytest.mark.parametrize(
    ("effect", "expected"),
    [
        (
            "deny",
            "Workplace policy denied this action. Use an allowed action or ask an"
            " administrator to change the policy.",
        ),
        (
            "review",
            "Workplace policy requires review. This surface cannot resume the action."
            " Use a governed tool or workflow.",
        ),
    ],
)
def test_signed_slack_capture_states_direct_policy_refusal(effect, expected, fresh_db, monkeypatch):
    from fastapi.testclient import TestClient

    from app import config
    from app.extensions import (
        PolicyContribution,
        PolicyDecision,
        PolicyEffect,
        SkeinModule,
    )
    from app.main import create_app

    def capture_rule(request):
        if request.action == "task.create":
            return PolicyDecision(PolicyEffect(effect), ("capture is governed",))
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.4.0",
        policies=(PolicyContribution("acme.workplace.slack-capture", capture_rule),),
    )
    monkeypatch.setattr(config, "SLACK_SIGNING_SECRET", "shhh")
    body = b"text=todo%3A%20policy%20capture&user_name=ava"
    with TestClient(create_app(modules=(module,))) as client:
        pending_before = fresh_db.query_one("SELECT COUNT(*) AS n FROM pending_changes")["n"]
        activity_before = fresh_db.query_one("SELECT COUNT(*) AS n FROM activity")["n"]
        response = client.post(
            "/api/slack/command",
            content=body,
            headers=_slack_headers("shhh", body),
        )
        pending_after = fresh_db.query_one("SELECT COUNT(*) AS n FROM pending_changes")["n"]
        activity_after = fresh_db.query_one("SELECT COUNT(*) AS n FROM activity")["n"]

    assert response.status_code == 200
    assert response.json()["text"] == expected
    assert "⚠" not in response.text
    assert pending_after == pending_before
    assert activity_after == activity_before
    assert fresh_db.query_one("SELECT id FROM tasks WHERE title = 'policy capture'") is None


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


def test_telemetry_redacts_conversation_content_by_default(fresh_db, monkeypatch):
    """The strands tracer emits gen_ai input/output messages and system
    instructions unredacted unless the opt-in token says otherwise. The
    collector sits outside every Skein access control, so conversation
    content must not reach it by default."""
    import os

    import strands.telemetry as st

    from app import config
    from app.telemetry import setup_telemetry

    monkeypatch.setattr(config, "OTEL_ENDPOINT", "http://collector:4318")
    monkeypatch.setattr(
        st,
        "StrandsTelemetry",
        lambda: type("T", (), {"setup_otlp_exporter": lambda self: None})(),
    )
    for var in (
        "OTEL_SEMCONV_STABILITY_OPT_IN",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_SERVICE_NAME",
    ):
        monkeypatch.delenv(var, raising=False)

    assert setup_telemetry() is True
    assert os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] == "gen_ai_unredacted_attributes="

    # an operator's unrelated opt-in tokens survive; the redaction token joins them
    monkeypatch.setenv("OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental")
    assert setup_telemetry() is True
    assert (
        os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"]
        == "gen_ai_latest_experimental,gen_ai_unredacted_attributes="
    )

    # an operator who chose an explicit unredacted list keeps that choice
    chosen = "gen_ai_unredacted_attributes=gen_ai.input.messages"
    monkeypatch.setenv("OTEL_SEMCONV_STABILITY_OPT_IN", chosen)
    assert setup_telemetry() is True
    assert os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] == chosen

    # a bare token without "=" does NOT enable redaction in the tracer — a
    # substring guard would skip the append here and every span would export
    # unredacted while the operator believes redaction is on
    monkeypatch.setenv("OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_unredacted_attributes")
    assert setup_telemetry() is True
    assert (
        os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"]
        == "gen_ai_unredacted_attributes,gen_ai_unredacted_attributes="
    )

    # the token lands even with no Skein endpoint: an exporter wired through
    # plain OTel env autoconfig reads the same variable
    monkeypatch.setattr(config, "OTEL_ENDPOINT", "")
    monkeypatch.delenv("OTEL_SEMCONV_STABILITY_OPT_IN", raising=False)
    assert setup_telemetry() is False
    assert os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] == "gen_ai_unredacted_attributes="


def test_api_token_allows_cors_preflight(client, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "API_TOKEN", "sekrit")
    r = client.options(
        "/api/tasks",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,x-user,content-type",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_slack_garbage_timestamp_is_401(client, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "SLACK_SIGNING_SECRET", "shhh")
    r = client.post(
        "/api/slack/command",
        content=b"text=hi",
        headers={
            "X-Slack-Request-Timestamp": "not-a-number",
            "X-Slack-Signature": "v0=deadbeef",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    assert r.status_code == 401


def _mcp_server(name: str, url: str, tool_name: str) -> dict:
    return {
        "name": name,
        "url": url,
        "tools": {
            tool_name: {
                "version": "1.0.0",
                "effect": "read",
                "risk": "low",
                "policy_action": f"remote.{tool_name}",
                "allowed_agents": [],
                "required_capabilities": [],
                "output_schema": {"type": "object"},
                "timeout_seconds": 1,
                "error_codes": [],
                "receipt": "required",
                "provenance": "service",
            }
        },
    }


@pytest.fixture
def clean_mcp():
    from app.agents import mcp_tools as module

    module.shutdown_mcp()
    yield module
    module.shutdown_mcp()


def test_a_hung_mcp_server_blocks_no_other_agent_build(monkeypatch, clean_mcp):
    """The state lock never spans network I/O. A dead integration costs its
    own tools, not every concurrent chat build."""
    import json
    import threading

    from app import config

    m = clean_mcp
    monkeypatch.setattr(config, "MCP_SERVERS_ERROR", "")
    monkeypatch.setattr(
        config,
        "MCP_SERVERS",
        json.dumps([_mcp_server("a", "https://a.invalid/mcp", "fake-tool")]),
    )
    hang = threading.Event()
    started = threading.Event()

    class FakeTool:
        tool_name = "fake-tool"

    class FakeClient:
        def __exit__(self, *_args):
            return None

    def slow_connect(_server_ids):
        started.set()
        hang.wait(5)
        connection = m._MCPConnection("a", FakeClient(), (FakeTool(),))
        return ([connection.tools[0]], [connection])

    monkeypatch.setattr(m, "_connect_servers", slow_connect)
    result: list = []
    loader = threading.Thread(target=lambda: result.extend(m.mcp_tools()))
    loader.start()
    assert started.wait(2)
    assert m.mcp_tools() == []
    hang.set()
    loader.join(2)
    assert [tool.tool_name for tool in result] == ["fake-tool"]
    assert [tool.tool_name for tool in m.mcp_tools()] == ["fake-tool"]


def test_mcp_retries_only_failed_servers_and_keeps_successes_available(monkeypatch, clean_mcp):
    import json
    import threading
    import time

    from app import config

    m = clean_mcp
    monkeypatch.setattr(config, "MCP_SERVERS_ERROR", "")
    monkeypatch.setattr(
        config,
        "MCP_SERVERS",
        json.dumps(
            [
                _mcp_server("a", "https://a.invalid/mcp", "tool_a"),
                _mcp_server("b", "https://b.invalid/mcp", "tool_b"),
            ]
        ),
    )
    attempts = {"a": 0, "b": 0}
    closes = {"a": 0, "b": 0}
    retry_started = threading.Event()
    release_retry = threading.Event()

    class RemoteTool:
        def __init__(self, name):
            self.tool_name = name

    class FakeClient:
        def __init__(self, factory):
            self.name = "a" if "a.invalid" in factory.args[0] else "b"

        def __enter__(self):
            attempts[self.name] += 1
            return self

        def __exit__(self, *_args):
            closes[self.name] += 1

        def list_tools_sync(self):
            if self.name == "b" and attempts["b"] == 1:
                raise RuntimeError("temporary")
            if self.name == "b":
                retry_started.set()
                release_retry.wait(2)
            return [RemoteTool(f"tool_{self.name}")]

    monkeypatch.setattr("strands.tools.mcp.MCPClient", FakeClient)

    assert [tool.tool_name for tool in m.mcp_tools()] == ["tool_a"]
    assert attempts == {"a": 1, "b": 1}
    assert closes == {"a": 0, "b": 1}
    assert [tool.tool_name for tool in m.mcp_tools()] == ["tool_a"]
    assert attempts == {"a": 1, "b": 1}

    m._retry_state["b"] = (1, 0)
    started = time.monotonic()
    assert [tool.tool_name for tool in m.mcp_tools()] == ["tool_a"]
    assert time.monotonic() - started < 1
    assert retry_started.wait(2)
    assert [tool.tool_name for tool in m.mcp_tools()] == ["tool_a"]
    retry = next(thread for thread in threading.enumerate() if thread.name == "skein-mcp-retry")
    release_retry.set()
    retry.join(2)

    assert attempts == {"a": 1, "b": 2}
    assert [tool.tool_name for tool in m.mcp_tools()] == ["tool_a", "tool_b"]
    assert attempts == {"a": 1, "b": 2}

    m.shutdown_mcp()
    assert closes == {"a": 1, "b": 2}


def test_a_recovered_mcp_collision_removes_both_tools(monkeypatch, clean_mcp):
    import json
    import threading

    from app import config

    m = clean_mcp
    monkeypatch.setattr(config, "MCP_SERVERS_ERROR", "")
    monkeypatch.setattr(
        config,
        "MCP_SERVERS",
        json.dumps(
            [
                _mcp_server("a", "https://a.invalid/mcp", "shared"),
                _mcp_server("b", "https://b.invalid/mcp", "shared"),
            ]
        ),
    )
    attempts = {"a": 0, "b": 0}
    retry_started = threading.Event()
    release_retry = threading.Event()

    class RemoteTool:
        tool_name = "shared"

    class FakeClient:
        def __init__(self, factory):
            self.name = "a" if "a.invalid" in factory.args[0] else "b"

        def __enter__(self):
            attempts[self.name] += 1
            return self

        def __exit__(self, *_args):
            return None

        def list_tools_sync(self):
            if self.name == "b" and attempts["b"] == 1:
                raise RuntimeError("temporary")
            if self.name == "b":
                retry_started.set()
                release_retry.wait(2)
            return [RemoteTool()]

    monkeypatch.setattr("strands.tools.mcp.MCPClient", FakeClient)

    assert [tool.tool_name for tool in m.mcp_tools()] == ["shared"]
    assert [tool.tool_name for tool in m.mcp_tools()] == ["shared"]
    assert attempts == {"a": 1, "b": 1}

    m._retry_state["b"] = (1, 0)
    assert [tool.tool_name for tool in m.mcp_tools()] == ["shared"]
    assert retry_started.wait(2)
    retry = next(thread for thread in threading.enumerate() if thread.name == "skein-mcp-retry")
    release_retry.set()
    retry.join(2)
    assert attempts == {"a": 1, "b": 2}
    assert m.mcp_tools() == []
    assert attempts == {"a": 1, "b": 2}


def test_a_shutdown_mid_connect_is_not_resurrected(monkeypatch, clean_mcp):
    """An in-flight result from before shutdown cannot publish new sessions."""
    import json

    from app import config

    m = clean_mcp
    monkeypatch.setattr(config, "MCP_SERVERS_ERROR", "")
    monkeypatch.setattr(
        config,
        "MCP_SERVERS",
        json.dumps([_mcp_server("a", "https://a.invalid/mcp", "stale-tool")]),
    )
    closed: list = []

    class FakeTool:
        tool_name = "stale-tool"

    class FakeClient:
        def __exit__(self, *_args):
            closed.append(self)

    stale = FakeClient()

    def connect_then_race(_server_ids):
        m.shutdown_mcp()
        connection = m._MCPConnection("a", stale, (FakeTool(),))
        return ([connection.tools[0]], [connection])

    monkeypatch.setattr(m, "_connect_servers", connect_then_race)
    assert m.mcp_tools() == []
    assert closed == [stale]
    assert m._tools is None


def test_a_non_object_mcp_entry_costs_only_its_tools(monkeypatch):
    from app import config
    from app.agents import mcp_tools as m

    monkeypatch.setattr(config, "MCP_SERVERS_ERROR", "")
    monkeypatch.setattr(config, "MCP_SERVERS", '["bad-entry"]')
    assert m._connect_servers() == ([], [])


def test_an_unexpected_mcp_load_error_retries_after_backoff(monkeypatch, clean_mcp):
    import json
    import threading

    from app import config

    m = clean_mcp
    monkeypatch.setattr(config, "MCP_SERVERS_ERROR", "")
    monkeypatch.setattr(
        config,
        "MCP_SERVERS",
        json.dumps([_mcp_server("a", "https://a.invalid/mcp", "tool_a")]),
    )
    calls = 0
    retry_started = threading.Event()
    release_retry = threading.Event()

    def boom(*_args):
        nonlocal calls
        calls += 1
        if calls == 2:
            retry_started.set()
            release_retry.wait(2)
        raise RuntimeError("unexpected")

    monkeypatch.setattr(m, "_connect_servers", boom)
    assert m.mcp_tools() == []
    assert m._loading is False
    assert m._tools is None
    assert m.mcp_tools() == []
    assert calls == 1

    m._retry_state["a"] = (1, 0)
    assert m.mcp_tools() == []
    assert retry_started.wait(2)
    retry = next(thread for thread in threading.enumerate() if thread.name == "skein-mcp-retry")
    release_retry.set()
    retry.join(2)
    assert calls == 2


def test_a_failed_retry_thread_start_leaves_mcp_retryable(monkeypatch, clean_mcp):
    import json
    import threading

    from app import config

    m = clean_mcp
    monkeypatch.setattr(config, "MCP_SERVERS_ERROR", "")
    monkeypatch.setattr(
        config,
        "MCP_SERVERS",
        json.dumps([_mcp_server("a", "https://a.invalid/mcp", "tool_a")]),
    )
    calls = 0
    retried = threading.Event()

    def boom(*_args):
        nonlocal calls
        calls += 1
        if calls == 2:
            retried.set()
        raise RuntimeError("unexpected")

    monkeypatch.setattr(m, "_connect_servers", boom)
    assert m.mcp_tools() == []
    assert calls == 1

    real_thread = threading.Thread

    class DeadThread:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            raise RuntimeError("cannot start new thread")

    # Thread exhaustion at retry time: the call must degrade to the loaded
    # tools and leave _loading clear, or no server ever retries again.
    monkeypatch.setattr(threading, "Thread", DeadThread)
    m._retry_state["a"] = (1, 0)
    assert m.mcp_tools() == []
    assert m._loading is False

    monkeypatch.setattr(threading, "Thread", real_thread)
    assert m.mcp_tools() == []
    assert retried.wait(2)
    assert calls == 2


def test_the_slack_digest_carries_no_message_body(fresh_db, monkeypatch):
    """One channel, N audiences. Every notify() addresses somebody who can
    read the row it quotes, but this batch posts them together — so a crew
    row's title addressed to one member would land in front of the roster.
    `notifications` has no tier to filter on, so nothing is carried at all.

    BOTH tiers, because only the digest was fixed the first time: the
    immediate path posts to the same channel the moment a caller quotes a
    scoped title into it, which blockers.resolve_blocker and delegation do.
    """
    from app.services import notifications

    posts: list[str] = []
    monkeypatch.setattr(notifications, "_post_slack", posts.append)
    notifications.notify("ava", "ZZSECRETZZ vendor terms", tier="digest")
    notifications.notify("bo", "ZZSECRETZZ vendor terms", tier="digest")
    notifications.notify("bo", "another", tier="digest")
    notifications.flush_digest_tier()

    assert len(posts) == 1
    assert "ZZSECRETZZ" not in posts[0]
    assert posts[0] == (
        "Skein digest — 1 notification for ava, 2 notifications for bo. Open Skein to read them."
    )

    notifications.notify("ava", "ZZSECRETZZ escalated", tier="immediate")
    assert len(posts) == 2
    assert "ZZSECRETZZ" not in posts[1]
