"""Executable scenarios for the fictional private Atlas package."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "workplace-extension"
SOURCE = EXAMPLE / "backend" / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from atlas_skein import AtlasSettings, atlas_module  # noqa: E402
from atlas_skein.integration import (  # noqa: E402
    AtlasBadResponseError,
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


def _test_directory(name: str) -> dict[str, object] | None:
    return {
        "ava": {"active": True, "groups": ()},
        "mira": {"active": True, "groups": ("atlas-delivery-managers",)},
    }.get(name)


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
    assert event_updates == []

    with TestClient(app, headers={"X-User": "mira"}) as human:
        assert human.patch(f"/api/tasks/{task['id']}", json={"status": "done"}).status_code == 200
    delivered = dispatch_events(
        app.state.skein_registry.events,
        EventExecutionContext(
            registry.policy_engine,
            work_items,
            registry.service_subject,
            namespace="atlas.workplace.task-events",
        ),
    )
    assert delivered["failed"] == 0
    assert client.updates[-1][0:2] == ("ATLAS-7", "done")


def test_reference_route_uses_core_issued_provenance(fresh_db, tmp_path, monkeypatch):
    from app import oidc

    monkeypatch.setattr(oidc, "validate", lambda token: {"token": token})
    monkeypatch.setattr(
        oidc,
        "identity",
        lambda claims: ("https://idp.test", f"subject:{claims['token']}"),
    )
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


@pytest.mark.parametrize(
    ("failure", "status", "code", "retryable"),
    (
        (AtlasUnavailableError("transport details"), 503, "ATLAS_UNAVAILABLE", True),
        (AtlasBadResponseError("remote details"), 502, "ATLAS_BAD_RESPONSE", False),
    ),
)
def test_reference_sync_route_classifies_remote_failures(
    fresh_db, tmp_path, monkeypatch, failure, status, code, retryable
):
    from app import oidc

    class BrokenAtlas(MemoryAtlasClient):
        def list_items(self):
            raise failure

    monkeypatch.setattr(oidc, "validate", lambda token: {"token": token})
    monkeypatch.setattr(
        oidc,
        "identity",
        lambda claims: ("https://idp.test", f"subject:{claims['token']}"),
    )
    monkeypatch.setattr(oidc, "principal", lambda _claims: ("mira", ["atlas-integrations"]))
    settings = replace(
        AppSettings.from_config(),
        scheduler_enabled=False,
        auth_mode="oidc",
        auth_error="",
        api_token="",
        docs_enabled=False,
    )
    with TestClient(
        create_app(settings, (_module(tmp_path, BrokenAtlas()),)),
        headers={"Authorization": "Bearer integrator-token"},
    ) as http:
        response = http.post("/api/extensions/atlas.workplace/sync", json={"full": False})
    assert response.status_code == status
    assert response.json()["code"] == code
    assert response.json()["retryable"] is retryable
    assert response.headers.get("retry-after") == ("60" if retryable else None)
    assert "details" not in response.text


def test_reference_sync_job_preserves_declared_remote_error(fresh_db, tmp_path, caplog):
    from app.main import _job_specs

    class BrokenAtlas(MemoryAtlasClient):
        def list_items(self):
            raise AtlasUnavailableError("transport details")

    registry = ExtensionRegistry.build((_module(tmp_path, BrokenAtlas()),))
    spec = next(
        item
        for item in _job_specs(registry, AppSettings.from_config())
        if item.name == "atlas.workplace.sync"
    )
    result = spec.fn()
    assert result == {
        "status": "error",
        "error_code": "ATLAS_UNAVAILABLE",
        "retryable": True,
    }
    assert "transport details" not in caplog.text


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


def test_overlapping_sync_does_not_send_a_stale_status(fresh_db, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from app.public.work import _bind_execution_context

    store_name = "atlas-extension"
    initial_client = MemoryAtlasClient((AtlasItem("ATLAS-ORDER", "One task", "todo"),))
    module = atlas_module(AtlasSettings(store_name), initial_client)
    app = create_app(replace(AppSettings.from_config(), scheduler_enabled=False), (module,))
    with TestClient(app):
        registry = app.state.skein_registry
        work_items = WorkItems(registry.policy_engine)
        subject = registry.service_subject("atlas-sync")
        raw = JobExecutionContext(
            registry.policy_engine,
            work_items,
            subject,
            "atlas.workplace.sync:status-order",
            "atlas.workplace.sync",
        )
        context = _bind_execution_context(
            work_items,
            raw,
            subject=subject,
            namespace="atlas.workplace.sync",
            receipt_namespace="job:atlas.workplace.sync",
            correlation_id="atlas.workplace.sync:status-order",
        ).command_context(project_type="standard")
        store = ExtensionStore(store_name)
        AtlasIntegration(initial_client, store).sync(work_items, context)
        initial_client.updates.clear()

        first_client = MemoryAtlasClient((AtlasItem("ATLAS-ORDER", "One task", "in_progress"),))
        second_client = MemoryAtlasClient((AtlasItem("ATLAS-ORDER", "One task", "blocked"),))
        first = AtlasIntegration(first_client, store)
        second = AtlasIntegration(second_client, store)
        first_updated = Event()
        release = Event()
        record_status = first._record_status

        def delayed_record(external_id, task):
            first_updated.set()
            release.wait(3)
            record_status(external_id, task)

        monkeypatch.setattr(first, "_record_status", delayed_record)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(first.sync, work_items, context)
            assert first_updated.wait(2)
            try:
                second.sync(work_items, context)
            finally:
                release.set()
            future.result(timeout=3)

    assert first_client.updates == []
    assert [update[0:2] for update in second_client.updates] == [("ATLAS-ORDER", "blocked")]
    assert ExtensionStore(store_name).query_one(
        "SELECT dead, error_code FROM status_outbox"
        " WHERE external_id = 'ATLAS-ORDER' AND status = 'in_progress'"
    ) == {"dead": 1, "error_code": "ATLAS_STATUS_SUPERSEDED"}


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
        "identity",
        lambda claims: ("https://idp.test", f"subject:{claims['token']}"),
    )
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
                raise AtlasUnavailableError("remote response was lost")
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
        with pytest.raises(AtlasUnavailableError, match="response was lost"):
            first.sync(work_items, context("atlas.workplace.sync"))

        assert fresh_db.query_one("SELECT COUNT(*) AS count FROM tasks") == {"count": 1}
        assert ExtensionStore(store_path).query_one(
            "SELECT skein_task_id FROM work_links WHERE external_id = 'ATLAS-RETRY'"
        ) == {"skein_task_id": 1}
        ExtensionStore(store_path).execute(
            "UPDATE status_outbox SET lease_until = now() - INTERVAL '1 second'"
            " WHERE external_id = 'ATLAS-RETRY'"
        )

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

        def read(self, _size=-1):
            payload, self.payload = self.payload, b""
            return payload

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
        maximum_core_exclusive="0.5.0",
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
                origin="background",
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


@pytest.mark.parametrize(
    ("failure", "code"),
    (
        (AtlasUnavailableError("transport details"), "ATLAS_UNAVAILABLE"),
        (AtlasBadResponseError("remote details"), "ATLAS_BAD_RESPONSE"),
    ),
)
def test_reference_sync_tool_preserves_declared_remote_errors(fresh_db, tmp_path, failure, code):
    import asyncio

    from app.extensions.tools import ToolCallContext, execute_tool

    class BrokenAtlas(MemoryAtlasClient):
        def list_items(self):
            raise failure

    module = _module(tmp_path, BrokenAtlas())
    registry = ExtensionRegistry.build((module,))
    registry.migrations[0].store.migrate(registry.migrations[0].migrations)
    specialist = registry.specialists[0]
    result = asyncio.run(
        execute_tool(
            registry.tools[0],
            {},
            ToolCallContext(
                PolicySubject(
                    "mira",
                    capabilities=("atlas.integration", "atlas.specialist"),
                ),
                specialist.name,
                origin="background",
            ),
            registry.policy_engine,
        )
    )
    assert result.status == "completion_unknown"
    assert result.error_code == code


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
    import atlas_skein.module as atlas_module_source

    from app import config, oidc

    monkeypatch.setattr(atlas_module_source, "atlas_directory", _test_directory)
    monkeypatch.setattr(config, "PLAYBOOKS_OVERLAY", EXAMPLE / "content" / "playbooks")
    monkeypatch.setattr(oidc, "validate", lambda token: {"token": token})
    monkeypatch.setattr(
        oidc,
        "identity",
        lambda claims: ("https://idp.test", f"subject:{claims['token']}"),
    )
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
    import atlas_skein.module as atlas_module_source

    from app import config, oidc

    monkeypatch.setattr(atlas_module_source, "atlas_directory", _test_directory)
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
        "identity",
        lambda claims: ("https://idp.test", f"subject:{claims['token']}"),
    )
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
        {"version": 5},
    ]


def test_extension_store_upgrades_pending_statuses_to_leased_delivery(fresh_db, tmp_path):
    module = _module(tmp_path)
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


def test_reference_consumer_owns_the_complete_local_runtime_gate():
    for relative in (
        "scripts/local-contract.sh",
        "scripts/contract-db.py",
        "scripts/stub-idp.py",
        "scripts/validate-deployment.py",
        "playwright.config.ts",
        "e2e/workplace-runtime.spec.ts",
    ):
        assert (EXAMPLE / relative).is_file(), relative
    script = (EXAMPLE / "scripts/local-contract.sh").read_text()
    for required in (
        "SKEIN_SOURCE",
        "SKEIN_LOCAL_DIST",
        "SHA256SUMS",
        "--user 1001230000:0",
        "--read-only",
        "/ready",
        "playwright test",
        "scripts/validate-deployment.py",
    ):
        assert required in script
    package = json.loads((EXAMPLE / "package.json").read_text())
    assert package["devDependencies"]["@playwright/test"] == "1.62.1"
    for relative in ("deployment/Dockerfile", "deployment/Frontend.Dockerfile"):
        assert "@sha256:" in (EXAMPLE / relative).read_text()


def test_contract_database_clean_run_keeps_only_the_restricted_connection():
    environment = {
        "PATH": os.environ["PATH"],
        "SKEIN_DATABASE_URL": "postgresql://admin:admin-secret@db.example.invalid/admin",
        "SKEIN_CONTRACT_ROLE_NAME": "skein_atlas_role_run_clean",
        "SKEIN_CONTRACT_ROLE_PASSWORD": "a" * 48,
        "AMBIENT_SECRET": "must-not-cross",
    }
    result = subprocess.run(  # noqa: S603 -- fixed helper and interpreter
        (
            sys.executable,
            str(EXAMPLE / "scripts/contract-db.py"),
            "run-clean",
            "skein_atlas_contract_run_clean",
            sys.executable,
            "-c",
            "import json, os; print(json.dumps(dict(os.environ)))",
        ),
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    child = json.loads(result.stdout)
    assert child["PATH"] == environment["PATH"]
    assert "skein_atlas_role_run_clean" in child["SKEIN_DATABASE_URL"]
    assert "skein_atlas_contract_run_clean" in child["SKEIN_DATABASE_URL"]
    assert "admin-secret" not in child["SKEIN_DATABASE_URL"]
    assert "AMBIENT_SECRET" not in child
    assert "SKEIN_CONTRACT_ROLE_NAME" not in child
    assert "SKEIN_CONTRACT_ROLE_PASSWORD" not in child


def test_consumer_deployment_validator_rejects_probe_and_worker_drift(tmp_path):
    digest = "sha256:" + "0" * 64

    def workload(name: str, container: str, *, backend: bool) -> dict:
        probes = {
            "readinessProbe": {"httpGet": {"path": "/ready" if backend else "/", "port": "http"}},
            "livenessProbe": {"httpGet": {"path": "/health" if backend else "/", "port": "http"}},
        }
        if backend:
            probes["startupProbe"] = {"httpGet": {"path": "/health", "port": "http"}}
        return {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": name},
            "spec": {
                "replicas": 1,
                **({"strategy": {"type": "Recreate"}} if backend else {}),
                "template": {
                    "spec": {
                        "automountServiceAccountToken": False,
                        "securityContext": {
                            "runAsNonRoot": True,
                            "seccompProfile": {"type": "RuntimeDefault"},
                        },
                        "containers": [
                            {
                                "name": container,
                                "image": f"example.invalid/{container}:test@{digest}",
                                "securityContext": {
                                    "allowPrivilegeEscalation": False,
                                    "readOnlyRootFilesystem": True,
                                    "capabilities": {"drop": ["ALL"]},
                                },
                                **probes,
                            }
                        ],
                    }
                },
            },
        }

    documents = [
        workload("skein", "skein", backend=True),
        workload("skein-frontend", "frontend", backend=False),
    ]
    rendered = tmp_path / "rendered.yaml"
    command = (
        sys.executable,
        str(EXAMPLE / "scripts/validate-deployment.py"),
        str(rendered),
        "skein",
        "skein",
        "skein-frontend",
        "frontend",
    )
    rendered.write_text(yaml.safe_dump_all(documents))
    assert (
        subprocess.run(  # noqa: S603 -- fixed validator command
            command, check=False
        ).returncode
        == 0
    )

    documents[1]["spec"]["template"]["spec"]["containers"][0]["readinessProbe"]["httpGet"][
        "path"
    ] = "/health"
    rendered.write_text(yaml.safe_dump_all(documents))
    assert (
        subprocess.run(  # noqa: S603 -- fixed validator command
            command, check=False, capture_output=True
        ).returncode
        != 0
    )

    documents[1]["spec"]["template"]["spec"]["containers"][0]["readinessProbe"]["httpGet"][
        "path"
    ] = "/"
    documents.append(
        {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": "worker"},
            "spec": {
                "template": {
                    "spec": {
                        "automountServiceAccountToken": False,
                        "securityContext": {
                            "runAsNonRoot": True,
                            "seccompProfile": {"type": "RuntimeDefault"},
                        },
                        "containers": [
                            {"name": "worker", "image": "example.invalid/worker:latest"}
                        ],
                    }
                }
            },
        }
    )
    rendered.write_text(yaml.safe_dump_all(documents))
    assert (
        subprocess.run(  # noqa: S603 -- fixed validator command
            command, check=False, capture_output=True
        ).returncode
        != 0
    )
