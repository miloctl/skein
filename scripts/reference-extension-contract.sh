#!/usr/bin/env bash
# Build the core and the fictional workplace package as separate artifacts.
# Then verify their contracts from installed files, outside both source trees.
set -euo pipefail
cd "$(dirname "$0")/.."

python="backend/.venv/bin/python"
[ -x "$python" ] || python="$(command -v python)"
if [[ "$python" != /* ]]; then
    python="$(pwd)/$python"
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/core" "$tmp/extension" "$tmp/site" "$tmp/run"

UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" \
    uv build --quiet --wheel --out-dir "$tmp/core" backend
UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" \
    uv build --quiet --wheel --out-dir "$tmp/extension" examples/workplace-extension

core_wheels=("$tmp/core"/skein-*.whl)
extension_wheels=("$tmp/extension"/atlas_skein_extension-*.whl)
[ "${#core_wheels[@]}" -eq 1 ]
[ "${#extension_wheels[@]}" -eq 1 ]

UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" uv pip install --quiet \
    --target "$tmp/site" --no-deps "${core_wheels[0]}" "${extension_wheels[0]}" \

(
    cd "$tmp/run"
    SKEIN_DATA_DIR="$tmp/core-data" \
    ATLAS_SKEIN_DATA="$tmp/atlas-data/atlas.db" \
    PYTHONPATH="$tmp/site" \
    "$python" - <<'PY'
from dataclasses import replace
from importlib.metadata import requires, version
from pathlib import Path

from app import db
from app.extensions import AppSettings, ExtensionRegistry
from app.main import create_app
from atlas_skein import AtlasSettings, atlas_module

assert version("skein") == "0.1.0"
assert version("atlas-skein-extension") == "1.0.0"
skein_requirement = next(
    item for item in (requires("atlas-skein-extension") or ()) if item.startswith("skein")
)
assert ">=0.1.0" in skein_requirement and "<0.2.0" in skein_requirement

# This call proves that the installed core wheel contains its SQL migrations.
db.init_db()
assert db.query_one("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")

path = Path("../atlas-data/atlas.db").resolve()
module = atlas_module(AtlasSettings(path))
registry = ExtensionRegistry.build((module,))
owned = registry.migrations[0]
owned.store.migrate(owned.migrations[:1])
owned.store.execute(
    "INSERT INTO work_links (external_id, skein_task_id, classification)"
    " VALUES (?, ?, ?)",
    ("ATLAS-UPGRADE", 42, "internal"),
)

settings = replace(AppSettings.from_config(), scheduler_enabled=False)
app = create_app(settings, (module,))
for contribution in app.state.skein_registry.migrations:
    contribution.store.migrate(contribution.migrations)

paths = {
    route.path
    for contribution in app.state.skein_registry.routes
    for route in contribution.router.routes
    if hasattr(route, "path")
}
assert "/api/extensions/atlas.workplace/metrics" in paths
assert owned.store.query_one(
    "SELECT external_id FROM work_links WHERE skein_task_id = ?", (42,)
) == {"external_id": "ATLAS-UPGRADE"}
assert owned.store.query_one(
    "SELECT version FROM extension_schema_version ORDER BY version DESC LIMIT 1"
) == {"version": 2}
print("reference-extension-contract: installed artifacts and upgrade are compatible")
PY
)
