"""Prove the Atlas boundary through the public extension surfaces only.

The Atlas source imports only `app.extensions`, `app.public`, and
`app.main.create_app`. These tests also exercise the installed host error
wrappers. They cover registration, policy, tool gating, events, provenance,
data ownership, and extension removal.
"""

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from atlas_skein.policy import atlas_directory
from fastapi.testclient import TestClient

from app.extensions import (
    AppSettings,
    EventExecutionContext,
    ExtensionRegistry,
    ExtensionStore,
    PolicyEffect,
    PolicyInput,
    PolicyResource,
    PolicySubject,
    ToolCallContext,
    assert_import_boundary,
    execute_tool,
    registry_for,
)
from app.main import create_app
from app.public import DomainEvent, EventActor, ResourceReference, WorkItems, dispatch_events

AGENT = "atlas.workplace.delivery-specialist"
ATLAS_SOURCE = Path(__file__).resolve().parents[1] / "src" / "atlas_skein"


def test_atlas_imports_only_published_backend_contracts():
    assert_import_boundary(ATLAS_SOURCE)


def test_directory_refresh_fails_closed_without_a_private_adapter():
    assert atlas_directory("mira") is None


def _sync_tool_result(app, arguments=None, *, agent=AGENT, origin="agent_tool"):
    registry = registry_for(app)
    return asyncio.run(
        execute_tool(
            registry.tool("atlas.workplace.sync-tool"),
            arguments or {},
            ToolCallContext(
                registry.service_subject("atlas-sync"),
                agent,
                origin=origin,
            ),
            registry.policy_engine,
        )
    )


def test_every_atlas_contribution_composes(atlas):
    module, _client = atlas
    registry = registry_for(create_app(modules=(module,)))
    assert {item.name for item in registry.routes} >= {"atlas.workplace.routes"}
    assert {item.name for item in registry.jobs} >= {"atlas.workplace.sync"}
    assert {item.name for item in registry.policies} >= {"atlas.workplace.policy"}
    assert {item.subject for item in registry.service_identities} >= {
        "atlas-sync",
        "atlas-events",
    }
    sync_tool = registry.tool("atlas.workplace.sync-tool")
    assert sync_tool.effect == "write"
    assert sync_tool.error_codes == ("ATLAS_UNAVAILABLE", "ATLAS_BAD_RESPONSE")
    assert registry.specialist(AGENT).tools == ("atlas.workplace.sync-tool",)
    assert {item.name for item in registry.events} >= {"atlas.workplace.task-events"}
    assert {item.name for item in registry.migrations} >= {"atlas.workplace.data"}
    assert {item.name for item in registry.workflow_actions} >= {"atlas.workplace.notify-manager"}


def test_disabling_the_extension_leaves_core_intact(atlas):
    module, _client = atlas
    with TestClient(create_app(modules=(module,)), headers={"X-User": "mira"}) as on:
        assert on.get("/health").status_code == 200
    with TestClient(create_app(), headers={"X-User": "mira"}) as off:
        assert off.get("/health").status_code == 200
        missing = off.get("/api/extensions/atlas.workplace/metrics")
        assert missing.status_code == 404


def test_backend_denies_sync_without_the_integration_capability(atlas):
    module, client = atlas
    with TestClient(create_app(modules=(module,)), headers={"X-User": "ava"}) as api:
        response = api.post("/api/extensions/atlas.workplace/sync")
    assert response.status_code == 403
    assert client.updates == []


