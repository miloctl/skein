#!/usr/bin/env bash
# Rehearse the explicit 0.2.3 package transition and the current installed
# dependency model. No source tree is on PYTHONPATH.
set -euo pipefail
cd "$(dirname "$0")/.."

python="backend/.venv/bin/python"
[ -x "$python" ] || python="$(command -v python)"
if [[ "$python" != /* ]]; then
    python="$(pwd)/$python"
fi
contract="$(pwd)/scripts/contract"

if [ -z "${SKEIN_DATABASE_URL:-}" ]; then
    echo "reference-extension-contract: SKEIN_DATABASE_URL is not set." >&2
    exit 1
fi
contract_run_label="${SKEIN_CONTRACT_RUN_ID:-}"
release_dist="${SKEIN_RELEASE_DIST:-}"
# shellcheck source=lib/hermetic-env.sh
. "$(dirname "$0")/lib/hermetic-env.sh"
skein_hermetic_env

db_base="${SKEIN_DATABASE_URL%/*}"
if [[ ! "$contract_run_label" =~ ^[a-z0-9_]{1,20}$ ]]; then
    echo "reference-extension-contract: set SKEIN_CONTRACT_RUN_ID to 1-20 lowercase letters, digits, or underscores." >&2
    exit 1
fi
contract_run_id="${contract_run_label}_$$"
created_dbs=()
new_db() {  # new_db <label> <url-variable>
    local name="skein_contract_$1_$contract_run_id"
    if [ "${#name}" -gt 63 ]; then
        echo "reference-extension-contract: a contract database name is too long. Use a shorter run ID." >&2
        exit 1
    fi
    created_dbs+=("$name")
    psql "$SKEIN_DATABASE_URL" -qtAc "DROP DATABASE IF EXISTS \"$name\" WITH (FORCE)" >/dev/null
    psql "$SKEIN_DATABASE_URL" -qtAc "CREATE DATABASE \"$name\"" >/dev/null
    printf -v "$2" '%s' "$db_base/$name"
}
drop_dbs() {
    local name
    for name in "${created_dbs[@]}"; do
        psql "$SKEIN_DATABASE_URL" -qtAc "DROP DATABASE IF EXISTS \"$name\" WITH (FORCE)" >/dev/null 2>&1 || true
    done
}

tmp="$(mktemp -d)"
trap 'drop_dbs; rm -rf "$tmp"' EXIT

new_db core_data db_core_data
new_db legacy_core_data db_legacy_core_data
new_db extension_tests db_extension_tests_current
new_db fresh_current_data db_fresh_current_data
mkdir -p \
    "$tmp/prior-core-source" "$tmp/prior-core" \
    "$tmp/prior-extension-tree" "$tmp/prior-extension" \
    "$tmp/current-core-source" "$tmp/current-core" \
    "$tmp/current-extension-source" "$tmp/current-extension" "$tmp/run"

# v0.2.3 is the first PostgreSQL-era core. Atlas 1.x is pinned to the tree
# immediately before the dependency-model change, so the rehearsal never
# rewrites one package identity with different dependency metadata.
PRIOR_CORE="0.2.3"
PRIOR_ATLAS_REF="60f03de5"
export PRIOR_CORE
prior_backend_tree="$(git rev-parse a9f67dd4e2c5a6adc50e896a9360330d8f6b6c39:backend)"
next_backend_tree="$(git rev-parse HEAD:backend)"
if [[ "$prior_backend_tree" == "$next_backend_tree" ]]; then
    echo "reference-extension-contract: backend implementations must differ" >&2
    exit 1
fi

git archive a9f67dd4e2c5a6adc50e896a9360330d8f6b6c39 backend \
    | tar -x -C "$tmp/prior-core-source"
UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" \
    uv build --quiet --wheel --out-dir "$tmp/prior-core" "$tmp/prior-core-source/backend"

git archive "$PRIOR_ATLAS_REF" examples/workplace-extension \
    | tar -x -C "$tmp/prior-extension-tree"
prior_extension_source="$tmp/prior-extension-tree/examples/workplace-extension"
UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" \
    uv build --quiet --wheel --out-dir "$tmp/prior-extension" "$prior_extension_source"

tar --exclude=node_modules --exclude=build --exclude='*.egg-info' -cf - \
    -C examples/workplace-extension . | tar -xf - -C "$tmp/current-extension-source"
UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" \
    uv build --quiet --wheel --out-dir "$tmp/current-extension" "$tmp/current-extension-source"

tar --exclude=.venv --exclude=build --exclude='*.egg-info' -cf - -C backend . \
    | tar -xf - -C "$tmp/current-core-source"
NEXT_CORE="$(sed -n 's/^version = "\(.*\)"/\1/p' "$tmp/current-core-source/pyproject.toml" | head -1)"
export NEXT_CORE
if [[ -z "$NEXT_CORE" || "$NEXT_CORE" == "$PRIOR_CORE" ]]; then
    echo "reference-extension-contract: HEAD must claim a core version other than $PRIOR_CORE" >&2
    exit 1
fi
if [ -n "$release_dist" ]; then
    cp "$release_dist/skein_agents-$NEXT_CORE-py3-none-any.whl" "$tmp/current-core/"
else
    UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" \
        uv build --quiet --wheel --out-dir "$tmp/current-core" "$tmp/current-core-source"
fi

shopt -s nullglob
prior_core_wheels=("$tmp/prior-core"/skein-*.whl)
current_core_wheels=("$tmp/current-core"/skein_agents-*.whl)
prior_extension_wheels=("$tmp/prior-extension"/atlas_skein_extension-*.whl)
current_extension_wheels=("$tmp/current-extension"/atlas_skein_extension-*.whl)
shopt -u nullglob
[ "${#prior_core_wheels[@]}" -eq 1 ]
[ "${#current_core_wheels[@]}" -eq 1 ]
[ "${#prior_extension_wheels[@]}" -eq 1 ]
[ "${#current_extension_wheels[@]}" -eq 1 ]

PRIOR_CORE_WHEEL="${prior_core_wheels[0]}" \
NEXT_CORE_WHEEL="${current_core_wheels[0]}" \
PRIOR_EXTENSION_WHEEL="${prior_extension_wheels[0]}" \
NEXT_EXTENSION_WHEEL="${current_extension_wheels[0]}" \
    "$python" "$contract/wheel_metadata.py"

UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" uv venv --quiet --python "$python" "$tmp/venv"
run_python() {
    env -u PYTHONPATH -u PYTHONHOME PYTHONNOUSERSITE=1 "$tmp/venv/bin/python" "$@"
}
UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" uv pip install --quiet \
    --python "$tmp/venv/bin/python" \
    "${prior_core_wheels[0]}" "${prior_extension_wheels[0]}" pytest
run_python -c \
    'from importlib.metadata import version; import app; from pathlib import Path; assert version("skein") == "0.2.3"; assert "site-packages" in str(Path(app.__file__).resolve())'

mkdir -p "$tmp/legacy-playbooks" "$tmp/legacy-personas" "$tmp/legacy-flocks"
cp scripts/fixtures/legacy-content/playbooks/legacy_delivery.yaml "$tmp/legacy-playbooks/"
cp scripts/fixtures/legacy-content/personas/legacy-reviewer.md "$tmp/legacy-personas/"
cp scripts/fixtures/legacy-content/flocks/legacy-team.yaml "$tmp/legacy-flocks/"

run_python -m app.content \
    --playbooks "$prior_extension_source/content/playbooks" \
    --personas "$prior_extension_source/content/personas" \
    --flocks "$prior_extension_source/content/flocks" \
    --workflow-action atlas.workplace.notify-manager

(
    cd "$tmp/run"
    SKEIN_DATABASE_URL="$db_core_data" \
    SKEIN_PLAYBOOKS_DIR="$prior_extension_source/content/playbooks" \
    SKEIN_PERSONAS_DIR="$prior_extension_source/content/personas" \
    SKEIN_FLOCKS_DIR="$prior_extension_source/content/flocks" \
        run_python "$contract/prior_core.py"
)
(
    cd "$tmp/run"
    SKEIN_DATABASE_URL="$db_legacy_core_data" \
    SKEIN_PLAYBOOKS_DIR="$tmp/legacy-playbooks" \
    SKEIN_PERSONAS_DIR="$tmp/legacy-personas" \
    SKEIN_FLOCKS_DIR="$tmp/legacy-flocks" \
        run_python "$contract/legacy_content_prior.py"
)

UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" uv pip uninstall --quiet \
    --python "$tmp/venv/bin/python" atlas-skein-extension skein
run_python -c \
    'from importlib.metadata import PackageNotFoundError, version
for name in ("skein", "atlas-skein-extension"):
    try:
        version(name)
    except PackageNotFoundError:
        continue
    raise AssertionError(f"{name} remains installed")'

UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" uv pip install --quiet \
    --python "$tmp/venv/bin/python" \
    "${current_core_wheels[0]}" "${current_extension_wheels[0]}[test]"
UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" uv pip check --python "$tmp/venv/bin/python"
run_python -c \
    'import os; from importlib.metadata import version; import app; from pathlib import Path; assert version("skein-agents") == os.environ["NEXT_CORE"]; assert version("atlas-skein-extension") == "2.0.0"; assert "site-packages" in str(Path(app.__file__).resolve())'

run_python -m app.content \
    --playbooks "$tmp/current-extension-source/content/playbooks" \
    --personas "$tmp/current-extension-source/content/personas" \
    --flocks "$tmp/current-extension-source/content/flocks" \
    --workflow-action atlas.workplace.notify-manager

(
    cd "$tmp/run"
    SKEIN_DATABASE_URL="$db_core_data" \
    SKEIN_PLAYBOOKS_DIR="$tmp/current-extension-source/content/playbooks" \
    SKEIN_PERSONAS_DIR="$tmp/current-extension-source/content/personas" \
    SKEIN_FLOCKS_DIR="$tmp/current-extension-source/content/flocks" \
    EXTENSION_SOURCE="$tmp/current-extension-source" \
        run_python "$contract/next_core.py"
)

"$python" -m mypy \
    --python-executable "$tmp/venv/bin/python" \
    --strict \
    --follow-imports=silent \
    --no-incremental \
    "$tmp/current-extension-source/backend/src/atlas_skein" \
    "$tmp/current-extension-source/backend/typecheck_contract.py" \
    "$tmp/current-extension-source/backend/typecheck_current_contract.py"

SKEIN_DATABASE_URL="$db_extension_tests_current" \
    run_python -m pytest -q -p no:cacheprovider \
    "$tmp/current-extension-source/backend/tests"

(
    cd "$tmp/run"
    SKEIN_DATABASE_URL="$db_legacy_core_data" \
    SKEIN_PLAYBOOKS_DIR="$tmp/legacy-playbooks" \
    SKEIN_PERSONAS_DIR="$tmp/legacy-personas" \
    SKEIN_FLOCKS_DIR="$tmp/legacy-flocks" \
        run_python "$contract/legacy_content_next.py"
)

SKEIN_DATABASE_URL="$db_fresh_current_data" run_python "$contract/fresh_current.py"
run_python "$contract/schema_parity.py" "$db_core_data" "$db_fresh_current_data"

echo "reference-extension-contract: explicit skein 0.2.3/Atlas 1.x to skein-agents $NEXT_CORE/Atlas 2.0 transition passed from installed wheels"
