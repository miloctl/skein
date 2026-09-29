"""GitLab project webhooks move tasks as Gitea's do (docs/intent/gitlab-forge.md).
The payloads are documented shapes, not captured ones (gitlab_payloads.py)."""

import json

import pytest
from gitlab_payloads import EVENTS, WEB_URL, ZEROS, merge_request, pipeline, push

from app import db
from app.services import users, work

TOKEN = "test-gitlab-token"


@pytest.fixture()
def gitlab(client, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "GITLAB_WEBHOOK_TOKEN", TOKEN)

    def post(payload: dict, *, event: str = "", token: str = TOKEN, key: str = "", **headers):
        header = event or EVENTS.get(str(payload.get("object_kind")), "")
        sent = {"X-Gitlab-Event": header, "X-Gitlab-Token": token, **headers}
        if key:
            sent["Idempotency-Key"] = key
        return client.post(
            "/api/webhooks/gitlab",
            content=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", **sent},
        )

    return post


def _task() -> int:
    return work.create_task("Fix login")["id"]


def _status(tid: int) -> tuple[str, str]:
    row = db.query_one("SELECT status, forge_url FROM tasks WHERE id = ?", (tid,))
    return row["status"], row["forge_url"] or ""


def test_a_push_starts_the_task_and_a_merge_finishes_it(gitlab, fresh_db):
    tid = _task()
    branch = f"task/{tid}-login"
    started = gitlab(push(branch))
    assert started.status_code == 200, started.text
    assert _status(tid) == ("in_progress", f"{WEB_URL}/-/tree/task/{tid}-login")
    merged = gitlab(merge_request("merge", source_branch=branch))
    assert merged.json()["status"] == "done"
    assert _status(tid) == ("done", f"{WEB_URL}/-/merge_requests/7")


@pytest.mark.parametrize(
    "payload",
    [
        lambda b: merge_request("close", source_branch=b),
        lambda b: merge_request("update", source_branch=b),
        lambda b: push(b, after=ZEROS),
        lambda b: push(b, ref="refs/tags/v1"),
        lambda b: push(b, username="scout"),
    ],
    ids=["closed-unmerged", "update", "branch-deleted", "tag-ref", "agent-pusher"],
)
def test_events_that_are_not_work_move_nothing(gitlab, fresh_db, payload):
    users.ensure_user("scout", kind="agent")
    tid = _task()
    answer = gitlab(payload(f"task/{tid}-x"))
    assert answer.status_code == 200, answer.text
    assert "ignored" in answer.json()
    assert _status(tid) == ("todo", "")


def test_a_wrong_token_is_refused_before_the_body_is_read(client, fresh_db, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "GITLAB_WEBHOOK_TOKEN", TOKEN)
    read = False

    def chunks():
        nonlocal read
        read = True
        yield b"{}"

    answer = client.post(
        "/api/webhooks/gitlab",
        content=chunks(),
        headers={"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": "wrong"},
    )
    assert answer.status_code == 401
    assert answer.json()["detail"] == "the webhook token does not match"
    assert read is False


def test_an_unset_token_closes_the_endpoint_before_the_body_is_read(client, fresh_db, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "GITLAB_WEBHOOK_TOKEN", "")
    read = False

    def chunks():
        nonlocal read
        read = True
        yield b"{}"

    answer = client.post(
        "/api/webhooks/gitlab",
        content=chunks(),
        headers={"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": ""},
    )
    assert answer.status_code == 503
    assert "SKEIN_GITLAB_WEBHOOK_TOKEN" in answer.json()["detail"]
    assert read is False


def test_a_system_hook_is_refused(gitlab, fresh_db):
    tid = _task()
    answer = gitlab(push(f"task/{tid}-x"), event="System Hook")
    assert answer.status_code == 400
    assert "System hooks are not supported" in answer.json()["detail"]
    assert _status(tid) == ("todo", "")


def test_an_event_header_that_disagrees_with_the_payload_is_refused(gitlab, fresh_db):
    tid = _task()
    answer = gitlab(push(f"task/{tid}-x"), event="Merge Request Hook")
    assert answer.status_code == 400
    assert "does not match the payload" in answer.json()["detail"]
    assert _status(tid) == ("todo", "")


def test_two_token_headers_are_refused(client, fresh_db, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "GITLAB_WEBHOOK_TOKEN", TOKEN)
    tid = _task()
    answer = client.post(
        "/api/webhooks/gitlab",
        content=json.dumps(push(f"task/{tid}-x")).encode(),
        headers=[
            ("X-Gitlab-Event", "Push Hook"),
            ("X-Gitlab-Token", TOKEN),
            ("X-Gitlab-Token", "other"),
        ],
    )
    assert answer.status_code == 400
    assert _status(tid) == ("todo", "")


def test_a_tag_push_hook_is_ignored_without_a_receipt(gitlab, fresh_db):
    answer = gitlab(push("v1", ref="refs/tags/v1"), event="Tag Push Hook", key="k-tag")
    assert answer.json() == {"ignored": "only push, merge request and pipeline events move work"}
    assert db.query_one("SELECT 1 FROM forge_receipts") is None


def test_a_resend_writes_nothing_twice(gitlab, fresh_db):
    """GitLab's Resend request can carry a new key. The bytes decide."""
    tid = _task()
    payload = push(f"task/{tid}-x")
    assert gitlab(payload, key="first").json()["status"] == "in_progress"
    work.update_task(tid, status="todo", actor="mira")
    again = gitlab(payload, key="second")
    assert again.json() == {"ignored": "this delivery was already applied"}
    assert _status(tid)[0] == "todo"
    receipts = db.query(
        "SELECT delivery_id, provider, task_id FROM forge_receipts ORDER BY delivery_id"
    )
    assert [(r["delivery_id"], r["provider"], r["task_id"]) for r in receipts] == [
        ("first", "gitlab", tid),
        ("second", "gitlab", None),
    ]
    other = gitlab(push(f"task/{tid}-y"), key="first")
    assert other.status_code == 400
    assert "names a different payload" in other.json()["detail"]