@pytest.mark.parametrize(
    ("failure", "status", "code", "retryable"),
    (
        ("unavailable", 503, "ATLAS_UNAVAILABLE", True),
        ("bad", 502, "ATLAS_BAD_RESPONSE", False),
    ),
)
def test_sync_route_preserves_remote_failure_contract(
    monkeypatch, failure, status, code, retryable
):
    from app import oidc
    from atlas_skein.integration import (
        AtlasBadResponseError,
        AtlasUnavailableError,
        MemoryAtlasClient,
    )
    from atlas_skein.module import AtlasSettings, atlas_module

    error = (
        AtlasUnavailableError("private transport details")
        if failure == "unavailable"
        else AtlasBadResponseError("private response details")
    )

    class BrokenAtlas(MemoryAtlasClient):
        def list_items(self):
            raise error

    monkeypatch.setattr(oidc, "validate", lambda token: {"token": token})
    monkeypatch.setattr(
        oidc, "identity", lambda _claims: ("https://idp.test", "subject:nina")
    )
    monkeypatch.setattr(
        oidc,
        "principal",
        lambda _claims: ("nina", ["atlas-integrations"]),
    )
    settings = replace(
        AppSettings.from_config(),
        scheduler_enabled=False,
        auth_mode="oidc",
        auth_error="",
        api_token="",
        docs_enabled=False,
    )
    module = atlas_module(
        AtlasSettings(f"atlas-route-{failure}"),
        BrokenAtlas(),
    )
    with TestClient(
        create_app(settings, (module,)),
        headers={"Authorization": "Bearer integration-token"},
    ) as http:
        response = http.post("/api/extensions/atlas.workplace/sync", json={"full": False})
    assert response.status_code == status
    assert response.json()["code"] == code
    assert response.json()["retryable"] is retryable
    assert response.headers.get("retry-after") == ("60" if retryable else None)
    assert "private" not in response.text


def test_sync_job_preserves_remote_failure_code(caplog):
    from app.main import _job_specs
    from atlas_skein.integration import AtlasUnavailableError, MemoryAtlasClient
    from atlas_skein.module import AtlasSettings, atlas_module

    class BrokenAtlas(MemoryAtlasClient):
        def list_items(self):
            raise AtlasUnavailableError("private transport details")

    module = atlas_module(
        AtlasSettings("atlas-job-error"),
        BrokenAtlas(),
    )
    registry = ExtensionRegistry.build((module,))
    spec = next(
        item
        for item in _job_specs(registry, AppSettings.from_config())
        if item.name == "atlas.workplace.sync"
    )
    assert spec.fn() == {
        "status": "error",
        "error_code": "ATLAS_UNAVAILABLE",
        "retryable": True,
    }
    assert "private transport details" not in caplog.text


def test_policy_review_names_the_delivery_approvers(atlas):
    module, _client = atlas
    engine = registry_for(create_app(modules=(module,))).policy_engine
    decision = engine.decide(
        PolicyInput(
            PolicySubject("ava", kind="human", strong=True, source="oidc"),
            "atlas.release.approve",
            PolicyResource("atlas-release", project_type="regulated"),
            "human",
            tool_risk="high",
        )
    )
    assert decision.effect == PolicyEffect.REVIEW
    assert decision.approver_groups == ("atlas-delivery-managers",)
    assert decision.approver_capabilities == ("atlas.approve",)


def test_a_workplace_review_rule_holds_the_sync_tool_for_approval(atlas):
    """Compose Atlas beside a second workplace module. Its review rule must
    stop the write and store a durable proposal before the handler runs."""
    from dataclasses import dataclass

    from app.extensions import PolicyContribution, PolicyDecision, SkeinModule

    @dataclass(frozen=True)
    class ReviewAgentSync:
        skein_policy_actions: tuple[str, ...] = ("atlas.integration.sync",)

        def __call__(self, request: PolicyInput) -> PolicyDecision | None:
            if request.origin != "agent_tool":
                return None
            return PolicyDecision(
                PolicyEffect.REVIEW,
                ("A person approves agent-driven Atlas synchronization.",),
            )

    review_module = SkeinModule(
        module_id="testlab.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.4.0",
        policies=(PolicyContribution("testlab.workplace.review-sync", ReviewAgentSync()),),
    )
    module, client = atlas
    app = create_app(modules=(module, review_module))
    with TestClient(app):
        result = _sync_tool_result(app)
    assert result.status == "review_required"
    assert result.review_id > 0
    assert client.updates == []


def test_wrong_agent_cannot_call_the_sync_tool(atlas):
    module, client = atlas
    app = create_app(modules=(module,))
    with TestClient(app):
        result = _sync_tool_result(app, agent="other-agent")
    assert result.status == "refused"
    assert result.error_code == "agent_not_allowed"
    assert client.updates == []


def test_invalid_tool_input_is_refused_by_the_schema(atlas):
    module, client = atlas
    app = create_app(modules=(module,))
    with TestClient(app):
        result = _sync_tool_result(app, arguments={"full": "not-a-boolean"})
    assert result.status == "refused"
    assert result.error_code == "invalid_input"
    assert client.updates == []


