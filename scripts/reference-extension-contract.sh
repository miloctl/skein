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

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p \
    "$tmp/base" "$tmp/current" "$tmp/current-source" "$tmp/next" \
    "$tmp/extension" "$tmp/extension-source" "$tmp/run"

prior_backend_tree="$(git rev-parse d611d79c3c2962adcbc09a68b92976d8baf47b4a:backend)"
next_backend_tree="$(git rev-parse HEAD:backend)"
if [[ "$prior_backend_tree" == "$next_backend_tree" ]]; then
    echo "reference-extension-contract: backend implementations must differ" >&2
    exit 1
fi

git archive d3b0f2ebbb6437b9ba34afb398d548ec955d3ae3 backend | tar -x -C "$tmp/base"
UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" \
    uv build --quiet --wheel --out-dir "$tmp/base-dist" "$tmp/base/backend"
git archive d611d79c3c2962adcbc09a68b92976d8baf47b4a backend \
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
sed -i 's/version = "0.2.0"/version = "0.2.1"/' "$tmp/core-next/pyproject.toml"
UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" \
    uv build --quiet --wheel --out-dir "$tmp/next" "$tmp/core-next"

base_wheels=("$tmp/base-dist"/skein-*.whl)
current_wheels=("$tmp/current"/skein-*.whl)
next_wheels=("$tmp/next"/skein-*.whl)
extension_wheels=("$tmp/extension"/atlas_skein_extension-*.whl)
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
assert Version("0.2.0") in requirement.specifier
assert Version("0.2.1") in requirement.specifier
PY

UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" uv venv --quiet "$tmp/venv"
UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" uv pip install --quiet \
    --python "$tmp/venv/bin/python" "${current_wheels[0]}" "${extension_wheels[0]}"

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
    SKEIN_DATA_DIR="$tmp/core-data" \
    ATLAS_SKEIN_DATA="$tmp/atlas-data/atlas.db" \
    SKEIN_PLAYBOOKS_DIR="$tmp/extension-source/content/playbooks" \
    SKEIN_PERSONAS_DIR="$tmp/extension-source/content/personas" \
    SKEIN_FLOCKS_DIR="$tmp/extension-source/content/flocks" \
    "$tmp/venv/bin/python" - <<'PY'
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.extensions import (
    AppSettings,
    IdentityContribution,
    JobExecutionContext,
    PolicyContribution,
    PolicyDecision,
    PolicyEffect,
    SKEIN_CORE_VERSION,
    SkeinModule,
)
from app.main import create_app
from app.services import review, users
from atlas_skein import AtlasSettings, atlas_module

assert version("skein") == "0.2.0"
assert SKEIN_CORE_VERSION == "0.2.0"
module = atlas_module(AtlasSettings(Path("../atlas-data/atlas.db").resolve()))
def review_playbook(request):
    if request.action == "playbook.create":
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
with TestClient(create_app(settings, (module, compatibility))) as client:
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

users.ensure_user("legacy-agent", kind="agent")
legacy = review.propose_change(
    "task",
    "create",
    {"title": "Legacy unbound task"},
    actor="legacy-agent",
    origin="agent",
)
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
assert db.pending_migrations() == []
PY
)

(
    cd "$tmp/run"
    SKEIN_DATA_DIR="$tmp/legacy-core-data" \
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
    SKEIN_DATA_DIR="$tmp/core-data" \
    ATLAS_SKEIN_DATA="$tmp/atlas-data/atlas.db" \
    SKEIN_PLAYBOOKS_DIR="$tmp/extension-source/content/playbooks" \
    SKEIN_PERSONAS_DIR="$tmp/extension-source/content/personas" \
    SKEIN_FLOCKS_DIR="$tmp/extension-source/content/flocks" \
    "$tmp/venv/bin/python" - <<'PY'
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path
import asyncio

from fastapi.testclient import TestClient

from app.extensions import (
    AppSettings,
    IdentityContribution,
    JobExecutionContext,
    PolicyContribution,
    PolicyDecision,
    PolicyEffect,
    PolicySubject,
    SKEIN_CORE_VERSION,
    SkeinModule,
)
from app.extensions.tools import ToolCallContext, execute_tool
from app.main import _job_specs, create_app
from app.public import CreateTaskCommand, UpdateTaskCommand, WorkItems
from app.public.events import dispatch_events
from app.public.workflow import WorkflowContext, WorkflowEngine
from app.services import users
from app.extensions import EventExecutionContext
from atlas_skein import AtlasSettings, atlas_module

assert version("skein") == "0.2.1"
assert SKEIN_CORE_VERSION == "0.2.1"
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
users.rename_user("RACE-OWNER", "person-owner", actor="RACE-OWNER")
assert users.folded_identity_collisions() == []
assert users.ensure_human_identity("person-owner")["kind"] == "human"
assert users.ensure_agent_identity("race-owner")["kind"] == "agent"
module = atlas_module(AtlasSettings(Path("../atlas-data/atlas.db").resolve()))
def review_playbook(request):
    if request.action == "playbook.create":
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
    finally:
        deps._resolve = original_resolve
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

workflow = WorkflowEngine(registry.workflow_actions, registry.policy_engine)
workflow_result = workflow.run(
    workflow.prepare(
        [{
            "type": "action",
            "name": "atlas.workplace.notify-manager",
            "input": {"channel": "delivery", "message": "upgrade passed"},
        }]
    ),
    WorkflowContext(subject, "workflow"),
)
assert workflow_result.status == "completed"

specs = _job_specs(registry, settings)
job = next(item for item in specs if item.name == "atlas.workplace.sync")
assert "error_code" not in job.fn()
event_job = next(item for item in specs if item.name == "extension-events")
assert event_job.fn()["delivered"] >= 1
from app import db
columns = {row["name"] for row in db.query("PRAGMA table_info(pending_changes)")}
assert "review_contract_version" in columns
assert module.migrations[0].store.query_one(
    "SELECT external_id FROM work_links WHERE skein_task_id = ?", (42,)
) == {"external_id": "ATLAS-CORE-UPGRADE"}
PY
)

(
    cd "$tmp/run"
    SKEIN_DATA_DIR="$tmp/legacy-core-data" \
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

SKEIN_DATA_DIR="$tmp/fresh-next-data" "$tmp/venv/bin/python" -c \
    "from app import db; db.init_db()"
"$tmp/venv/bin/python" - "$tmp/core-data/platform.db" \
    "$tmp/fresh-next-data/platform.db" <<'PY'
import sqlite3
import sys


def schema(path):
    connection = sqlite3.connect(path)
    try:
        return connection.execute(
            "SELECT type, name, sql FROM sqlite_master"
            " WHERE sql IS NOT NULL ORDER BY type, name"
        ).fetchall()
    finally:
        connection.close()


assert schema(sys.argv[1]) == schema(sys.argv[2]), "fresh and upgraded schemas differ"
PY

echo "reference-extension-contract: old core rejected; unchanged Atlas package passed distinct 0.2.0 -> 0.2.1 implementations"
