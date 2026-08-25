"""Invited agents in private shared chats."""

import json
import threading
import time
from types import SimpleNamespace

from app import db
from app.services import personas, users
from app.services.api_keys import create_key


def auth(name: str) -> dict[str, str]:
    users.ensure_user(name)
    return {"Authorization": f"Bearer {create_key(name, 'shared-chat-agent')['key']}"}


def create_room(client, owner: str = "mira") -> tuple[dict, dict]:
    headers = auth(owner)
    response = client.post(
        "/api/shared-chats",
        json={"title": "Agent room"},
        headers=headers,
    )
    assert response.status_code == 200
    return response.json(), headers


def add_agent(client, thread_id: str, headers: dict, agent: str) -> dict:
    response = client.post(
        f"/api/shared-chats/{thread_id}/agents",
        json={"agent": agent, "share_history": True},
        headers=headers,
    )
    assert response.status_code == 200
    return response.json()


def post_message(
    client,
    thread_id: str,
    headers: dict,
    text: str,
    key: str,
    *,
    invoke_agent: str = "",
) -> dict:
    response = client.post(
        f"/api/shared-chats/{thread_id}/messages",
        json={"message": text, "client_key": key, "invoke_agent": invoke_agent},
        headers=headers,
    )
    assert response.status_code == 200
    return response.json()


def wait_for_terminal_run(thread_id: str) -> dict:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        row = db.query_one(
            "SELECT * FROM chat_agent_runs WHERE thread_id = ? ORDER BY requested_at DESC LIMIT 1",
            (thread_id,),
        )
        if row and row["status"] not in ("pending", "running"):
            return row
        time.sleep(0.02)
    raise AssertionError("shared-chat agent run did not settle")


def test_only_a_steward_adds_dormant_bench_agents(client):
    agent = sorted(personas.bench_slugs())[0]
    room, mira = create_room(client)
    dana = auth("dana")
    invitation = client.post(
        f"/api/shared-chats/{room['id']}/invitations",
        json={"person": "dana", "share_history": True},
        headers=mira,
    ).json()
    client.post(
        f"/api/shared-chats/invitations/{invitation['id']}/accept",
        headers=dana,
    )

    refused = client.post(
        f"/api/shared-chats/{room['id']}/agents",
        json={"agent": agent, "share_history": True},
        headers=dana,
    )
    assert refused.status_code == 403
    assert (
        client.post(
            f"/api/shared-chats/{room['id']}/agents",
            json={"agent": agent, "share_history": False},
            headers=mira,
        ).status_code
        == 422
    )

    detail = add_agent(client, room["id"], mira, agent)
    added = next(member for member in detail["members"] if member["person"] == agent)
    assert added["kind"] == "agent"
    assert added["role"] == "member"
    assert db.query_one("SELECT 1 FROM chat_agent_runs") is None

    second = sorted(personas.bench_slugs() - {agent})[0]
    duplicate = client.post(
        f"/api/shared-chats/{room['id']}/agents",
        json={"agent": second, "share_history": True},
        headers=mira,
    )
    assert duplicate.status_code == 200
    promote = client.post(
        f"/api/shared-chats/{room['id']}/members/role",
        json={"person": agent, "role": "steward"},
        headers=mira,
    )
    assert promote.status_code == 400

    post_message(client, room["id"], mira, f"Hello @{agent}", "plain")
    assert db.query_one("SELECT 1 FROM chat_agent_runs") is None
    removed = client.request(
        "DELETE",
        f"/api/shared-chats/{room['id']}/agents",
        json={"agent": agent},
        headers=mira,
    )
    assert removed.status_code == 200
    assert all(
        member["person"] != agent
        for member in client.get(f"/api/shared-chats/{room['id']}", headers=mira).json()["members"]
    )


def test_mock_agent_runs_only_on_explicit_invocation_and_retry_is_idempotent(client):
    agent = sorted(personas.bench_slugs())[0]
    room, mira = create_room(client)
    add_agent(client, room["id"], mira, agent)

    trigger = post_message(
        client,
        room["id"],
        mira,
        f"@{agent} review this decision",
        "invoke-once",
        invoke_agent=agent,
    )
    duplicate = post_message(
        client,
        room["id"],
        mira,
        f"@{agent} review this decision",
        "invoke-once",
        invoke_agent=agent,
    )
    assert duplicate == trigger

    run = wait_for_terminal_run(room["id"])
    assert run["status"] == "completed"
    assert run["agent"] == agent
    assert run["requested_by"] == "mira"
    assert run["trigger_message_id"] == trigger["id"]
    assert run["turn_id"] == trigger["turn_id"]

    messages = client.get(
        f"/api/shared-chats/{room['id']}/messages",
        headers=mira,
    ).json()
    replies = [message for message in messages if message["author_kind"] == "agent"]
    assert len(replies) == 1
    assert replies[0]["author"] == agent
    assert replies[0]["turn_id"] == trigger["turn_id"]
    assert replies[0]["reply_to_message_id"] == trigger["id"]
    assert db.query_row("SELECT COUNT(*) AS n FROM chat_agent_runs")["n"] == 1
    assert db.query_row("SELECT COUNT(*) AS n FROM sessions")["n"] == 0
    assert db.query_row("SELECT COUNT(*) AS n FROM usage_log")["n"] == 0


def test_agent_run_status_is_private_and_startup_never_retries_unknown_work(client):
    from app.services import shared_chat_agents

    agent = sorted(personas.bench_slugs())[0]
    room, mira = create_room(client)
    add_agent(client, room["id"], mira, agent)
    trigger = post_message(
        client,
        room["id"],
        mira,
        f"@{agent} inspect this",
        "private-run",
        invoke_agent=agent,
    )
    wait_for_terminal_run(room["id"])

    outsider = auth("outsider")
    assert (
        client.get(
            f"/api/shared-chats/{room['id']}/agent-runs",
            headers=outsider,
        ).status_code
        == 404
    )

    with db.transaction():
        db.execute(
            "UPDATE chat_agent_runs SET status = 'running', started_at = ?,"
            " finished_at = NULL, response_message_id = NULL WHERE turn_id = ?",
            (db.now(), trigger["turn_id"]),
        )
    assert shared_chat_agents.recover_startup() == 1
    recovered = db.query_row(
        "SELECT status, error_code FROM chat_agent_runs WHERE turn_id = ?",
        (trigger["turn_id"],),
    )
    assert recovered == {
        "status": "completion_unknown",
        "error_code": "process_restarted",
    }
    shared_chat_agents.kick()
    time.sleep(0.05)
    assert (
        db.query_row(
            "SELECT status FROM chat_agent_runs WHERE turn_id = ?",
            (trigger["turn_id"],),
        )["status"]
        == "completion_unknown"
    )
    with db.transaction():
        db.execute(
            "UPDATE chat_agent_runs SET execution_active = TRUE WHERE turn_id = ?",
            (trigger["turn_id"],),
        )
    assert shared_chat_agents.recover_startup() == 1
    assert (
        db.query_row(
            "SELECT execution_active FROM chat_agent_runs WHERE turn_id = ?",
            (trigger["turn_id"],),
        )["execution_active"]
        is False
    )


def test_active_agent_run_blocks_removal_and_archival(client):
    agent = sorted(personas.bench_slugs())[0]
    room, mira = create_room(client)
    add_agent(client, room["id"], mira, agent)
    message = post_message(client, room["id"], mira, "queued", "queued")
    with db.transaction():
        db.execute(
            "INSERT INTO chat_agent_runs"
            " (turn_id, batch_id, thread_id, trigger_message_id, agent, requested_by,"
            " requester_subject, status, execution_active, requested_at)"
            " VALUES ('held-turn', 'held-turn', ?, ?, ?, 'mira', '{}',"
            " 'completion_unknown', TRUE, ?)",
            (room["id"], message["id"], agent, db.now()),
        )

    removed = client.request(
        "DELETE",
        f"/api/shared-chats/{room['id']}/agents",
        json={"agent": agent},
        headers=mira,
    )
    assert removed.status_code == 409
    assert client.post(f"/api/shared-chats/{room['id']}/archive", headers=mira).status_code == 409


