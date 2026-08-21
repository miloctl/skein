import asyncio
import os
import sys
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path

from _expect import ok
from atlas_skein import AtlasSettings, atlas_module
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app import config, db, identity_audit

# Five of the names below are imported and never called. They were unused
# while this file was a heredoc, where nothing could see them; the extraction
# is behaviour-preserving, so they stay. In a cross-version rehearsal an
# import is not decoration — it is the check that the NEXT core still exports
# a name an extension built against the prior core imports, and prior_core.py
# does call two of them. Delete one only together with a decision that the
# next core no longer owes that export.
from app.extensions import (
    SKEIN_CORE_VERSION,
    AppSettings,
    EventExecutionContext,  # noqa: F401 — export-surface check
    ExtensionRegistry,
    IdentityContribution,
    JobExecutionContext,  # noqa: F401 — export-surface check
    PolicyContribution,
    PolicyDecision,
    PolicyEffect,
    PolicySubject,
    SkeinModule,
    ToolContribution,
    WorkflowActionContribution,
)
from app.extensions.tools import ToolCallContext, execute_tool
from app.main import _job_specs, create_app
from app.public import (  # noqa: F401 — UpdateTaskCommand/WorkItems are export-surface checks
    CreateTaskCommand,
    PublicError,
    UpdateTaskCommand,
    WorkItems,
)
from app.public.events import dispatch_events  # noqa: F401 — export-surface check
from app.services import users

next_core = os.environ["NEXT_CORE"]
assert version("skein") == next_core
assert next_core == SKEIN_CORE_VERSION
assert (Path(db.__file__).resolve().parent / "py.typed").is_file()
# The installed wheel must carry the migrations directory: a packaging edit
# that drops it boots a fresh database into "no such table" with no other
# CI symptom, because every test tree runs from source.
assert (db.MIGRATIONS_DIR / "001_baseline.sql").is_file()
# An upgrade applies core migrations before it uses the new public contracts.
# Application startup does this automatically. The artifact rehearsal uses
# identity helpers before startup, so it applies the same step explicitly.
db.init_db()
# Nothing can infer private ownership from a generic machine row (the seeds
# above carry the schema's empty identity_owner). The deployment makes this
# one-time decision before application start.
for legacy_name, owner in (
    ("atlas.workplace.delivery-specialist", "specialist:atlas.workplace.delivery-specialist"),
    ("atlas-sync", "service:atlas.workplace.sync-identity"),
    ("atlas-events", "service:atlas.workplace.event-identity"),
    ("mcp-agent", "mcp"),
):
    sys.argv = ["identity_audit", "claim-machine", legacy_name, owner]
    identity_audit.main()
collisions = users.folded_identity_collisions()
assert [[row["name"] for row in group] for group in collisions] == [["RACE-OWNER", "race-owner"]]
for helper, name in (
    (users.ensure_human_identity, "RACE-OWNER"),
    (users.ensure_agent_identity, "race-owner"),
):
    try:
        helper(name)
    except ValueError as exc:
        assert "conflicting roster ownership" in str(exc)
    else:
        raise AssertionError("an ambiguous upgraded identity was not quarantined")
sys.argv = ["identity_audit", "rename", "RACE-OWNER", "person-owner"]
identity_audit.main()
assert users.folded_identity_collisions() == []
assert users.ensure_human_identity("person-owner")["kind"] == "human"
assert users.ensure_agent_identity("race-owner")["kind"] == "agent"
# after the rename above, deliberately: this file preserves the ordering the
# heredoc had, and hoisting a service import ahead of identity_audit.main()
# is unverified rather than obviously safe.
from app.services import private_notes  # noqa: E402

assert [row["body"] for row in private_notes.list_notes("person-owner", "manager")] == [
    "private upgrade recovery marker"
]
assert any(
    row["action"] == "system_identity_repair:RACE-OWNER->person-owner"
    for row in private_notes.list_audit("person-owner")
)
module = atlas_module(AtlasSettings("atlas-contract"))


class ReviewedWorkIn(BaseModel):
    title: str


class ReviewedWorkOut(BaseModel):
    task_id: int


def create_reviewed_work(context, request):
    task = context.work_items.create_task(
        CreateTaskCommand(
            title=request.title,
            idempotency_key="current-reviewed-local-write",
        ),
        context.command_context(project_type="standard"),
    )
    return {"task_id": task.id}


def review_local_work(request):
    if request.action == "next.workplace.task.create":
        return PolicyDecision(
            PolicyEffect.REVIEW,
            approver_capabilities=("upgrade.approve",),
        )
    return None


