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

    # the verb must MATCH for the ambiguous separators to backtrack at all —
    # a fixture without one never reaches the pathological path and passes
    # against the vulnerable pattern too. Measured: 48s on `\s*#?\s*`.
    hostile = "closes task" + " " * 100_000
    start = time.monotonic()
    assert forge._TEXT.search(hostile) is None
    assert time.monotonic() - start < 1.0


def test_a_closing_verb_at_the_end_of_a_long_body_still_matches(fresh_db):
    from app.services import forge

    # the body is scanned whole: a pull request description is long, and the
    # closing line lands at the bottom of it
    assert forge.match_task(body="x" * 20_000 + "\n\ncloses task 42") == 42


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
    # the stable branch page, never Gitea's compare_url
    assert row["forge_url"] == f"https://git.example/skein/src/branch/task/{tid}-fix-login"


def test_workplace_policy_can_deny_forge_transitions(fresh_db, monkeypatch):
    from fastapi.testclient import TestClient

    from app import config
    from app.extensions import (
        PolicyContribution,
        PolicyDecision,
        PolicyEffect,
        SkeinModule,
    )
    from app.main import create_app
    from app.routes import deps
    from app.services import work

    def deny_forge(request):
        if request.action == "skein.integration.forge":
            return PolicyDecision(PolicyEffect.DENY, ("forge writes are disabled",))
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.6.0",
        policies=(PolicyContribution("acme.workplace.forge", deny_forge),),
    )
    monkeypatch.setattr(config, "FORGE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(deps.config, "FORGE_WEBHOOK_SECRET", SECRET)
    task = work.create_task("keep todo")["id"]
    body = json.dumps(_push(f"task/{task}-policy")).encode()
    with TestClient(create_app(modules=(module,))) as client:
        response = client.post(
            "/api/webhooks/forge",
            content=body,
            headers={
                "X-Gitea-Event": "push",
                "X-Gitea-Signature": hmac.new(SECRET.encode(), body, sha256).hexdigest(),
                "Content-Type": "application/json",
            },
        )
    assert response.status_code == 403
    assert fresh_db.query_one("SELECT status FROM tasks WHERE id = ?", (task,)) == {
        "status": "todo"
    }


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
    # hash-chained rows can never be corrected, so a secret holder must not be
    # able to write one attributed to a person — AND `forge` is a system actor
    # the feed shows to everyone, so the pusher's name must appear nowhere
    assert row["actor"] == "forge"
    assert "mira" not in row["detail"].lower()


def test_a_push_links_the_branch_page_not_the_compare_url(signed, fresh_db):
    from app import db
    from app.services import work

    tid = work.create_task("Fix login")["id"]
    # Gitea's compare_url carries the two shas, so it differs on every push;
    # storing it would defeat the repeat-push guard below
    signed("push", _push(f"task/{tid}-fix", compare="https://git.example/skein/compare/aaa...bbb"))
    row = db.query_one("SELECT forge_url FROM tasks WHERE id = ?", (tid,))
    assert row["forge_url"] == f"https://git.example/skein/src/branch/task/{tid}-fix"


def test_a_non_http_url_never_reaches_a_task(fresh_db):
    from app import db
    from app.services import forge, work

    # the pull request path passes html_url straight through — _clean_url is
    # the only guard on it, so drive that path, not the rebuilt push URL. A
    # fresh task each time: the write only happens on a real transition.
    for hostile in (
        "javascript:fetch('//evil/')",
        "data:text/html,<script>alert(1)</script>",
        'https://ok.example/" onmouseover="alert(1)',
        "https://user:pass@evil.example/x",
        "https://ok.example/x\x0conmouseover=alert(1)",
        "https://ok.example/x\u2028y",  # line separator: HTML whitespace
    ):
        tid = work.create_task("Fix login")["id"]
        forge.forge_event("pr_opened", branch=f"task/{tid}-x", url=hostile)
        stored = db.query_one("SELECT forge_url, status FROM tasks WHERE id = ?", (tid,))
        assert stored["status"] == "in_progress"  # the transition still lands
        assert stored["forge_url"] == "", hostile
    good = work.create_task("Fix login")["id"]
    # a scheme is case-insensitive, so an uppercase base URL must survive
    forge.forge_event("pr_opened", branch=f"task/{good}-x", url="HTTPS://ok.example/pulls/1")
    row = db.query_one("SELECT forge_url FROM tasks WHERE id = ?", (good,))
    assert row["forge_url"] == "HTTPS://ok.example/pulls/1"


def test_repeat_pushes_do_not_append_ledger_rows(signed, fresh_db):
    from app import db
    from app.services import work

    tid = work.create_task("Fix login")["id"]
    # a real repo sends a DIFFERENT compare_url every push — the fixture must
    # vary it, or the test proves only that identical input dedupes
    for n in range(3):
        signed("push", _push(f"task/{tid}-x", compare=f"https://git.example/skein/compare/{n}"))
    rows = db.query("SELECT id FROM activity WHERE action = 'update_task'")
    # activity is hash-chained and retention never prunes it: a busy repo must
    # not append a permanent row per push
    assert len(rows) == 1


def test_an_event_without_a_url_never_erases_the_stored_one(signed, fresh_db):
    from app import db
    from app.services import forge, work

    tid = work.create_task("Fix login")["id"]
    signed("push", _push(f"task/{tid}-x"))
    stored = db.query_one("SELECT forge_url FROM tasks WHERE id = ?", (tid,))["forge_url"]
    out = forge.forge_event("branch_push", branch=f"task/{tid}-x", url="")
    assert "ignored" in out
    assert db.query_one("SELECT forge_url FROM tasks WHERE id = ?", (tid,))["forge_url"] == stored
    assert len(db.query("SELECT id FROM activity WHERE action = 'update_task'")) == 1


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


def test_a_body_that_hides_its_length_is_still_refused(client, fresh_db, monkeypatch):
    from app import config
    from app.routes import deps

    monkeypatch.setattr(config, "FORGE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(deps.config, "FORGE_WEBHOOK_SECRET", SECRET)

    def chunks():
        for _ in range(64):
            yield b"x" * 65_536

    # no Content-Length: the in-stream counter is the guard that matters for
    # an unsigned caller, and the header check cannot see this at all
    r = client.post(
        "/api/webhooks/forge",
        content=chunks(),
        headers={"X-Gitea-Event": "push", "X-Gitea-Signature": "0" * 64},
    )
    assert r.status_code == 400


def test_an_unconfigured_webhook_refuses_before_it_reads_a_body(client, fresh_db, monkeypatch):
    from app import config
    from app.routes import deps

    monkeypatch.setattr(config, "FORGE_WEBHOOK_SECRET", "")
    monkeypatch.setattr(deps.config, "FORGE_WEBHOOK_SECRET", "")
    read = False

    def chunks():
        nonlocal read
        read = True
        yield b"{}"

    r = client.post(
        "/api/webhooks/forge",
        content=chunks(),
        headers={"X-Gitea-Event": "push", "X-Gitea-Signature": "0" * 64},
    )
    # a deployment that never turned the webhook on must not buffer a byte
    # for an unsigned caller
    assert r.status_code == 503
    assert read is False


def test_a_json_array_payload_is_a_4xx_not_a_500(signed, fresh_db):
    # parse_gitea calls .get on it — a caller's input must never be a 500
    assert signed("push", [{"ref": "refs/heads/task/1-x"}]).status_code == 400


def test_a_wrong_typed_nested_field_is_never_a_500(signed, fresh_db):
    from app.services import work

    tid = work.create_task("Fix login")["id"]
    # Gitea types every field correctly, so only a caller holding the secret
    # sends these — and each one reached an AttributeError inside parse_gitea
    # before the _dict/_str boundary, which main.py maps to no 4xx. The
    # suite's other fixtures are all well-typed and never reach these
    # branches — these cases are the only coverage.
    payloads = [
        ("push", {"ref": 1}),
        ("push", {"ref": ["refs/heads/task/1-x"]}),
        ("push", {"ref": True, "sender": "x"}),
        (
            "push",
            {"ref": f"refs/heads/task/{tid}-x", "pusher": 3, "repository": [], "sender": "x"},
        ),
        ("pull_request", {"action": "opened", "pull_request": ["x"]}),
        ("pull_request", {"action": 1, "pull_request": {"merged": True}}),
        (
            "pull_request",
            {"action": "opened", "pull_request": {"head": "x", "title": 7, "body": {}}},
        ),
        (
            "pull_request",
            {
                "action": "opened",
                "pull_request": {"head": {"ref": f"task/{tid}-x"}, "html_url": 9, "user": 2},
            },
        ),
    ]
    for event, payload in payloads:
        assert signed(event, payload).status_code == 200, (event, payload)
    # coerced, not refused: the well-typed part of a payload still lands, so
    # a forge that grows one odd field does not silently stop moving work
    assert work.list_tasks_joined()[0]["status"] == "in_progress"


def test_a_forge_move_is_visible_in_the_activity_feed(signed, fresh_db):
    from app.services import activity, users, work

    users.ensure_user("mira")
    tid = work.create_task("Fix login")["id"]
    signed("push", _push(f"task/{tid}-x", login="nobody-on-the-roster"))
    # the feed's actor filter is default-closed: an unclassified actor writes
    # rows no viewer can see, and the task looks like it moved by itself
    rows = activity.feed("mira", limit=50)["entries"]
    assert any(r["actor"] == "forge" and str(tid) in r["detail"] for r in rows)


def test_an_agent_login_never_acts_through_the_webhook(signed, fresh_db):
    from app.services import forge, users, work

    users.ensure_user("scout", kind="agent")
    tid = work.create_task("Fix login")["id"]
    # refused, not merely un-named: the write itself must not happen, or an
    # agent reaches the human path with the review gate behind it
    out = signed("push", _push(f"task/{tid}-x", login="scout")).json()
    assert "gated tools" in out["ignored"]
    assert work.list_tasks_joined()[0]["status"] == "todo"
    assert forge.is_agent_login("scout") is True
    assert forge.is_agent_login(" scout ") is True  # a login carries whitespace


def test_a_human_named_like_an_agent_cannot_disarm_the_refusal(signed, fresh_db):
    # ensure_user refuses to CREATE this collision now, so plant it the way a
    # database written before that guard carries it. `users.name` is
    # case-sensitively unique, so both rows coexist, and a lookup that returns
    # "the first row" gets the human — BINARY order sorts `Scout` first.
    from app import db
    from app.services import forge, users, work

    users.ensure_user("scout", kind="agent")
    db.execute(
        "INSERT INTO users (name, kind, active, created_at) VALUES ('Scout', 'human', 1, ?)",
        (db.now(),),
    )
    tid = work.create_task("Fix login")["id"]
    assert forge.is_agent_login("scout") is True
    out = signed("push", _push(f"task/{tid}-x", login="scout")).json()
    assert "gated tools" in out["ignored"]
    assert work.list_tasks_joined()[0]["status"] == "todo"


def test_the_push_pr_push_cycle_writes_one_row_per_transition(signed, fresh_db):
    from app import db
    from app.services import work

    tid = work.create_task("Fix login")["id"]
    branch = f"task/{tid}-x"
    # the ordinary workflow: push, open the PR, then push review fixes. Every
    # hop alternates the URL between the branch page and the PR link, so a
    # URL-keyed guard writes a permanent hash-chained row on each one.
    signed("push", _push(branch))
    for _ in range(4):
        signed("pull_request", _pr(branch, action="opened"))
        signed("push", _push(branch))
    rows = db.query("SELECT id FROM activity WHERE action = 'update_task'")
    assert len(rows) == 1  # one transition: todo -> in_progress
    signed("pull_request", _pr(branch, action="closed", merged=True))
    assert len(db.query("SELECT id FROM activity WHERE action = 'update_task'")) == 2


def test_a_moving_repository_url_does_not_append_rows(signed, fresh_db):
    from app import db
    from app.services import work

    tid = work.create_task("Fix login")["id"]
    for n in range(4):
        p = _push(f"task/{tid}-x")
        # a rename, a mirror, or a hostile signed caller
        p["repository"]["html_url"] = f"https://git.example/skein?v={n}"
        signed("push", p)
    assert len(db.query("SELECT id FROM activity WHERE action = 'update_task'")) == 1


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


def test_the_address_meter_runs_before_the_signature_check(signed, fresh_db, monkeypatch):
    """The property that makes forge_addr a defense is its POSITION: an
    unsigned caller has no name to key on, and the HMAC over the body is the
    expensive part. Metering after verification protects nothing they touch."""
    from app import ratelimit

    monkeypatch.setitem(ratelimit.LIMITS, "forge_addr", 3)
    # every one of these fails the signature — the meter must still count them
    for _ in range(3):
        assert signed("push", _push("task/1-x"), secret="wrong").status_code == 401
    r = signed("push", _push("task/1-x"), secret="wrong")
    assert r.status_code == 429
    assert "webhook deliveries" in r.json()["detail"]


def test_the_link_rides_the_transition_that_earns_it(signed, fresh_db):
    from app import db
    from app.services import work

    tid = work.create_task("Fix login")["id"]
    signed("push", _push(f"task/{tid}-x"))
    assert db.query_one("SELECT forge_url FROM tasks WHERE id = ?", (tid,))["forge_url"].endswith(
        f"/src/branch/task/{tid}-x"
    )
    signed("pull_request", _pr(f"task/{tid}-x", action="closed", merged=True))
    # the merge earned the transition, so it carries the pull request link
    assert (
        db.query_one("SELECT forge_url FROM tasks WHERE id = ?", (tid,))["forge_url"]
        == "https://git.example/skein/pulls/7"
    )


def test_a_task_someone_else_closed_keeps_its_branch_link(signed, fresh_db):
    from app import db
    from app.services import work

    tid = work.create_task("Fix login")["id"]
    signed("push", _push(f"task/{tid}-x"))
    work.update_task(tid, status="done", actor="mira")
    out = signed("pull_request", _pr(f"task/{tid}-x", action="closed", merged=True)).json()
    # no transition left to earn, so nothing is recorded — docs/FEATURES.md
    # says exactly this, and once claimed the opposite
    assert out["ignored"] == "task is already done"
    assert db.query_one("SELECT forge_url FROM tasks WHERE id = ?", (tid,))["forge_url"].endswith(
        f"/src/branch/task/{tid}-x"
    )


def test_workplace_policy_denies_a_forge_transition_in_the_write_transaction(fresh_db, monkeypatch):
    """The forge decision and mutation share one BEGIN IMMEDIATE. This pins
    the deny half: a workplace rule on the matched task refuses the move and
    nothing lands."""
    from fastapi.testclient import TestClient

    from app import config
    from app.extensions import (
        PolicyContribution,
        PolicyDecision,
        PolicyEffect,
        SkeinModule,
    )
    from app.main import create_app
    from app.routes import deps
    from app.services import work

    monkeypatch.setattr(config, "FORGE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(deps.config, "FORGE_WEBHOOK_SECRET", SECRET)
    task = work.create_task("forge denied probe")

    def deny_forge(request):
        if request.action == "skein.integration.forge":
            return PolicyDecision(PolicyEffect.DENY, ("forge writes are closed",))
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.6.0",
        policies=(PolicyContribution("acme.workplace.forge", deny_forge),),
    )
    body = json.dumps(_push(f"task/{task['id']}-probe")).encode()
    with TestClient(create_app(modules=(module,))) as workplace_client:
        response = workplace_client.post(
            "/api/webhooks/forge",
            content=body,
            headers={
                "X-Gitea-Event": "push",
                "X-Gitea-Signature": hmac.new(SECRET.encode(), body, sha256).hexdigest(),
                "Content-Type": "application/json",
            },
        )
    assert response.status_code == 403
    assert (
        fresh_db.query_one("SELECT status FROM tasks WHERE id = ?", (task["id"],))["status"]
        == "todo"
    )
