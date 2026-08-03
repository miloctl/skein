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


def _push(branch, login="mira", compare="https://git.example/skein/compare/abc"):
    return {
        "ref": f"refs/heads/{branch}",
        "compare_url": compare,
        "repository": {"html_url": "https://git.example/skein"},
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


def test_text_fallback_requires_a_closing_verb(fresh_db):
    from app.services import forge

    assert forge.match_task(title="closes task 42") == 42
    assert forge.match_task(body="fixes task #42 at last") == 42
    assert forge.match_task(body="Resolved task 42") == 42
    # a bare #42 is how the forge numbers ITS issues — matching it would
    # close Skein task 42 for a pull request about Gitea issue 42
    assert forge.match_task(title="fixes #42") is None
    assert forge.match_task(body="see #42") is None
    # naming a task is not closing it: these must never move work
    assert forge.match_task(body="Blocked by task 2 until Friday.") is None
    assert forge.match_task(body="Subtask 3 of the upgrade epic.") is None
    assert forge.match_task(title="Multitask 5 rewrite") is None
    assert forge.match_task(body="this does NOT close task 99") == 99  # verb wins, by design


def test_text_scan_is_bounded_and_linear(fresh_db):
    import time

    from app.services import forge

    # `\s*#?\s*` backtracks n+1 ways per position: 80 KB of spaces measured at
    # 31s before the separators were fixed-width. The route is async, so that
    # is the whole event loop, not one request.
    hostile = "task" + " " * 200_000 + "1"
    start = time.monotonic()
    assert forge.match_task(title=hostile, body=hostile) is None
    assert time.monotonic() - start < 1.0


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
    assert out["ignored"] == "only push and pull_request events move work"


# --- identity and attribution ----------------------------------------------


def test_the_ledger_never_reads_as_the_teammates_own_edit(signed, fresh_db):
    from app import db
    from app.services import users, work

    users.ensure_user("mira")
    tid = work.create_task("Fix login")["id"]
    signed("push", _push(f"task/{tid}-x", login="Mira"))
    row = db.query_one(
        "SELECT actor, detail FROM activity WHERE action = 'update_task' ORDER BY id DESC LIMIT 1"
    )
    # activity rows are hash-chained and can never be corrected, so a secret
    # holder must not be able to write one attributed to a person
    assert row["actor"] == "forge"
    assert "pushed by mira" in row["detail"]


def test_a_new_branch_push_links_the_branch_not_the_forge_home_page(signed, fresh_db):
    from app import db
    from app.services import work

    tid = work.create_task("Fix login")["id"]
    # Gitea fills compare_url only for a branch that already existed; the push
    # that CREATES the branch sends the bare instance root
    signed("push", _push(f"task/{tid}-fix", compare="https://git.example/"))
    row = db.query_one("SELECT forge_url FROM tasks WHERE id = ?", (tid,))
    assert row["forge_url"] == f"https://git.example/skein/src/branch/task/{tid}-fix"


def test_a_non_http_url_is_dropped(signed, fresh_db):
    from app import db
    from app.services import work

    tid = work.create_task("Fix login")["id"]
    signed("push", _push(f"task/{tid}-x", compare="javascript:fetch('//evil/')"))
    row = db.query_one("SELECT forge_url, status FROM tasks WHERE id = ?", (tid,))
    assert row["status"] == "in_progress"
    assert row["forge_url"] == f"https://git.example/skein/src/branch/task/{tid}-x"


def test_repeat_pushes_do_not_append_ledger_rows(signed, fresh_db):
    from app import db
    from app.services import work

    tid = work.create_task("Fix login")["id"]
    for _ in range(3):
        signed("push", _push(f"task/{tid}-x"))
    rows = db.query("SELECT id FROM activity WHERE action = 'update_task'")
    # activity is hash-chained and retention never prunes it: a busy repo must
    # not append a permanent row per push
    assert len(rows) == 1


def test_a_pull_request_opening_starts_the_task(signed, fresh_db):
    from app.services import work

    tid = work.create_task("Fix login")["id"]
    out = signed("pull_request", _pr(f"task/{tid}-x", action="opened")).json()
    assert out["status"] == "in_progress"
    assert work.list_tasks_joined()[0]["status"] == "in_progress"


def test_an_unknown_login_names_nobody(signed, fresh_db):
    from app import db
    from app.services import work

    tid = work.create_task("Fix login")["id"]
    signed("push", _push(f"task/{tid}-x", login="someone-else"))
    row = db.query_one(
        "SELECT actor, detail FROM activity WHERE action = 'update_task' ORDER BY id DESC LIMIT 1"
    )
    assert row["actor"] == "forge" and "from the forge" in row["detail"]


def test_the_webhook_works_in_a_locked_deployment(signed, fresh_db, monkeypatch):
    from app import config
    from app.services import work

    # a forge holds no personal key and cannot sign in: if the perimeter
    # demands one here, the integration dies the moment anyone hardens the
    # deployment, and the fix an operator reaches for is to unharden it
    monkeypatch.setattr(config, "AUTH_MODE", "api-key")
    tid = work.create_task("Fix login")["id"]
    assert signed("push", _push(f"task/{tid}-x")).status_code == 200
    assert work.list_tasks_joined()[0]["status"] == "in_progress"


def test_a_non_ascii_signature_is_a_4xx(fresh_db, monkeypatch):
    import pytest as _pytest
    from fastapi import HTTPException

    from app import config
    from app.routes import deps

    monkeypatch.setattr(config, "FORGE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(deps.config, "FORGE_WEBHOOK_SECRET", SECRET)
    # starlette decodes raw header bytes as latin-1, and compare_digest raises
    # TypeError on a non-ASCII str. Asserted at the function, because the test
    # client refuses to encode the header the real server accepts.
    with _pytest.raises(HTTPException) as caught:
        deps.verify_forge_signature(b"{}", b"\xff".decode("latin-1") * 64)
    assert caught.value.status_code == 401


def test_an_oversized_body_is_refused(client, fresh_db, monkeypatch):
    from app import config
    from app.routes import deps

    monkeypatch.setattr(config, "FORGE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(deps.config, "FORGE_WEBHOOK_SECRET", SECRET)
    r = client.post(
        "/api/webhooks/forge",
        content=b"x" * 2_000_000,
        headers={"X-Gitea-Event": "push", "X-Gitea-Signature": "0" * 64},
    )
    assert r.status_code == 400


def test_a_forge_move_is_visible_in_the_activity_feed(signed, fresh_db):
    from app.services import activity, users, work

    users.ensure_user("mira")
    tid = work.create_task("Fix login")["id"]
    signed("push", _push(f"task/{tid}-x", login="nobody-on-the-roster"))
    # the feed's actor filter is default-closed: an unclassified actor writes
    # rows no viewer can see, and the task looks like it moved by itself
    rows = activity.feed("mira", limit=50)["entries"]
    assert any(r["actor"] == "forge" and str(tid) in r["detail"] for r in rows)


def test_an_agent_login_never_acts_through_the_webhook(fresh_db):
    from app.services import forge, users

    users.ensure_user("scout", kind="agent")
    assert forge.resolve_pusher("scout") == ""


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