def test_removing_a_requester_cancels_pending_calls_and_waits_for_running_calls(
    client, monkeypatch
):
    from app.services import shared_chat_agents

    agent = sorted(personas.bench_slugs())[0]
    room, mira = create_room(client)
    dana = auth("dana")
    invitation = client.post(
        f"/api/shared-chats/{room['id']}/invitations",
        json={"person": "dana", "share_history": True},
        headers=mira,
    ).json()
    client.post(
        f"/api/shared-chats/invitations/{invitation['id']}/accept",
        headers=dana,
    )
    add_agent(client, room["id"], mira, agent)
    monkeypatch.setattr(shared_chat_agents, "kick", lambda: False)
    post_message(
        client,
        room["id"],
        dana,
        f"@{agent} queued by Dana",
        "dana-pending",
        invoke_agent=agent,
    )

    removed = client.request(
        "DELETE",
        f"/api/shared-chats/{room['id']}/members",
        json={"person": "dana"},
        headers=mira,
    )
    assert removed.status_code == 200
    assert db.query_row("SELECT status, error_code FROM chat_agent_runs") == {
        "status": "refused",
        "error_code": "requester_removed",
    }

    second_room, second_owner = create_room(client, owner="mira")
    second_invitation = client.post(
        f"/api/shared-chats/{second_room['id']}/invitations",
        json={"person": "dana", "share_history": True},
        headers=second_owner,
    ).json()
    client.post(
        f"/api/shared-chats/invitations/{second_invitation['id']}/accept",
        headers=dana,
    )
    add_agent(client, second_room["id"], second_owner, agent)
    trigger = post_message(
        client,
        second_room["id"],
        dana,
        f"@{agent} running for Dana",
        "dana-running",
        invoke_agent=agent,
    )
    with db.transaction():
        db.execute(
            "UPDATE chat_agent_runs SET status = 'running', execution_active = TRUE"
            " WHERE trigger_message_id = ?",
            (trigger["id"],),
        )
    refused = client.request(
        "DELETE",
        f"/api/shared-chats/{second_room['id']}/members",
        json={"person": "dana"},
        headers=second_owner,
    )
    assert refused.status_code == 409


def test_deactivated_agent_cannot_be_added_called_or_drain_pending_work(client, monkeypatch):
    from app.services import shared_chat_agents

    agent = sorted(personas.bench_slugs())[0]
    room, mira = create_room(client)
    add_agent(client, room["id"], mira, agent)
    monkeypatch.setattr(shared_chat_agents, "kick", lambda: False)
    trigger = post_message(
        client,
        room["id"],
        mira,
        f"@{agent} queued before deactivation",
        "deactivated-pending",
        invoke_agent=agent,
    )
    users.set_active(agent, False, actor="tester")
    assert db.query_row(
        "SELECT status, error_code FROM chat_agent_runs WHERE trigger_message_id = ?",
        (trigger["id"],),
    ) == {"status": "refused", "error_code": "agent_unavailable"}
    assert (
        client.post(
            f"/api/shared-chats/{room['id']}/messages",
            json={
                "message": f"@{agent} call after deactivation",
                "client_key": "inactive-call",
                "invoke_agent": agent,
            },
            headers=mira,
        ).status_code
        == 400
    )
    client.request(
        "DELETE",
        f"/api/shared-chats/{room['id']}/agents",
        json={"agent": agent},
        headers=mira,
    )
    assert (
        client.post(
            f"/api/shared-chats/{room['id']}/agents",
            json={"agent": agent, "share_history": True},
            headers=mira,
        ).status_code
        == 400
    )


def test_deactivation_waits_for_a_live_shared_chat_execution(client, monkeypatch):
    from app.services import shared_chat_agents

    agent = sorted(personas.bench_slugs())[0]
    room, mira = create_room(client)
    add_agent(client, room["id"], mira, agent)
    monkeypatch.setattr(shared_chat_agents, "kick", lambda: False)
    trigger = post_message(
        client,
        room["id"],
        mira,
        f"@{agent} live execution",
        "deactivate-live",
        invoke_agent=agent,
    )
    with db.transaction():
        db.execute(
            "UPDATE chat_agent_runs SET status = 'running', execution_active = TRUE"
            " WHERE trigger_message_id = ?",
            (trigger["id"],),
        )

    for identity in (agent, "mira"):
        try:
            users.set_active(identity, False, actor="tester")
        except db.Conflict:
            pass
        else:
            raise AssertionError("an active shared-chat identity was deactivated")
        assert users.is_active(identity)


def test_idle_invited_bench_agent_must_leave_rooms_before_rename(client):
    agent = sorted(personas.bench_slugs())[0]
    room, mira = create_room(client)
    add_agent(client, room["id"], mira, agent)

    try:
        users.rename_user(agent, "renamed-agent", actor="tester")
    except db.Conflict:
        pass
    else:
        raise AssertionError("an invited bench identity was renamed out of its persona")


def test_identity_rename_waits_for_pending_or_live_agent_work(client, monkeypatch):
    from app.services import shared_chat_agents

    agent = sorted(personas.bench_slugs())[0]
    room, mira = create_room(client)
    add_agent(client, room["id"], mira, agent)
    monkeypatch.setattr(shared_chat_agents, "kick", lambda: False)
    post_message(
        client,
        room["id"],
        mira,
        f"@{agent} hold identities",
        "rename-hold",
        invoke_agent=agent,
    )

    for old, new in (("mira", "mira-renamed"), (agent, "renamed-agent")):
        try:
            users.rename_user(old, new, actor="tester")
        except db.Conflict:
            pass
        else:
            raise AssertionError("an identity moved while its shared-chat turn was pending")


def test_provider_fault_stores_only_a_safe_code(client, monkeypatch):
    from app import config

    agent = sorted(personas.bench_slugs())[0]
    room, mira = create_room(client)
    add_agent(client, room["id"], mira, agent)
    monkeypatch.setattr(
        config,
        "MODEL_PROVIDER_ERROR",
        "secret token sk-live-abcd request_id=42",
    )
    post_message(
        client,
        room["id"],
        mira,
        f"@{agent} inspect this",
        "provider-fault",
        invoke_agent=agent,
    )
    run = wait_for_terminal_run(room["id"])
    assert run["status"] == "failed"
    assert run["error_code"] == "provider_unavailable"
    projection = client.get(
        f"/api/shared-chats/{room['id']}/agent-runs",
        headers=mira,
    ).text
    assert "sk-live" not in projection and "request_id" not in projection
    assert (
        db.query_one(
            "SELECT 1 FROM chat_messages WHERE thread_id = ? AND author_kind = 'agent'",
            (room["id"],),
        )
        is None
    )


def test_failed_turn_persists_receipts_without_provider_error_text(client, monkeypatch):
    from app import config
    from app.agents import team_agent

    agent = sorted(personas.bench_slugs())[0]
    room, mira = create_room(client)
    add_agent(client, room["id"], mira, agent)
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")

    class FakeAgent:
        event_loop_metrics = SimpleNamespace(
            accumulated_usage={}, accumulated_metrics={}, cycle_count=1
        )
        model = SimpleNamespace(get_config=lambda: {"model_id": "test-model"})

        def __call__(self, prompt):
            del prompt
            from app.tools._gate import gated_write

            gated_write(
                "task",
                "create",
                {"title": "proposal before provider failure"},
                direct=lambda: {"id": 999},
            )
            raise RuntimeError("secret sk-live-abcd request_id=42")

    monkeypatch.setattr(team_agent, "build_agent", lambda *args, **kwargs: FakeAgent())
    trigger = post_message(
        client,
        room["id"],
        mira,
        f"@{agent} file and fail",
        "receipt-failure",
        invoke_agent=agent,
    )
    run = wait_for_terminal_run(room["id"])
    assert run["status"] == "completion_unknown"
    receipt = db.query_row(
        "SELECT content FROM chat_messages WHERE reply_to_message_id = ? AND author_kind = 'agent'",
        (trigger["id"],),
    )["content"]
    assert "Proposal queued for human review" in receipt
    assert "sk-live" not in receipt and "request_id" not in receipt


