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

# The rehearsal steps, as importable files rather than heredocs. Absolute,
# because most steps run with cwd set to "$tmp/run". Each file runs under the
# INSTALLED artifact's interpreter, never this repo's — that is the whole
# point of the rehearsal, so these are not importable from backend/ and mypy
# does not type them (its `files` list is app + seed.py). ruff does check
# them, which is 654 lines it could not see while they were heredocs.
contract="$(pwd)/scripts/contract"

if [ -z "${SKEIN_DATABASE_URL:-}" ]; then
    echo "reference-extension-contract: SKEIN_DATABASE_URL is not set." >&2
    exit 1
fi
# Every OTHER SKEIN_* setting is this script's to state, not the caller's.
# scripts/lib/hermetic-env.sh says which two regressions that closes.
# shellcheck source=lib/hermetic-env.sh
. "$(dirname "$0")/lib/hermetic-env.sh"
skein_hermetic_env
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
prior_backend_tree="$(git rev-parse a9f67dd4e2c5a6adc50e896a9360330d8f6b6c39:backend)"
next_backend_tree="$(git rev-parse HEAD:backend)"
if [[ "$prior_backend_tree" == "$next_backend_tree" ]]; then
    echo "reference-extension-contract: backend implementations must differ" >&2
    exit 1
fi

git archive 4b642300f96bb9e4944a640f373a41512d50f1f0 backend | tar -x -C "$tmp/base"
UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" \
    uv build --quiet --wheel --out-dir "$tmp/base-dist" "$tmp/base/backend"
git archive a9f67dd4e2c5a6adc50e896a9360330d8f6b6c39 backend \
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
    "$python" "$contract/wheel_metadata.py"

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
    "$tmp/venv/bin/python" "$contract/prior_core.py"
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
    "$tmp/venv/bin/python" "$contract/legacy_content_prior.py"
)

UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" uv pip install --quiet \
    --python "$tmp/venv/bin/python" --no-deps --upgrade --reinstall "${next_wheels[0]}"

(
    cd "$tmp/run"
    SKEIN_DATABASE_URL="${db_core_data}" \
    SKEIN_PLAYBOOKS_DIR="$tmp/extension-source/content/playbooks" \
    SKEIN_PERSONAS_DIR="$tmp/extension-source/content/personas" \
    SKEIN_FLOCKS_DIR="$tmp/extension-source/content/flocks" \
    "$tmp/venv/bin/python" "$contract/next_core.py"
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
    "$tmp/venv/bin/python" "$contract/legacy_content_next.py"
)

SKEIN_DATABASE_URL="${db_fresh_next_data}" "$tmp/venv/bin/python" -c \
    "from app import db; db.init_db()"
"$tmp/venv/bin/python" "$contract/schema_parity.py" "$db_core_data" "$db_fresh_next_data"

echo "reference-extension-contract: old core rejected; unchanged Atlas sync and strict source checks passed distinct $PRIOR_CORE -> $NEXT_CORE implementations; $NEXT_CORE declared tool errors and reviewed local writes passed"
