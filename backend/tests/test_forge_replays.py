"""Native Gitea retries bind UUIDs and suppress accepted signed-byte replays."""

import hmac
import json
from hashlib import sha256
from uuid import uuid4

import pytest
from test_forge import SECRET, _pr, _push

DUPLICATE = {"ignored": "this delivery was already applied"}


@pytest.fixture()
def packet(client, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "FORGE_WEBHOOK_SECRET", SECRET)

    def post(payload, delivery="", event="push", *, raw=None):
        body = json.dumps(payload).encode() if raw is None else raw
        return client.post(
            "/api/webhooks/forge",
            content=body,
            headers={
                "X-Gitea-Event": event,
                "X-Gitea-Delivery": delivery,
                "X-Gitea-Signature": hmac.new(SECRET.encode(), body, sha256).hexdigest(),
                "Content-Type": "application/json",
            },
        )

    return post


def _state(db, task_id):
    return (
        db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,)),
        db.query("SELECT * FROM activity ORDER BY id"),
        db.query("SELECT * FROM extension_outbox ORDER BY seq"),
    )


def test_new_uuid_replay_preserves_human_edits_and_binds_alias(packet, client, fresh_db):
    from app.services import work

    task = work.create_task("Keep the human edit", actor="tester")
    payload = _push(f"task/{task['id']}-replay")
    original, alias = str(uuid4()), str(uuid4())
    assert packet(payload, original).json()["status"] == "in_progress"
    edited = client.patch(
        f"/api/tasks/{task['id']}",
        json={
            "status": "todo",
            "title": "Human revision",
            "description": "Keep this note",
            "priority": "high",
        },
    )
    assert edited.status_code == 200
    before = _state(fresh_db, task["id"])
    assert before[0]["status"] == "todo"
    assert before[1] and before[2]

    assert packet(payload, original).json() == DUPLICATE
    assert _state(fresh_db, task["id"]) == before
    assert packet(payload, alias).json() == DUPLICATE
    assert _state(fresh_db, task["id"]) == before
    receipts = fresh_db.query(
        "SELECT delivery_id, event, payload_sha256, task_id FROM forge_receipts ORDER BY delivery_id"
    )
    assert {row["delivery_id"]: row["task_id"] for row in receipts} == {
        original: task["id"],
        alias: None,
    }
    assert {row["event"] for row in receipts} == {"push"}
    assert {row["payload_sha256"] for row in receipts} == {
        sha256(json.dumps(payload).encode()).hexdigest()
    }


@pytest.mark.parametrize("replacement", ["bytes", "saved_bytes", "event", "unsupported"])
@pytest.mark.parametrize("use_alias", [False, True])
def test_original_and_alias_uuid_reject_conflicting_reuse(packet, fresh_db, replacement, use_alias):
    from app.services import work

    task = work.create_task("Bound delivery")
    payload = _push(f"task/{task['id']}-bound")
    original, alias = str(uuid4()), str(uuid4())
    assert packet(payload, original).json()["status"] == "in_progress"
    if use_alias:
        assert packet(payload, alias).status_code == 200
    if replacement == "saved_bytes":
        payload = {**payload, "after": "c" * 40}
        assert packet(payload, str(uuid4())).status_code == 200
    work.update_task(task["id"], status="todo", actor="tester")
    before = _state(fresh_db, task["id"])
    receipts = fresh_db.query("SELECT * FROM forge_receipts ORDER BY delivery_id")
    event = {
        "bytes": "push",
        "saved_bytes": "push",
        "event": "pull_request",
        "unsupported": "issues",
    }[replacement]
    raw = json.dumps(payload).encode() + (b"\n" if replacement == "bytes" else b"")
    response = packet(payload, alias if use_alias else original, event, raw=raw)
    assert response.status_code == 400
    assert response.json() == {
        "detail": "The delivery ID names a different payload. Send the original delivery."
    }
    assert _state(fresh_db, task["id"]) == before
    assert fresh_db.query("SELECT * FROM forge_receipts ORDER BY delivery_id") == receipts