def test_long_agent_reply_keeps_authoritative_receipts(client, monkeypatch):
    from app import config
    from app.agents import team_agent

    agent = sorted(personas.bench_slugs())[0]
    room, mira = create_room(client)
    add_agent(client, room["id"], mira, agent)
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")

    class FakeAgent:
        event_loop_metrics = SimpleNamespace(
            accumulated_usage={}, accumulated_metrics={}, cycle_count=1
        )
        model = SimpleNamespace(get_config=lambda: {"model_id": "test-model"})

        def __call__(self, prompt):
            del prompt
            from app.tools._gate import gated_write

            gated_write(
                "task",
                "create",
                {"title": "proposal after long reply"},
                direct=lambda: {"id": 999},
            )
            return "x" * 20_000

    monkeypatch.setattr(team_agent, "build_agent", lambda *args, **kwargs: FakeAgent())
    trigger = post_message(
        client,
        room["id"],
        mira,
        f"@{agent} answer at length",
        "long-receipt",
        invoke_agent=agent,
    )
    assert wait_for_terminal_run(room["id"])["status"] == "completed"
    content = db.query_row(
        "SELECT content FROM chat_messages WHERE reply_to_message_id = ?",
        (trigger["id"],),
    )["content"]
    assert "Proposal queued for human review" in content


def test_late_timeout_completion_persists_reply_receipts_and_releases_access(client, monkeypatch):
    from app import config
    from app.agents import team_agent

    agent = sorted(personas.bench_slugs())[0]
    room, mira = create_room(client)
    add_agent(client, room["id"], mira, agent)
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "AGENT_RUN_SECONDS", 0.05)
    started = threading.Event()
    release = threading.Event()

    class FakeAgent:
        event_loop_metrics = SimpleNamespace(
            accumulated_usage={}, accumulated_metrics={}, cycle_count=1
        )
        model = SimpleNamespace(get_config=lambda: {"model_id": "test-model"})

        def __call__(self, prompt):
            del prompt
            started.set()
            assert release.wait(2)
            from app.tools._gate import gated_write

            gated_write(
                "task",
                "create",
                {"title": "proposal after timeout"},
                direct=lambda: {"id": 999},
            )
            return "late answer"

    monkeypatch.setattr(team_agent, "build_agent", lambda *args, **kwargs: FakeAgent())
    trigger = post_message(
        client,
        room["id"],
        mira,
        f"@{agent} finish late",
        "late-timeout",
        invoke_agent=agent,
    )
    assert started.wait(1)
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        run = db.query_row(
            "SELECT status, execution_active FROM chat_agent_runs WHERE trigger_message_id = ?",
            (trigger["id"],),
        )
        if run["status"] == "completion_unknown":
            break
        time.sleep(0.01)
    assert run == {"status": "completion_unknown", "execution_active": True}
    assert (
        client.request(
            "DELETE",
            f"/api/shared-chats/{room['id']}/agents",
            json={"agent": agent},
            headers=mira,
        ).status_code
        == 409
    )
    release.set()

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        run = db.query_row(
            "SELECT status, execution_active, response_message_id FROM chat_agent_runs"
            " WHERE trigger_message_id = ?",
            (trigger["id"],),
        )
        if run["status"] == "completed":
            break
        time.sleep(0.02)
    assert run["execution_active"] is False
    content = db.query_row(
        "SELECT content FROM chat_messages WHERE id = ?",
        (run["response_message_id"],),
    )["content"]
    assert "late answer" in content
    assert "Proposal queued for human review" in content


def test_late_persistence_failure_releases_session_and_execution_slot(client, monkeypatch):
    from app import config
    from app.agents import team_agent
    from app.services import shared_chat_agents

    agent = sorted(personas.bench_slugs())[0]
    room, mira = create_room(client)
    add_agent(client, room["id"], mira, agent)
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "AGENT_RUN_SECONDS", 0.05)
    started = threading.Event()
    release = threading.Event()

    class FakeAgent:
        event_loop_metrics = SimpleNamespace(
            accumulated_usage={}, accumulated_metrics={}, cycle_count=1
        )
        model = SimpleNamespace(get_config=lambda: {"model_id": "test-model"})

        def __call__(self, prompt):
            del prompt
            started.set()
            release.wait(2)
            return "late answer that cannot persist"

    monkeypatch.setattr(team_agent, "build_agent", lambda *_args, **_kwargs: FakeAgent())
    monkeypatch.setattr(
        shared_chat_agents,
        "_finish_success",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("database fault")),
    )
    trigger = post_message(
        client,
        room["id"],
        mira,
        f"@{agent} fail after timeout",
        "late-persistence-fault",
        invoke_agent=agent,
    )
    assert started.wait(1)
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            run = db.query_row(
                "SELECT status, execution_active FROM chat_agent_runs WHERE trigger_message_id = ?",
                (trigger["id"],),
            )
            if run["status"] == "completion_unknown":
                break
            time.sleep(0.01)
        assert run == {"status": "completion_unknown", "execution_active": True}
    finally:
        release.set()

    lock = shared_chat_agents._session_lock(room["id"], agent)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        run = db.query_row(
            "SELECT status, execution_active FROM chat_agent_runs WHERE trigger_message_id = ?",
            (trigger["id"],),
        )
        if not run["execution_active"] and not lock.locked():
            break
        time.sleep(0.01)
    assert run == {"status": "completion_unknown", "execution_active": False}
    assert not lock.locked()
    assert shared_chat_agents.wait_for_idle(2)