def test_permitted_sync_writes_with_service_provenance(atlas):
    module, client = atlas
    app = create_app(modules=(module,))
    with TestClient(app, headers={"X-User": "mira"}) as api:
        result = _sync_tool_result(app, origin="background")
        assert result.status == "completed"
        assert result.output == {"created": 2, "updated": 0}
        store = ExtensionStore(module.migrations[0].store.name)
        links = store.query("SELECT skein_task_id FROM work_links ORDER BY external_id")
        assert len(links) == 2
        task = api.get(f"/api/tasks/{links[0]['skein_task_id']}").json()
        assert task["origin"].startswith("extension:")
        # The writer of record is the executing agent, not the requester.
        assert task["created_by"] == AGENT
        # The remote received each status once, keyed for idempotent retries.
        assert len(client.updates) == 2


def test_sync_originated_task_events_do_not_echo_status(atlas):
    module, client = atlas
    app = create_app(modules=(module,))
    with TestClient(app, headers={"X-User": "mira"}):
        first = _sync_tool_result(app, origin="background")
        assert first.status == "completed"
        client.items = tuple(
            type(item)(item.external_id, item.title, "in_progress", item.classification)
            for item in client.items
        )
        second = _sync_tool_result(app, origin="background")
        assert second.status == "completed"
        assert second.output == {"created": 0, "updated": 2}
        registry = registry_for(app)
        engine = registry.policy_engine
        context = EventExecutionContext(engine, WorkItems(engine), registry.service_subject)
        delivered_before = len(client.updates)
        counts = dispatch_events(registry.events, context)
        assert counts["failed"] == 0
        assert len(client.updates) == delivered_before
        again = dispatch_events(registry.events, context)
        assert again == {"delivered": 0, "failed": 0, "dead": 0}
        assert len(client.updates) == delivered_before

        task_id = module.migrations[0].store.query(
            "SELECT skein_task_id FROM work_links ORDER BY external_id LIMIT 1"
        )[0]["skein_task_id"]
        from types import SimpleNamespace

        class ReportingWorkItems:
            def get_task(self, *_args):
                return SimpleNamespace(status="in_progress")

        class ReportingContext:
            delivery_id = "reporting-delivery"
            work_items = ReportingWorkItems()

            def command_context(self):
                return object()

        registry.events[0].handler(
            DomainEvent(
                event_id="reporting-event",
                event_type="skein.task.updated",
                timestamp="2026-08-28T00:00:00+00:00",
                actor=EventActor(name="reporter", kind="service"),
                origin="extension:atlas.workplace.reporting",
                resource=ResourceReference(type="task", id=str(task_id)),
            ),
            ReportingContext(),
        )
        assert client.updates[-1][2] == "reporting-delivery"


def test_status_delivery_commits_before_the_remote_call_waits(atlas):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from atlas_skein.integration import AtlasIntegration, MemoryAtlasClient

    module, _client = atlas
    store = module.migrations[0].store
    started = Event()
    release = Event()

    class InspectingClient(MemoryAtlasClient):
        transaction_id = None

        def update_status(self, external_id: str, status: str, event_id: str = "") -> None:
            store.execute(
                "UPDATE status_outbox SET status = 'sending' WHERE event_id = ?",
                (event_id,),
            )
            self.transaction_id = store.query_one("SELECT txid_current_if_assigned() AS id")["id"]
            started.set()
            release.wait(2)
            super().update_status(external_id, status, event_id)

    client = InspectingClient()
    with TestClient(create_app(modules=(module,))):
        store.execute(
            "INSERT INTO status_outbox"
            " (event_id, external_id, status, delivered) VALUES ('visible', 'A', 'todo', 0)"
        )
        integration = AtlasIntegration(client, store)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(integration._deliver_pending_statuses)
            assert started.wait(2)
            try:
                assert client.transaction_id is None
                assert store.query_one(
                    "SELECT status FROM status_outbox WHERE event_id = 'visible'"
                ) == {"status": "sending"}
            finally:
                release.set()
            future.result(timeout=2)
    assert store.query_one("SELECT delivered FROM status_outbox WHERE event_id = 'visible'") == {
        "delivered": 1
    }


