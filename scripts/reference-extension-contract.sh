#!/usr/bin/env bash
# Rehearse a private extension across an incompatible old core and two
# compatible installed core artifacts. No source tree is on PYTHONPATH.
set -euo pipefail
cd "$(dirname "$0")/.."

python="backend/.venv/bin/python"
[ -x "$python" ] || python="$(command -v python)"
if [[ "$python" != /* ]]; then
    python="$(pwd)/$python"
fi

if [ -z "${SKEIN_DATABASE_URL:-}" ]; then
    echo "reference-extension-contract: SKEIN_DATABASE_URL is not set." >&2
    exit 1
fi
# Isolation is a DATABASE per instance now, not a directory per instance: the
# app keeps no database under SKEIN_DATA_DIR any more, so two instances
# sharing a server would otherwise share one schema and the upgrade rehearsal
# would prove nothing. Names are suffixed with the PID so two runs on one
# server cannot drop each other's.
db_base="${SKEIN_DATABASE_URL%/*}"
new_db() {  # new_db <label> -> echoes a URL
    local name="skein_contract_$1_$$"
    psql "$SKEIN_DATABASE_URL" -qtAc "DROP DATABASE IF EXISTS \"$name\" WITH (FORCE)" >/dev/null
    psql "$SKEIN_DATABASE_URL" -qtAc "CREATE DATABASE \"$name\"" >/dev/null
    echo "$db_base/$name"
}
drop_dbs() {
    local name
    # Names come from the catalog, not from a list new_db appends to: every
    # new_db call runs inside "$(...)", so an append there mutates a SUBSHELL
    # copy and the parent's list stays empty — this dropped nothing, and each
    # run leaked five databases that never collided (the PID suffix) and so
    # never surfaced. Reading them back also cleans up after a killed run.
    for name in $(psql "$SKEIN_DATABASE_URL" -qtAc \
            "SELECT datname FROM pg_database WHERE datname LIKE 'skein_contract_%_$$'"); do
        psql "$SKEIN_DATABASE_URL" -qtAc "DROP DATABASE IF EXISTS \"$name\" WITH (FORCE)" >/dev/null 2>&1 || true
    done
}

tmp="$(mktemp -d)"
trap 'drop_dbs; rm -rf "$tmp"' EXIT

db_core_data="$(new_db core_data)"
db_legacy_core_data="$(new_db legacy_core_data)"
db_extension_tests_current="$(new_db extension_tests_current)"
db_extension_tests_next="$(new_db extension_tests_next)"
db_fresh_next_data="$(new_db fresh_next_data)"
mkdir -p \
    "$tmp/base" "$tmp/current" "$tmp/current-source" "$tmp/next" \
    "$tmp/extension" "$tmp/extension-source" "$tmp/run"

# The prior-core fixture is the v0.2.3 release commit — the first
# PostgreSQL-era version, pinned by SHA so a moved tag cannot silently change
# what "prior" means. An extension's migrations are engine-specific SQL, so
# no fixture from before the engine change can serve here: an older pin
# turns every leg below into a false failure. HEAD claims its own version in
# committed metadata — the pair is two real version identities from two real
# trees, with no rewriting. The guard stops the rehearsal from ever
# comparing one implementation with itself.
#
# This pin advances when the SUPPORTED FLOOR moves, never once per release.
# Do not convert it to "the newest tag other than HEAD" the way
# upgrade-path.sh derives its baseline: the two answer different questions.
# That one asks what a deployment runs today, so newest is right. This one
# asks whether ONE unchanged extension spans the range its own metadata
# claims (skein>=0.2.0,<0.3.0), so the fixture must be the FLOOR of that
# range — deriving it from the newest tag shrinks the rehearsed span to a
# single patch release and weakens the check silently with every tag.
# NEXT_CORE is already read from HEAD's pyproject.toml below, so tagging
# 0.2.5, 0.2.6 and onward needs no edit here at all.
PRIOR_CORE="0.2.3"
export PRIOR_CORE
prior_backend_tree="$(git rev-parse dfb8a103d67cfff9cad23492f34f2a0e63bf70ee:backend)"
next_backend_tree="$(git rev-parse HEAD:backend)"
if [[ "$prior_backend_tree" == "$next_backend_tree" ]]; then
    echo "reference-extension-contract: backend implementations must differ" >&2
    exit 1
fi

git archive d3b0f2ebbb6437b9ba34afb398d548ec955d3ae3 backend | tar -x -C "$tmp/base"
UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" \
    uv build --quiet --wheel --out-dir "$tmp/base-dist" "$tmp/base/backend"
git archive dfb8a103d67cfff9cad23492f34f2a0e63bf70ee backend \
    | tar -x -C "$tmp/current-source"
UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" \
    uv build --quiet --wheel --out-dir "$tmp/current" "$tmp/current-source/backend"
tar --exclude=node_modules --exclude=build --exclude='*.egg-info' -cf - \
    -C examples/workplace-extension . | tar -xf - -C "$tmp/extension-source"
UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" \
    uv build --quiet --wheel --out-dir "$tmp/extension" "$tmp/extension-source"

mkdir -p "$tmp/core-next"
tar --exclude=.venv --exclude=build --exclude='*.egg-info' -cf - -C backend . \
    | tar -xf - -C "$tmp/core-next"
# The next version comes from the source of truth, so a release bump does not
# need an edit here. The guard is that the pair is two DIFFERENT identities.
NEXT_CORE="$(sed -n 's/^version = "\(.*\)"/\1/p' "$tmp/core-next/pyproject.toml" | head -1)"
export NEXT_CORE
if [[ -z "$NEXT_CORE" || "$NEXT_CORE" == "$PRIOR_CORE" ]]; then
    echo "reference-extension-contract: HEAD must claim a core version other than $PRIOR_CORE" >&2
    exit 1
fi
UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" \
    uv build --quiet --wheel --out-dir "$tmp/next" "$tmp/core-next"

# nullglob, so a build that produced NO wheel gives an empty array and the
# count checks below fail. Without it bash leaves the unmatched pattern in
# place as a literal, every array holds exactly one element, and all four
# guards pass on zero wheels — the failure then surfaces at `uv pip install`
# as a missing-file error naming a path with a `*` in it.
shopt -s nullglob
base_wheels=("$tmp/base-dist"/skein-*.whl)
current_wheels=("$tmp/current"/skein-*.whl)
next_wheels=("$tmp/next"/skein-*.whl)
extension_wheels=("$tmp/extension"/atlas_skein_extension-*.whl)
shopt -u nullglob
[ "${#base_wheels[@]}" -eq 1 ]
[ "${#current_wheels[@]}" -eq 1 ]
[ "${#next_wheels[@]}" -eq 1 ]
[ "${#extension_wheels[@]}" -eq 1 ]

BASE_WHEEL="${base_wheels[0]}" EXTENSION_WHEEL="${extension_wheels[0]}" \
    "$python" - <<'PY'
import os
from pathlib import Path
from zipfile import ZipFile

from packaging.requirements import Requirement
from packaging.version import Version

def metadata(wheel: str):
    root = Path(wheel)
    with ZipFile(root) as archive:
        name = next(item for item in archive.namelist() if item.endswith(".dist-info/METADATA"))
        return archive.read(name).decode()

base = metadata(os.environ["BASE_WHEEL"])
extension = metadata(os.environ["EXTENSION_WHEEL"])
assert "Version: 0.1.0" in base
requirement = Requirement(
    next(line.removeprefix("Requires-Dist: ") for line in extension.splitlines() if line.startswith("Requires-Dist: skein"))
)
assert Version("0.1.0") not in requirement.specifier
assert Version(os.environ["PRIOR_CORE"]) in requirement.specifier
assert Version(os.environ["NEXT_CORE"]) in requirement.specifier
PY

UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" uv venv --quiet "$tmp/venv"
UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" uv pip install --quiet \
    --python "$tmp/venv/bin/python" "${current_wheels[0]}" "${extension_wheels[0]}" pytest

# A base-era deployment could have unversioned, open-ended content. Keep the
# same files in place across both installed core artifacts.
mkdir -p "$tmp/legacy-playbooks" "$tmp/legacy-personas" "$tmp/legacy-flocks"
cp scripts/fixtures/legacy-content/playbooks/legacy_delivery.yaml "$tmp/legacy-playbooks/"
cp scripts/fixtures/legacy-content/personas/legacy-reviewer.md "$tmp/legacy-personas/"
cp scripts/fixtures/legacy-content/flocks/legacy-team.yaml "$tmp/legacy-flocks/"

"$tmp/venv/bin/python" -m app.content \
    --playbooks "$tmp/extension-source/content/playbooks" \
    --personas "$tmp/extension-source/content/personas" \
    --flocks "$tmp/extension-source/content/flocks" \
    --workflow-action atlas.workplace.notify-manager

(
    cd "$tmp/run"
    SKEIN_DATABASE_URL="${db_core_data}" \
    SKEIN_PLAYBOOKS_DIR="$tmp/extension-source/content/playbooks" \
    SKEIN_PERSONAS_DIR="$tmp/extension-source/content/personas" \
    SKEIN_FLOCKS_DIR="$tmp/extension-source/content/flocks" \
    "$tmp/venv/bin/python" - <<'PY'
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path

from fastapi.testclient import TestClient

import asyncio
import os

from app import db
from app.extensions import (
    AppSettings,
    ExtensionRegistry,
    ExtensionValidationError,
    IdentityContribution,
    JobExecutionContext,
    PolicyContribution,
    PolicyDecision,
    PolicyEffect,
    PolicySubject,
    SKEIN_CORE_VERSION,
    SkeinModule,
    ToolCallContext,
    execute_tool,
    registry_for,
)
from app.main import create_app
from app.public import PublicError, WorkItems
from app.services import blockers, crews, engagements, private_notes, review, users, work
from atlas_skein import AtlasSettings, atlas_module
from atlas_skein.integration import AtlasItem, MemoryAtlasClient

prior_core = os.environ["PRIOR_CORE"]
assert version("skein") == prior_core
assert SKEIN_CORE_VERSION == prior_core
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
                minimum_core="0.2.4",
                maximum_core_exclusive="0.3.0",
            ),
        )
    )
except ExtensionValidationError as exc:
    assert "supports core versions from 0.2.4" in str(exc)
else:
    raise AssertionError("core 0.2.3 accepted a package that requires core 0.2.4")


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
    policies=(
        PolicyContribution("upgrade.workplace.playbook-review", review_playbook),
    ),
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
assert module.migrations[0].store.query_one(
    "SELECT external_id FROM sync_claims WHERE external_id = ?",
    ("ATLAS-OLD-CORE",),
) is None

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
PY
)

"$python" -m mypy \
    --python-executable "$tmp/venv/bin/python" \
    --strict \
    --follow-imports=silent \
    --no-incremental \
    "$tmp/extension-source/backend/src/atlas_skein" \
    "$tmp/extension-source/backend/typecheck_contract.py"

# The reference extension test suite must pass against the installed
# current-core artifact before the upgrade rehearses the next one.
SKEIN_DATABASE_URL="${db_extension_tests_current}" \
    "$tmp/venv/bin/python" -m pytest -q -p no:cacheprovider \
    "$tmp/extension-source/backend/tests"

(
    cd "$tmp/run"
    SKEIN_DATABASE_URL="${db_legacy_core_data}" \
    SKEIN_PLAYBOOKS_DIR="$tmp/legacy-playbooks" \
    SKEIN_PERSONAS_DIR="$tmp/legacy-personas" \
    SKEIN_FLOCKS_DIR="$tmp/legacy-flocks" \
    "$tmp/venv/bin/python" - <<'PY'
from dataclasses import replace

from fastapi.testclient import TestClient

from app.extensions import AppSettings
from app.main import create_app

with TestClient(
    create_app(replace(AppSettings.from_config(), scheduler_enabled=False)),
    headers={"X-User": "upgrade-user"},
) as client:
    assert "legacy_delivery" in {row["slug"] for row in client.get("/api/playbooks").json()}
    assert "legacy-reviewer" in {row["slug"] for row in client.get("/api/personas").json()}
    assert "legacy-team" in {row["slug"] for row in client.get("/api/flocks").json()}
PY
)

UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" uv pip install --quiet \
    --python "$tmp/venv/bin/python" --no-deps --upgrade --reinstall "${next_wheels[0]}"

(
    cd "$tmp/run"
    SKEIN_DATABASE_URL="${db_core_data}" \
    SKEIN_PLAYBOOKS_DIR="$tmp/extension-source/content/playbooks" \
    SKEIN_PERSONAS_DIR="$tmp/extension-source/content/personas" \
    SKEIN_FLOCKS_DIR="$tmp/extension-source/content/flocks" \
    "$tmp/venv/bin/python" - <<'PY'
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path
import asyncio
import os
import sys

from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.extensions import (
    AppSettings,
    ExtensionRegistry,
    IdentityContribution,
    JobExecutionContext,
    PolicyContribution,
    PolicyDecision,
    PolicyEffect,
    PolicySubject,
    SKEIN_CORE_VERSION,
    SkeinModule,
    ToolContribution,
    WorkflowActionContribution,
)
from app.extensions.tools import ToolCallContext, execute_tool
from app import db, identity_audit
from app.main import _job_specs, create_app
from app.public import CreateTaskCommand, PublicError, UpdateTaskCommand, WorkItems
from app.public.events import dispatch_events
from app.services import users
from app.extensions import EventExecutionContext
from atlas_skein import AtlasSettings, atlas_module

next_core = os.environ["NEXT_CORE"]
assert version("skein") == next_core
assert SKEIN_CORE_VERSION == next_core
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
assert [[row["name"] for row in group] for group in collisions] == [
    ["RACE-OWNER", "race-owner"]
]
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
from app.services import private_notes
assert [
    row["body"] for row in private_notes.list_notes("person-owner", "manager")
] == ["private upgrade recovery marker"]
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
    policies=(
        PolicyContribution("upgrade.workplace.playbook-review", review_playbook),
    ),
)
settings = replace(AppSettings.from_config(), scheduler_enabled=False)
app = create_app(settings, (module, compatibility, local_write_module))
with TestClient(app) as client:
    assert client.get("/health").status_code == 200
    assert "atlas_delivery" in {
        item["slug"] for item in client.get("/api/playbooks").json()
    }
    from app.routes import deps
    original_resolve = deps._resolve
    deps._resolve = lambda *_args, **_kwargs: (
        "mira", True, ("atlas-delivery-managers", "atlas-integrations")
    )
    try:
        assert client.get(
            "/api/extensions/atlas.workplace/metrics",
            headers={"X-User": "mira"},
        ).status_code == 200
        assert client.post(
            "/api/extensions/atlas.workplace/sync",
            headers={"X-User": "mira"},
            json={"full": False},
        ).status_code == 200
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
        "manager", True, ("atlas-delivery-managers", "atlas-integrations")
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
tool_result = asyncio.run(
    execute_tool(
        tool,
        {"full": False},
        ToolCallContext(subject, "atlas.workplace.delivery-specialist"),
        registry.policy_engine,
    )
)
assert tool_result.status == "completed"

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
PY
)

"$python" -m mypy \
    --python-executable "$tmp/venv/bin/python" \
    --strict \
    --follow-imports=silent \
    --no-incremental \
    "$tmp/extension-source/backend/src/atlas_skein" \
    "$tmp/extension-source/backend/typecheck_contract.py" \
    "$tmp/extension-source/backend/typecheck_current_contract.py"

# The unchanged extension test suite must also pass on the upgraded core.
SKEIN_DATABASE_URL="${db_extension_tests_next}" \
    "$tmp/venv/bin/python" -m pytest -q -p no:cacheprovider \
    "$tmp/extension-source/backend/tests"

(
    cd "$tmp/run"
    SKEIN_DATABASE_URL="${db_legacy_core_data}" \
    SKEIN_PLAYBOOKS_DIR="$tmp/legacy-playbooks" \
    SKEIN_PERSONAS_DIR="$tmp/legacy-personas" \
    SKEIN_FLOCKS_DIR="$tmp/legacy-flocks" \
    "$tmp/venv/bin/python" - <<'PY'
from dataclasses import replace
import sys

from fastapi.testclient import TestClient

from app import identity_audit
from app.extensions import AppSettings
from app.main import create_app

sys.argv = ["identity_audit", "claim-machine", "mcp-agent", "mcp"]
identity_audit.main()
with TestClient(
    create_app(replace(AppSettings.from_config(), scheduler_enabled=False)),
    headers={"X-User": "upgrade-user"},
) as client:
    assert "legacy_delivery" in {row["slug"] for row in client.get("/api/playbooks").json()}
    assert "legacy-reviewer" in {row["slug"] for row in client.get("/api/personas").json()}
    assert "legacy-team" in {row["slug"] for row in client.get("/api/flocks").json()}
    started = client.post(
        "/api/playbooks/instantiate",
        json={
            "playbook": "legacy_delivery",
            "engagement_name": "Typed legacy after upgrade",
        },
    )
    assert started.status_code == 200, started.text
    assert started.json()["engagement"]["name"] == "Typed legacy after upgrade"
PY
)

SKEIN_DATABASE_URL="${db_fresh_next_data}" "$tmp/venv/bin/python" -c \
    "from app import db; db.init_db()"
"$tmp/venv/bin/python" - "$db_core_data" "$db_fresh_next_data" <<'PY'
import sys

import psycopg


def schema(url):
    """Every table and column, as the catalog reports them.

    The catalog, not a dump: pg_dump output carries owners, comments and an
    ordering that differ between two freshly created databases, which would
    make every run a false failure.
    """
    with psycopg.connect(url) as connection:
        return connection.execute(
            "SELECT table_name, column_name, data_type, is_nullable, column_default"
            " FROM information_schema.columns WHERE table_schema = 'public'"
            " ORDER BY table_name, column_name"
        ).fetchall()


upgraded, fresh = schema(sys.argv[1]), schema(sys.argv[2])
if upgraded != fresh:
    print("upgraded-only:", [r for r in upgraded if r not in fresh][:10])
    print("fresh-only:", [r for r in fresh if r not in upgraded][:10])
    sys.exit("reference-extension-contract: upgraded schema differs from fresh")
PY

echo "reference-extension-contract: old core rejected; unchanged Atlas sync and strict source checks passed distinct $PRIOR_CORE -> $NEXT_CORE implementations; $NEXT_CORE declared tool errors and reviewed local writes passed"