def test_real_turn_uses_workspace_tools_and_forces_requester_attributed_review(client, monkeypatch):
    from app import config
    from app.agents import team_agent
    from app.services import activity, collab, delegation, review, scope
    from app.services.chat_threads import persona_session_id

    agent = sorted(personas.bench_slugs())[0]
    room, mira = create_room(client)
    add_agent(client, room["id"], mira, agent)
    private_note = collab.save_note(
        "private",
        "not for the room agent",
        actor="mira",
        visibility="private",
    )["id"]
    delegation.set_authority(agent, "task", "autonomous", actor="tester")
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "AGENT_REVIEW", False)
    built_with: dict = {}

    class FakeAgent:
        event_loop_metrics = SimpleNamespace(
            accumulated_usage={"inputTokens": 12, "outputTokens": 4},
            accumulated_metrics={"latencyMs": 8},
            cycle_count=1,
        )
        model = SimpleNamespace(get_config=lambda: {"model_id": "test-model"})

        def __call__(self, prompt):
            from app.tools._gate import gated_write

            built_with["prompt"] = prompt
            built_with["private_write"] = json.loads(
                gated_write(
                    "task",
                    "create",
                    {
                        "title": "private from a shared chat",
                        "visibility": "private",
                    },
                    direct=lambda: {"id": 998},
                )
            )
            built_with["private_update"] = json.loads(
                gated_write(
                    "note_edit",
                    "update",
                    {"topic": "leaked"},
                    direct=lambda: {"id": private_note},
                    entity_id=private_note,
                )
            )
            built_with["absent_update"] = json.loads(
                gated_write(
                    "note_edit",
                    "update",
                    {"topic": "absent"},
                    direct=lambda: {"id": private_note + 1000},
                    entity_id=private_note + 1000,
                )
            )
            gated_write(
                "task",
                "create",
                {"title": "reviewed from a shared chat"},
                direct=lambda: {"id": 999},
            )
            return "I proposed one task."

    def fake_build(thread_id, **kwargs):
        built_with.update({"thread_id": thread_id, **kwargs})
        return FakeAgent()

    monkeypatch.setattr(team_agent, "build_agent", fake_build)
    trigger = post_message(
        client,
        room["id"],
        mira,
        f"@{agent} file the follow-up",
        "governed",
        invoke_agent=agent,
    )
    assert wait_for_terminal_run(room["id"])["status"] == "completed"

    assert built_with["thread_id"] == persona_session_id(room["id"], agent)
    assert built_with["user"] == "shared-chat"
    assert built_with["viewer"] is scope.NOBODY
    assert built_with["review_forced"] is True
    assert "recall_memories" not in built_with["allowed_tools"]
    assert "consult_specialist" not in built_with["allowed_tools"]
    assert "submit_for_acceptance" not in built_with["allowed_tools"]
    workspace_error = {
        "error": "This shared-chat agent can use tools on workspace-visible records only."
    }
    assert built_with["private_write"] == workspace_error
    target_error = {"error": "No workspace-visible record was found."}
    assert built_with["private_update"] == target_error
    assert built_with["absent_update"] == target_error
    proposal = db.query_row("SELECT * FROM pending_changes ORDER BY id DESC LIMIT 1")
    assert proposal["proposed_by"] == agent
    assert proposal["requested_by"] == "mira"
    assert proposal["review_visibility"] == "private"
    assert proposal["review_owner"] == "mira"
    assert db.query_row("SELECT COUNT(*) AS n FROM pending_changes")["n"] == 1
    assert [row["id"] for row in review.list_changes(viewer=scope.Viewer("mira", True))] == [
        proposal["id"]
    ]
    assert review.list_changes(viewer=scope.Viewer("outsider", True)) == []
    assert all(
        row["action"] != "propose_private_change" for row in activity.feed("outsider")["entries"]
    )
    assert any(
        row["action"] == "propose_private_change" for row in activity.feed("mira")["entries"]
    )
    outsider = auth("outsider")
    assert client.get(f"/api/review/{proposal['id']}/diff", headers=outsider).status_code == 404
    assert client.post(
        "/api/review/seen",
        json={"ids": [proposal["id"]]},
        headers=outsider,
    ).json() == {"seen": 0}
    assert (
        db.query_row("SELECT claim_at FROM pending_changes WHERE id = ?", (proposal["id"],))[
            "claim_at"
        ]
        is None
    )
    assert client.get(f"/api/review/{proposal['id']}/diff", headers=mira).status_code == 200
    assert (
        db.query_one("SELECT 1 FROM notifications WHERE pending_change_id = ?", (proposal["id"],))
        is None
    )
    assert db.query_one("SELECT 1 FROM tasks WHERE title = 'reviewed from a shared chat'") is None
    usage_row = db.query_row(
        "SELECT thread_id, agent_name, requested_by, trigger_message_id,"
        " chat_agent_run_id FROM usage_log"
    )
    assert usage_row == {
        "thread_id": room["id"],
        "agent_name": agent,
        "requested_by": "mira",
        "trigger_message_id": trigger["id"],
        "chat_agent_run_id": trigger["turn_id"],
    }
    reply = db.query_row(
        "SELECT content FROM chat_messages WHERE reply_to_message_id = ?",
        (trigger["id"],),
    )["content"]
    assert "Proposal queued for human review" in reply


def test_two_turns_run_serially_against_one_agent_session(client, monkeypatch):
    from app import config
    from app.agents import team_agent

    agent = sorted(personas.bench_slugs())[0]
    room, mira = create_room(client)
    add_agent(client, room["id"], mira, agent)
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    first_started = threading.Event()
    release_first = threading.Event()
    state = {"active": 0, "max_active": 0, "calls": 0, "sessions": []}
    state_lock = threading.Lock()

    class FakeAgent:
        event_loop_metrics = SimpleNamespace(
            accumulated_usage={},
            accumulated_metrics={},
            cycle_count=1,
        )
        model = SimpleNamespace(get_config=lambda: {"model_id": "test-model"})

        def __call__(self, prompt):
            del prompt
            with state_lock:
                state["active"] += 1
                state["calls"] += 1
                call = state["calls"]
                state["max_active"] = max(state["max_active"], state["active"])
            if call == 1:
                first_started.set()
                assert release_first.wait(2)
            with state_lock:
                state["active"] -= 1
            return f"reply {call}"

    def fake_build(thread_id, **kwargs):
        del kwargs
        state["sessions"].append(thread_id)
        return FakeAgent()

    monkeypatch.setattr(team_agent, "build_agent", fake_build)
    post_message(
        client,
        room["id"],
        mira,
        f"@{agent} first",
        "serial-one",
        invoke_agent=agent,
    )
    assert first_started.wait(1)
    post_message(
        client,
        room["id"],
        mira,
        f"@{agent} second",
        "serial-two",
        invoke_agent=agent,
    )
    time.sleep(0.05)
    assert (
        db.query_row("SELECT COUNT(*) AS n FROM chat_agent_runs WHERE status = 'running'")["n"] == 1
    )
    assert (
        db.query_row("SELECT COUNT(*) AS n FROM chat_agent_runs WHERE status = 'pending'")["n"] == 1
    )
    release_first.set()

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if (
            db.query_row("SELECT COUNT(*) AS n FROM chat_agent_runs WHERE status = 'completed'")[
                "n"
            ]
            == 2
        ):
            break
        time.sleep(0.02)
    assert state["calls"] == 2
    assert state["max_active"] == 1
    assert len(set(state["sessions"])) == 1


def test_same_second_calls_run_in_trigger_message_order(client, monkeypatch):
    from app import config
    from app.agents import team_agent
    from app.services import chat_threads, shared_chat_agents

    agent = sorted(personas.bench_slugs())[0]
    room, mira = create_room(client)
    add_agent(client, room["id"], mira, agent)
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(db, "now", lambda: "2026-08-24T12:00:00+00:00")
    turns = iter(("f" * 32, "0" * 32))
    monkeypatch.setattr(chat_threads.secrets, "token_hex", lambda _bytes: next(turns))
    real_kick = shared_chat_agents.kick
    monkeypatch.setattr(shared_chat_agents, "kick", lambda: False)
    prompts: list[str] = []

    class FakeAgent:
        event_loop_metrics = SimpleNamespace(
            accumulated_usage={}, accumulated_metrics={}, cycle_count=1
        )
        model = SimpleNamespace(get_config=lambda: {"model_id": "test-model"})

        def __call__(self, prompt):
            prompts.append(str(prompt))
            return "done"

    monkeypatch.setattr(team_agent, "build_agent", lambda _thread, **_kwargs: FakeAgent())
    post_message(
        client,
        room["id"],
        mira,
        f"@{agent} first ordered call",
        "ordered-one",
        invoke_agent=agent,
    )
    post_message(
        client,
        room["id"],
        mira,
        f"@{agent} second ordered call",
        "ordered-two",
        invoke_agent=agent,
    )
    monkeypatch.setattr(shared_chat_agents, "kick", real_kick)
    real_kick()

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if (
            db.query_row("SELECT COUNT(*) AS n FROM chat_agent_runs WHERE status = 'completed'")[
                "n"
            ]
            == 2
        ):
            break
        time.sleep(0.02)
    assert "first ordered call" in prompts[0]
    assert "second ordered call" in prompts[1]