def test_status_delivery_leases_keep_each_item_in_order(atlas):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, Lock

    from atlas_skein.integration import AtlasIntegration, MemoryAtlasClient

    module, _client = atlas
    store = module.migrations[0].store
    first_started = Event()
    other_item_sent = Event()
    release = Event()
    lock = Lock()
    calls: list[str] = []

    class OrderedClient(MemoryAtlasClient):
        def update_status(self, external_id: str, status: str, event_id: str = "") -> None:
            with lock:
                calls.append(event_id)
            if event_id == "a-1":
                first_started.set()
                release.wait(3)
            elif event_id == "b-1":
                other_item_sent.set()
            super().update_status(external_id, status, event_id)

    with TestClient(create_app(modules=(module,))):
        for event_id, external_id, status in (
            ("a-1", "A", "todo"),
            ("a-2", "A", "done"),
            ("b-1", "B", "blocked"),
        ):
            store.execute(
                "INSERT INTO status_outbox"
                " (event_id, external_id, status, delivered) VALUES (?, ?, ?, 0)",
                (event_id, external_id, status),
            )
        integration = AtlasIntegration(OrderedClient(), store)
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(integration._deliver_pending_statuses)
            assert first_started.wait(2)
            second = executor.submit(integration._deliver_pending_statuses)
            try:
                assert other_item_sent.wait(2)
                assert calls == ["a-1", "b-1"]
            finally:
                release.set()
            first.result(timeout=3)
            second.result(timeout=3)
    assert calls == ["a-1", "b-1", "a-2"]
    assert store.query_one("SELECT COUNT(*) AS count FROM status_outbox WHERE delivered = 0") == {
        "count": 0
    }


def test_task_event_waits_behind_an_older_leased_status(atlas):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from types import SimpleNamespace

    from atlas_skein.integration import (
        AtlasIntegration,
        AtlasUnavailableError,
        MemoryAtlasClient,
    )

    module, _client = atlas
    store = module.migrations[0].store
    older_started = Event()
    release = Event()
    calls: list[str] = []

    class OrderedClient(MemoryAtlasClient):
        def update_status(self, external_id: str, status: str, event_id: str = "") -> None:
            calls.append(event_id)
            if event_id == "older-status":
                older_started.set()
                release.wait(3)
            super().update_status(external_id, status, event_id)

    class CurrentWorkItems:
        def get_task(self, *_args):
            return SimpleNamespace(status="done")

    class CurrentContext:
        delivery_id = "task-event-done"
        work_items = CurrentWorkItems()

        def command_context(self):
            return object()

    with TestClient(create_app(modules=(module,))):
        store.execute(
            "INSERT INTO work_links"
            " (external_id, skein_task_id, classification) VALUES ('A', 99, 'internal')"
        )
        store.execute(
            "INSERT INTO status_outbox"
            " (event_id, external_id, status, delivered)"
            " VALUES ('older-status', 'A', 'todo', 0)"
        )
        integration = AtlasIntegration(OrderedClient(), store)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(integration._deliver_pending_statuses)
            assert older_started.wait(2)
            try:
                with pytest.raises(AtlasUnavailableError):
                    integration.deliver_task_event(
                        DomainEvent(
                            event_id="task-event",
                            event_type="skein.task.updated",
                            timestamp="2026-08-28T00:00:00+00:00",
                            actor=EventActor(name="mira", kind="human"),
                            origin="human",
                            resource=ResourceReference(type="task", id="99"),
                        ),
                        CurrentContext(),
                    )
                assert calls == ["older-status"]
            finally:
                release.set()
            future.result(timeout=3)
    assert calls == ["older-status", "task-event-done"]


def test_expired_status_lease_retries_after_a_crash(atlas):
    from atlas_skein.integration import AtlasIntegration, MemoryAtlasClient

    module, _client = atlas
    store = module.migrations[0].store
    client = MemoryAtlasClient()
    integration = AtlasIntegration(client, store)
    with TestClient(create_app(modules=(module,))):
        store.execute(
            "INSERT INTO status_outbox"
            " (event_id, external_id, status, delivered) VALUES ('expired', 'A', 'todo', 0)"
        )
        claim = integration._claim_pending_status()
        assert claim is not None
        integration._deliver_pending_statuses()
        assert client.updates == []
        store.execute(
            "UPDATE status_outbox SET lease_until = now() - INTERVAL '1 second'"
            " WHERE event_id = 'expired'"
        )
        integration._deliver_pending_statuses()
    assert client.updates == [("A", "todo", "expired")]
    assert store.query_one(
        "SELECT delivered, lease_token, lease_until FROM status_outbox WHERE event_id = 'expired'"
    ) == {"delivered": 1, "lease_token": "", "lease_until": None}


