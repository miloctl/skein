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

git archive d3b0f2ebbb6437b9ba34afb398d548ec955d3ae3 backend | tar -x -C "$tmp/base"
UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" \
    uv build --quiet --wheel --out-dir "$tmp/base-dist" "$tmp/base/backend"
tar --exclude=.venv --exclude=build --exclude='*.egg-info' -cf - -C backend . \
    | tar -xf - -C "$tmp/current-source"
UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" \
    uv build --quiet --wheel --out-dir "$tmp/current" "$tmp/current-source"
tar --exclude=node_modules --exclude=build --exclude='*.egg-info' -cf - \
    -C examples/workplace-extension . | tar -xf - -C "$tmp/extension-source"
UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" \
    uv build --quiet --wheel --out-dir "$tmp/extension" "$tmp/extension-source"

cp -R "$tmp/current-source" "$tmp/core-next"
sed -i 's/version = "0.2.0"/version = "0.2.1"/' "$tmp/core-next/pyproject.toml"
cp scripts/fixtures/015_compatible_upgrade_probe.sql \
    "$tmp/core-next/app/core_migrations/015_compatible_upgrade_probe.sql"
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

(
    cd "$tmp/run"
    SKEIN_DATA_DIR="$tmp/core-data" \
    ATLAS_SKEIN_DATA="$tmp/atlas-data/atlas.db" \
    "$tmp/venv/bin/python" - <<'PY'
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.extensions import AppSettings, SKEIN_CORE_VERSION
from app.main import create_app
from atlas_skein import AtlasSettings, atlas_module

assert version("skein") == "0.2.0"
assert SKEIN_CORE_VERSION == "0.2.0"
module = atlas_module(AtlasSettings(Path("../atlas-data/atlas.db").resolve()))
settings = replace(AppSettings.from_config(), scheduler_enabled=False)
with TestClient(create_app(settings, (module,))) as client:
    assert client.get("/health").status_code == 200
    assert client.get("/api/playbooks").status_code == 200
    assert client.get("/api/personas").status_code == 200

store = module.migrations[0].store
store.execute(
    "INSERT INTO work_links (external_id, skein_task_id, classification) VALUES (?, ?, ?)",
    ("ATLAS-CORE-UPGRADE", 42, "internal"),
)
assert db.pending_migrations() == []
PY
)

UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" uv pip install --quiet \
    --python "$tmp/venv/bin/python" --no-deps --upgrade --reinstall "${next_wheels[0]}"

(
    cd "$tmp/run"
    SKEIN_DATA_DIR="$tmp/core-data" \
    ATLAS_SKEIN_DATA="$tmp/atlas-data/atlas.db" \
    "$tmp/venv/bin/python" - <<'PY'
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path

from fastapi.testclient import TestClient

from app.extensions import AppSettings, SKEIN_CORE_VERSION
from app.main import create_app
from atlas_skein import AtlasSettings, atlas_module

assert version("skein") == "0.2.1"
assert SKEIN_CORE_VERSION == "0.2.1"
module = atlas_module(AtlasSettings(Path("../atlas-data/atlas.db").resolve()))
settings = replace(AppSettings.from_config(), scheduler_enabled=False)
with TestClient(create_app(settings, (module,))) as client:
    assert client.get("/health").status_code == 200
from app import db
assert db.query_one(
    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
    ("compatible_upgrade_probe",),
) == {"name": "compatible_upgrade_probe"}
assert module.migrations[0].store.query_one(
    "SELECT external_id FROM work_links WHERE skein_task_id = ?", (42,)
) == {"external_id": "ATLAS-CORE-UPGRADE"}
PY
)

echo "reference-extension-contract: old core rejected; 0.2.0 -> 0.2.1 installed upgrade passed"