def test_run_projection_keeps_the_newest_status_rows(client):
    agent = sorted(personas.bench_slugs())[0]
    room, mira = create_room(client)
    add_agent(client, room["id"], mira, agent)
    now = db.now()
    with db.transaction():
        for index in range(1_001):
            message_id = db.execute(
                "INSERT INTO chat_messages"
                " (thread_id, role, content, created_at, author_kind, author, client_key, turn_id)"
                " VALUES (?, 'user', 'call', ?, 'human', 'mira', ?, ?) RETURNING id",
                (room["id"], now, f"run-key-{index}", f"turn-{index}"),
            )
            db.execute(
                "INSERT INTO chat_agent_runs"
                " (turn_id, batch_id, thread_id, trigger_message_id, agent, requested_by,"
                " requester_subject, status, requested_at, finished_at)"
                " VALUES (?, ?, ?, ?, ?, 'mira', '{}', 'completed', ?, ?)",
                (
                    f"turn-{index}",
                    f"turn-{index}",
                    room["id"],
                    message_id,
                    agent,
                    now,
                    now,
                ),
            )

    rows = client.get(
        f"/api/shared-chats/{room['id']}/agent-runs",
        headers=mira,
    ).json()
    assert len(rows) == 1_000
    assert rows[-1]["turn_id"] == "turn-1000"
    assert all(row["turn_id"] != "turn-0" for row in rows)


def test_one_message_runs_four_invited_agents_and_retry_stays_idempotent(client):
    agents = sorted(personas.bench_slugs())[:4]
    room, mira = create_room(client)
    for agent in agents:
        add_agent(client, room["id"], mira, agent)

    response = client.post(
        f"/api/shared-chats/{room['id']}/messages",
        json={
            "message": " ".join(f"@{agent}" for agent in agents) + " review this",
            "client_key": "four-agent-call",
            "invoke_agents": agents,
        },
        headers=mira,
    )
    assert response.status_code == 200
    trigger = response.json()
    duplicate = client.post(
        f"/api/shared-chats/{room['id']}/messages",
        json={
            "message": " ".join(f"@{agent}" for agent in agents) + " review this",
            "client_key": "four-agent-call",
            "invoke_agents": agents,
        },
        headers=mira,
    )
    assert duplicate.status_code == 200
    assert duplicate.json() == trigger

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        completed = db.query_row(
            "SELECT COUNT(*) AS n FROM chat_agent_runs WHERE thread_id = ?"
            " AND status = 'completed'",
            (room["id"],),
        )["n"]
        if completed == 4:
            break
        time.sleep(0.02)
    runs = db.query(
        "SELECT * FROM chat_agent_runs WHERE thread_id = ? ORDER BY agent",
        (room["id"],),
    )
    assert len(runs) == 4
    assert {run["agent"] for run in runs} == set(agents)
    assert {run["batch_id"] for run in runs} == {trigger["turn_id"]}
    replies = db.query(
        "SELECT author, reply_to_message_id FROM chat_messages"
        " WHERE thread_id = ? AND author_kind = 'agent'",
        (room["id"],),
    )
    assert {reply["author"] for reply in replies} == set(agents)
    assert {reply["reply_to_message_id"] for reply in replies} == {trigger["id"]}


def test_a_fifth_agent_is_refused_and_four_invocations_spend_four_chat_slots(client, monkeypatch):
    from app import ratelimit

    agents = sorted(personas.bench_slugs())[:5]
    room, mira = create_room(client)
    for agent in agents[:4]:
        add_agent(client, room["id"], mira, agent)
    fifth = client.post(
        f"/api/shared-chats/{room['id']}/agents",
        json={"agent": agents[4], "share_history": True},
        headers=mira,
    )
    assert fifth.status_code == 409
    hidden = client.post(
        f"/api/shared-chats/{room['id']}/messages",
        json={
            "message": "call four without showing who",
            "client_key": "hidden-fanout",
            "invoke_agents": agents[:4],
        },
        headers=mira,
    )
    assert hidden.status_code == 400

    ratelimit.reset()
    monkeypatch.setitem(ratelimit.LIMITS, "chat", 3)
    called = client.post(
        f"/api/shared-chats/{room['id']}/messages",
        json={
            "message": " ".join(f"@{agent}" for agent in agents[:4]) + " call four",
            "client_key": "over-chat-cap",
            "invoke_agents": agents[:4],
        },
        headers=mira,
    )
    assert called.status_code == 429
    assert (
        db.query_one(
            "SELECT 1 FROM chat_messages WHERE thread_id = ? AND client_key = 'over-chat-cap'",
            (room["id"],),
        )
        is None
    )
    plain = client.post(
        f"/api/shared-chats/{room['id']}/messages",
        json={"message": "human only", "client_key": "after-weighted-refusal"},
        headers=mira,
    )
    assert plain.status_code == 200


def test_distinct_invited_agents_execute_in_parallel_sessions(client, monkeypatch):
    from app import config
    from app.agents import team_agent

    agents = sorted(personas.bench_slugs())[:2]
    room, mira = create_room(client)
    for agent in agents:
        add_agent(client, room["id"], mira, agent)
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    barrier = threading.Barrier(2)
    sessions: dict[str, str] = {}

    class FakeAgent:
        event_loop_metrics = SimpleNamespace(
            accumulated_usage={}, accumulated_metrics={}, cycle_count=1
        )
        model = SimpleNamespace(get_config=lambda: {"model_id": "test-model"})

        def __init__(self, agent):
            self.agent = agent

        def __call__(self, prompt):
            del prompt
            barrier.wait(timeout=2)
            return f"reply from {self.agent}"

    def fake_build(thread_id, **kwargs):
        sessions[kwargs["persona"]] = thread_id
        return FakeAgent(kwargs["persona"])

    monkeypatch.setattr(team_agent, "build_agent", fake_build)
    response = client.post(
        f"/api/shared-chats/{room['id']}/messages",
        json={
            "message": f"@{agents[0]} @{agents[1]} compare",
            "client_key": "parallel-call",
            "invoke_agents": agents,
        },
        headers=mira,
    )
    assert response.status_code == 200

    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        if (
            db.query_row(
                "SELECT COUNT(*) AS n FROM chat_agent_runs WHERE thread_id = ?"
                " AND status = 'completed'",
                (room["id"],),
            )["n"]
            == 2
        ):
            break
        time.sleep(0.02)
    assert set(sessions) == set(agents)
    assert len(set(sessions.values())) == 2
    assert (
        db.query_row(
            "SELECT COUNT(*) AS n FROM chat_messages WHERE thread_id = ? AND author_kind = 'agent'",
            (room["id"],),
        )["n"]
        == 2
    )


