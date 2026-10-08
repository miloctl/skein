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
    called .lower() on it - a caller's input must never reach a 500."""
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


def test_two_red_runs_at_once_file_one_blocker(fresh_db, monkeypatch):
    """Both runs read "no open blocker" before either inserts, so each filed
    its own. The first run is held inside raise_blocker until the second has
    had its chance to read."""
    import threading

    from app.services import blockers, ci

    raise_blocker = blockers.raise_blocker
    first_inside, second_inside = threading.Event(), threading.Event()
    calls = []

    def held(**kwargs):
        calls.append(kwargs["source"])
        if len(calls) == 1:
            first_inside.set()
            # with the lock the second run waits at it, and this times out
            second_inside.wait(2)
        else:
            second_inside.set()
        return raise_blocker(**kwargs)

    monkeypatch.setattr(ci.blockers, "raise_blocker", held)
    errors = []

    def run():
        try:
            ci.ci_event("team/app", "main", "failure", "https://ci.example/1")
        except Exception as exc:
            errors.append(exc)

    first = threading.Thread(target=run)
    first.start()
    assert first_inside.wait(5)
    second = threading.Thread(target=run)
    second.start()
    first.join()
    second.join()
    assert errors == []
    open_rows = fresh_db.query(
        "SELECT id FROM blockers WHERE source = 'ci:team/app:main' AND status != 'resolved'"
    )
    assert len(open_rows) == 1


def test_a_green_run_racing_a_person_resolving_the_blocker_is_not_refused(fresh_db, monkeypatch):
    """A person resolves a CI blocker without the CI lock. The green run read
    the blocker as open, then its own resolve found it resolved and answered
    400, and the forge's receipt rolled back."""
    import threading

    from app.services import blockers, ci

    blocker = ci.ci_event("team/app", "main", "failure")["blocker_id"]
    resolve = blockers.resolve_blocker
    inside, person_done = threading.Event(), threading.Event()

    def held(*args, **kwargs):
        inside.set()
        # with the row lock the person waits for this run, and this times out
        person_done.wait(2)
        return resolve(*args, **kwargs)

    monkeypatch.setattr(ci.blockers, "resolve_blocker", held)
    errors = []

    def green():
        try:
            ci.ci_event("team/app", "main", "success")
        except Exception as exc:
            errors.append(exc)

    def person():
        try:
            resolve(blocker, actor="mira")
        except ValueError:
            pass
        finally:
            person_done.set()

    run = threading.Thread(target=green)
    run.start()
    assert inside.wait(5)
    other = threading.Thread(target=person)
    other.start()
    run.join()
    other.join()
    assert errors == []
    assert (
        fresh_db.query_one("SELECT status FROM blockers WHERE id = ?", (blocker,))["status"]
        == "resolved"
    )


def test_the_generic_ci_body_cannot_name_a_default_branch(client, fresh_db):
    from conftest import _strong

    answer = client.post(
        "/api/webhooks/ci",
        headers=_strong(client, "mira"),
        json={
            "repo": "team/app",
            "branch": "develop",
            "status": "failure",
            "default_branch": "develop",
        },
    )
    assert "ignored" in answer.json()
    assert fresh_db.query_one("SELECT id FROM blockers") is None
