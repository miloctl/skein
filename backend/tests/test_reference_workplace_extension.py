"""Executable scenarios for the fictional private Atlas package."""

from __future__ import annotations

import ast
import sys
import tomllib
from dataclasses import replace
from pathlib import Path

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
    ExtensionRegistry,
    PolicyEffect,
    PolicyInput,
    PolicyResource,
    PolicySubject,
)
from app.main import create_app  # noqa: E402
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
    assert len(registry.contexts) == 1
    assert len(registry.tools) == 1
    assert len(registry.specialists) == 1
    assert len(registry.events) == 1
    assert len(registry.migrations) == 1
    assert len(registry.workflow_actions) == 1


def test_enterprise_adapter_syncs_both_directions_through_public_work(fresh_db, tmp_path):
    client = MemoryAtlasClient((AtlasItem("ATLAS-7", "Map dependency", "in_progress"),))
    module = _module(tmp_path, client)
    settings = replace(AppSettings.from_config(), scheduler_enabled=False)
    app = create_app(settings, (module,))
    with TestClient(app, headers={"X-User": "tester"}) as http:
        job = next(item for item in app.state.skein_registry.jobs if item.name.endswith(".sync"))
        assert job.handler() == {"created": 1, "updated": 0}
        assert job.handler() == {"created": 0, "updated": 1}
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
        ("ATLAS-7", "todo", ""),
        ("ATLAS-7", "in_progress", ""),
    ]

    delivery = dispatch_events(app.state.skein_registry.events)
    assert delivery["delivered"] == 2
    event_updates = [update for update in client.updates if update[2]]
    assert len(event_updates) == 1
    assert event_updates[0][0:2] == ("ATLAS-7", "in_progress")


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
    from app import config

    monkeypatch.setattr(config, "PLAYBOOKS_OVERLAY", EXAMPLE / "content" / "playbooks")
    module = _module(tmp_path)
    settings = replace(AppSettings.from_config(), scheduler_enabled=False)
    with TestClient(create_app(settings, (module,)), headers={"X-User": "tester"}) as http:
        response = http.post(
            "/api/playbooks/instantiate",
            json={"playbook": "atlas_delivery", "engagement_name": "Atlas launch"},
        )
    assert response.status_code == 200, response.text
    workflow = response.json()["workflow"]
    assert workflow["status"] == "review_required"
    assert workflow["checkpoint"] == "manager-approval"
    assert workflow["obligations"] == [
        "approver-group:atlas-delivery-managers",
        "approver-capability:atlas.approve",
    ]
    assert fresh_db.query_one("SELECT name FROM engagements")["name"] == "Atlas launch"


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
