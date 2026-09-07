"""Native GitHub payloads use sender.login, pusher.name and /tree/ URLs.

Shapes follow docs.github.com/en/webhooks/webhook-events-and-payloads
(push and pull_request). The fixture task IDs come from the running service.
"""

import copy
import hmac
import json
from hashlib import sha256
from uuid import uuid4

import pytest

SECRET = "github-fixture-secret"
REPOSITORY = "octocat/Hello-World"
HOOK = 12345678


def push(task_id):
    return {
        "ref": f"refs/heads/task/{task_id}-fixture",
        "before": "a" * 40,
        "after": "b" * 40,
        "created": False,
        "deleted": False,
        "forced": False,
        "base_ref": None,
        "compare": "https://github.com/octocat/Hello-World/compare/aaaa...bbbb",
        "commits": [],
        "head_commit": None,
        "repository": {
            "id": 1296269,
            "full_name": REPOSITORY,
            "html_url": f"https://github.com/{REPOSITORY}",
        },
        "pusher": {"name": "Octocat", "email": "octocat@example.com"},
        "sender": {"login": "octocat", "id": 1, "type": "User"},
    }


def pull_request(task_id, *, action="opened", merged=False, draft=False):
    return {
        "action": action,
        "number": 1347,
        "repository": push(task_id)["repository"],
        "sender": {"login": "octocat", "id": 1, "type": "User"},
        "pull_request": {
            "id": 1,
            "number": 1347,
            "state": "closed" if action == "closed" else "open",
            "merged": merged,
            "draft": draft,
            "title": "Fix the task",
            "body": None,
            "html_url": f"https://github.com/{REPOSITORY}/pull/1347",
            "head": {"ref": f"task/{task_id}-fixture", "repo": {"full_name": "other/fork"}},
            "base": {"ref": "main", "repo": {"full_name": REPOSITORY}},
            "user": {"login": "octocat"},
        },
    }


def headers(event, body, delivery=None):
    return {
        "X-GitHub-Event": event,
        "X-Hub-Signature-256": "sha256=" + hmac.new(SECRET.encode(), body, sha256).hexdigest(),
        "X-GitHub-Delivery": delivery or str(uuid4()),
        "X-GitHub-Hook-ID": str(HOOK),
        "Content-Type": "application/json",
    }


@pytest.fixture()
def github(client, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "FORGE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(
        config, "GITHUB_HOOKS", [{"repository": REPOSITORY.lower(), "hook_id": HOOK}], raising=False
    )
    monkeypatch.setattr(config, "GITHUB_CONFIG_ERROR", "", raising=False)
    monkeypatch.setattr(config, "GITHUB_API_URL", "https://api.github.com", raising=False)

    def post(event, payload, *, delivery=None, extra=None):
        body = json.dumps(payload).encode()
        return client.post(
            "/api/webhooks/forge",
            content=body,
            headers={**headers(event, body, delivery), **(extra or {})},
        )

    return post


def test_native_push_and_merge_use_one_policy_receipt_service(github, fresh_db):
    from app.services import work

    task = work.create_task("Native GitHub transition")
    response = github("push", push(task["id"]))
    assert response.status_code == 200
    assert response.json()["status"] == "in_progress"
    assert (
        response.json()["url"]
        == f"https://github.com/{REPOSITORY.lower()}/tree/task/{task['id']}-fixture"
    )
    merged = github("pull_request", pull_request(task["id"], action="closed", merged=True))
    assert merged.json()["status"] == "done"
    assert (
        fresh_db.query_one(
            "SELECT COUNT(*) AS n FROM forge_receipts WHERE provider = 'github' AND task_id = ?",
            (task["id"],),
        )["n"]
        == 2
    )
    assert (
        len(
            fresh_db.query(
                "SELECT id FROM activity WHERE actor = 'forge' AND action = 'update_task'"
            )
        )
        == 2
    )


def test_guid_retry_does_not_undo_later_human_edit(github, fresh_db):
    from app.services import work

    task = work.create_task("One delivery")
    guid = str(uuid4())
    assert github("push", push(task["id"]), delivery=guid).status_code == 200
    work.update_task(task["id"], status="todo", actor="mira")
    assert github("push", push(task["id"]), delivery=guid).json() == {
        "ignored": "this delivery was already applied"
    }
    assert (
        fresh_db.query_one("SELECT status FROM tasks WHERE id = ?", (task["id"],))["status"]
        == "todo"
    )


@pytest.mark.parametrize(
    "extra,status",
    [
        ({"X-Hub-Signature-256": "sha256=" + "0" * 64}, 401),
        ({"X-Gitea-Event": "push"}, 400),
        ({"X-Gitea-Delivery": "other-provider"}, 400),
        ({"X-GitHub-Event": "push,pull_request"}, 400),
        ({"X-GitHub-Delivery": "bad-guid"}, 400),
        ({"X-GitHub-Hook-ID": "999"}, 403),
    ],
)
def test_bad_signature_and_ambiguous_routing_are_refused(github, fresh_db, extra, status):
    from app.services import work

    task = work.create_task("Refuse forged metadata")
    response = github("push", push(task["id"]), extra=extra)
    assert response.status_code == status
    assert (
        fresh_db.query_one("SELECT status FROM tasks WHERE id = ?", (task["id"],))["status"]
        == "todo"
    )
    assert not fresh_db.query("SELECT * FROM forge_receipts")


