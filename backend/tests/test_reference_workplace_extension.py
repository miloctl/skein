"""Executable scenarios for the fictional private Atlas package."""

from __future__ import annotations

import ast
import sys
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "workplace-extension"
SOURCE = EXAMPLE / "backend" / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from atlas_skein import AtlasSettings, atlas_module  # noqa: E402
from atlas_skein.integration import (  # noqa: E402
    AtlasHttpClient,
    AtlasIntegration,
    AtlasItem,
    AtlasUnavailableError,
    MemoryAtlasClient,
)

from app.extensions import (  # noqa: E402
    AppSettings,
    EventExecutionContext,
    ExtensionRegistry,
    ExtensionStore,
    JobExecutionContext,
    PolicyContribution,
    PolicyDecision,
    PolicyEffect,
    PolicyInput,
    PolicyResource,
    PolicySubject,
    SkeinModule,
)
from app.main import create_app  # noqa: E402
from app.public import WorkItems  # noqa: E402
from app.public.events import dispatch_events  # noqa: E402


def _module(tmp_path, client=None):
    return atlas_module(
        AtlasSettings("atlas-extension"),
        client,
    )


def test_reference_package_imports_only_public_skein_modules():
    allowed = {"app.extensions", "app.public", "app.main"}
    offenders = []
    for path in sorted((EXAMPLE / "backend" / "src").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app."):
                module = node.module or ""
                if module not in allowed:
                    offenders.append(f"{path.name}: {module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("app.") and alias.name not in allowed:
                        offenders.append(f"{path.name}: {alias.name}")
    assert offenders == []


def test_reference_manifest_matches_the_composed_module(tmp_path):
    manifest = tomllib.loads((EXAMPLE / "extension.toml").read_text(encoding="utf-8"))["extension"]
    module = _module(tmp_path)
    assert manifest == {
        "id": module.module_id,
        "version": module.version,
        "extension_api": module.extension_api,
        "frontend_extension_api": "1.0",
        "minimum_core": module.minimum_core,
        "maximum_core_exclusive": module.maximum_core_exclusive,
    }


def test_reference_module_exercises_each_supported_backend_contribution(tmp_path):
    registry = ExtensionRegistry.build((_module(tmp_path),))
    assert len(registry.routes) == 1
    assert len(registry.jobs) == 1
    assert len(registry.policies) == 1
    assert len(registry.identities) == 2
    assert len(registry.service_identities) == 2
    assert len(registry.contexts) == 1
    assert len(registry.tools) == 1
    assert len(registry.specialists) == 1
    assert len(registry.events) == 1
    assert len(registry.migrations) == 1
    assert len(registry.workflow_actions) == 1
    assert tuple(registry.policies[0].rule.skein_policy_actions) == (
        "atlas.dashboard.view",
        "atlas.integration.sync",
        "atlas.release.approve",
    )


def test_service_identities_cannot_be_claimed_by_humans(fresh_db, tmp_path):
    from app.services.users import ensure_user

    ensure_user("atlas-sync", kind="human")
    settings = replace(AppSettings.from_config(), scheduler_enabled=False)
    with (
        pytest.raises(RuntimeError, match="already owned by a human"),
        TestClient(create_app(settings, (_module(tmp_path),))),
    ):
        pass


def test_human_identity_mapping_never_grants_service_capabilities(tmp_path):
    registry = ExtensionRegistry.build((_module(tmp_path),))
    human = registry.identity_attributes("atlas-sync", (), True)
    service = registry.service_subject("atlas-sync")
    assert "atlas.integration" not in human["capabilities"]
    assert service.kind == "service"
    assert service.capabilities == ("atlas.integration",)


def test_enterprise_adapter_syncs_both_directions_through_public_work(fresh_db, tmp_path):
    client = MemoryAtlasClient((AtlasItem("ATLAS-7", "Map dependency", "in_progress"),))
    module = _module(tmp_path, client)
    settings = replace(AppSettings.from_config(), scheduler_enabled=False)
    app = create_app(settings, (module,))
    with TestClient(app, headers={"X-User": "tester"}) as http:
        registry = app.state.skein_registry
        work_items = WorkItems(registry.policy_engine)
        from app.public.work import _bind_execution_context

        subject = registry.service_subject("atlas-sync")
        execution_context = JobExecutionContext(
            registry.policy_engine,
            work_items,
            subject,
            "atlas.workplace.sync:test",
            "atlas.workplace.sync",
        )
        context = _bind_execution_context(
            work_items,
            execution_context,
            subject=subject,
            namespace="atlas.workplace.sync",
            receipt_namespace="job:atlas.workplace.sync",
            correlation_id="atlas.workplace.sync:test",
        )
        job = next(item for item in registry.jobs if item.name.endswith(".sync"))
        assert job.handler(context) == {"created": 1, "updated": 0}
        activity_before = fresh_db.query_row("SELECT COUNT(*) AS count FROM activity")["count"]
        outbox_before = fresh_db.query_row("SELECT COUNT(*) AS count FROM extension_outbox")[
            "count"
        ]
        assert job.handler(context) == {"created": 0, "updated": 0}
        assert fresh_db.query_row("SELECT COUNT(*) AS count FROM activity")["count"] == (
            activity_before
        )
        assert fresh_db.query_row("SELECT COUNT(*) AS count FROM extension_outbox")["count"] == (
            outbox_before
        )
        denied = http.get("/api/extensions/atlas.workplace/metrics")

    task = fresh_db.query_one("SELECT * FROM tasks")
    assert task["title"] == "Map dependency"
    assert task["status"] == "in_progress"
    assert task["origin"] == "extension:atlas.workplace.sync"
    assert (
        # the extension's table lives in ITS OWN schema; the core schema
        # must not carry it
        fresh_db.query_one(
            "SELECT 1 AS present FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name = 'work_links'"
        )
        is None
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "POLICY_DENIED"
    assert len(client.updates) == 1
    assert client.updates[0][0:2] == ("ATLAS-7", "in_progress")
    assert client.updates[0][2].startswith("atlas-status:ATLAS-7:")
    sync_event_id = client.updates[0][2]

    delivery = dispatch_events(
        app.state.skein_registry.events,
        EventExecutionContext(
            registry.policy_engine,
            work_items,
            registry.service_subject,
            namespace="atlas.workplace.task-events",
        ),
    )
    assert delivery["delivered"] == 2
    event_updates = [update for update in client.updates if update[2] != sync_event_id]
    assert len(event_updates) >= 1
    assert all(update[0:2] == ("ATLAS-7", "in_progress") for update in event_updates)


def test_reference_route_uses_core_issued_provenance(fresh_db, tmp_path, monkeypatch):
    from app import oidc

    monkeypatch.setattr(oidc, "validate", lambda token: {"token": token})
    monkeypatch.setattr(
        oidc,
        "principal",
        lambda _claims: ("mira", ["atlas-integrations"]),
    )
    client = MemoryAtlasClient((AtlasItem("ATLAS-ROUTE", "Route import"),))
    settings = replace(
        AppSettings.from_config(),
        scheduler_enabled=False,
        auth_mode="oidc",
        auth_error="",
        api_token="",
        docs_enabled=False,
    )

    with TestClient(
        create_app(settings, (_module(tmp_path, client),)),
        headers={"Authorization": "Bearer integrator-token"},
    ) as http:
        response = http.post("/api/extensions/atlas.workplace/sync", json={"full": False})

    assert response.status_code == 200, response.text
    assert fresh_db.query_one("SELECT origin, created_by FROM tasks") == {
        "origin": "extension:atlas.workplace.routes",
        "created_by": "mira",
    }
    payload = __import__("json").loads(
        fresh_db.query_one("SELECT payload FROM extension_outbox")["payload"]
    )
    assert payload["actor"] == {"name": "mira", "kind": "human"}


def test_concurrent_sync_uses_operation_scoped_idempotency_keys(fresh_db, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    client = MemoryAtlasClient(
        (
            AtlasItem("ATLAS-7", "Map dependency", "in_progress"),
            AtlasItem("ATLAS-8", "Check rollout", "todo"),
        )
    )
    store_path = "atlas-extension"
    module = atlas_module(AtlasSettings(store_path), client)
    settings = replace(AppSettings.from_config(), scheduler_enabled=False)
    app = create_app(settings, (module,))
    with TestClient(app):
        registry = app.state.skein_registry
        integration = AtlasIntegration(client, ExtensionStore(store_path))
        work_items = WorkItems(registry.policy_engine)
        from app.public.work import _bind_execution_context

        subject = registry.service_subject("atlas-sync")
        execution_context = JobExecutionContext(
            registry.policy_engine,
            work_items,
            subject,
            "atlas.workplace.sync:window-7",
            "atlas.workplace.sync",
        )
        execution = _bind_execution_context(
            work_items,
            execution_context,
            subject=subject,
            namespace="atlas.workplace.sync",
            receipt_namespace="job:atlas.workplace.sync",
            correlation_id="atlas.workplace.sync:window-7",
        )
        context = execution.command_context(project_type="standard")
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda _index: integration.sync(work_items, context),
                    range(2),
                )
            )

    assert sum(result["created"] for result in results) == 2
    assert sum(result["updated"] for result in results) == 0
    assert fresh_db.query_one("SELECT COUNT(*) AS count FROM tasks") == {"count": 2}
    assert ExtensionStore(store_path).query_one("SELECT COUNT(*) AS count FROM work_links") == {
        "count": 2
    }
    assert sorted(update[0:2] for update in client.updates) == [
        ("ATLAS-7", "in_progress"),
        ("ATLAS-8", "todo"),
    ]
    assert all(update[2].startswith(f"atlas-status:{update[0]}:") for update in client.updates)


def test_route_and_job_share_one_extension_owned_sync_claim(
    fresh_db,
    tmp_path,
    monkeypatch,
):
    """Different contribution receipts cannot duplicate one Atlas business key."""
    from concurrent.futures import ThreadPoolExecutor

    from app import oidc
    from app.public.work import _bind_execution_context

    monkeypatch.setattr(oidc, "validate", lambda token: {"token": token})
    monkeypatch.setattr(
        oidc,
        "principal",
        lambda _claims: ("mira", ["atlas-integrations"]),
    )
    atlas_client = MemoryAtlasClient((AtlasItem("ATLAS-RACE", "One task"),))
    store_path = "atlas-extension"
    module = atlas_module(AtlasSettings(store_path), atlas_client)
    settings = replace(
        AppSettings.from_config(),
        scheduler_enabled=False,
        auth_mode="oidc",
        auth_error="",
        api_token="",
        docs_enabled=False,
    )
    app = create_app(settings, (module,))
    with TestClient(
        app,
        headers={"Authorization": "Bearer integrator-token"},
    ) as http:
        registry = app.state.skein_registry
        work_items = WorkItems(registry.policy_engine)
        subject = registry.service_subject("atlas-sync")
        raw_job_context = JobExecutionContext(
            registry.policy_engine,
            work_items,
            subject,
            "atlas.workplace.sync:route-job-race",
            "atlas.workplace.sync",
        )
        job_context = _bind_execution_context(
            work_items,
            raw_job_context,
            subject=subject,
            namespace="atlas.workplace.sync",
            receipt_namespace="job:atlas.workplace.sync",
            correlation_id="atlas.workplace.sync:route-job-race",
        )
        job = next(item for item in registry.jobs if item.name == "atlas.workplace.sync")
        with ThreadPoolExecutor(max_workers=2) as executor:
            route_future = executor.submit(
                http.post,
                "/api/extensions/atlas.workplace/sync",
                json={"full": False},
            )
            job_future = executor.submit(job.handler, job_context)
            route_response = route_future.result(timeout=10)
            job_result = job_future.result(timeout=10)

    assert route_response.status_code == 200, route_response.text
    results = (route_response.json(), job_result)
    assert sum(int(result["created"]) for result in results) == 1
    assert sum(int(result["updated"]) for result in results) == 0
    assert fresh_db.query_one("SELECT COUNT(*) AS count FROM tasks WHERE title = 'One task'") == {
        "count": 1
    }
    assert ExtensionStore(store_path).query_one(
        "SELECT COUNT(*) AS count FROM work_links WHERE external_id = 'ATLAS-RACE'"
    ) == {"count": 1}


def test_failed_remote_delivery_keeps_mapping_for_cross_entry_retry(fresh_db, tmp_path):
    from app.public.work import _bind_execution_context

    class FailOnceClient(MemoryAtlasClient):
        def __init__(self):
            super().__init__((AtlasItem("ATLAS-RETRY", "One durable task"),))
            self.fail = True

        def update_status(self, external_id: str, status: str, event_id: str = "") -> None:
            if self.fail:
                self.fail = False
                raise RuntimeError("remote response was lost")
            super().update_status(external_id, status, event_id)

    client = FailOnceClient()
    store_path = "atlas-extension"
    module = atlas_module(AtlasSettings(store_path), client)
    app = create_app(replace(AppSettings.from_config(), scheduler_enabled=False), (module,))
    with TestClient(app):
        registry = app.state.skein_registry
        work_items = WorkItems(registry.policy_engine)
        subject = registry.service_subject("atlas-sync")

        def context(namespace: str):
            raw = JobExecutionContext(
                registry.policy_engine,
                work_items,
                subject,
                f"{namespace}:retry",
                namespace,
            )
            return _bind_execution_context(
                work_items,
                raw,
                subject=subject,
                namespace=namespace,
                receipt_namespace=f"job:{namespace}",
                correlation_id=f"{namespace}:retry",
            ).command_context(project_type="standard")

        first = AtlasIntegration(client, ExtensionStore(store_path))
        with pytest.raises(RuntimeError, match="response was lost"):
            first.sync(work_items, context("atlas.workplace.sync"))

        assert fresh_db.query_one("SELECT COUNT(*) AS count FROM tasks") == {"count": 1}
        assert ExtensionStore(store_path).query_one(
            "SELECT skein_task_id FROM work_links WHERE external_id = 'ATLAS-RETRY'"
        ) == {"skein_task_id": 1}

        retried = AtlasIntegration(client, ExtensionStore(store_path)).sync(
            work_items,
            context("atlas.workplace.routes"),
        )

    assert retried == {"created": 0, "updated": 0}
    assert fresh_db.query_one("SELECT COUNT(*) AS count FROM tasks") == {"count": 1}
    assert len(client.updates) == 1
    assert client.updates[0][0:2] == ("ATLAS-RETRY", "todo")


def test_failed_mapping_keeps_task_claim_for_cross_entry_retry(
    fresh_db,
    tmp_path,
    monkeypatch,
):
    from app.public.work import _bind_execution_context

    client = MemoryAtlasClient((AtlasItem("ATLAS-MAP-RETRY", "One claimed task"),))
    store_path = "atlas-extension"
    module = atlas_module(AtlasSettings(store_path), client)
    app = create_app(replace(AppSettings.from_config(), scheduler_enabled=False), (module,))
    with TestClient(app):
        registry = app.state.skein_registry
        work_items = WorkItems(registry.policy_engine)
        subject = registry.service_subject("atlas-sync")

        def context(namespace: str):
            raw = JobExecutionContext(
                registry.policy_engine,
                work_items,
                subject,
                f"{namespace}:mapping-retry",
                namespace,
            )
            return _bind_execution_context(
                work_items,
                raw,
                subject=subject,
                namespace=namespace,
                receipt_namespace=f"job:{namespace}",
                correlation_id=f"{namespace}:mapping-retry",
            ).command_context(project_type="standard")

        first = AtlasIntegration(client, ExtensionStore(store_path))
        # the mapping write goes through _query now, because the INSERT has to
        # report whether it actually created the link (RETURNING)
        original_query = first._query
        failed = False

        def fail_mapping_once(sql, params=()):
            nonlocal failed
            if not failed and sql.startswith("INSERT INTO work_links"):
                failed = True
                raise RuntimeError("mapping write failed")
            return original_query(sql, params)

        monkeypatch.setattr(first, "_query", fail_mapping_once)
        with pytest.raises(RuntimeError, match="mapping write failed"):
            first.sync(work_items, context("atlas.workplace.sync"))

        assert fresh_db.query_one("SELECT COUNT(*) AS count FROM tasks") == {"count": 1}
        store = ExtensionStore(store_path)
        assert store.query_one(
            "SELECT owner_namespace, skein_task_id FROM sync_claims"
            " WHERE external_id = 'ATLAS-MAP-RETRY'"
        ) == {
            "owner_namespace": "atlas.workplace.sync",
            "skein_task_id": 1,
        }
        assert (
            store.query_one(
                "SELECT skein_task_id FROM work_links WHERE external_id = 'ATLAS-MAP-RETRY'"
            )
            is None
        )

        retried = AtlasIntegration(client, store).sync(
            work_items,
            context("atlas.workplace.routes"),
        )

    assert retried == {"created": 1, "updated": 0}
    assert fresh_db.query_one("SELECT COUNT(*) AS count FROM tasks") == {"count": 1}
    assert ExtensionStore(store_path).query_one(
        "SELECT skein_task_id FROM work_links WHERE external_id = 'ATLAS-MAP-RETRY'"
    ) == {"skein_task_id": 1}
    assert (
        ExtensionStore(store_path).query_one(
            "SELECT external_id FROM sync_claims WHERE external_id = 'ATLAS-MAP-RETRY'"
        )
        is None
    )


def test_failed_claim_staging_requires_owner_replay_without_duplicate(
    fresh_db,
    tmp_path,
    monkeypatch,
):
    from app.public.work import _bind_execution_context

    client = MemoryAtlasClient((AtlasItem("ATLAS-STAGE-RETRY", "One staged task"),))
    store_path = "atlas-extension"
    module = atlas_module(AtlasSettings(store_path), client)
    app = create_app(replace(AppSettings.from_config(), scheduler_enabled=False), (module,))
    with TestClient(app):
        registry = app.state.skein_registry
        work_items = WorkItems(registry.policy_engine)
        subject = registry.service_subject("atlas-sync")

        def context(namespace: str):
            raw = JobExecutionContext(
                registry.policy_engine,
                work_items,
                subject,
                f"{namespace}:stage-retry",
                namespace,
            )
            return _bind_execution_context(
                work_items,
                raw,
                subject=subject,
                namespace=namespace,
                receipt_namespace=f"job:{namespace}",
                correlation_id=f"{namespace}:stage-retry",
            ).command_context(project_type="standard")

        first = AtlasIntegration(client, ExtensionStore(store_path))
        original_execute = first._execute
        failed = False

        def fail_staging_once(sql, params=()):
            nonlocal failed
            if not failed and sql.startswith("UPDATE sync_claims SET skein_task_id"):
                failed = True
                raise RuntimeError("claim staging failed")
            return original_execute(sql, params)

        monkeypatch.setattr(first, "_execute", fail_staging_once)
        with pytest.raises(RuntimeError, match="claim staging failed"):
            first.sync(work_items, context("atlas.workplace.sync"))

        store = ExtensionStore(store_path)
        assert fresh_db.query_one("SELECT COUNT(*) AS count FROM tasks") == {"count": 1}
        assert store.query_one(
            "SELECT owner_namespace, skein_task_id FROM sync_claims"
            " WHERE external_id = 'ATLAS-STAGE-RETRY'"
        ) == {
            "owner_namespace": "atlas.workplace.sync",
            "skein_task_id": None,
        }

        other_result = AtlasIntegration(client, store).sync(
            work_items,
            context("atlas.workplace.routes"),
        )
        assert other_result == {"created": 0, "updated": 0}
        assert fresh_db.query_one("SELECT COUNT(*) AS count FROM tasks") == {"count": 1}

        owner_result = AtlasIntegration(client, store).sync(
            work_items,
            context("atlas.workplace.sync"),
        )

    assert owner_result == {"created": 1, "updated": 0}
    assert fresh_db.query_one("SELECT COUNT(*) AS count FROM tasks") == {"count": 1}
    assert ExtensionStore(store_path).query_one(
        "SELECT skein_task_id FROM work_links WHERE external_id = 'ATLAS-STAGE-RETRY'"
    ) == {"skein_task_id": 1}


def test_http_adapter_uses_the_deployment_secret(monkeypatch):
    from atlas_skein import integration

    calls = []

    class Response:
        def __init__(self, payload: bytes):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self.payload

    class Opener:
        # The adapter opens through its redirect-refusing opener, never the
        # module-level urlopen — patching the opener keeps that pinned.
        def open(self, request, timeout):
            calls.append((request, timeout))
            if request.get_method() == "GET":
                return Response(b'{"items":[{"external_id":"ATLAS-7","title":"Map"}]}')
            return Response(b"")

    monkeypatch.setattr(integration, "_NO_REDIRECT_OPENER", Opener())
    client = AtlasHttpClient("https://atlas.example.invalid/api", "secret-token", 4)
    assert client.list_items() == (AtlasItem("ATLAS-7", "Map"),)
    client.update_status("ATLAS-7", "done", "event-7")
    client.notify_manager("delivery", "Ready", "notification-7")
    assert [request.get_method() for request, _timeout in calls] == ["GET", "PATCH", "POST"]
    assert all(
        request.get_header("Authorization") == "Bearer secret-token" for request, _timeout in calls
    )
    assert calls[1][0].data == b'{"status": "done", "idempotency_key": "event-7"}'
    assert calls[2][0].data == (
        b'{"channel": "delivery", "message": "Ready", "idempotency_key": "notification-7"}'
    )


def test_a_second_module_can_deny_the_reference_background_job(fresh_db, tmp_path):
    def compliance_rule(request: PolicyInput):
        if request.action == "atlas.integration.sync" and request.subject.kind == "service":
            return PolicyDecision(PolicyEffect.DENY, ("Background imports are paused.",))
        return None

    compliance = SkeinModule(
        module_id="compliance.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("compliance.workplace.background-policy", compliance_rule),),
    )
    client = MemoryAtlasClient((AtlasItem("ATLAS-9", "Must not land"),))
    settings = replace(AppSettings.from_config(), scheduler_enabled=False)
    app = create_app(settings, (_module(tmp_path, client), compliance))
    with TestClient(app):
        from app.main import _job_specs

        spec = next(
            item
            for item in _job_specs(app.state.skein_registry, settings)
            if item.name == "atlas.workplace.sync"
        )
        assert spec.fn()["error_code"] == "POLICY_DENIED"
    assert fresh_db.query_one("SELECT 1 AS present FROM tasks") is None
    assert client.updates == []


def test_directory_groups_map_to_roles_and_backend_policy(tmp_path):
    registry = ExtensionRegistry.build((_module(tmp_path),))
    attributes = registry.identity_attributes(
        "mira",
        ("atlas-delivery-managers",),
        True,
    )
    assert attributes == {
        "roles": ("delivery-manager",),
        "capabilities": ("atlas.dashboard", "atlas.approve", "atlas.specialist"),
    }
    manager = PolicySubject(
        "mira",
        groups=("atlas-delivery-managers",),
        roles=attributes["roles"],
        capabilities=attributes["capabilities"],
    )
    denied = registry.policy_engine.decide(
        PolicyInput(
            PolicySubject("ava"),
            "atlas.dashboard.view",
            PolicyResource("dashboard"),
            "human",
        )
    )
    permitted = registry.policy_engine.decide(
        PolicyInput(manager, "atlas.dashboard.view", PolicyResource("dashboard"), "human")
    )
    approval = registry.policy_engine.decide(
        PolicyInput(
            PolicySubject("ava"),
            "atlas.release.approve",
            PolicyResource("release", project_type="regulated"),
            "workflow",
            tool_effect="write",
            tool_risk="high",
        )
    )
    assert denied.effect == PolicyEffect.DENY
    assert permitted.effect == PolicyEffect.PERMIT
    assert approval.effect == PolicyEffect.REVIEW
    assert approval.approver_groups == ("atlas-delivery-managers",)


def test_reference_specialist_tool_is_governed_and_uses_public_work(fresh_db, tmp_path):
    import asyncio

    from app.extensions.tools import ToolCallContext, execute_tool

    client = MemoryAtlasClient((AtlasItem("ATLAS-8", "Check release"),))
    module = _module(tmp_path, client)
    registry = ExtensionRegistry.build((module,))
    registry.migrations[0].store.migrate(registry.migrations[0].migrations)
    specialist = registry.specialists[0]
    assert specialist.tools == ("atlas.workplace.sync-tool",)
    assert specialist.context_sources == ("atlas.workplace.delivery-context",)
    result = asyncio.run(
        execute_tool(
            registry.tools[0],
            {"full": True},
            ToolCallContext(
                PolicySubject(
                    "mira",
                    capabilities=("atlas.integration", "atlas.specialist"),
                ),
                specialist.name,
                correlation_id="atlas-tool-call-1",
            ),
            registry.policy_engine,
        )
    )
    assert result.status == "completed"
    assert result.output == {"created": 1, "updated": 0}
    task = fresh_db.query_one("SELECT origin, created_by FROM tasks")
    assert task == {
        "origin": "extension:atlas.workplace.sync-tool",
        "created_by": "atlas.workplace.delivery-specialist",
    }
    event = fresh_db.query_one("SELECT payload FROM extension_outbox")
    assert event is not None
    payload = __import__("json").loads(event["payload"])
    assert payload["actor"] == {
        "name": "atlas.workplace.delivery-specialist",
        "kind": "agent",
    }
    assert payload["correlation_id"] == "atlas-tool-call-1"
    activities = fresh_db.query(
        "SELECT actor, action, detail FROM activity"
        " WHERE action IN ('create_task', 'external_tool') ORDER BY id"
    )
    assert [row["actor"] for row in activities] == [specialist.name, specialist.name]
    assert "correlation=atlas-tool-call-1" in activities[-1]["detail"]


def test_reference_workflow_translates_adapter_failure_to_its_declared_code(fresh_db, tmp_path):
    from app.public.workflow import WorkflowEngine, _issue_workflow_context

    class UnavailableAtlas(MemoryAtlasClient):
        def notify_manager(self, channel: str, message: str, event_id: str = "") -> None:
            raise AtlasUnavailableError("transport details stay private")

    module = _module(tmp_path, UnavailableAtlas())
    registry = ExtensionRegistry.build((module,))
    engine = WorkflowEngine(registry.workflow_actions, registry.policy_engine)
    context = _issue_workflow_context(
        engine,
        PolicySubject("atlas-sync", kind="service"),
        "workflow",
    )

    result = engine.run(
        engine.prepare(
            [
                {
                    "type": "action",
                    "name": "atlas.workplace.notify-manager",
                    "input": {"channel": "delivery", "message": "Ready"},
                }
            ]
        ),
        context,
    )

    assert result.status == "completion_unknown"
    assert result.error_code == "NOTIFICATION_UNAVAILABLE"


def test_reference_playbook_uses_real_policy_and_workflow_registry(fresh_db, tmp_path, monkeypatch):
    from app import config, oidc

    monkeypatch.setattr(config, "PLAYBOOKS_OVERLAY", EXAMPLE / "content" / "playbooks")
    monkeypatch.setattr(oidc, "validate", lambda token: {"token": token})
    monkeypatch.setattr(
        oidc,
        "principal",
        lambda claims: (
            ("mira", ["atlas-delivery-managers"])
            if claims["token"] == "manager-token"
            else ("ava", [])
        ),
    )
    client = MemoryAtlasClient()
    module = _module(tmp_path, client)
    settings = replace(
        AppSettings.from_config(),
        scheduler_enabled=False,
        auth_mode="oidc",
        auth_error="",
        api_token="",
        docs_enabled=False,
    )
    with TestClient(
        create_app(settings, (module,)),
        headers={"Authorization": "Bearer requester-token"},
    ) as http:
        response = http.post(
            "/api/playbooks/instantiate",
            json={"playbook": "atlas_delivery", "engagement_name": "Atlas launch"},
        )
        assert response.status_code == 200, response.text
        workflow = response.json()["workflow"]
        assert workflow["status"] == "review_required"
        assert workflow["checkpoint"] == "manager-approval"
        assert workflow["review_id"] > 0
        assert workflow["obligations"] == [
            "approver-group:atlas-delivery-managers",
            "approver-capability:atlas.approve",
        ]
        assert fresh_db.query_one("SELECT name FROM engagements") is None

        approved = http.post(
            f"/api/review/{workflow['review_id']}/approve",
            json={"note": "Approved for delivery."},
            headers={"Authorization": "Bearer manager-token"},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["result"]["workflow"]["status"] == "completed"

    assert fresh_db.query_one("SELECT name FROM engagements") == {"name": "Atlas launch"}
    assert len(client.notifications) == 1
    channel, message, idempotency_key = client.notifications[0]
    assert channel == "delivery-managers"
    assert message == "Atlas delivery work is ready."
    run_id, separator, step_key = idempotency_key.partition(":")
    assert len(run_id) == 32
    assert separator == ":"
    assert step_key == "root.1:manager-notification"
    client.notify_manager(*client.notifications[0])
    assert len(client.notifications) == 1
    assert fresh_db.query_one(
        "SELECT status FROM extension_review_invocations WHERE change_id = ?",
        (workflow["review_id"],),
    ) == {"status": "approved"}


def test_rest_review_resumes_only_the_exact_workflow_step(fresh_db, tmp_path, monkeypatch):
    from app import config, oidc

    overlay = tmp_path / "playbooks"
    overlay.mkdir()
    (overlay / "two_approvals.yaml").write_text(
        """\
schema_version: 1
name: Two approvals
project_class: regulated
description: Each occurrence needs a separate manager verdict.
milestones:
  - title: Prepare delivery
workflow:
  - type: approval
    name: manager-approval
    action: atlas.release.approve
    resource_type: release
    risk: high
  - type: approval
    name: manager-approval
    action: atlas.release.approve
    resource_type: release
    risk: high
"""
    )
    monkeypatch.setattr(config, "PLAYBOOKS_OVERLAY", overlay)
    monkeypatch.setattr(oidc, "validate", lambda token: {"token": token})
    monkeypatch.setattr(
        oidc,
        "principal",
        lambda claims: (
            ("mira", ["atlas-delivery-managers"])
            if claims["token"] == "manager-token"
            else ("ava", [])
        ),
    )
    settings = replace(
        AppSettings.from_config(),
        scheduler_enabled=False,
        auth_mode="oidc",
        auth_error="",
        api_token="",
        docs_enabled=False,
    )
    with TestClient(
        create_app(settings, (_module(tmp_path),)),
        headers={"Authorization": "Bearer requester-token"},
    ) as http:
        started = http.post(
            "/api/playbooks/instantiate",
            json={"playbook": "two_approvals", "engagement_name": "Exact grants"},
        ).json()
        first_id = started["workflow"]["review_id"]
        first_key = started["workflow"]["review_key"]

        first = http.post(
            f"/api/review/{first_id}/approve",
            json={"note": "First occurrence only."},
            headers={"Authorization": "Bearer manager-token"},
        )
        assert first.status_code == 200, first.text
        resumed = first.json()["result"]["workflow"]
        assert resumed["status"] == "review_required"
        assert resumed["review_key"] != first_key
        assert fresh_db.query_one("SELECT name FROM engagements") is None

        second = http.post(
            f"/api/review/{resumed['review_id']}/approve",
            json={"note": "Second occurrence."},
            headers={"Authorization": "Bearer manager-token"},
        )
        assert second.status_code == 200, second.text
        assert second.json()["result"]["workflow"]["status"] == "completed"

    assert fresh_db.query_one("SELECT name FROM engagements") == {"name": "Exact grants"}


def test_extension_store_upgrades_its_own_v1_data_during_composition(fresh_db, tmp_path):
    module = _module(tmp_path)
    contribution = module.migrations[0]
    contribution.store.migrate(contribution.migrations[:1])
    contribution.store.execute(
        "INSERT INTO work_links (external_id, skein_task_id, classification) VALUES (?, ?, ?)",
        ("ATLAS-OLD", 91, "internal"),
    )
    settings = replace(AppSettings.from_config(), scheduler_enabled=False)
    with TestClient(create_app(settings, (module,)), headers={"X-User": "tester"}):
        pass
    assert contribution.store.query_one(
        "SELECT external_id FROM work_links WHERE skein_task_id = ?",
        (91,),
    ) == {"external_id": "ATLAS-OLD"}
    versions = contribution.store.query(
        "SELECT version FROM extension_schema_version ORDER BY version"
    )
    assert versions == [
        {"version": 1},
        {"version": 2},
        {"version": 3},
        {"version": 4},
    ]
