"""Prove the Atlas boundary through the public extension surfaces only.

These tests are the reference pattern for a private extension repository:
they import `app.extensions`, `app.public`, and `app.main.create_app`, and
nothing else from the core. They cover the categories that
docs/EXTENSIONS.md requires: registration, policy, tool gating, event
idempotency, provenance, data ownership, and disabling the extension.
"""

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.extensions import (
    EventExecutionContext,
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
from app.public import WorkItems, dispatch_events

AGENT = "atlas.workplace.delivery-specialist"
ATLAS_SOURCE = Path(__file__).resolve().parents[1] / "src" / "atlas_skein"


def test_atlas_imports_only_published_backend_contracts():
    assert_import_boundary(ATLAS_SOURCE)


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
    assert registry.tool("atlas.workplace.sync-tool").effect == "write"
    assert registry.specialist(AGENT).tools == ("atlas.workplace.sync-tool",)
    assert {item.name for item in registry.events} >= {"atlas.workplace.task-events"}
    assert {item.name for item in registry.migrations} >= {"atlas.workplace.data"}
    assert {item.name for item in registry.workflow_actions} >= {
        "atlas.workplace.notify-manager"
    }


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
        policies=(
            PolicyContribution("testlab.workplace.review-sync", ReviewAgentSync()),
        ),
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


def test_task_events_deliver_once_per_subscriber(atlas):
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
        context = EventExecutionContext(
            engine, WorkItems(engine), registry.service_subject
        )
        delivered_before = len(client.updates)
        counts = dispatch_events(registry.events, context)
        assert counts["failed"] == 0
        assert len(client.updates) > delivered_before
        after_first_dispatch = len(client.updates)
        again = dispatch_events(registry.events, context)
        assert again == {"delivered": 0, "failed": 0, "dead": 0}
        assert len(client.updates) == after_first_dispatch


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

    from atlas_skein.integration import AtlasHttpClient, AtlasUnavailableError

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
        with pytest.raises(AtlasUnavailableError):
            client.list_items()
    finally:
        server.shutdown()