def test_duplicate_headers_and_json_keys_are_refused(client, github, fresh_db):
    from app.services import work

    task = work.create_task("Reject ambiguous input")
    body = json.dumps(push(task["id"])).encode()
    duplicate = [*headers("push", body).items(), ("X-GitHub-Event", "pull_request")]
    assert client.post("/api/webhooks/forge", content=body, headers=duplicate).status_code == 400
    body = body[:-1] + b', "ref": "refs/heads/main"}'
    assert (
        client.post("/api/webhooks/forge", content=body, headers=headers("push", body)).status_code
        == 400
    )


def test_relabeling_signed_github_bytes_as_gitea_cannot_bypass_repository_allowlist(
    github, client, fresh_db
):
    from app.services import work

    task = work.create_task("The HMAC does not cover routing headers")
    payload = push(task["id"])
    payload["repository"]["full_name"] = "outside/repo"
    payload["repository"]["html_url"] = "https://github.com/outside/repo"
    body = json.dumps(payload).encode()
    response = client.post(
        "/api/webhooks/forge",
        content=body,
        headers={
            "X-Gitea-Event": "push",
            "X-Gitea-Delivery": str(uuid4()),
            "X-Gitea-Signature": hmac.new(SECRET.encode(), body, sha256).hexdigest(),
        },
    )
    assert response.status_code == 400
    assert not fresh_db.query("SELECT * FROM forge_receipts")
    assert (
        fresh_db.query_one("SELECT status FROM tasks WHERE id = ?", (task["id"],))["status"]
        == "todo"
    )


def test_disallowed_repository_never_claims_a_receipt(github, fresh_db):
    from app.services import work

    task = work.create_task("Repository boundary")
    payload = push(task["id"])
    payload["repository"]["full_name"] = "attacker/repository"
    response = github("push", payload)
    assert response.status_code == 403
    assert "attacker" not in response.text
    assert not fresh_db.query("SELECT * FROM forge_receipts")


@pytest.mark.parametrize(
    "change",
    [
        {"deleted": "false"},
        {"sender": "octocat"},
        {"ref": 42},
        {"pull_request": {"merged": True}},
    ],
)
def test_malformed_github_payload_is_a_safe_4xx(github, fresh_db, change):
    from app.services import work

    task = work.create_task("Malformed event")
    payload = {**push(task["id"]), **change}
    response = github("push", payload)
    assert response.status_code == 400
    assert not fresh_db.query("SELECT * FROM forge_receipts")


def test_deleted_branch_draft_pr_and_closed_unmerged_do_not_move_work(github, fresh_db):
    from app.services import work

    task = work.create_task("Not a transition")
    deleted = {**push(task["id"]), "deleted": True}
    assert "ignored" in github("push", deleted).json()
    assert "ignored" in github("pull_request", pull_request(task["id"], draft=True)).json()
    assert "ignored" in github("pull_request", pull_request(task["id"], action="closed")).json()
    assert (
        fresh_db.query_one("SELECT status FROM tasks WHERE id = ?", (task["id"],))["status"]
        == "todo"
    )


def test_github_sender_and_sponsor_cannot_be_bypassed(github, fresh_db):
    from app.services import delegation, users, work

    users.ensure_user("scout", kind="agent")
    users.ensure_user("mira")
    task = work.create_task("Agent boundary", actor="mira")
    payload = push(task["id"])
    payload["sender"]["login"] = "scout"
    assert "gated tools" in github("push", payload).json()["ignored"]
    delegation.delegate_task(task["id"], "scout", "mira", actor="mira")
    assert (
        "sponsor"
        in github("pull_request", pull_request(task["id"], action="closed", merged=True)).json()[
            "ignored"
        ]
    )


def test_github_policy_denial_rolls_back_receipt(github, fresh_db, monkeypatch):
    from fastapi.testclient import TestClient

    from app.extensions import PolicyContribution, PolicyDecision, PolicyEffect, SkeinModule
    from app.main import create_app
    from app.services import work

    task = work.create_task("Policy refusal")

    def deny(request):
        if request.action == "skein.integration.forge":
            return PolicyDecision(PolicyEffect.DENY, ("Forge writes are disabled.",))
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.6.0",
        policies=(PolicyContribution("acme.workplace.forge", deny),),
    )
    body = json.dumps(push(task["id"])).encode()
    with TestClient(create_app(modules=(module,))) as denied:
        assert (
            denied.post(
                "/api/webhooks/forge", content=body, headers=headers("push", body)
            ).status_code
            == 403
        )
    assert not fresh_db.query("SELECT * FROM forge_receipts")
    assert github("push", push(task["id"])).json()["status"] == "in_progress"


def test_delivery_namespace_separates_providers_and_repositories(
    github, client, fresh_db, monkeypatch
):
    from app import config
    from app.services import work

    first, second = work.create_task("First"), work.create_task("Second")
    guid = str(uuid4())
    assert github("push", push(first["id"]), delivery=guid).json()["status"] == "in_progress"
    payload = copy.deepcopy(push(second["id"]))
    payload["repository"]["full_name"] = "octocat/other"
    payload["repository"]["html_url"] = "https://github.com/octocat/other"
    monkeypatch.setattr(
        config,
        "GITHUB_HOOKS",
        [*config.GITHUB_HOOKS, {"repository": "octocat/other", "hook_id": HOOK}],
    )
    assert github("push", payload, delivery=guid).json()["status"] == "in_progress"
    work.update_task(second["id"], status="todo", actor="mira")
    body = json.dumps(
        {"ref": f"refs/heads/task/{second['id']}-fixture", "sender": {"login": "mira"}}
    ).encode()
    response = client.post(
        "/api/webhooks/forge",
        content=body,
        headers={
            "X-Gitea-Event": "push",
            "X-Gitea-Delivery": guid,
            "X-Gitea-Signature": hmac.new(SECRET.encode(), body, sha256).hexdigest(),
        },
    )
    assert response.json()["status"] == "in_progress"
