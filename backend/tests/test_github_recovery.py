"""Drive GitHub's delivery-list and redelivery API over a real local socket.

GitHub's repository-webhook example groups delivery attempts by guid, checks
all attempts for a success, and POSTs /deliveries/{id}/attempts (202).
https://docs.github.com/en/webhooks/using-webhooks/automatically-redelivering-failed-deliveries-for-a-repository-webhook
"""

import json
import socket
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest
from test_github_webhooks import HOOK, REPOSITORY, SECRET, headers, push


@pytest.fixture()
def api(client, monkeypatch):
    from types import SimpleNamespace

    from app import config
    from app.services import forge, github_recovery, mcp_servers

    upstream = SimpleNamespace(
        pages=[[]],
        requests=[],
        posts=[],
        post_modes=[],
        get_modes=[],
        payloads={},
        now=datetime.now(UTC).replace(microsecond=0),
        link=None,
        slow=False,
        client=client,
        pages_by_repo={},
        on_get=None,
        rate_headers={"Retry-After": "600"},
        response_headers={},
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            upstream.requests.append(("GET", self.path, self.headers.get("Authorization")))
            if upstream.on_get:
                upstream.on_get(self.path)
            if upstream.get_modes:
                mode = upstream.get_modes.pop(0)
                if mode == "disconnect":
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                if isinstance(mode, int):
                    self.send_response(mode)
                    for name, value in upstream.rate_headers.items():
                        self.send_header(name, value)
                    self.end_headers()
                    return
            index = 1 if "cursor=older" in self.path else 0
            repository = "/".join(self.path.split("/")[2:4])
            pages = upstream.pages_by_repo.get(repository, upstream.pages)
            body = json.dumps(pages[index]).encode()
            self.send_response(200)
            for name, value in upstream.response_headers.items():
                self.send_header(name, value)
            if upstream.link:
                self.send_header("Link", upstream.link)
            elif index + 1 < len(pages):
                self.send_header(
                    "Link",
                    f'<{upstream.url}{self.path.split("?")[0]}?per_page=100&cursor=older>; rel="next"',
                )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if upstream.slow:
                import time

                for byte in body:
                    try:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        break
                    time.sleep(0.01)
            else:
                self.wfile.write(body)

        def do_POST(self):
            upstream.requests.append(("POST", self.path, self.headers.get("Authorization")))
            delivery_id = int(self.path.split("/")[-2])
            upstream.posts.append(delivery_id)
            mode = upstream.post_modes.pop(0) if upstream.post_modes else 202
            # The fake GitHub sends the ORIGINAL signed webhook, rather than
            # calling a task writer that bypasses the real receipt/policy path.
            if mode in (202, "disconnect") and delivery_id in upstream.payloads:
                guid, event, payload = upstream.payloads[delivery_id]
                body = json.dumps(payload).encode()
                upstream.forward_response = upstream.client.post(
                    "/api/webhooks/forge", content=body, headers=headers(event, body, guid)
                )
            if mode == "disconnect":
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self.send_response(mode)
            if mode == 429:
                for name, value in upstream.rate_headers.items():
                    self.send_header(name, value)
            if mode == 302:
                self.send_header("Location", "https://credential-thief.invalid/")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    upstream.url = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(config, "GITHUB_API_URL", upstream.url)
    monkeypatch.setattr(
        config, "GITHUB_HOOKS", [{"repository": REPOSITORY.lower(), "hook_id": HOOK}]
    )
    monkeypatch.setattr(config, "GITHUB_RECOVERY", True)
    monkeypatch.setattr(config, "GITHUB_TOKEN", "fixture-token-never-log")
    monkeypatch.setattr(config, "GITHUB_CONFIG_ERROR", "")
    monkeypatch.setattr(config, "FORGE_WEBHOOK_SECRET", SECRET)
    # Production rejects loopback. Only this socket fixture grants its local
    # server, without adding a production test setting or bypass endpoint.
    monkeypatch.setattr(mcp_servers, "check_url", lambda _: None)
    monkeypatch.setattr(forge, "github_web_base", lambda: "https://github.com")
    monkeypatch.setattr(github_recovery, "_now", lambda: upstream.now)
    upstream.run = lambda: github_recovery.run(registry=client.app.state.skein_registry)
    try:
        yield upstream
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def delivery(delivery_id, guid=None, *, code=500, ago=0, event="push", redelivery=False):
    return {
        "id": delivery_id,
        "guid": guid or str(uuid4()),
        "delivered_at": (datetime.now(UTC) - timedelta(seconds=ago)).isoformat(),
        "redelivery": redelivery,
        "duration": 0.03,
        "status": "OK" if code == 200 else "Service Timeout",
        "status_code": code,
        "event": event,
        "action": None,
        "installation_id": None,
        "repository_id": 1296269,
    }


def _run_in_new_process(api):
    import os
    import subprocess
    import sys

    from app import config

    script = """
import json, os
from datetime import datetime
from app import config, db
from app.services import github_recovery, mcp_servers
config.GITHUB_API_URL = os.environ["FIXTURE_API_URL"]
mcp_servers.check_url = lambda _: None
github_recovery._now = lambda: datetime.fromisoformat(os.environ["FIXTURE_NOW"])
print(json.dumps(github_recovery.run()))
db.close_pool()
"""
    env = {
        **os.environ,
        "SKEIN_DATABASE_URL": config.DATABASE_URL,
        "FIXTURE_API_URL": api.url,
        "FIXTURE_NOW": api.now.isoformat(),
        "SKEIN_GITHUB_RECOVERY": "1",
        "SKEIN_GITHUB_TOKEN": "fixture-token-never-log",
        "SKEIN_FORGE_WEBHOOK_SECRET": SECRET,
        "SKEIN_GITHUB_HOOKS": json.dumps(config.GITHUB_HOOKS),
        "SKEIN_GITHUB_HOOKS_FILE": "",
    }
    completed = subprocess.run(  # noqa: S603 -- this interpreter and a literal test program
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    return json.loads(completed.stdout)


def test_poll_redelivers_through_signed_policy_and_persists_attempt(api, fresh_db):
    from app.services import work

    task = work.create_task("Recover a missed push")
    failed = delivery(101)
    api.pages = [[failed]]
    api.payloads[101] = (failed["guid"], "push", push(task["id"]))
    result = api.run()
    assert api.posts == [101]
    assert api.forward_response.status_code == 200
    assert (
        fresh_db.query_one("SELECT status FROM tasks WHERE id = ?", (task["id"],))["status"]
        == "in_progress"
    )
    assert fresh_db.query_one("SELECT state FROM github_recovery_attempts")["state"] == "accepted"
    assert result["requested"] == 1
    for table, actor in (
        ("forge_receipts", "forge"),
        ("github_recovery_hooks", "scheduler"),
        ("github_recovery_deliveries", "scheduler"),
        ("github_recovery_attempts", "scheduler"),
    ):
        assert fresh_db.query_one(f"SELECT origin, created_by FROM {table}") == {  # noqa: S608 -- literal table inventory
            "origin": "human",
            "created_by": actor,
        }
    assert all(auth == "Bearer fixture-token-never-log" for _, _, auth in api.requests)


def test_all_attempt_pages_are_checked_for_prior_guid_success(api, fresh_db):
    guid = str(uuid4())
    api.pages = [[delivery(203, guid, redelivery=True)], [delivery(101, guid, code=200)]]
    api.run()
    assert len(api.requests) == 2
    assert not api.posts
    assert (
        fresh_db.query_one("SELECT successful FROM github_recovery_deliveries")["successful"]
        is True
    )


def test_bounded_pages_checkpoint_then_restart_without_skipping_new_arrivals(
    api, fresh_db, monkeypatch
):
    from app.services import github_recovery

    first = delivery(102)
    success = delivery(101, first["guid"], code=200)
    newer = delivery(103)
    api.pages = [[first], [success]]
    monkeypatch.setattr(github_recovery, "MAX_PAGES", 1)
    api.run()
    assert fresh_db.query_one("SELECT cursor FROM github_recovery_hooks")["cursor"] == "older"
    assert not api.posts
    api.pages[0] = [newer, first]
    api.run()
    assert "cursor=older" in api.requests[-1][1]
    assert not api.posts
    assert fresh_db.query_one("SELECT cursor FROM github_recovery_hooks")["cursor"] == ""
    api.run()
    api.run()
    assert api.posts == [103]


def test_page_failure_keeps_checkpoint_and_honors_rate_limit(api, fresh_db, monkeypatch):
    from app.services import github_recovery

    api.pages = [[delivery(102)], [delivery(101)]]
    monkeypatch.setattr(github_recovery, "MAX_PAGES", 1)
    api.run()
    api.get_modes = [429]
    api.run()
    row = fresh_db.query_one("SELECT * FROM github_recovery_hooks")
    assert row["cursor"] == "older" and row["error_code"] == "RATE_LIMITED"
    before = len(api.requests)
    api.run()
    assert len(api.requests) == before
    api.now += timedelta(seconds=601)
    api.run()
    assert "cursor=older" in api.requests[before][1]


def test_ambiguous_post_is_recorded_and_local_receipt_prevents_repeat(api, fresh_db):
    from app.services import work

    task = work.create_task("Commit before redelivery acknowledgment")
    failed = delivery(101)
    api.pages = [[failed]]
    api.payloads[101] = (failed["guid"], "push", push(task["id"]))
    api.post_modes = ["disconnect"]
    api.run()
    assert fresh_db.query_one("SELECT state FROM github_recovery_attempts")["state"] == "unknown"
    work.update_task(task["id"], status="todo", actor="mira")
    api.now += timedelta(hours=1)
    api.run()
    assert api.posts == [101]
    assert (
        fresh_db.query_one("SELECT state FROM github_recovery_deliveries")["state"]
        == "local_receipt"
    )
    assert (
        fresh_db.query_one("SELECT status FROM tasks WHERE id = ?", (task["id"],))["status"]
        == "todo"
    )


def test_unknown_post_without_receipt_retries_only_after_backoff_and_history_check(api, fresh_db):
    api.pages = [[delivery(101)]]
    api.post_modes = ["disconnect", 202]
    api.run()
    api.run()
    assert api.posts == [101]
    api.now += timedelta(hours=1)
    api.run()
    assert api.posts == [101, 101]
    assert [
        r["state"] for r in fresh_db.query("SELECT state FROM github_recovery_attempts ORDER BY id")
    ] == ["unknown", "accepted"]


def test_redelivery_redirect_is_not_followed_and_error_is_safe(api, fresh_db, caplog):
    api.pages = [[delivery(101)]]
    api.post_modes = [302]
    result = api.run()
    assert api.posts == [101]
    assert len(api.requests) == 2
    assert result["status"] == "error"
    assert "fixture-token-never-log" not in caplog.text
    assert "credential-thief" not in json.dumps(result)


def test_network_error_and_byte_bound_do_not_advance_scan(api, fresh_db, monkeypatch):
    from app.services import github_recovery

    api.get_modes = ["disconnect"]
    assert api.run()["status"] == "error"
    assert (
        fresh_db.query_one("SELECT last_complete_at FROM github_recovery_hooks")["last_complete_at"]
        == ""
    )
    api.now += timedelta(hours=1)
    api.pages = [[delivery(101)]]
    monkeypatch.setattr(github_recovery, "MAX_PAGE_BYTES", 20)
    assert api.run()["status"] == "error"
    assert not api.posts
    assert (
        fresh_db.query_one("SELECT last_complete_at FROM github_recovery_hooks")["last_complete_at"]
        == ""
    )


def test_outage_beyond_retained_history_is_visible_and_never_reconstructs_events(api, fresh_db):
    from app.extensions.policy import PolicySubject
    from app.services import github_recovery

    api.run()
    api.now += timedelta(days=4)
    result = api.run()
    assert result["status"] == "error"
    row = fresh_db.query_one("SELECT * FROM github_recovery_hooks")
    assert row["gap_since"] and row["gap_until"]
    assert not row["reconciled_at"]
    assert not api.posts and not fresh_db.query("SELECT * FROM forge_receipts")
    github_recovery.acknowledge_gap(
        row["namespace"],
        row["gap_until"],
        "Compared current branches and pull requests with task state.",
        policy=api.client.app.state.skein_registry.policy_engine,
        subject=PolicySubject("operator", strong=True),
    )
    acknowledged = fresh_db.query_one("SELECT * FROM github_recovery_hooks")
    assert acknowledged["reconciled_by"] == "operator" and acknowledged["reconciled_at"]
    assert acknowledged["gap_since"] == row["gap_since"]
    assert fresh_db.query_one(
        "SELECT 1 FROM activity WHERE actor = 'operator' AND action = 'reconcile_github_gap'"
    )
    assert not fresh_db.query("SELECT * FROM forge_receipts")


def test_slow_drip_cannot_hold_a_scan_past_its_wall_clock(api, fresh_db, monkeypatch):
    import time

    from app.services import github_recovery

    api.pages = [[delivery(101)]]
    api.slow = True
    request = github_recovery._request
    times = {}

    def measured_request(client, method, path, budget):
        def started(_path):
            times["started"] = time.monotonic()
            budget.deadline = times["started"] + 0.1

        # Setup keeps the normal budget. The short streaming deadline starts
        # only after a real GET reaches the server, not during TLS/DB setup.
        api.on_get = started
        try:
            return request(client, method, path, budget)
        finally:
            times["stopped"] = time.monotonic()

    monkeypatch.setattr(github_recovery, "_request", measured_request)
    result = api.run()
    assert len(api.requests) == 1 and api.requests[0][0] == "GET"
    assert "started" in times
    assert result["status"] == "error" and result["error_code"] == "WORK_BOUND"
    # DB settlement after the socket closes is not streaming time.
    assert times["stopped"] - times["started"] < 1.0
    assert not api.posts


def test_expired_setup_budget_without_work_does_not_report_success(api, fresh_db, monkeypatch):
    from app.services import github_recovery

    budget_type = github_recovery._Budget
    monkeypatch.setattr(github_recovery, "_Budget", lambda _deadline: budget_type(-1))
    result = api.run()
    assert result["status"] == "noop"
    assert result["pages"] == result["requested"] == 0
    assert not api.requests
    assert not fresh_db.query("SELECT * FROM github_recovery_hooks")


@pytest.mark.parametrize("code", [200, 204, 301])
def test_github_success_range_prevents_redelivery(api, fresh_db, code):
    api.pages = [[delivery(101, code=code)]]
    api.run()
    assert not api.posts


def test_post_server_error_leaves_ambiguous_outcome(api, fresh_db):
    api.pages = [[delivery(101)]]
    api.post_modes = [503]
    assert api.run()["status"] == "error"
    assert fresh_db.query_one("SELECT state FROM github_recovery_attempts")["state"] == "unknown"


@pytest.mark.parametrize(
    "target",
    [
        "https://credential-thief.invalid/path?cursor=older",
        "http://127.0.0.1/other?cursor=older",
    ],
)
def test_pagination_cannot_choose_another_credential_destination(api, fresh_db, target):
    api.pages = [[delivery(101)]]
    api.link = f'<{target}>; rel="next"'
    result = api.run()
    assert result["error_code"] == "INVALID_PAGINATION"
    assert len(api.requests) == 1 and not api.posts
    assert (
        fresh_db.query_one("SELECT last_complete_at FROM github_recovery_hooks")["last_complete_at"]
        == ""
    )


def test_retained_pending_delivery_missing_from_history_becomes_gap(api, fresh_db):
    api.pages = [[delivery(101)]]
    api.post_modes = ["disconnect"]
    api.run()
    api.now += timedelta(hours=1)
    api.pages = [[]]
    assert api.run()["error_code"] == "HISTORY_GAP"
    assert (
        fresh_db.query_one("SELECT state FROM github_recovery_deliveries")["state"] == "unavailable"
    )
    assert api.posts == [101]


def test_recovered_delivery_still_obeys_current_task_policy(api, fresh_db):
    from fastapi.testclient import TestClient

    from app.extensions import PolicyContribution, PolicyDecision, PolicyEffect, SkeinModule
    from app.main import create_app
    from app.services import work

    def deny(request):
        if request.action == "skein.integration.forge" and request.resource.type == "task":
            return PolicyDecision(PolicyEffect.DENY, ("Forge changes are not permitted.",))
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.6.0",
        policies=(PolicyContribution("acme.workplace.forge", deny),),
    )
    task = work.create_task("Recovery cannot bypass task policy")
    failed = delivery(101)
    api.pages = [[failed]]
    api.payloads[101] = (failed["guid"], "push", push(task["id"]))
    with TestClient(create_app(modules=(module,))) as denied:
        api.client = denied
        api.run()
    assert api.forward_response.status_code == 403
    assert not fresh_db.query("SELECT * FROM forge_receipts")
    assert (
        fresh_db.query_one("SELECT status FROM tasks WHERE id = ?", (task["id"],))["status"]
        == "todo"
    )


def test_recovery_status_and_reconciliation_require_strong_admin(
    api, client, fresh_db, monkeypatch
):
    from app import config
    from app.services import api_keys

    monkeypatch.setattr(config, "ADMINS", ["operator"])
    assert client.get("/api/webhooks/github/recovery").status_code == 403
    auth = {"Authorization": "Bearer " + api_keys.create_key("operator", "fixture")["key"]}
    api.run()
    api.now += timedelta(days=4)
    api.run()
    response = client.get("/api/webhooks/github/recovery", headers=auth)
    assert response.status_code == 200
    assert "fixture-token-never-log" not in response.text and api.url not in response.text
    hook = response.json()["hooks"][0]
    payload = {
        "namespace": hook["namespace"],
        "gap_until": hook["gap_until"],
        "note": "Compared current branches, PRs and task status.",
    }
    assert client.post("/api/webhooks/github/recovery/reconcile", json=payload).status_code == 403
    assert (
        client.post(
            "/api/webhooks/github/recovery/reconcile",
            json={**payload, "gap_until": "outdated"},
            headers=auth,
        ).status_code
        == 400
    )
    assert client.post(
        "/api/webhooks/github/recovery/reconcile", json=payload, headers=auth
    ).json() == {"reconciled": True}
    assert not fresh_db.query("SELECT * FROM forge_receipts")


@pytest.mark.parametrize("effect", ["deny", "review"])
def test_recovery_admin_routes_obey_composed_policy_before_read_or_ack(
    api, fresh_db, monkeypatch, effect
):
    from fastapi.testclient import TestClient

    from app import config, db
    from app.extensions import PolicyContribution, PolicyDecision, PolicyEffect, SkeinModule
    from app.main import create_app
    from app.services import api_keys

    monkeypatch.setattr(config, "ADMINS", ["operator"])
    auth = {"Authorization": "Bearer " + api_keys.create_key("operator", "fixture")["key"]}
    api.run()
    api.now += timedelta(days=4)
    api.run()
    gap = fresh_db.query_one("SELECT * FROM github_recovery_hooks")
    decisions = []

    def refuse(request):
        decisions.append((request, db.in_transaction()))
        return PolicyDecision(PolicyEffect(effect), ("Recovery access is restricted.",))

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.6.0",
        policies=(PolicyContribution("acme.workplace.recovery", refuse),),
    )
    with TestClient(create_app(modules=(module,))) as denied:
        assert denied.get("/api/webhooks/github/recovery", headers=auth).status_code == 403
        assert (
            denied.post(
                "/api/webhooks/github/recovery/reconcile",
                headers=auth,
                json={
                    "namespace": gap["namespace"],
                    "gap_until": gap["gap_until"],
                    "note": "Checked current state.",
                },
            ).status_code
            == 403
        )
    assert [item.action for item, _ in decisions] == [
        "skein.integration.github_recovery.read",
        "skein.integration.github_recovery.reconcile",
    ]
    assert all(item.subject.name == "operator" and item.subject.strong for item, _ in decisions)
    assert decisions[-1][1] is True
    assert decisions[-1][0].resource.id == gap["namespace"]
    assert decisions[-1][0].resource.attributes["repository"] == REPOSITORY.lower()
    assert fresh_db.query_one("SELECT reconciled_at, reconciled_by FROM github_recovery_hooks") == {
        "reconciled_at": "",
        "reconciled_by": "",
    }
    assert not fresh_db.query("SELECT * FROM activity WHERE action = 'reconcile_github_gap'")