def test_status_delivery_drain_is_bounded(atlas):
    import atlas_skein.integration as integration_module
    from atlas_skein.integration import AtlasIntegration, MemoryAtlasClient

    module, _client = atlas
    store = module.migrations[0].store
    client = MemoryAtlasClient()
    integration = AtlasIntegration(client, store)
    total = integration_module.MAX_STATUS_DELIVERIES + 1
    with TestClient(create_app(modules=(module,))):
        for index in range(total):
            store.execute(
                "INSERT INTO status_outbox"
                " (event_id, external_id, status, delivered) VALUES (?, ?, 'todo', 0)",
                (f"bounded-{index:03}", f"ITEM-{index:03}"),
            )
        integration._deliver_pending_statuses()
        assert len(client.updates) == integration_module.MAX_STATUS_DELIVERIES
        integration._deliver_pending_statuses()
    assert len(client.updates) == total


def test_bad_status_does_not_block_other_items(atlas):
    from atlas_skein.integration import (
        AtlasBadResponseError,
        AtlasIntegration,
        MemoryAtlasClient,
    )

    module, _client = atlas
    store = module.migrations[0].store
    calls: list[str] = []

    class RejectingClient(MemoryAtlasClient):
        def update_status(self, external_id: str, status: str, event_id: str = "") -> None:
            calls.append(event_id)
            if event_id == "a-bad":
                raise AtlasBadResponseError("rejected")
            super().update_status(external_id, status, event_id)

    with TestClient(create_app(modules=(module,))):
        for event_id, external_id, status in (
            ("a-bad", "A", "todo"),
            ("a-next", "A", "done"),
            ("b-next", "B", "blocked"),
        ):
            store.execute(
                "INSERT INTO status_outbox"
                " (event_id, external_id, status, delivered) VALUES (?, ?, ?, 0)",
                (event_id, external_id, status),
            )
        with pytest.raises(AtlasBadResponseError):
            AtlasIntegration(RejectingClient(), store)._deliver_pending_statuses()
    assert calls == ["a-bad", "a-next", "b-next"]
    assert store.query_one(
        "SELECT dead, error_code FROM status_outbox WHERE event_id = 'a-bad'"
    ) == {"dead": 1, "error_code": "ATLAS_BAD_RESPONSE"}
    assert store.query_one("SELECT COUNT(*) AS count FROM status_outbox WHERE delivered = 1") == {
        "count": 2
    }


def test_temporary_status_failure_backs_off_without_blocking_other_items(atlas):
    from atlas_skein.integration import (
        AtlasIntegration,
        AtlasUnavailableError,
        MemoryAtlasClient,
    )

    module, _client = atlas
    store = module.migrations[0].store
    calls: list[str] = []
    fail = True

    class UnavailableClient(MemoryAtlasClient):
        def update_status(self, external_id: str, status: str, event_id: str = "") -> None:
            nonlocal fail
            calls.append(event_id)
            if event_id == "a-retry" and fail:
                fail = False
                raise AtlasUnavailableError("unavailable")
            super().update_status(external_id, status, event_id)

    client = UnavailableClient()
    integration = AtlasIntegration(client, store)
    with TestClient(create_app(modules=(module,))):
        for event_id, external_id, status in (
            ("a-retry", "A", "todo"),
            ("a-next", "A", "done"),
            ("b-next", "B", "blocked"),
        ):
            store.execute(
                "INSERT INTO status_outbox"
                " (event_id, external_id, status, delivered) VALUES (?, ?, ?, 0)",
                (event_id, external_id, status),
            )
        with pytest.raises(AtlasUnavailableError):
            integration._deliver_pending_statuses()
        assert calls == ["a-retry", "b-next"]
        with pytest.raises(AtlasUnavailableError):
            integration._deliver_pending_statuses()
        assert calls == ["a-retry", "b-next"]
        store.execute(
            "UPDATE status_outbox SET lease_until = now() - INTERVAL '1 second'"
            " WHERE event_id = 'a-retry'"
        )
        integration._deliver_pending_statuses()
    assert calls == ["a-retry", "b-next", "a-retry", "a-next"]
    assert store.query_one("SELECT COUNT(*) AS count FROM status_outbox WHERE delivered = 1") == {
        "count": 3
    }