Path("../extension-source/content/playbooks/current_reviewed_work.yaml").write_text(
    """\
schema_version: 1
name: Current reviewed local work
project_class: standard
milestones:
  - title: Prepare
workflow:
  - type: action
    name: next.workplace.create-task
    input:
      title: Installed reviewed workflow task
"""
)
local_write_module = SkeinModule(
    module_id="next.workplace",
    version="1.0.0",
    extension_api="1.0",
    minimum_core="0.2.1",
    maximum_core_exclusive="0.3.0",
    workflow_actions=(
        WorkflowActionContribution(
            name="next.workplace.create-task",
            version="1.0.0",
            handler=create_reviewed_work,
            input_schema=ReviewedWorkIn,
            output_schema=ReviewedWorkOut,
            effect="write",
            risk="high",
            policy_action="next.workplace.task.create",
            timeout_seconds=1,
        ),
    ),
    policies=(PolicyContribution("next.workplace.local-review", review_local_work),),
)


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
    maximum_core_exclusive="0.3.0",
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
app = create_app(settings, (module, compatibility, local_write_module))
with TestClient(app) as client:
    assert client.get("/health").status_code == 200
    assert "atlas_delivery" in {item["slug"] for item in ok(client.get("/api/playbooks"))}
    from app.routes import deps

    original_resolve = deps._resolve
    deps._resolve = lambda *_args, **_kwargs: (
        "mira",
        True,
        ("atlas-delivery-managers", "atlas-integrations"),
    )
    try:
        assert (
            client.get(
                "/api/extensions/atlas.workplace/metrics",
                headers={"X-User": "mira"},
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/extensions/atlas.workplace/sync",
                headers={"X-User": "mira"},
                json={"full": False},
            ).status_code
            == 200
        )
        deps._resolve = lambda *_args, **_kwargs: ("ava", True, ())
        workflow_started = client.post(
            "/api/playbooks/instantiate",
            headers={"X-User": "ava"},
            json={
                "playbook": "atlas_delivery",
                "engagement_name": "Atlas workflow upgrade",
            },
        )
        assert workflow_started.status_code == 200, workflow_started.text
        workflow_review_id = workflow_started.json()["workflow"]["review_id"]
        local_started = client.post(
            "/api/playbooks/instantiate",
            headers={"X-User": "ava"},
            json={
                "playbook": "current_reviewed_work",
                "engagement_name": "Installed reviewed local work",
            },
        )
        assert local_started.status_code == 200, local_started.text
        local_review_id = local_started.json()["workflow"]["review_id"]
    finally:
        deps._resolve = original_resolve
    deps._resolve = lambda *_args, **_kwargs: (
        "manager",
        True,
        ("atlas-delivery-managers", "atlas-integrations"),
    )
    try:
        workflow_approved = client.post(
            f"/api/review/{workflow_review_id}/approve",
            headers={"X-User": "manager"},
            json={"note": "Approve the installed workflow action."},
        )
        local_approved = client.post(
            f"/api/review/{local_review_id}/approve",
            headers={"X-User": "manager"},
            json={"note": "Approve the installed local-write action."},
        )
    finally:
        deps._resolve = original_resolve
    assert workflow_approved.status_code == 200, workflow_approved.text
    assert workflow_approved.json()["result"]["workflow"]["status"] == "completed"
    assert local_approved.status_code == 200, local_approved.text
    assert local_approved.json()["result"]["workflow"]["status"] == "completed"
    assert db.query_one(
        "SELECT title FROM tasks WHERE title = ?",
        ("Installed reviewed workflow task",),
    ) == {"title": "Installed reviewed workflow task"}
    review_id = int(Path("../pending-review-id").read_text())
    approved = client.post(
        f"/api/review/{review_id}/approve",
        headers={"X-User": "manager"},
        json={"note": "Approve the unchanged previous-release definition."},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["result"]["engagement"]["name"] == "Digest upgrade review"
    legacy_review_id = int(Path("../legacy-review-id").read_text())
    refused = client.post(
        f"/api/review/{legacy_review_id}/approve",
        headers={"X-User": "manager"},
        json={"note": "This old proposal has no policy binding."},
    )
    assert refused.status_code == 403, refused.text
    rejected = client.post(
        f"/api/review/{legacy_review_id}/reject",
        headers={"X-User": "manager"},
        json={"note": "Replace this proposal through the current policy path."},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    listed = client.get("/api/tasks", headers={"X-User": "manager"})
    assert listed.status_code == 200, listed.text
    assert "Legacy visible policy child" not in {row["title"] for row in listed.json()}
    blocker_id = int(Path("../legacy-policy-blocker-id").read_text())
    blockers_list = client.get("/api/blockers", headers={"X-User": "manager"})
    assert blockers_list.status_code == 200, blockers_list.text
    assert blocker_id not in {row["id"] for row in blockers_list.json()}
    concealed = client.patch(
        f"/api/blockers/{blocker_id}",
        headers={"X-User": "manager"},
        json={"title": "must remain unchanged"},
    )
    assert concealed.status_code == 404, concealed.text
    assert concealed.json() == {"detail": f"no blocker #{blocker_id}"}

registry = app.state.skein_registry
subject = PolicySubject(
    "mira",
    groups=("atlas-delivery-managers", "atlas-integrations"),
    capabilities=("atlas.dashboard", "atlas.specialist", "atlas.integration"),
)
tool = registry.tools[0]


def run_sync_tool():
    return asyncio.run(
        execute_tool(
            tool,
            {"full": False},
            ToolCallContext(subject, "atlas.workplace.delivery-specialist"),
            registry.policy_engine,
        )
    )


# The review gate is a DEPLOYMENT policy, and it decides what a governed
# extension tool RETURNS: the write itself, or a proposal for a human. Both
# are correct outcomes of the same contract — the core routed the extension's
# tool and neither refused nor errored. Only an error or a refusal is a
# failure here.
#
# Rehearsed on BOTH settings rather than inheriting one. This assertion used
# to read `status == "completed"` against whatever default was compiled in,
# so it silently encoded "the gate is off" as if it were the extension
# contract — and flipping that default turned an unrelated release into a red
# CI job pointing at the extension. Set explicitly, the flip is a fact this
# gate states instead of a surprise it reports.
original_agent_review = config.AGENT_REVIEW
try:
    config.AGENT_REVIEW = False
    direct = run_sync_tool()
    assert direct.status == "completed", direct

    config.AGENT_REVIEW = True
    gated = run_sync_tool()
    # review_required carries the proposal a human judges (app/extensions/
    # tools.py). An extension author reads this as: with the gate on, a
    # governed tool's effect lands only after a verdict.
    assert gated.status == "review_required", gated
    assert gated.review_id > 0, gated
finally:
    config.AGENT_REVIEW = original_agent_review


class FailureIn(BaseModel):
    marker: str = ""


class FailureOut(BaseModel):
    accepted: bool


def declared_failure(_context, _request):
    raise PublicError("DECLARED_UNAVAILABLE", "The remote service is unavailable.")


error_module = SkeinModule(
    module_id="next.workplace",
    version="1.0.0",
    extension_api="1.0",
    minimum_core="0.2.1",
    maximum_core_exclusive="0.3.0",
    tools=(
        ToolContribution(
            name="next.workplace.failure-tool",
            version="1.0.0",
            model_name="next_workplace_failure",
            description="Verify the public declared-error contract.",
            handler=declared_failure,
            input_schema=FailureIn,
            output_schema=FailureOut,
            effect="read",
            risk="low",
            policy_action="next.workplace.read",
            error_codes=("DECLARED_UNAVAILABLE",),
        ),
    ),
)
error_registry = ExtensionRegistry.build((error_module,))
error_result = asyncio.run(
    execute_tool(
        error_registry.tools[0],
        {},
        ToolCallContext(PolicySubject("upgrade-service", kind="service"), ""),
        error_registry.policy_engine,
    )
)
assert error_result.status == "failed"
assert error_result.error_code == "DECLARED_UNAVAILABLE"
assert error_result.detail == "The remote service is unavailable."

specs = _job_specs(registry, settings)
job = next(item for item in specs if item.name == "atlas.workplace.sync")
assert "error_code" not in job.fn()
event_job = next(item for item in specs if item.name == "extension-events")
assert event_job.fn()["delivered"] >= 1
CATALOG = (
    "SELECT column_name AS name FROM information_schema.columns"
    " WHERE table_schema = 'public' AND table_name = ?"
)
columns = {row["name"] for row in db.query(CATALOG, ("pending_changes",))}
assert "review_contract_version" in columns
assert "identity_owner" in {row["name"] for row in db.query(CATALOG, ("users",))}
assert module.migrations[0].store.query_one(
    "SELECT external_id FROM work_links WHERE skein_task_id = ?", (42,)
) == {"external_id": "ATLAS-CORE-UPGRADE"}
