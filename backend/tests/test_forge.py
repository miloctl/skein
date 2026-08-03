"""Forge webhook: what matches a task, what moves it, and what it refuses."""

import hmac
import json
from hashlib import sha256

import pytest

SECRET = "test-forge-secret"


@pytest.fixture()
def signed(client, monkeypatch):
    from app import config
    from app.routes import deps

    monkeypatch.setattr(config, "FORGE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(deps.config, "FORGE_WEBHOOK_SECRET", SECRET)

    def post(event: str, payload: dict, secret: str = SECRET):
        body = json.dumps(payload).encode()
        return client.post(
            "/api/webhooks/forge",
            content=body,
            headers={
                "X-Gitea-Event": event,
                "X-Gitea-Signature": hmac.new(secret.encode(), body, sha256).hexdigest(),
                "Content-Type": "application/json",
            },
        )

    return post


def _push(branch, login="mira"):
    return {
        "ref": f"refs/heads/{branch}",
        "compare_url": "https://git.example/skein/compare/abc",
        "pusher": {"login": login},
        "sender": {"login": login},
    }


def _pr(branch, action="opened", merged=False, title="", body=""):
    return {
        "action": action,
        "sender": {"login": "mira"},
        "pull_request": {
            "merged": merged,
            "title": title,
            "body": body,
            "head": {"ref": branch},
            "html_url": "https://git.example/skein/pulls/7",
        },
    }


# --- matching ---------------------------------------------------------------


def test_branch_forms_that_name_a_task(fresh_db):
    from app.services import forge

    for branch in ("task/42-fix-login", "task-42", "feature/task-42-x", "fix/task/42"):
        assert forge.match_task(branch=branch) == 42, branch


def test_branch_without_a_task_matches_nothing(fresh_db):
    from app.services import forge

    for branch in ("main", "fix-login", "task/abc", "tasks/42"):
        assert forge.match_task(branch=branch) is None, branch


def test_text_fallback_requires_the_word_task(fresh_db):
    from app.services import forge

    assert forge.match_task(title="closes task 42") == 42
    assert forge.match_task(body="fixes task #42 at last") == 42
    # a bare #42 is how the forge numbers ITS issues — matching it would
    # close Skein task 42 for a pull request about Gitea issue 42
    assert forge.match_task(title="fixes #42") is None
    assert forge.match_task(body="see #42") is None


def test_branch_name_beats_the_title(fresh_db):
    from app.services import forge

    assert forge.match_task(branch="task/7-x", title="closes task 99") == 7


# --- transitions ------------------------------------------------------------


def test_push_starts_the_task(signed, fresh_db):
    from app.services import work

    tid = work.create_task("Fix login")["id"]
    out = signed("push", _push(f"task/{tid}-fix-login")).json()
    assert out["status"] == "in_progress"
    row = work.list_tasks_joined()[0]
    assert row["status"] == "in_progress"
    assert row["forge_url"] == "https://git.example/skein/compare/abc"


def test_merged_pull_request_finishes_the_task(signed, fresh_db):
    from app import db
    from app.services import work

    tid = work.create_task("Fix login")["id"]
    out = signed("pull_request", _pr(f"task/{tid}-x", action="closed", merged=True)).json()
    assert out["status"] == "done"
    row = db.query_one("SELECT status, completed_at, forge_url FROM tasks WHERE id = ?", (tid,))
    assert row["status"] == "done" and row["completed_at"]
    assert row["forge_url"] == "https://git.example/skein/pulls/7"


def test_closed_unmerged_pull_request_moves_nothing(signed, fresh_db):
    from app.services import work

    tid = work.create_task("Fix login")["id"]
    out = signed("pull_request", _pr(f"task/{tid}-x", action="closed", merged=False)).json()
    assert "ignored" in out
    assert work.list_tasks_joined()[0]["status"] == "todo"


def test_push_never_reopens_a_done_task(signed, fresh_db):
    from app.services import work

    tid = work.create_task("Fix login")["id"]
    work.update_task(tid, status="done", actor="mira")
    out = signed("push", _push(f"task/{tid}-more")).json()
    assert out["ignored"] == "task is already done"
    assert work.list_tasks_joined()[0]["status"] == "done"


def test_merge_does_not_close_delegated_work(signed, fresh_db):
    from app.services import delegation, users, work

    users.ensure_user("mira")
    users.ensure_user("scout", kind="agent")
    tid = work.create_task("Fix login", actor="mira")["id"]
    delegation.delegate_task(tid, "scout", "mira", actor="mira")
    out = signed("pull_request", _pr(f"task/{tid}-x", action="closed", merged=True)).json()
    assert "sponsor" in out["ignored"]
    assert work.list_tasks_joined()[0]["status"] != "done"


def test_unknown_task_and_unknown_branch_are_ignored(signed, fresh_db):
    assert "ignored" in signed("push", _push("task/9999-ghost")).json()
    assert "ignored" in signed("push", _push("main")).json()


def test_tag_push_is_not_a_branch(signed, fresh_db):
    out = signed("push", {"ref": "refs/tags/v1", "sender": {"login": "mira"}}).json()
    assert "not an event that moves work" in out["ignored"]


# --- identity and attribution ----------------------------------------------


def test_a_forge_login_that_names_a_teammate_is_attributed(signed, fresh_db):
    from app import db
    from app.services import users, work

    users.ensure_user("mira")
    tid = work.create_task("Fix login")["id"]
    signed("push", _push(f"task/{tid}-x", login="Mira"))
    row = db.query_one(
        "SELECT actor FROM activity WHERE action = 'update_task' ORDER BY id DESC LIMIT 1"
    )
    assert row["actor"] == "mira"


def test_an_unknown_login_is_the_forge_itself(signed, fresh_db):
    from app import db
    from app.services import work

    tid = work.create_task("Fix login")["id"]
    signed("push", _push(f"task/{tid}-x", login="someone-else"))
    row = db.query_one(
        "SELECT actor FROM activity WHERE action = 'update_task' ORDER BY id DESC LIMIT 1"
    )
    assert row["actor"] == "forge"


def test_an_agent_login_never_acts_through_the_webhook(fresh_db):
    from app.services import forge, users

    users.ensure_user("scout", kind="agent")
    assert forge.resolve_actor("scout") == "forge"


# --- the signature is the door ---------------------------------------------


def test_a_wrong_signature_is_refused(signed, fresh_db):
    from app.services import work

    tid = work.create_task("Fix login")["id"]
    assert signed("push", _push(f"task/{tid}-x"), secret="wrong").status_code == 401
    assert work.list_tasks_joined()[0]["status"] == "todo"


def test_no_secret_configured_closes_the_endpoint(client, fresh_db, monkeypatch):
    from app import config
    from app.routes import deps

    monkeypatch.setattr(config, "FORGE_WEBHOOK_SECRET", "")
    monkeypatch.setattr(deps.config, "FORGE_WEBHOOK_SECRET", "")
    r = client.post("/api/webhooks/forge", json={}, headers={"X-Gitea-Event": "push"})
    assert r.status_code == 503
    assert "SKEIN_FORGE_WEBHOOK_SECRET" in r.json()["detail"]


def test_a_bad_payload_is_a_4xx_not_a_500(client, fresh_db, monkeypatch):
    from app import config
    from app.routes import deps

    monkeypatch.setattr(config, "FORGE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(deps.config, "FORGE_WEBHOOK_SECRET", SECRET)
    body = b"{not json"
    r = client.post(
        "/api/webhooks/forge",
        content=body,
        headers={
            "X-Gitea-Event": "push",
            "X-Gitea-Signature": hmac.new(SECRET.encode(), body, sha256).hexdigest(),
        },
    )
    assert r.status_code == 400