def test_recovery_read_checks_each_repository_policy(api, fresh_db, monkeypatch):
    from fastapi.testclient import TestClient

    from app import config
    from app.extensions import PolicyContribution, PolicyDecision, PolicyEffect, SkeinModule
    from app.main import create_app
    from app.services import api_keys

    monkeypatch.setattr(config, "ADMINS", ["operator"])
    auth = {"Authorization": "Bearer " + api_keys.create_key("operator", "fixture")["key"]}
    api.run()

    def refuse(request):
        if request.resource.attributes.get("repository") == REPOSITORY.lower():
            return PolicyDecision(PolicyEffect.DENY, ("This repository is restricted.",))
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.6.0",
        policies=(PolicyContribution("acme.workplace.recovery", refuse),),
    )
    with TestClient(create_app(modules=(module,))) as denied:
        response = denied.get("/api/webhooks/github/recovery", headers=auth)
    assert response.status_code == 403
    assert REPOSITORY.lower() not in response.text


def test_restart_in_a_fresh_process_reads_the_durable_pagination_checkpoint(
    api, fresh_db, monkeypatch
):
    from app.services import github_recovery

    failed = delivery(102)
    api.pages = [[failed], [delivery(101, failed["guid"], code=200)]]
    monkeypatch.setattr(github_recovery, "MAX_PAGES", 1)
    api.run()
    assert fresh_db.query_one("SELECT cursor FROM github_recovery_hooks")["cursor"] == "older"
    assert _run_in_new_process(api)["status"] == "ok"
    assert "cursor=older" in api.requests[-1][1]
    assert not api.posts
    assert fresh_db.query_one("SELECT cursor FROM github_recovery_hooks")["cursor"] == ""