def test_status_delivery_has_a_wall_clock_drain_bound(atlas, monkeypatch):
    from time import monotonic, sleep

    import atlas_skein.integration as integration_module
    from atlas_skein.integration import (
        AtlasIntegration,
        AtlasUnavailableError,
        MemoryAtlasClient,
    )

    module, _client = atlas
    store = module.migrations[0].store

    class SlowClient(MemoryAtlasClient):
        def update_status(self, external_id: str, status: str, event_id: str = "") -> None:
            sleep(0.2)
            super().update_status(external_id, status, event_id)

    monkeypatch.setattr(integration_module, "STATUS_DRAIN_SECONDS", 0.05)
    with TestClient(create_app(modules=(module,))):
        store.execute(
            "INSERT INTO status_outbox"
            " (event_id, external_id, status, delivered)"
            " VALUES ('slow', 'ITEM', 'todo', 0)"
        )
        started = monotonic()
        with pytest.raises(AtlasUnavailableError):
            AtlasIntegration(SlowClient(), store)._deliver_pending_statuses()
        assert monotonic() - started < 0.15


def test_pending_statuses_upgrade_to_leased_delivery(atlas):
    module, _client = atlas
    contribution = module.migrations[0]
    contribution.store.migrate(contribution.migrations[:4])
    contribution.store.execute(
        "INSERT INTO status_outbox"
        " (event_id, external_id, status, delivered) VALUES ('pending-v4', 'A', 'todo', 0)"
    )
    contribution.store.migrate(contribution.migrations)
    assert contribution.store.query_one(
        "SELECT sequence_id, lease_token, lease_until, dead, error_code"
        " FROM status_outbox WHERE event_id = 'pending-v4'"
    ) == {
        "sequence_id": 1,
        "lease_token": "",
        "lease_until": None,
        "dead": 0,
        "error_code": "",
    }


def test_the_extension_store_cannot_name_a_core_schema(atlas):
    module, _client = atlas
    # An extension named `public` or `private` must land in a prefixed schema
    # of its own, never in the core tables or the 1:1 notes.
    assert ExtensionStore("public").schema == "ext_public"
    assert ExtensionStore("private").schema == "ext_private"
    with pytest.raises(ValueError, match="starts with a letter"):
        ExtensionStore("7atlas")
    assert module.migrations[0].store.schema.startswith("ext_")


def test_the_http_client_refuses_bearer_tokens_over_plaintext():
    """The adapter sends Authorization on every request. A plaintext
    endpoint hands the token to any network-positioned reader."""
    from atlas_skein.integration import AtlasHttpClient

    with pytest.raises(ValueError, match="HTTPS"):
        AtlasHttpClient("http://atlas.internal:8080", "secret")
    assert AtlasHttpClient("https://atlas.example", "secret").endpoint == "https://atlas.example"
    local = AtlasHttpClient("http://localhost:8080", "secret")
    assert local.endpoint == "http://localhost:8080"