def test_the_event_uuid_is_the_delivery_id_without_an_idempotency_key(gitlab, fresh_db):
    tid = _task()
    gitlab(push(f"task/{tid}-x"), **{"X-Gitlab-Event-UUID": "uuid-1"})
    assert db.query_one("SELECT delivery_id FROM forge_receipts")["delivery_id"] == "uuid-1"


def test_gitea_and_gitlab_keep_separate_namespaces_for_one_url(
    gitlab, client, fresh_db, monkeypatch
):
    """One delivery id from each forge for the same repository URL: neither
    reads as the other's resend."""
    import hmac
    from hashlib import sha256

    from app import config

    secret = "test-forge-secret"
    monkeypatch.setattr(config, "FORGE_WEBHOOK_SECRET", secret)
    first, second = _task(), _task()
    body = json.dumps(
        {
            "ref": f"refs/heads/task/{first}-x",
            "repository": {"html_url": WEB_URL},
            "pusher": {"login": "mira"},
        }
    ).encode()
    signed = client.post(
        "/api/webhooks/forge",
        content=body,
        headers={
            "X-Gitea-Event": "push",
            "X-Gitea-Delivery": "same-id",
            "X-Gitea-Signature": hmac.new(secret.encode(), body, sha256).hexdigest(),
        },
    )
    assert signed.json()["status"] == "in_progress"
    answer = gitlab(push(f"task/{second}-x"), key="same-id")
    assert answer.json()["status"] == "in_progress"
    assert sorted(r["provider"] for r in db.query("SELECT provider FROM forge_receipts")) == [
        "gitea",
        "gitlab",
    ]


def test_a_merge_request_on_any_branch_with_the_trailer_closes_the_task(gitlab, fresh_db):
    tid = _task()
    description = f"Fix the login form.\n\nCloses-Task: #{tid}"
    merged = gitlab(merge_request("merge", source_branch="fix-login", description=description))
    assert merged.json()["status"] == "done"
    assert _status(tid) == ("done", f"{WEB_URL}/-/merge_requests/7")


def _open_ci() -> list[dict]:
    return db.query(
        "SELECT title, created_by, source, detail FROM blockers WHERE status != 'resolved'"
    )


def test_a_red_default_branch_pipeline_files_one_blocker_and_green_resolves_it(gitlab, fresh_db):
    assert gitlab(pipeline("failed"), key="p1").json()["raised"] is True
    assert gitlab(pipeline("failed", pipeline_id=32), key="p2").json()["deduped"] is True
    rows = _open_ci()
    assert [(r["title"], r["created_by"], r["source"]) for r in rows] == [
        ("CI red on team/app@main", "forge", "ci:team/app:main")
    ]
    assert f"{WEB_URL}/-/pipelines/31" in rows[0]["detail"]
    receipt = db.query_one("SELECT provider, task_id FROM forge_receipts WHERE delivery_id = 'p1'")
    assert (receipt["provider"], receipt["task_id"]) == ("gitlab", None)
    assert len(gitlab(pipeline("success", pipeline_id=33)).json()["resolved"]) == 1
    assert _open_ci() == []


@pytest.mark.parametrize(
    "payload",
    [
        pipeline("running"),
        pipeline("canceled"),
        pipeline("skipped"),
        pipeline("failed", ref="v1.0", tag=True),
        pipeline("failed", ref="refs/merge-requests/7/head"),
        pipeline("failed", ref="feature/x"),
    ],
    ids=["running", "canceled", "skipped", "tag", "merge-request-ref", "other-branch"],
)
def test_pipelines_that_are_not_a_red_default_branch_do_nothing(gitlab, fresh_db, payload):
    answer = gitlab(payload)
    assert answer.status_code == 200, answer.text
    assert "ignored" in answer.json()
    assert _open_ci() == []


def test_the_projects_own_default_branch_counts(gitlab, fresh_db):
    assert gitlab(pipeline("failed", ref="develop", default_branch="develop")).json()["raised"]
    assert [r["title"] for r in _open_ci()] == ["CI red on team/app@develop"]


def test_a_policy_deny_on_ci_writes_nothing_and_leaves_no_receipt(fresh_db):
    from fastapi.testclient import TestClient

    from app import config
    from app.extensions import PolicyContribution, PolicyDecision, PolicyEffect, SkeinModule
    from app.main import create_app

    seen = []

    def deny_ci(request):
        if request.action == "skein.integration.ci":
            seen.append(dict(request.resource.attributes))
            return PolicyDecision(PolicyEffect.DENY, ("CI writes are disabled",))
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.7.0",
        policies=(PolicyContribution("acme.workplace.ci", deny_ci),),
    )
    config.GITLAB_WEBHOOK_TOKEN, saved = TOKEN, config.GITLAB_WEBHOOK_TOKEN
    try:
        with TestClient(create_app(modules=(module,))) as client:
            answer = client.post(
                "/api/webhooks/gitlab",
                content=json.dumps(pipeline("failed")).encode(),
                headers={
                    "X-Gitlab-Event": "Pipeline Hook",
                    "X-Gitlab-Token": TOKEN,
                    "Idempotency-Key": "denied",
                },
            )
    finally:
        config.GITLAB_WEBHOOK_TOKEN = saved
    assert answer.status_code == 403
    assert seen == [{"repository": "team/app", "provider": "gitlab"}]
    assert _open_ci() == []
    assert db.query_one("SELECT 1 FROM forge_receipts") is None
