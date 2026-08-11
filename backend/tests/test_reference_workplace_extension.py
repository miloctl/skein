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
from atlas_skein.integration import AtlasItem, MemoryAtlasClient  # noqa: E402

from app.extensions import (  # noqa: E402
    AppSettings,
    EventExecutionContext,
    ExtensionRegistry,
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
        AtlasSettings(tmp_path / "atlas-extension.db"),
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
    assert len(registry.identities) == 1
    assert len(registry.service_identities) == 2
    assert len(registry.contexts) == 1
    assert len(registry.tools) == 1
    assert len(registry.specialists) == 1
    assert len(registry.events) == 1
    assert len(registry.migrations) == 1
    assert len(registry.workflow_actions) == 1


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
        context = JobExecutionContext(
            registry.policy_engine,
            work_items,
            registry.service_subject("atlas-sync"),
            "atlas.workplace.sync:test",
        )
        job = next(item for item in registry.jobs if item.name.endswith(".sync"))
        assert job.handler(context) == {"created": 1, "updated": 0}
        assert job.handler(context) == {"created": 0, "updated": 1}
        denied = http.get("/api/extensions/atlas.workplace/metrics")

    task = fresh_db.query_one("SELECT * FROM tasks")
    assert task["title"] == "Map dependency"
    assert task["status"] == "in_progress"
    assert task["origin"] == "atlas-integration"
    assert (
        fresh_db.query_one("SELECT 1 AS present FROM sqlite_master WHERE name = 'work_links'")
        is None
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "POLICY_DENIED"
    assert client.updates[:2] == [
        ("ATLAS-7", "in_progress", ""),
        ("ATLAS-7", "in_progress", ""),
    ]

    delivery = dispatch_events(
        app.state.skein_registry.events,
        EventExecutionContext(
            registry.policy_engine,
            work_items,
            registry.service_subject,
        ),
    )
    assert delivery["delivered"] == 3
    event_updates = [update for update in client.updates if update[2]]
    assert len(event_updates) == 2
    assert all(update[0:2] == ("ATLAS-7", "in_progress") for update in event_updates)


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
            ),
            registry.policy_engine,
        )
    )
    assert result.status == "completed"
    assert result.output == {"created": 1, "updated": 0}
    assert fresh_db.query_one("SELECT origin FROM tasks")["origin"] == "atlas-integration"


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
    module = _module(tmp_path)
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
    assert versions == [{"version": 1}, {"version": 2}]