def test_concurrent_different_uuids_apply_policy_and_work_once(fresh_db, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from app.extensions import PolicyContribution, SkeinModule
    from app.main import create_app
    from app.services import forge, work

    task = work.create_task("Concurrent native redelivery")
    payload = _push(f"task/{task['id']}-concurrent")
    digest = sha256(json.dumps(payload).encode()).hexdigest()
    deliveries = [str(uuid4()) for _ in range(3)]
    arrived = Barrier(len(deliveries))
    connections = []
    decisions = []
    applied = []
    name_lock = fresh_db.name_lock
    forge_event = forge.forge_event

    def synchronize(namespace, name):
        name_lock(namespace, name)
        if namespace == 4_216_030:
            connections.append(fresh_db.query_row("SELECT pg_backend_pid() AS pid")["pid"])
            arrived.wait(timeout=10)

    def policy(request):
        if request.action == "skein.integration.forge":
            decisions.append(request.resource.id)
        return None

    def apply(*args, **kwargs):
        applied.append(True)
        return forge_event(*args, **kwargs)

    registry = create_app(
        modules=(
            SkeinModule(
                module_id="acme.workplace",
                version="1.0.0",
                extension_api="1.0",
                minimum_core="0.2.0",
                maximum_core_exclusive="0.7.0",
                policies=(PolicyContribution("acme.workplace.forge", policy),),
            ),
        )
    ).state.skein_registry
    monkeypatch.setattr(fresh_db, "name_lock", synchronize)
    monkeypatch.setattr(forge, "forge_event", apply)
    with ThreadPoolExecutor(max_workers=len(deliveries)) as pool:
        futures = [
            pool.submit(forge.apply_delivery, registry, "push", payload, delivery, digest)
            for delivery in deliveries
        ]
        results = [future.result(timeout=15) for future in futures]
    assert len(set(connections)) == len(deliveries)
    assert sum(result == DUPLICATE for result in results) == len(deliveries) - 1
    assert sum(result.get("status") == "in_progress" for result in results) == 1
    assert len(decisions) == len(applied) == 1
    receipts = fresh_db.query("SELECT delivery_id, task_id FROM forge_receipts")
    assert {row["delivery_id"] for row in receipts} == set(deliveries)
    assert sum(row["task_id"] == task["id"] for row in receipts) == 1
    assert sum(row["task_id"] is None for row in receipts) == len(deliveries) - 1
    assert len(fresh_db.query("SELECT 1 FROM activity WHERE action = 'update_task'")) == 1
    assert (
        len(
            fresh_db.query("SELECT 1 FROM extension_outbox WHERE event_type = 'skein.task.updated'")
        )
        == 1
    )


@pytest.mark.parametrize("change", ["commits", "metadata", "formatting", "reopen_snapshot"])
def test_changed_signed_bytes_remain_eligible(packet, fresh_db, change):
    from app.services import work

    task = work.create_task("Independent signed bytes")
    branch = f"task/{task['id']}-bytes"
    event = "pull_request" if change == "reopen_snapshot" else "push"
    payload = _pr(branch, action="reopened") if event == "pull_request" else _push(branch)
    payload["repository"] = {"html_url": "https://git.example/skein"}
    payload.update(before="a" * 40, after="b" * 40)
    assert packet(payload, str(uuid4()), event).json()["status"] == "in_progress"
    work.update_task(task["id"], status="todo", actor="tester")
    if change == "commits":
        payload.update(before="b" * 40, after="c" * 40)
    elif change == "metadata":
        payload["sender"]["avatar_url"] = "https://git.example/avatars/new"
    elif change == "reopen_snapshot":
        payload["pull_request"]["updated_at"] = "2026-09-07T12:01:00Z"
    raw = json.dumps(payload, indent=2).encode() if change == "formatting" else None
    assert packet(payload, str(uuid4()), event, raw=raw).json()["status"] == "in_progress"
    assert len(fresh_db.query("SELECT DISTINCT payload_sha256 FROM forge_receipts")) == 2


def test_native_event_is_part_of_the_fingerprint(packet, fresh_db):
    from app.services import work

    task = work.create_task("Native event scopes accepted bytes")
    branch = f"task/{task['id']}-event"
    payload = {**_push(branch), **_pr(branch)}
    assert packet(payload, str(uuid4()), "push").json()["status"] == "in_progress"
    work.update_task(task["id"], status="todo", actor="tester")
    assert packet(payload, str(uuid4()), "pull_request").json()["status"] == "in_progress"
    receipts = fresh_db.query("SELECT event, payload_sha256 FROM forge_receipts")
    assert {row["event"] for row in receipts} == {"push", "pull_request"}
    assert len({row["payload_sha256"] for row in receipts}) == 1


def test_repository_scope_allows_the_same_uuid_without_reusing_history(packet, fresh_db):
    from app.services import work

    task = work.create_task("Separate repository receipt")
    payload = _push(f"task/{task['id']}-repository")
    delivery = str(uuid4())
    assert packet(payload, delivery).json()["status"] == "in_progress"
    work.update_task(task["id"], status="todo", actor="tester")
    payload["repository"]["html_url"] = "https://git.example/other"
    assert packet(payload, delivery).json()["status"] == "in_progress"
    assert len(fresh_db.query("SELECT DISTINCT namespace FROM forge_receipts")) == 2


@pytest.mark.parametrize(
    "outcome", ["same_status", "done", "delegated", "agent", "no_reference", "missing_task"]
)
def test_accepted_ignored_outcomes_suppress_replays(packet, fresh_db, monkeypatch, outcome):
    from app.services import delegation, forge, users, work

    users.ensure_user("tester")
    task = work.create_task("Accepted ignored packet", actor="tester")
    payload = _push(f"task/{task['id']}-ignored")
    event = "push"
    if outcome in ("same_status", "done"):
        work.update_task(task["id"], status="in_progress" if outcome == "same_status" else "done")
    elif outcome == "delegated":
        users.ensure_user("scout", kind="agent")
        delegation.delegate_task(task["id"], "scout", "tester", actor="tester")
        event = "pull_request"
        payload = _pr(f"task/{task['id']}-ignored", action="closed", merged=True)
    elif outcome == "agent":
        users.ensure_user("scout", kind="agent")
        payload["pusher"]["login"] = "scout"
    elif outcome == "no_reference":
        payload["ref"] = "refs/heads/main"
    else:
        # There is no task-delete API. Exercise the receipt FK against a removed
        # service-created row, not an invented task ID that never existed.
        fresh_db.execute("DELETE FROM tasks WHERE id = ?", (task["id"],))
    original, alias = str(uuid4()), str(uuid4())
    accepted = packet(payload, original, event)
    assert accepted.status_code == 200
    assert "ignored" in accepted.json() and accepted.json() != DUPLICATE
    assert fresh_db.query_row("SELECT task_id FROM forge_receipts")["task_id"] is None
    if outcome in ("same_status", "done"):
        work.update_task(task["id"], status="todo", actor="tester")
    before = _state(fresh_db, task["id"])

    def unexpected_apply(*args, **kwargs):
        pytest.fail("An accepted fingerprint reached task application again")

    monkeypatch.setattr(forge, "forge_event", unexpected_apply)
    assert packet(payload, alias, event).json() == DUPLICATE
    assert _state(fresh_db, task["id"]) == before
    assert fresh_db.query("SELECT task_id FROM forge_receipts") == [{"task_id": None}] * 2


def test_deleted_transition_task_does_not_break_alias_receipt(packet, fresh_db):
    from app.services import work

    task = work.create_task("Receipt survives its task")
    payload = _push(f"task/{task['id']}-deleted")
    assert packet(payload, str(uuid4())).json()["status"] == "in_progress"
    fresh_db.execute("DELETE FROM tasks WHERE id = ?", (task["id"],))
    before = _state(fresh_db, task["id"])
    assert packet(payload, str(uuid4())).json() == DUPLICATE
    assert _state(fresh_db, task["id"]) == before
    assert fresh_db.query("SELECT task_id FROM forge_receipts") == [{"task_id": None}] * 2


def test_headerless_replay_recognizes_native_history_but_creates_no_receipt(packet, fresh_db):
    from app.services import work

    task = work.create_task("Headerless compatibility")
    payload = _push(f"task/{task['id']}-headerless")
    assert packet(payload).json()["status"] == "in_progress"
    assert not fresh_db.query("SELECT 1 FROM forge_receipts")
    work.update_task(task["id"], status="todo", actor="tester")
    assert packet(payload).json()["status"] == "in_progress"
    assert not fresh_db.query("SELECT 1 FROM forge_receipts")
    work.update_task(task["id"], status="todo", actor="tester")
    assert packet(payload, str(uuid4())).json()["status"] == "in_progress"
    work.update_task(task["id"], status="todo", actor="tester")
    before = _state(fresh_db, task["id"])
    receipts = fresh_db.query("SELECT * FROM forge_receipts")
    assert packet(payload).json() == DUPLICATE
    assert _state(fresh_db, task["id"]) == before
    assert fresh_db.query("SELECT * FROM forge_receipts") == receipts


@pytest.mark.parametrize("event", ["issues", "push", "pull_request"])
def test_unmapped_new_events_remain_unreceipted(packet, fresh_db, event):
    from app.services import work

    task = work.create_task("Unmapped native packet")
    if event == "push":
        payload = {**_push(f"task/{task['id']}-tag"), "ref": "refs/tags/v1"}
    else:
        payload = _pr(f"task/{task['id']}-unmapped", action="closed", merged=False)
    before = _state(fresh_db, task["id"])
    for delivery in ("", str(uuid4())):
        assert packet(payload, delivery, event).json() == {
            "ignored": "only push and pull_request events move work"
        }
    assert _state(fresh_db, task["id"]) == before
    assert not fresh_db.query("SELECT 1 FROM forge_receipts")


def test_legacy_receipts_keep_uuid_protection_without_inventing_fingerprint_history(
    packet, fresh_db
):
    from app.services import forge, work

    task = work.create_task("Pre-028 delivery")
    payload = _push(f"task/{task['id']}-legacy")
    delivery = str(uuid4())
    # This is the pre-028 write path: generic job claim plus forge mutation.
    with fresh_db.transaction():
        assert fresh_db.claim_job("forge-delivery", delivery)
        assert forge.forge_event(**forge.parse_gitea("push", payload))["status"] == "in_progress"
    work.update_task(task["id"], status="todo", actor="tester")
    before = _state(fresh_db, task["id"])
    assert packet(payload, delivery).json() == DUPLICATE
    assert _state(fresh_db, task["id"]) == before
    assert not fresh_db.query("SELECT 1 FROM forge_receipts")
    assert packet(payload, str(uuid4())).json()["status"] == "in_progress"


def test_native_fingerprints_and_aliases_survive_retention(packet, fresh_db, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.services import retention, work

    task = work.create_task("Permanent native fingerprint")
    payload = _push(f"task/{task['id']}-retained")
    original, alias = str(uuid4()), str(uuid4())
    assert packet(payload, original).json()["status"] == "in_progress"
    assert packet(payload, alias).json() == DUPLICATE
    work.update_task(task["id"], status="todo", actor="tester")
    cutoff = (datetime.now(UTC) + timedelta(days=1)).isoformat(timespec="seconds")
    monkeypatch.setattr(retention, "_cutoff", lambda days: cutoff)
    retention.prune()
    before = _state(fresh_db, task["id"])
    for delivery in (original, alias, str(uuid4())):
        assert packet(payload, delivery).json() == DUPLICATE
    assert _state(fresh_db, task["id"]) == before
    assert len(fresh_db.query("SELECT 1 FROM forge_receipts")) == 3


@pytest.mark.parametrize("failure", ["work", "receipt"])
def test_apply_or_receipt_failure_rolls_back_work_and_allows_retry(
    packet, fresh_db, monkeypatch, failure
):
    from app.services import work

    task = work.create_task("Retry after failed transaction")
    payload = _push(f"task/{task['id']}-rollback")
    delivery = str(uuid4())
    before = _state(fresh_db, task["id"])
    update_task = work.update_task
    execute = fresh_db.execute

    def failed_work(*args, **kwargs):
        update_task(*args, **kwargs)
        raise RuntimeError("work transaction failed")

    def failed_receipt(sql, *args, **kwargs):
        result = execute(sql, *args, **kwargs)
        if sql.startswith("INSERT INTO forge_receipts"):
            raise RuntimeError("receipt transaction failed")
        return result

    with monkeypatch.context() as failing:
        if failure == "work":
            failing.setattr(work, "update_task", failed_work)
        else:
            failing.setattr(fresh_db, "execute", failed_receipt)
        with pytest.raises(RuntimeError, match="transaction failed"):
            packet(payload, delivery)
    assert _state(fresh_db, task["id"]) == before
    assert not fresh_db.query("SELECT 1 FROM forge_receipts")
    assert packet(payload, delivery).json()["status"] == "in_progress"
    assert packet(payload, str(uuid4())).json() == DUPLICATE


def test_failed_alias_insert_keeps_original_receipt_and_retries(packet, fresh_db, monkeypatch):
    from app.services import work

    task = work.create_task("Retry alias binding")
    payload = _push(f"task/{task['id']}-alias")
    original, alias = str(uuid4()), str(uuid4())
    assert packet(payload, original).json()["status"] == "in_progress"
    work.update_task(task["id"], status="todo", actor="tester")
    before = _state(fresh_db, task["id"])
    receipts = fresh_db.query("SELECT * FROM forge_receipts")
    execute = fresh_db.execute

    def failed_receipt(sql, *args, **kwargs):
        result = execute(sql, *args, **kwargs)
        if sql.startswith("INSERT INTO forge_receipts"):
            raise RuntimeError("alias transaction failed")
        return result

    with monkeypatch.context() as failing:
        failing.setattr(fresh_db, "execute", failed_receipt)
        with pytest.raises(RuntimeError, match="alias transaction failed"):
            packet(payload, alias)
    assert _state(fresh_db, task["id"]) == before
    assert fresh_db.query("SELECT * FROM forge_receipts") == receipts
    assert packet(payload, alias).json() == DUPLICATE
    assert _state(fresh_db, task["id"]) == before
    assert (
        fresh_db.query_row("SELECT task_id FROM forge_receipts WHERE delivery_id = ?", (alias,))[
            "task_id"
        ]
        is None
    )


def test_denied_packet_stays_retryable_but_accepted_replay_skips_policy(
    packet, client, fresh_db, monkeypatch
):
    from app.extensions import PolicyContribution, PolicyDecision, PolicyEffect, SkeinModule
    from app.main import create_app
    from app.services import work

    denied = True
    decisions = []

    def policy(request):
        if request.action == "skein.integration.forge":
            decisions.append(request.resource.id)
            if denied:
                return PolicyDecision(PolicyEffect.DENY, ("forge writes are disabled",))
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.7.0",
        policies=(PolicyContribution("acme.workplace.forge", policy),),
    )
    registry = create_app(modules=(module,)).state.skein_registry
    monkeypatch.setattr(client.app.state, "skein_registry", registry)
    task = work.create_task("Denied delivery remains retryable")
    payload = _push(f"task/{task['id']}-policy")
    delivery = str(uuid4())
    before = _state(fresh_db, task["id"])
    assert packet(payload, delivery).status_code == 403
    assert _state(fresh_db, task["id"]) == before
    assert not fresh_db.query("SELECT 1 FROM forge_receipts")
    denied = False
    assert packet(payload, delivery).json()["status"] == "in_progress"
    work.update_task(task["id"], status="todo", actor="tester")
    before = _state(fresh_db, task["id"])
    denied = True
    assert packet(payload, str(uuid4())).json() == DUPLICATE
    assert len(decisions) == 2
    assert _state(fresh_db, task["id"]) == before
    payload["after"] = "c" * 40
    assert packet(payload, str(uuid4())).status_code == 403
    assert len(decisions) == 3
    assert len(fresh_db.query("SELECT 1 FROM forge_receipts")) == 2
    assert _state(fresh_db, task["id"]) == before


def test_fingerprint_lock_uses_the_separate_bigint_keyspace(packet, fresh_db, monkeypatch):
    from app.services import forge, work

    task = work.create_task("Separate receipt lock tiers")
    payload = _push(f"task/{task['id']}-locks")
    digest = sha256(json.dumps(payload).encode()).hexdigest()
    namespace = forge._namespace("gitea", payload["repository"]["html_url"].lower())
    expected = int(forge._namespace(namespace, "push", digest)[:16], 16)
    forge_event = forge.forge_event
    locks = []

    def apply(*args, **kwargs):
        locks.extend(
            fresh_db.query(
                "SELECT classid::bigint, objid::bigint, objsubid FROM pg_locks"
                " WHERE pid = pg_backend_pid() AND locktype = 'advisory' AND granted"
            )
        )
        return forge_event(*args, **kwargs)

    monkeypatch.setattr(forge, "forge_event", apply)
    assert packet(payload, str(uuid4())).json()["status"] == "in_progress"
    bigint_locks = [row for row in locks if row["objsubid"] == 1]
    assert len(bigint_locks) == 1
    assert bigint_locks[0]["classid"] << 32 | bigint_locks[0]["objid"] == expected
    assert any(row["objsubid"] == 2 for row in locks)