def test_the_http_client_refuses_redirected_authenticated_requests():
    """A redirect can move the Authorization header to another origin or
    downgrade it to plaintext."""
    import http.server
    import threading

    from atlas_skein.integration import AtlasBadResponseError, AtlasHttpClient

    class Redirector(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://attacker.example/items")
            self.end_headers()

        def log_message(self, *_args):
            return None

    server = http.server.HTTPServer(("127.0.0.1", 0), Redirector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = AtlasHttpClient(f"http://127.0.0.1:{server.server_port}", "secret")
        with pytest.raises(AtlasBadResponseError):
            client.list_items()
    finally:
        server.shutdown()


def test_redirect_refusal_closes_the_upstream_response():
    from urllib.request import Request

    import atlas_skein.integration as integration

    class Response:
        closed = False

        def close(self):
            self.closed = True

    response = Response()
    with pytest.raises(integration.AtlasBadResponseError):
        integration._RefuseRedirect().http_error_302(
            Request("https://atlas.example/items"),
            response,
            302,
            "Found",
            {"location": "https://other.example/items"},
        )
    assert response.closed


def test_the_http_client_caps_item_responses_before_parsing(monkeypatch):
    import atlas_skein.integration as integration
    from atlas_skein.integration import AtlasBadResponseError, AtlasHttpClient

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size):
            assert size == integration.MAX_RESPONSE_BYTES + 1
            return b"x" * size

    monkeypatch.setattr(
        integration._NO_REDIRECT_OPENER,
        "open",
        lambda *_args, **_kwargs: Response(),
    )
    client = AtlasHttpClient("http://127.0.0.1:8080", "secret")
    with pytest.raises(AtlasBadResponseError):
        client.list_items()


def test_the_http_client_bounds_trickled_response_time(monkeypatch):
    import atlas_skein.integration as integration

    clock = [0.0]

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            return b"[]"

        def read1(self, _size):
            clock[0] += 0.06
            return b"x"

    monkeypatch.setattr(integration, "monotonic", lambda: clock[0], raising=False)
    monkeypatch.setattr(
        integration._NO_REDIRECT_OPENER,
        "open",
        lambda *_args, **_kwargs: Response(),
    )
    client = integration.AtlasHttpClient(
        "http://127.0.0.1:8080",
        "secret",
        timeout_seconds=0.1,
    )
    with pytest.raises(integration.AtlasUnavailableError):
        client.list_items()


def test_the_http_client_bounds_slow_response_headers():
    import socketserver
    import threading
    from time import monotonic, sleep

    import atlas_skein.integration as integration

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    class SlowHeaders(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.recv(4096)
            response = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n[]"
            for byte in response:
                try:
                    self.request.sendall(bytes((byte,)))
                except OSError:
                    break
                sleep(0.02)

    server = Server(("127.0.0.1", 0), SlowHeaders)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    started = monotonic()
    try:
        client = integration.AtlasHttpClient(
            f"http://127.0.0.1:{server.server_address[1]}",
            "secret",
            timeout_seconds=0.05,
        )
        with pytest.raises(integration.AtlasUnavailableError):
            client.list_items()
        assert monotonic() - started < 0.3
    finally:
        server.shutdown()
        server.server_close()


def test_the_http_client_classifies_deep_json_as_a_bad_response(monkeypatch):
    import atlas_skein.integration as integration

    class Response:
        payload = (b"[" * 20_000) + (b"]" * 20_000)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            payload, self.payload = self.payload, b""
            return payload

    monkeypatch.setattr(
        integration._NO_REDIRECT_OPENER,
        "open",
        lambda *_args, **_kwargs: Response(),
    )
    client = integration.AtlasHttpClient("http://127.0.0.1:8080", "secret")
    with pytest.raises(integration.AtlasBadResponseError):
        client.list_items()


@pytest.mark.parametrize(
    "payload",
    (
        {},
        {"items": "not-a-list"},
        [None],
        [{"external_id": "A", "title": "   "}],
        [{"external_id": "\x00", "title": "One"}],
        [{"external_id": "A", "title": "One\x00"}],
        [{"external_id": "A", "title": "\x1b]52;c;YXR0YWNr\x07"}],
        [{"external_id": "A", "title": "One", "classification": "\x00"}],
        [{"external_id": "A", "title": "One", "extra": True}],
        [
            {"external_id": "A", "title": "One"},
            {"external_id": "A", "title": "Two"},
        ],
        [{"external_id": "A", "title": "One", "status": "unknown"}],
        [{"external_id": "A", "title": "One"}] * 501,
    ),
)
def test_the_http_client_rejects_invalid_item_payloads(monkeypatch, payload):
    from atlas_skein.integration import AtlasBadResponseError, AtlasHttpClient

    client = AtlasHttpClient("http://127.0.0.1:8080", "secret")
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: payload)
    with pytest.raises(AtlasBadResponseError):
        client.list_items()


@pytest.mark.parametrize(
    ("status", "error"),
    (
        (408, "AtlasUnavailableError"),
        (425, "AtlasUnavailableError"),
        (429, "AtlasUnavailableError"),
        (501, "AtlasUnavailableError"),
        (503, "AtlasUnavailableError"),
        (400, "AtlasBadResponseError"),
        (404, "AtlasBadResponseError"),
        (600, "AtlasBadResponseError"),
    ),
)
def test_the_http_client_classifies_upstream_http_errors(monkeypatch, status, error):
    import atlas_skein.integration as integration

    failure = __import__("urllib.error").error.HTTPError(
        "https://atlas.example/items",
        status,
        "failed",
        {},
        __import__("io").BytesIO(b"failed"),
    )
    monkeypatch.setattr(
        integration._NO_REDIRECT_OPENER,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )
    client = integration.AtlasHttpClient("https://atlas.example", "secret")
    with pytest.raises(getattr(integration, error)):
        client.list_items()
    assert failure.closed