def test_failed_page_attempts_also_spend_the_page_budget(api, fresh_db, monkeypatch):
    from app import config
    from app.services import github_recovery

    monkeypatch.setattr(
        config,
        "GITHUB_HOOKS",
        [{"repository": f"owner/repo-{i}", "hook_id": i + 1} for i in range(10)],
    )
    monkeypatch.setattr(github_recovery, "MAX_PAGES", 2)
    api.get_modes = [503] * 10
    api.run()
    assert len(api.requests) == 2


def test_client_uses_system_ca_trust_without_ambient_proxy_settings(api, fresh_db, monkeypatch):
    import ssl

    import httpx

    from app.services import github_recovery

    original = httpx.Client
    settings = {}

    def capture(**kwargs):
        settings.update(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(github_recovery.httpx, "Client", capture)
    api.run()
    assert settings["trust_env"] is False and settings["follow_redirects"] is False
    assert isinstance(settings.get("verify"), ssl.SSLContext)
    assert settings["verify"].verify_mode == ssl.CERT_REQUIRED


def test_post_budget_gives_each_hook_progress_under_continuous_load(api, fresh_db, monkeypatch):
    from app import config

    monkeypatch.setattr(
        config,
        "GITHUB_HOOKS",
        [
            {"repository": "owner/alpha", "hook_id": 1},
            {"repository": "owner/beta", "hook_id": 2},
        ],
    )
    api.pages_by_repo["owner/beta"] = [[delivery(999)]]
    history = []
    for run in range(4):
        history = [*(delivery(100 + run * 20 + i) for i in range(20)), *history]
        api.pages_by_repo["owner/alpha"] = [history]
        api.run()
        api.now += timedelta(minutes=5)
    assert any(
        method == "POST" and "/repos/owner/beta/" in path for method, path, _ in api.requests
    )
    assert (
        fresh_db.query_one(
            "SELECT COUNT(*) AS n FROM github_recovery_attempts WHERE delivery_id = 999"
        )["n"]
        > 0
    )


def test_drain_rotation_is_fair_with_more_hooks_than_post_slots(api, fresh_db, monkeypatch):
    from app import config
    from app.services import github_recovery

    hooks = [{"repository": f"owner/repo-{i}", "hook_id": i + 1} for i in range(3)]
    monkeypatch.setattr(config, "GITHUB_HOOKS", hooks)
    monkeypatch.setattr(github_recovery, "MAX_POSTS", 1)
    for i, hook in enumerate(hooks):
        api.pages_by_repo[hook["repository"]] = [[delivery(101 + i)]]
    for _ in range(5):
        before = len(api.posts)
        api.run()
        assert len(api.posts) - before <= 1
        api.now += timedelta(minutes=5)
    reached = {
        "/".join(path.split("/")[2:4]) for method, path, _ in api.requests if method == "POST"
    }
    assert reached == {hook["repository"] for hook in hooks}


def test_saved_drain_runs_with_no_get_slots_and_does_not_repeat_attempted_guid(
    api, fresh_db, monkeypatch
):
    from app.services import github_recovery

    api.pages = [[delivery(101), delivery(102)]]
    monkeypatch.setattr(github_recovery, "MAX_POSTS", 1)
    api.run()
    assert api.posts == [101]
    api.now += timedelta(minutes=5)
    monkeypatch.setattr(github_recovery, "MAX_PAGES", 0)
    api.run()
    assert api.posts == [101, 102]
    assert len([method for method, _, _ in api.requests if method == "GET"]) == 1


def test_retained_retry_rearms_unavailable_guid_after_cursor_misses_new_head(
    api, fresh_db, monkeypatch
):
    from app.services import github_recovery

    failed = delivery(101)
    failed["delivered_at"] = (api.now - timedelta(days=3) + timedelta(seconds=80)).isoformat()
    api.pages = [[failed]]
    api.post_modes = ["disconnect"]
    api.run()
    api.now += timedelta(seconds=61)
    head = delivery(201, code=200)
    head["delivered_at"] = api.now.isoformat()
    api.pages = [[head], [failed]]
    monkeypatch.setattr(github_recovery, "MAX_PAGES", 1)
    api.run()
    assert fresh_db.query_one("SELECT cursor FROM github_recovery_hooks")["cursor"] == "older"
    api.now += timedelta(seconds=20)
    retry = delivery(202, failed["guid"], redelivery=True)
    retry["delivered_at"] = api.now.isoformat()
    api.pages = [[retry, head], []]
    api.run()
    assert (
        fresh_db.query_one(
            "SELECT state FROM github_recovery_deliveries WHERE guid = ?", (failed["guid"],)
        )["state"]
        == "unavailable"
    )
    api.now += timedelta(minutes=5)
    monkeypatch.setattr(github_recovery, "MAX_PAGES", 5)
    api.run()
    assert api.posts == [101, 202]
    assert fresh_db.query_one("SELECT gap_since, reconciled_at FROM github_recovery_hooks")[
        "gap_since"
    ]
    assert not fresh_db.query_one("SELECT reconciled_at FROM github_recovery_hooks")[
        "reconciled_at"
    ]


def test_final_page_rechecks_retention_before_advancing_coverage(api, fresh_db):
    api.run()
    baseline = api.now
    missed = delivery(101)
    missed["delivered_at"] = (baseline + timedelta(seconds=1)).isoformat()
    head = delivery(202, code=200)
    api.now = baseline + timedelta(days=3, seconds=-5)
    head["delivered_at"] = api.now.isoformat()
    api.pages = [[head], [missed]]

    def expire_unread_history(path):
        if "cursor=older" in path:
            api.now += timedelta(seconds=10)
            api.pages[1] = []

    api.on_get = expire_unread_history
    result = api.run()
    row = fresh_db.query_one(
        "SELECT last_complete_at, gap_since, gap_until FROM github_recovery_hooks"
    )
    assert result["error_code"] == "HISTORY_GAP"
    assert row["gap_since"] and row["gap_until"]
    assert not api.posts


def test_completed_scan_resumes_drain_after_work_deadline(api, fresh_db, monkeypatch):
    from app.services import github_recovery

    api.pages = [[delivery(101)]]
    original = github_recovery._save_page
    clock = [1000.0]
    monkeypatch.setattr(github_recovery.time, "monotonic", lambda: clock[0])

    def exhaust_after_scan(namespace, scan, rows, cursor):
        original(namespace, scan, rows, cursor)
        clock[0] += github_recovery.MAX_SECONDS + 0.001

    monkeypatch.setattr(github_recovery, "_save_page", exhaust_after_scan)
    for _ in range(3):
        api.run()
        api.now += timedelta(minutes=5)
    assert api.posts == [101]


def test_rate_limit_stops_all_hooks_and_survives_process_restart(api, fresh_db, monkeypatch):
    from app import config

    monkeypatch.setattr(
        config,
        "GITHUB_HOOKS",
        [
            {"repository": "owner/one", "hook_id": 1},
            {"repository": "owner/two", "hook_id": 2},
        ],
    )
    api.get_modes = [429]
    api.rate_headers = {
        "Retry-After": "600",
        "X-RateLimit-Remaining": "0",
        "X-RateLimit-Reset": str(int(api.now.timestamp()) + 1200),
    }
    assert api.run()["error_code"] == "RATE_LIMITED"
    assert len(api.requests) == 1
    api.now += timedelta(seconds=601)
    assert _run_in_new_process(api)["error_code"] == "RATE_LIMITED"
    assert len(api.requests) == 1
    api.now += timedelta(seconds=600)
    assert _run_in_new_process(api)["status"] == "ok"
    assert len(api.requests) == 3
    throttle = fresh_db.query_one("SELECT * FROM github_recovery_api")
    assert "fixture-token-never-log" not in json.dumps(throttle)
    assert api.url not in json.dumps(throttle)
    assert throttle["origin"] == "human" and throttle["created_by"] == "scheduler"


@pytest.mark.parametrize("method, code", [("GET", 403), ("POST", 429)])
def test_rate_reset_and_redelivery_throttles_apply_to_every_hook(
    api, fresh_db, monkeypatch, method, code
):
    from app import config

    monkeypatch.setattr(
        config,
        "GITHUB_HOOKS",
        [
            {"repository": "owner/one", "hook_id": 1},
            {"repository": "owner/two", "hook_id": 2},
        ],
    )
    api.pages = [[delivery(101)]]
    api.rate_headers = {
        "X-RateLimit-Remaining": "0",
        "X-RateLimit-Reset": str(int(api.now.timestamp()) + 600),
    }
    if method == "GET":
        api.get_modes = [code]
    else:
        api.post_modes = [code]
    assert api.run()["error_code"] == "RATE_LIMITED"
    count = len(api.requests)
    assert len(api.posts) == (1 if method == "POST" else 0)
    assert api.run()["error_code"] == "RATE_LIMITED"
    assert len(api.requests) == count


def test_retry_after_http_date_is_not_rounded_to_an_earlier_instant(api, fresh_db):
    from email.utils import format_datetime

    retry_at = api.now + timedelta(minutes=10)
    api.now += timedelta(microseconds=500_000)
    api.get_modes = [429]
    api.rate_headers = {"Retry-After": format_datetime(retry_at, usegmt=True)}
    api.run()
    row = fresh_db.query_one("SELECT retry_at FROM github_recovery_api")
    assert datetime.fromisoformat(row["retry_at"]) >= retry_at


def test_success_at_primary_rate_limit_defers_post_and_other_hooks(api, fresh_db, monkeypatch):
    from app import config

    monkeypatch.setattr(
        config,
        "GITHUB_HOOKS",
        [
            {"repository": "owner/one", "hook_id": 1},
            {"repository": "owner/two", "hook_id": 2},
        ],
    )
    api.pages = [[delivery(101)]]
    api.response_headers = {
        "X-RateLimit-Remaining": "0",
        "X-RateLimit-Reset": str(int(api.now.timestamp()) + 600),
    }
    assert api.run()["error_code"] == "RATE_LIMITED"
    assert len(api.requests) == 1
    assert not api.posts
    assert not fresh_db.query("SELECT * FROM github_recovery_attempts")


def test_disabled_or_unconfigured_is_keyless_and_does_not_open_http(fresh_db, monkeypatch):
    from app import config
    from app.services import github_recovery

    monkeypatch.setattr(config, "GITHUB_RECOVERY", False)
    assert github_recovery.run()["enabled"] is False
    monkeypatch.setattr(config, "GITHUB_RECOVERY", True)
    monkeypatch.setattr(config, "GITHUB_TOKEN", "")
    assert github_recovery.run()["error_code"] == "NOT_CONFIGURED"
    assert not fresh_db.query("SELECT * FROM github_recovery_hooks")
