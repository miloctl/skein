"""The CI webhook: deduped blocker on red, auto-resolve on green."""

from conftest import _strong


def test_ci_webhook_refuses_self_asserted_identity(client, fresh_db):
    response = client.post(
        "/api/webhooks/ci",
        json={"repo": "team/app", "branch": "main", "status": "failure"},
    )
    assert response.status_code == 403
    assert fresh_db.query_one("SELECT id FROM blockers") is None


def test_ci_webhook_dedupe_and_resolve(client):
    fail = {
        "repo": "team/app",
        "branch": "main",
        "status": "failure",
        "run_url": "https://ci/run/1",
    }
    first = client.post("/api/webhooks/ci", headers=_strong(client), json=fail).json()
    assert first["raised"]
    assert client.post("/api/webhooks/ci", headers=_strong(client), json=fail).json()["deduped"]

    blockers = client.get("/api/blockers").json()
    assert any("CI red" in b["title"] for b in blockers)

    ok = client.post(
        "/api/webhooks/ci", headers=_strong(client), json={**fail, "status": "success"}
    ).json()
    assert len(ok["resolved"]) == 1
    assert client.get("/api/blockers").json() == []

    ignored = client.post(
        "/api/webhooks/ci", headers=_strong(client), json={**fail, "branch": "feature/x"}
    ).json()
    assert "ignored" in ignored


def test_ci_webhook_github_actions_shape(client):
    payload = {
        "workflow_run": {
            "status": "completed",
            "conclusion": "failure",
            "head_branch": "main",
            "html_url": "https://gh/run/9",
        },
        "repository": {"full_name": "team/repo"},
    }
    out = client.post("/api/webhooks/ci", headers=_strong(client), json=payload).json()
    assert out["raised"]

    cancelled = {**payload, "workflow_run": {**payload["workflow_run"], "conclusion": "cancelled"}}
    assert (
        "ignored" in client.post("/api/webhooks/ci", headers=_strong(client), json=cancelled).json()
    )


def test_workplace_policy_can_deny_ci_side_effects(fresh_db):
    from fastapi.testclient import TestClient

    from app.extensions import (
        PolicyContribution,
        PolicyDecision,
        PolicyEffect,
        SkeinModule,
    )
    from app.main import create_app

    def deny_ci(request):
        if request.action == "skein.integration.ci":
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
    with TestClient(create_app(modules=(module,))) as client:
        response = client.post(
            "/api/webhooks/ci",
            headers=_strong(client, "mira"),
            json={"repo": "team/app", "branch": "main", "status": "failure"},
        )
    assert response.status_code == 403
    assert fresh_db.query_one("SELECT id FROM blockers") is None


def test_ci_policy_sees_the_repository_the_write_targets(fresh_db):
    """A GitHub Actions payload carries `repository.full_name` beside the
    generic `repo` field, and the write uses full_name. Policy judged `repo`,
    so a caller passed an allowed name there and filed against a denied one."""
    from fastapi.testclient import TestClient

    from app.extensions import (
        PolicyContribution,
        PolicyDecision,
        PolicyEffect,
        SkeinModule,
    )
    from app.main import create_app

    def deny_repo(request):
        if (
            request.action == "skein.integration.ci"
            and request.resource.attributes.get("repository") == "team/denied"
        ):
            return PolicyDecision(PolicyEffect.DENY, ("that repository is closed",))
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.7.0",
        policies=(PolicyContribution("acme.workplace.ci", deny_repo),),
    )
    with TestClient(create_app(modules=(module,))) as client:
        response = client.post(
            "/api/webhooks/ci",
            headers=_strong(client, "mira"),
            json={
                "repo": "team/allowed",
                "workflow_run": {
                    "status": "completed",
                    "conclusion": "failure",
                    "head_branch": "main",
                    "html_url": "https://gh/run/9",
                },
                "repository": {"full_name": "team/denied"},
            },
        )
    assert response.status_code == 403
    assert fresh_db.query_one("SELECT id FROM blockers") is None


def test_ci_webhook_rejects_an_unvalidated_repository_shape(client, fresh_db):
    """`repository` is an unschema'd dict, so full_name arrives as anything.
    Unvalidated, a nested dict raised inside the first policy rule that
    called .lower() on it — a caller's input must never reach a 500."""
    payload = {
        "workflow_run": {
            "status": "completed",
            "conclusion": "failure",
            "head_branch": "main",
            "html_url": "https://gh/run/9",
        },
        "repository": {"full_name": {"nested": "dict"}},
    }
    response = client.post("/api/webhooks/ci", headers=_strong(client), json=payload)
    assert response.status_code == 400
    assert "nested" not in response.text
    assert fresh_db.query_one("SELECT id FROM blockers") is None

    # the rejected value never comes back: pydantic's own message carries it
    oversized = {
        **payload,
        "repository": {"full_name": "secret-" + "A" * 100_000},
    }
    refused = client.post("/api/webhooks/ci", headers=_strong(client), json=oversized)
    assert refused.status_code == 400
    assert "secret-" not in refused.text
    assert refused.json()["detail"].startswith("repo:")
