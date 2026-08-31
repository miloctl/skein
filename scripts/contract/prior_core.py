import asyncio
import os
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path

from atlas_skein import AtlasSettings, atlas_module
from atlas_skein.integration import AtlasItem, MemoryAtlasClient
from fastapi.testclient import TestClient

from app import db
from app.extensions import (
    SKEIN_CORE_VERSION,
    AppSettings,
    ExtensionRegistry,
    ExtensionValidationError,
    IdentityContribution,
    JobExecutionContext,
    PolicyContribution,
    PolicyDecision,
    PolicyEffect,
    PolicySubject,
    SkeinModule,
    ToolCallContext,
    execute_tool,
    registry_for,
)
from app.main import create_app
from app.public import PublicError, WorkItems
from app.services import blockers, crews, engagements, private_notes, review, users, work

prior_core = os.environ["PRIOR_CORE"]
assert version("skein") == prior_core
assert prior_core == SKEIN_CORE_VERSION
module = atlas_module(
    AtlasSettings("atlas-contract"),
    MemoryAtlasClient((AtlasItem("ATLAS-OLD-CORE", "Old core sync"),)),
)
try:
    ExtensionRegistry.build(
        (
            SkeinModule(
                module_id="next.workplace",
                version="1.0.0",
                extension_api="1.0",
                minimum_core="0.3.0",
                maximum_core_exclusive="0.5.0",
            ),
        )
    )
except ExtensionValidationError as exc:
    assert "supports core versions from 0.3.0" in str(exc)
else:
    raise AssertionError("core 0.2.3 accepted a package that requires core 0.3.0")


def review_playbook(request):
    if request.action == "playbook.create" and request.resource.project_type == "prototype":
        return PolicyDecision(
            PolicyEffect.REVIEW,
            approver_capabilities=("upgrade.approve",),
        )
    return None


compatibility = SkeinModule(
    module_id="upgrade.workplace",
    version="1.0.0",
    extension_api="1.0",
    minimum_core="0.2.0",
    maximum_core_exclusive="0.5.0",
    identities=(
        IdentityContribution(
            "upgrade.workplace.identity",
            lambda name, _groups, _strong: {
                "capabilities": (("upgrade.approve",) if name == "manager" else ()),
            },
        ),
    ),
    policies=(PolicyContribution("upgrade.workplace.playbook-review", review_playbook),),
)
settings = replace(AppSettings.from_config(), scheduler_enabled=False)
app = create_app(settings, (module, compatibility))
with TestClient(app) as client:
    assert client.get("/health").status_code == 200
    playbooks = client.get("/api/playbooks")
    assert playbooks.status_code == 200
    assert "atlas_delivery" in {item["slug"] for item in playbooks.json()}
    personas = client.get("/api/personas")
    assert personas.status_code == 200
    assert "atlas-auditor" in {item["slug"] for item in personas.json()}
    queued = client.post(
        "/api/playbooks/instantiate",
        headers={"X-User": "requester"},
        json={"playbook": "prototype", "engagement_name": "Digest upgrade review"},
    )
    assert queued.status_code == 200, queued.text
    review_id = queued.json()["workflow"]["review_id"]
    Path("../pending-review-id").write_text(str(review_id))

    # The synchronization runs through the governed tool surface. The core
    # adapter inside execute_tool binds the command authority.
    registry = registry_for(app)
    sync = asyncio.run(
        execute_tool(
            registry.tool("atlas.workplace.sync-tool"),
            {},
            ToolCallContext(
                registry.service_subject("atlas-sync"),
                "atlas.workplace.delivery-specialist",
                origin="background",
            ),
            registry.policy_engine,
        )
    )
    assert sync.status == "completed"
    assert sync.output == {"created": 1, "updated": 0}

# A caller-created execution context cannot mint command authority on this
# core. The sealed boundary refused the forged sync before any write.
registry = ExtensionRegistry.build((module, compatibility))
old_context = JobExecutionContext(
    registry.policy_engine,
    WorkItems(registry.policy_engine),
    PolicySubject(
        "atlas-sync",
        kind="service",
        capabilities=("atlas.integration",),
    ),
    "old-core-sync",
    "atlas.workplace.sync",
)
try:
    module.jobs[0].handler(old_context)
except PublicError as exc:
    assert exc.code == "COMMAND_CONTEXT_REQUIRED"
else:
    raise AssertionError("a caller-created job context ran the Atlas sync")
assert module.migrations[0].store.query_one(
    "SELECT external_id FROM work_links WHERE external_id = ?",
    ("ATLAS-OLD-CORE",),
) == {"external_id": "ATLAS-OLD-CORE"}
assert (
    module.migrations[0].store.query_one(
        "SELECT external_id FROM sync_claims WHERE external_id = ?",
        ("ATLAS-OLD-CORE",),
    )
    is None
)

users.ensure_user("legacy-agent", kind="agent")
legacy = review.propose_change(
    "task",
    "create",
    {"title": "Legacy unbound task"},
    actor="legacy-agent",
    origin="agent",
)
# Preserve two relationship shapes that an older core or direct migration
# could contain. The next compatible artifact must fail closed without
# revealing the hidden regulated parent through collection or blocker policy.
crew_id = crews.create_crew("Legacy hidden policy", actor="other-person")["id"]
hidden_engagement = engagements.create_engagement(
    "Legacy hidden regulated",
    project_class="regulated",
    actor="other-person",
    visibility="crew",
    crew_id=crew_id,
)["id"]
hidden_task = work.create_task(
    "Legacy hidden task",
    engagement_id=hidden_engagement,
    actor="other-person",
    visibility="crew",
    crew_id=crew_id,
)["id"]
visible_task = work.create_task("Legacy visible policy child", actor="manager")["id"]
db.execute(
    "UPDATE tasks SET engagement_id = ? WHERE id = ?",
    (hidden_engagement, visible_task),
)
visible_blocker = blockers.raise_blocker("Legacy visible blocker", actor="manager")["id"]
db.execute("UPDATE blockers SET task_id = ? WHERE id = ?", (hidden_task, visible_blocker))
Path("../legacy-policy-blocker-id").write_text(str(visible_blocker))
db.execute(
    "UPDATE pending_changes SET policy_context = '{}', review_contract_version = 0 WHERE id = ?",
    (legacy["id"],),
)
Path("../legacy-review-id").write_text(str(legacy["id"]))

store = module.migrations[0].store
store.execute(
    "INSERT INTO work_links (external_id, skein_task_id, classification) VALUES (?, ?, ?)",
    ("ATLAS-CORE-UPGRADE", 42, "internal"),
)
# This pair reproduces a roster state that the historical core could create
# during concurrent case-variant claims. The compatible next artifact must
# quarantine it without merging either identity or its provenance.
for name, kind in (("RACE-OWNER", "human"), ("race-owner", "agent")):
    db.execute(
        "INSERT INTO users (name, kind, created_at) VALUES (?, ?, ?)",
        (name, kind, db.now()),
    )
private_notes.add_note("RACE-OWNER", "manager", "private upgrade recovery marker")
assert db.pending_migrations() == []