def test_global_four_call_drain_refills_across_rooms(client, monkeypatch):
    from app import config
    from app.agents import team_agent
    from app.services import shared_chat_agents

    agents = sorted(personas.bench_slugs())[:5]
    first_room, first_headers = create_room(client, owner="mira")
    second_room, second_headers = create_room(client, owner="mira")
    for agent in agents[:4]:
        add_agent(client, first_room["id"], first_headers, agent)
    add_agent(client, second_room["id"], second_headers, agents[4])
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "AGENT_RUN_SECONDS", 0.05)
    real_kick = shared_chat_agents.kick
    monkeypatch.setattr(shared_chat_agents, "kick", lambda: False)

    state_lock = threading.Lock()
    entered: list[str] = []
    active = 0
    max_active = 0
    exited = 0
    first_four_entered = threading.Event()
    fifth_entered = threading.Event()
    all_calls_exited = threading.Event()
    release_one = threading.Event()
    release_rest = threading.Event()
    released_agent = agents[0]

    class FakeAgent:
        event_loop_metrics = SimpleNamespace(
            accumulated_usage={}, accumulated_metrics={}, cycle_count=1
        )
        model = SimpleNamespace(get_config=lambda: {"model_id": "test-model"})

        def __init__(self, agent):
            self.agent = agent

        def __call__(self, prompt):
            nonlocal active, max_active, exited
            del prompt
            with state_lock:
                entered.append(self.agent)
                active += 1
                max_active = max(max_active, active)
                if len(entered) == 4:
                    first_four_entered.set()
                if self.agent == agents[4]:
                    fifth_entered.set()
            gate = release_one if self.agent == released_agent else release_rest
            gate.wait(10)
            with state_lock:
                active -= 1
                exited += 1
                if exited == 5:
                    all_calls_exited.set()
            return f"reply from {self.agent}"

    monkeypatch.setattr(
        team_agent,
        "build_agent",
        lambda _thread_id, **kwargs: FakeAgent(kwargs["persona"]),
    )
    first = client.post(
        f"/api/shared-chats/{first_room['id']}/messages",
        json={
            "message": " ".join(f"@{agent}" for agent in agents[:4]) + " compare",
            "client_key": "global-four-first",
            "invoke_agents": agents[:4],
        },
        headers=first_headers,
    )
    assert first.status_code == 200
    post_message(
        client,
        second_room["id"],
        second_headers,
        f"@{agents[4]} fifth",
        "global-four-fifth",
        invoke_agent=agents[4],
    )
    monkeypatch.setattr(shared_chat_agents, "kick", real_kick)
    assert real_kick() is True

    fifth_started = False
    active_when_fifth = 0
    observed_max = 0
    first_wave: set[str] = set()
    try:
        assert first_four_entered.wait(3)
        with state_lock:
            first_wave = set(entered)
            observed_max = max_active
        assert first_wave == set(agents[:4])
        assert not fifth_entered.is_set()
        assert observed_max == 4

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            first_rows = db.query(
                "SELECT status, execution_active FROM chat_agent_runs WHERE thread_id = ?",
                (first_room["id"],),
            )
            if len(first_rows) == 4 and all(
                row["status"] == "completion_unknown" for row in first_rows
            ):
                break
            time.sleep(0.01)
        assert len(first_rows) == 4
        assert all(
            row == {"status": "completion_unknown", "execution_active": True} for row in first_rows
        )
        fifth_run = db.query_row(
            "SELECT status, execution_active FROM chat_agent_runs WHERE thread_id = ?",
            (second_room["id"],),
        )
        # A call that has not acquired a real execution slot has not started.
        # Keep it pending so restart does not mislabel it completion-unknown.
        assert fifth_run == {"status": "pending", "execution_active": False}

        release_one.set()
        fifth_started = fifth_entered.wait(3)
        with state_lock:
            active_when_fifth = active
            observed_max = max_active
    finally:
        release_one.set()
        release_rest.set()
        all_calls_exited.wait(5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            rows = db.query(
                "SELECT status, execution_active FROM chat_agent_runs WHERE thread_id IN (?, ?)",
                (first_room["id"], second_room["id"]),
            )
            if (
                len(rows) == 5
                and all(row["status"] not in ("pending", "running") for row in rows)
                and all(not row["execution_active"] for row in rows)
                and not shared_chat_agents._worker_running
            ):
                break
            time.sleep(0.02)

    assert fifth_started
    assert active_when_fifth == 4
    assert observed_max == 4
    assert all_calls_exited.is_set()
    assert not shared_chat_agents._worker_running


def test_four_real_agent_calls_record_independent_request_and_message_usage(client, monkeypatch):
    from app import config
    from app.agents import team_agent

    agents = sorted(personas.bench_slugs())[:4]
    room, mira = create_room(client)
    for agent in agents:
        add_agent(client, room["id"], mira, agent)
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")

    class FakeAgent:
        event_loop_metrics = SimpleNamespace(
            accumulated_usage={"inputTokens": 2, "outputTokens": 1},
            accumulated_metrics={"latencyMs": 1},
            cycle_count=1,
        )
        model = SimpleNamespace(get_config=lambda: {"model_id": "test-model"})

        def __call__(self, prompt):
            del prompt
            return "answer"

    monkeypatch.setattr(team_agent, "build_agent", lambda _thread, **kwargs: FakeAgent())
    response = client.post(
        f"/api/shared-chats/{room['id']}/messages",
        json={
            "message": " ".join(f"@{agent}" for agent in agents) + " compare",
            "client_key": "four-usage-links",
            "invoke_agents": agents,
        },
        headers=mira,
    )
    assert response.status_code == 200
    trigger = response.json()

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if (
            db.query_row(
                "SELECT COUNT(*) AS n FROM usage_log WHERE thread_id = ?",
                (room["id"],),
            )["n"]
            == 4
        ):
            break
        time.sleep(0.02)
    rows = db.query(
        "SELECT agent_name, requested_by, trigger_message_id, chat_agent_run_id"
        " FROM usage_log WHERE thread_id = ? ORDER BY agent_name",
        (room["id"],),
    )
    assert len(rows) == 4
    assert {row["agent_name"] for row in rows} == set(agents)
    assert {row["requested_by"] for row in rows} == {"mira"}
    assert {row["trigger_message_id"] for row in rows} == {trigger["id"]}
    assert {row["chat_agent_run_id"] for row in rows} == {
        row["turn_id"]
        for row in db.query(
            "SELECT turn_id FROM chat_agent_runs WHERE thread_id = ?",
            (room["id"],),
        )
    }


def test_shared_audience_policy_denies_a_read_one_participant_cannot_make(fresh_db):
    del fresh_db
    from app.extensions.policy import (
        PolicyDecision,
        PolicyEffect,
        PolicyInput,
        PolicyResource,
        PolicySubject,
    )
    from app.services.shared_chat_agents import _AudiencePolicy

    class BasePolicy:
        def has_workplace_rules_for(self, action):
            return bool(action)

        def decide(self, request):
            return PolicyDecision(
                PolicyEffect.DENY if request.subject.name == "bob" else PolicyEffect.PERMIT
            )

    policy = _AudiencePolicy(
        BasePolicy(),
        (
            PolicySubject("alice", strong=True),
            PolicySubject("bob", strong=True),
        ),
    )
    decision = policy.decide(
        PolicyInput(
            PolicySubject("alice", strong=True),
            "skein.tool.read_artifact",
            PolicyResource("artifact", "7"),
            "agent_tool",
            agent="backend-architect",
            tool="read_artifact",
            tool_effect="read",
        )
    )
    assert decision.effect == PolicyEffect.DENY


def test_one_agent_failure_keeps_the_other_agent_reply(client, monkeypatch):
    from app import config
    from app.agents import team_agent

    agents = sorted(personas.bench_slugs())[:2]
    room, mira = create_room(client)
    for agent in agents:
        add_agent(client, room["id"], mira, agent)
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")

    class FakeAgent:
        event_loop_metrics = SimpleNamespace(
            accumulated_usage={}, accumulated_metrics={}, cycle_count=1
        )
        model = SimpleNamespace(get_config=lambda: {"model_id": "test-model"})

        def __init__(self, agent):
            self.agent = agent

        def __call__(self, prompt):
            del prompt
            if self.agent == agents[0]:
                raise RuntimeError("first agent failed")
            return "second agent answered"

    monkeypatch.setattr(
        team_agent,
        "build_agent",
        lambda _thread_id, **kwargs: FakeAgent(kwargs["persona"]),
    )
    response = client.post(
        f"/api/shared-chats/{room['id']}/messages",
        json={
            "message": f"@{agents[0]} @{agents[1]} compare",
            "client_key": "isolated-failure",
            "invoke_agents": agents,
        },
        headers=mira,
    )
    assert response.status_code == 200

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        rows = db.query(
            "SELECT agent, status FROM chat_agent_runs WHERE thread_id = ?",
            (room["id"],),
        )
        if len(rows) == 2 and all(row["status"] not in ("pending", "running") for row in rows):
            break
        time.sleep(0.02)
    assert {row["agent"]: row["status"] for row in rows} == {
        agents[0]: "completion_unknown",
        agents[1]: "completed",
    }
    assert db.query_row(
        "SELECT author, content FROM chat_messages WHERE thread_id = ? AND author_kind = 'agent'",
        (room["id"],),
    ) == {"author": agents[1], "content": "second agent answered"}


def test_shared_audience_policy_intersects_a_read_that_omitted_its_tool_name(fresh_db):
    """permits_resource with tool="" is the only producer of tool_effect
    "none". Passing it through to the base engine lets a future
    SHARED_CHAT_TOOLS entry that forgets tool= read as the requester alone."""
    del fresh_db
    from app.extensions.policy import (
        PolicyDecision,
        PolicyEffect,
        PolicyInput,
        PolicyResource,
        PolicySubject,
    )
    from app.services.shared_chat_agents import _AudiencePolicy

    class BasePolicy:
        def has_workplace_rules_for(self, action):
            return bool(action)

        def decide(self, request):
            return PolicyDecision(
                PolicyEffect.DENY if request.subject.name == "bob" else PolicyEffect.PERMIT
            )

    policy = _AudiencePolicy(
        BasePolicy(),
        (
            PolicySubject("alice", strong=True),
            PolicySubject("bob", strong=True),
        ),
    )
    decision = policy.decide(
        PolicyInput(
            PolicySubject("alice", strong=True),
            "skein.tool.read_artifact",
            PolicyResource("artifact", "7"),
            "agent_tool",
            agent="backend-architect",
            tool="",
            tool_effect="none",
        )
    )
    assert decision.effect == PolicyEffect.DENY


def test_startup_recovery_executes_a_crash_orphaned_pending_run(client):
    """The durable half of the queue: a pending row a crash left behind must
    run at boot. Only the negative half (completion_unknown never retried)
    was pinned before this test."""
    from app.services import shared_chat_agents

    agent = sorted(personas.bench_slugs())[0]
    room, mira = create_room(client)
    add_agent(client, room["id"], mira, agent)
    post_message(
        client,
        room["id"],
        mira,
        f"@{agent} inspect this",
        "orphaned-pending",
        invoke_agent=agent,
    )
    run = wait_for_terminal_run(room["id"])
    assert run["status"] == "completed"
    # Let the drain worker go idle first: a still-running drain would claim
    # the pending row itself, and the test would pass without recover_and_kick.
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and shared_chat_agents._worker_running:
        time.sleep(0.02)
    assert not shared_chat_agents._worker_running
    with db.transaction():
        db.execute(
            "UPDATE chat_agent_runs SET status = 'pending', started_at = NULL,"
            " finished_at = NULL, response_message_id = NULL, execution_active = FALSE"
            " WHERE turn_id = ?",
            (run["turn_id"],),
        )

    result = shared_chat_agents.recover_and_kick()

    assert result["started"] is True
    settled = wait_for_terminal_run(room["id"])
    assert settled["turn_id"] == run["turn_id"]
    assert settled["status"] == "completed"


def test_retried_client_key_naming_a_different_agent_set_is_a_conflict(client):
    agent = sorted(personas.bench_slugs())[0]
    room, mira = create_room(client)
    add_agent(client, room["id"], mira, agent)
    post_message(
        client,
        room["id"],
        mira,
        f"@{agent} first",
        "retry-agent-set",
        invoke_agent=agent,
    )

    response = client.post(
        f"/api/shared-chats/{room['id']}/messages",
        json={"message": f"@{agent} first", "client_key": "retry-agent-set"},
        headers=mira,
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "This message key already names a different agent call."


def _shared_prompt_body(prompt: str) -> str:
    return prompt.split("<shared-chat-transcript>\n", 1)[1].split("\n</shared-chat-transcript>", 1)[
        0
    ]


def _shared_prompt_block(kind: str, author: str, message_id: int, content: str) -> str:
    return f"[{kind} {author} | message {message_id}]\n{content}"


def test_prompt_uses_the_last_completed_same_agent_boundary(client, monkeypatch):
    from app.services import shared_chat_agents

    target, other = sorted(personas.bench_slugs())[:2]
    room, mira = create_room(client)
    add_agent(client, room["id"], mira, target)
    add_agent(client, room["id"], mira, other)
    monkeypatch.setattr(shared_chat_agents, "kick", lambda: False)

    before = post_message(client, room["id"], mira, "before boundary", "prompt-before")
    completed = post_message(
        client,
        room["id"],
        mira,
        f"@{target} completed boundary",
        "prompt-completed",
        invoke_agent=target,
    )
    with db.transaction():
        target_reply_id = db.execute(
            "INSERT INTO chat_messages"
            " (thread_id, role, content, created_at, author_kind, author, turn_id,"
            " reply_to_message_id) VALUES (?, 'assistant', ?, ?, 'agent', ?, ?, ?)"
            " RETURNING id",
            (
                room["id"],
                "target old reply",
                db.now(),
                target,
                completed["turn_id"],
                completed["id"],
            ),
        )
        db.execute(
            "UPDATE chat_agent_runs SET status = 'completed', response_message_id = ?,"
            " finished_at = ? WHERE turn_id = ?",
            (target_reply_id, db.now(), completed["turn_id"]),
        )

    after = post_message(client, room["id"], mira, "after boundary", "prompt-after")
    other_call = post_message(
        client,
        room["id"],
        mira,
        f"@{other} other call",
        "prompt-other",
        invoke_agent=other,
    )
    with db.transaction():
        other_reply_id = db.execute(
            "INSERT INTO chat_messages"
            " (thread_id, role, content, created_at, author_kind, author, turn_id,"
            " reply_to_message_id) VALUES (?, 'assistant', ?, ?, 'agent', ?, ?, ?)"
            " RETURNING id",
            (
                room["id"],
                "other agent reply",
                db.now(),
                other,
                other_call["turn_id"],
                other_call["id"],
            ),
        )
        db.execute(
            "UPDATE chat_agent_runs SET status = 'completed', response_message_id = ?,"
            " finished_at = ? WHERE turn_id = ?",
            (other_reply_id, db.now(), other_call["turn_id"]),
        )
    ordinary = post_message(client, room["id"], mira, "ordinary update", "prompt-ordinary")
    failed = post_message(
        client,
        room["id"],
        mira,
        f"@{target} failed call",
        "prompt-failed",
        invoke_agent=target,
    )
    with db.transaction():
        db.execute(
            "UPDATE chat_agent_runs SET status = 'failed', finished_at = ?,"
            " error_code = 'turn_failed' WHERE turn_id = ?",
            (db.now(), failed["turn_id"]),
        )
    current = post_message(
        client,
        room["id"],
        mira,
        f"@{target} current call",
        "prompt-current",
        invoke_agent=target,
    )
    run = db.query_row(
        "SELECT * FROM chat_agent_runs WHERE trigger_message_id = ?",
        (current["id"],),
    )

    body = _shared_prompt_body(shared_chat_agents._prompt(run))

    assert body == "\n\n---\n\n".join(
        (
            _shared_prompt_block("human", "mira", after["id"], "after boundary"),
            _shared_prompt_block("human", "mira", other_call["id"], f"@{other} other call"),
            _shared_prompt_block("agent", other, other_reply_id, "other agent reply"),
            _shared_prompt_block("human", "mira", ordinary["id"], "ordinary update"),
            _shared_prompt_block("human", "mira", failed["id"], f"@{target} failed call"),
            _shared_prompt_block("human", "mira", current["id"], f"@{target} current call"),
        )
    )
    assert "target old reply" not in body
    assert _shared_prompt_block("human", "mira", before["id"], "before boundary") not in body
    assert (
        _shared_prompt_block("human", "mira", completed["id"], f"@{target} completed boundary")
        not in body
    )


def test_prompt_body_is_bounded_and_keeps_the_newest_message(client, monkeypatch):
    from app.services import shared_chat_agents

    agent = sorted(personas.bench_slugs())[0]
    room, mira = create_room(client)
    add_agent(client, room["id"], mira, agent)
    monkeypatch.setattr(shared_chat_agents, "kick", lambda: False)
    post_message(client, room["id"], mira, "old " + "z" * 120, "bound-old")
    post_message(client, room["id"], mira, "x", "bound-short")
    post_message(client, room["id"], mira, "newer", "bound-newer")
    current = post_message(
        client,
        room["id"],
        mira,
        f"@{agent} latest",
        "bound-latest",
        invoke_agent=agent,
    )
    run = db.query_row(
        "SELECT * FROM chat_agent_runs WHERE trigger_message_id = ?",
        (current["id"],),
    )
    rows = db.query(
        "SELECT id, author_kind, author, content FROM chat_messages"
        " WHERE thread_id = ? AND id <= ? ORDER BY id",
        (room["id"], current["id"]),
    )
    rendered = [
        _shared_prompt_block(
            str(row["author_kind"]),
            str(row["author"] or "Skein"),
            int(row["id"]),
            str(row["content"]),
        )
        for row in rows
    ]
    marker = "[Earlier shared-chat messages were omitted from this bounded turn.]\n\n"
    separator = "\n\n---\n\n"
    expected = marker + separator.join(rendered[-2:])
    monkeypatch.setattr(shared_chat_agents, "_MAX_TRANSCRIPT_CHARS", len(expected))
    real_query = db.query
    prompt_queries: list[tuple[str, tuple]] = []

    def bounded_query(sql: str, params=()):
        if "FROM chat_messages" in sql:
            prompt_queries.append((sql, tuple(params)))
        return real_query(sql, params)

    monkeypatch.setattr(shared_chat_agents.db, "query", bounded_query)
    body = _shared_prompt_body(shared_chat_agents._prompt(run))

    assert prompt_queries
    assert all("ORDER BY id DESC LIMIT ?" in sql for sql, _params in prompt_queries)
    assert all(
        params[-1] == shared_chat_agents._PROMPT_BATCH + 1 for _sql, params in prompt_queries
    )
    assert body == expected
    assert len(body) <= shared_chat_agents._MAX_TRANSCRIPT_CHARS
    assert body.count(marker) == 1


def test_claim_scan_reaches_runnable_work_after_one_hundred_locked_rows(client):
    from app.services import shared_chat_agents

    blocked_agent, runnable_agent = sorted(personas.bench_slugs())[:2]
    blocked_room, blocked_headers = create_room(client, owner="mira")
    runnable_room, runnable_headers = create_room(client, owner="mira")
    add_agent(client, blocked_room["id"], blocked_headers, blocked_agent)
    add_agent(client, runnable_room["id"], runnable_headers, runnable_agent)
    now = db.now()
    with db.transaction():
        for index in range(100):
            message_id = db.execute(
                "INSERT INTO chat_messages"
                " (thread_id, role, content, created_at, author_kind, author, client_key, turn_id)"
                " VALUES (?, 'user', 'blocked', ?, 'human', 'mira', ?, ?) RETURNING id",
                (blocked_room["id"], now, f"blocked-key-{index}", f"blocked-{index}"),
            )
            db.execute(
                "INSERT INTO chat_agent_runs"
                " (turn_id, batch_id, thread_id, trigger_message_id, agent, requested_by,"
                " requester_subject, status, requested_at)"
                " VALUES (?, ?, ?, ?, ?, 'mira', '{}', 'pending', ?)",
                (
                    f"blocked-{index}",
                    f"blocked-{index}",
                    blocked_room["id"],
                    message_id,
                    blocked_agent,
                    now,
                ),
            )
        runnable_message_id = db.execute(
            "INSERT INTO chat_messages"
            " (thread_id, role, content, created_at, author_kind, author, client_key, turn_id)"
            " VALUES (?, 'user', 'runnable', ?, 'human', 'mira', 'runnable-key',"
            " 'runnable-turn') RETURNING id",
            (runnable_room["id"], now),
        )
        db.execute(
            "INSERT INTO chat_agent_runs"
            " (turn_id, batch_id, thread_id, trigger_message_id, agent, requested_by,"
            " requester_subject, status, requested_at)"
            " VALUES ('runnable-turn', 'runnable-turn', ?, ?, ?, 'mira', '{}', 'pending', ?)",
            (runnable_room["id"], runnable_message_id, runnable_agent, now),
        )

    blocked_lock = shared_chat_agents._session_lock(blocked_room["id"], blocked_agent)
    assert blocked_lock.acquire(blocking=False)
    claimed = None
    try:
        assert shared_chat_agents._has_runnable_pending() is True
        claimed = shared_chat_agents.claim_next()
    finally:
        blocked_lock.release()

    assert claimed is not None
    assert claimed["turn_id"] == "runnable-turn"
    with db.transaction():
        db.execute(
            "UPDATE chat_agent_runs SET status = 'failed', execution_active = FALSE,"
            " finished_at = ?, error_code = 'test_settled' WHERE turn_id = 'runnable-turn'",
            (db.now(),),
        )


def test_thread_start_failure_makes_pending_calls_visible_as_failed(client, monkeypatch):
    from app.services import shared_chat_agents

    agent = sorted(personas.bench_slugs())[0]
    room, mira = create_room(client)
    add_agent(client, room["id"], mira, agent)
    real_kick = shared_chat_agents.kick
    monkeypatch.setattr(shared_chat_agents, "kick", lambda: False)
    trigger = post_message(
        client,
        room["id"],
        mira,
        f"@{agent} cannot start",
        "thread-start-failure",
        invoke_agent=agent,
    )
    monkeypatch.setattr(shared_chat_agents, "kick", real_kick)
    assert shared_chat_agents.wait_for_idle()
    with monkeypatch.context() as patch:
        patch.setattr(
            threading.Thread,
            "start",
            lambda _thread: (_ for _ in ()).throw(RuntimeError("thread unavailable")),
        )
        try:
            real_kick()
            raise AssertionError("coordinator start unexpectedly succeeded")
        except RuntimeError as error:
            assert str(error) == "thread unavailable"

    run = db.query_row(
        "SELECT status, execution_active, error_code FROM chat_agent_runs"
        " WHERE trigger_message_id = ?",
        (trigger["id"],),
    )
    assert run == {
        "status": "failed",
        "execution_active": False,
        "error_code": "worker_start_failed",
    }
    assert shared_chat_agents.wait_for_idle()


def test_retry_timer_failure_stays_owned_until_pending_rows_settle(client, monkeypatch):
    from app.services import shared_chat_agents

    del client
    settling = threading.Event()
    release = threading.Event()

    class FailedTimer:
        daemon = True

        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            raise RuntimeError("timer unavailable")

        def cancel(self):
            pass

    def settle(_error_code):
        settling.set()
        release.wait(2)

    monkeypatch.setattr(shared_chat_agents.threading, "Timer", FailedTimer)
    monkeypatch.setattr(shared_chat_agents, "_fail_pending_queue", settle)
    scheduler = threading.Thread(target=shared_chat_agents._schedule_retry)
    scheduler.start()
    try:
        assert settling.wait(1)
        assert not shared_chat_agents.wait_for_idle(0.05)
    finally:
        release.set()
        scheduler.join(2)
    assert shared_chat_agents.wait_for_idle(1)


def test_drain_retries_a_transient_coordinator_failure(client, monkeypatch):
    from app.services import shared_chat_agents

    del client
    calls = 0
    retried = threading.Event()

    def flaky_claim():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient coordinator fault")
        retried.set()
        return None

    monkeypatch.setattr(shared_chat_agents, "claim_next", flaky_claim)

    assert shared_chat_agents.kick() is True
    assert retried.wait(2)
    assert shared_chat_agents.wait_for_idle(2)
    assert calls >= 2
