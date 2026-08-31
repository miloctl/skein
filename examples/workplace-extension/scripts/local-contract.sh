#!/usr/bin/env bash
# Build one unpublished Skein artifact set, then reuse its exact bytes through
# package, image, PostgreSQL, OIDC, and browser gates owned by this consumer.
set -euo pipefail
cd "$(dirname "$0")/.."
root="$PWD"

for command in python3.12 uv docker curl psql tar sha256sum kubectl; do
    command -v "$command" >/dev/null || {
        echo "local-contract: $command is not installed." >&2
        exit 1
    }
done
if [ -z "${SKEIN_DATABASE_URL:-}" ]; then
    echo "local-contract: Set SKEIN_DATABASE_URL to the PostgreSQL administrator database." >&2
    exit 1
fi
admin_database_url="$SKEIN_DATABASE_URL"
unset SKEIN_DATABASE_URL
source_root="${SKEIN_SOURCE:-}"
artifact_root="${SKEIN_LOCAL_DIST:-}"
if [ -n "$source_root" ] && [ -n "$artifact_root" ]; then
    echo "local-contract: Set only SKEIN_SOURCE or SKEIN_LOCAL_DIST." >&2
    exit 1
fi
if [ -z "$source_root" ] && [ -z "$artifact_root" ]; then
    echo "local-contract: Set SKEIN_SOURCE or SKEIN_LOCAL_DIST." >&2
    exit 1
fi
run_label="${SKEIN_CONTRACT_RUN_ID:-local}"
if [[ ! "$run_label" =~ ^[a-z0-9_]{1,16}$ ]]; then
    echo "local-contract: SKEIN_CONTRACT_RUN_ID must use 1-16 lowercase letters, digits, or underscores." >&2
    exit 1
fi

api_port="${SKEIN_CONTRACT_API_PORT:-8701}"
invalid_port="${SKEIN_CONTRACT_INVALID_PORT:-8702}"
app_port="${SKEIN_CONTRACT_APP_PORT:-3701}"
idp_port="${SKEIN_CONTRACT_IDP_PORT:-8710}"
node22_image="node:22-bookworm@sha256:8a34c4ab3ea2c5cd194f07e317b2a8f09461d3c8b05c4e34c8ccd56d56024c4d"
for port in "$api_port" "$invalid_port" "$app_port" "$idp_port"; do
    [[ "$port" =~ ^[0-9]{2,5}$ ]] && [ "$port" -le 65535 ] || {
        echo "local-contract: A contract port is invalid." >&2
        exit 1
    }
done

run_id="${run_label}_$$"
role_name="skein_atlas_role_${run_id}"
role_password="$(python3.12 -c 'import secrets; print(secrets.token_hex(24))')"
role_created=""
tmp="$(mktemp -d "/tmp/atlas-contract.${run_id}.XXXXXX")"
artifacts="$tmp/artifacts"
stage="$tmp/consumer"
run_dir="$tmp/run"
node_home="$tmp/node22"
venv="$tmp/venv"
mkdir -p "$artifacts" "$stage" "$run_dir" "$node_home/bin" "$node_home/lib" "$tmp/node-home"

node_container=""
containers=()
images=()
databases=()
db_helper() {
    SKEIN_DATABASE_URL="$admin_database_url" \
    SKEIN_CONTRACT_ROLE_NAME="$role_name" \
    SKEIN_CONTRACT_ROLE_PASSWORD="$role_password" \
        "$venv/bin/python" "$root/scripts/contract-db.py" "$@"
}
cleanup() {
    status=$?
    trap - EXIT
    for ((index=${#containers[@]} - 1; index >= 0; index--)); do
        docker rm -f "${containers[$index]}" >/dev/null 2>&1 || true
    done
    if [ -n "$node_container" ]; then
        docker rm -f "$node_container" >/dev/null 2>&1 || true
    fi
    if [ -x "$venv/bin/python" ]; then
        for database in "${databases[@]}"; do
            db_helper drop "$database" >/dev/null 2>&1 || true
        done
        if [ -n "$role_created" ]; then
            db_helper drop-role >/dev/null 2>&1 || true
        fi
    fi
    for image in "${images[@]}"; do
        docker image rm -f "$image" >/dev/null 2>&1 || true
    done
    rm -rf "$tmp"
    exit "$status"
}
trap cleanup EXIT

node_container="$(docker create "$node22_image")"
docker cp "$node_container:/usr/local/bin/node" "$node_home/bin/node"
docker cp "$node_container:/usr/local/lib/node_modules" "$node_home/lib/node_modules"
docker rm "$node_container" >/dev/null
node_container=""
ln -s ../lib/node_modules/npm/bin/npm-cli.js "$node_home/bin/npm"
ln -s ../lib/node_modules/npm/bin/npx-cli.js "$node_home/bin/npx"
export PATH="$node_home/bin:$PATH"
node_command=(
    env -i HOME="$tmp/node-home" PATH="$PATH"
    npm_config_cache="$tmp/npm-cache"
    NPM_CONFIG_REGISTRY="${NPM_CONFIG_REGISTRY:-https://registry.npmjs.org/}"
)
[[ "$(node --version)" == v22.* ]] || {
    echo "local-contract: Node 22 is required." >&2
    exit 1
}

# The staging copy is the only consumer tree this contract mutates.
tar --exclude=.git --exclude=node_modules --exclude=dist --exclude=build \
    --exclude=.venv-test --exclude=.pytest_cache -cf - -C "$root" . \
    | tar -xf - -C "$stage"
mkdir -p "$stage/dist"

if [ -n "$source_root" ]; then
    source_root="$(cd "$source_root" && pwd)"
    test -f "$source_root/backend/pyproject.toml"
    test -f "$source_root/frontend/package.json"
    mkdir -p "$tmp/skein-backend" "$tmp/skein-frontend"
    tar --exclude=.venv --exclude=build --exclude=data -cf - \
        -C "$source_root/backend" . | tar -xf - -C "$tmp/skein-backend"
    tar --exclude=node_modules --exclude='.next*' --exclude=dist \
        --exclude=test-results -cf - -C "$source_root/frontend" . \
        | tar -xf - -C "$tmp/skein-frontend"
    UV_PYTHON=python3.12 uv build --quiet --wheel --out-dir "$artifacts" \
        "$tmp/skein-backend"
    "${node_command[@]}" npm pack --silent \
        --pack-destination "$artifacts" \
        "$tmp/skein-frontend/packages/extension-api" >/dev/null
    "${node_command[@]}" npm pack --silent \
        --pack-destination "$artifacts" "$tmp/skein-frontend" >/dev/null
else
    artifact_root="$(cd "$artifact_root" && pwd)"
    python3.12 - "$artifact_root" <<'PY'
import hashlib
import sys
from pathlib import Path

root = Path(sys.argv[1])
expected = {
    "skein_agents-0.4.0-py3-none-any.whl",
    "miloctl-skein-extension-api-1.0.0.tgz",
    "miloctl-skein-frontend-host-0.4.0.tgz",
}
entries = {}
manifest = root / "SHA256SUMS"
if not manifest.is_file():
    raise SystemExit("local-contract: SHA256SUMS is absent from the shared artifact directory.")
for line in manifest.read_text().splitlines():
    digest, separator, name = line.partition("  ")
    if (
        not separator
        or len(digest) != 64
        or any(c not in "0123456789abcdef" for c in digest)
        or name in entries
    ):
        raise SystemExit("local-contract: The shared artifact manifest is invalid.")
    entries[name] = digest
if set(entries) != expected:
    raise SystemExit("local-contract: The shared artifact manifest does not name the exact Skein set.")
for name, digest in entries.items():
    artifact = root / name
    if not artifact.is_file() or hashlib.sha256(artifact.read_bytes()).hexdigest() != digest:
        raise SystemExit("local-contract: A shared Skein artifact does not match SHA256SUMS.")
PY
    cp "$artifact_root/skein_agents-0.4.0-py3-none-any.whl" "$artifacts/"
    cp "$artifact_root/miloctl-skein-extension-api-1.0.0.tgz" "$artifacts/"
    cp "$artifact_root/miloctl-skein-frontend-host-0.4.0.tgz" "$artifacts/"
fi
UV_PYTHON=python3.12 uv build --quiet --wheel --out-dir "$artifacts" "$stage"

for artifact in \
    skein_agents-0.4.0-py3-none-any.whl \
    atlas_skein_extension-2.0.0-py3-none-any.whl \
    miloctl-skein-extension-api-1.0.0.tgz \
    miloctl-skein-frontend-host-0.4.0.tgz; do
    test -f "$artifacts/$artifact"
done
artifact_count="$(find "$artifacts" -maxdepth 1 -type f ! -name '.*' | wc -l)"
[ "$artifact_count" -eq 4 ] || {
    echo "local-contract: The first-party artifact set is not exact." >&2
    exit 1
}
(
    cd "$artifacts"
    sha256sum * >"$tmp/artifacts.sha256"
)
cp "$artifacts"/* "$stage/dist/"

(
    cd "$stage"
    "${node_command[@]}" npm update @miloctl/skein-frontend-host \
        --package-lock-only --ignore-scripts --no-audit --no-fund >/dev/null
    "${node_command[@]}" npm ci --ignore-scripts --no-audit --no-fund >/dev/null
    "${node_command[@]}" npm run build:extension >/dev/null
    "${node_command[@]}" node - <<'JS'
const lock = require("./package-lock.json");
for (const name of ["@miloctl/skein-extension-api", "@miloctl/skein-frontend-host"]) {
  const entry = lock.packages[`node_modules/${name}`];
  if (!entry?.integrity || entry.link) throw new Error(`${name} is not an integrity-locked tarball`);
}
JS
)
for artifact in "$artifacts"/*; do
    cmp "$artifact" "$stage/dist/$(basename "$artifact")"
done
(
    cd "$artifacts"
    sha256sum -c "$tmp/artifacts.sha256" >/dev/null
)

UV_CACHE_DIR="$tmp/uv-cache" uv venv --quiet --python python3.12 "$venv"
UV_CACHE_DIR="$tmp/uv-cache" uv pip install --quiet \
    --python "$venv/bin/python" --require-hashes -r "$stage/requirements-test.lock"
UV_CACHE_DIR="$tmp/uv-cache" uv pip install --quiet \
    --python "$venv/bin/python" --no-deps \
    "$artifacts/skein_agents-0.4.0-py3-none-any.whl" \
    "$artifacts/atlas_skein_extension-2.0.0-py3-none-any.whl"
uv pip check --python "$venv/bin/python"
"$venv/bin/python" -m mypy \
    --python-executable "$venv/bin/python" --strict --follow-imports=silent \
    --no-incremental "$stage/backend/src/atlas_skein"
"$venv/bin/python" -m app.content \
    --playbooks "$stage/content/playbooks" \
    --personas "$stage/content/personas" \
    --flocks "$stage/content/flocks" \
    --workflow-action atlas.workplace.notify-manager

db_helper create-role
role_created=1
package_db="skein_atlas_contract_${run_id}_pkg"
db_helper create "$package_db"
databases+=("$package_db")
db_helper run-clean "$package_db" \
    env -C "$run_dir" \
    HOME="$run_dir" \
    PYTHONNOUSERSITE=1 \
    SKEIN_AUTH_MODE=trusted-header \
    SKEIN_MODEL_PROVIDER=mock \
    SKEIN_SCHEDULER=0 \
    SKEIN_EMBEDDINGS=0 \
    "$venv/bin/python" -m pytest -q -p no:cacheprovider "$stage/backend/tests"

kubectl kustomize "$stage" >"$tmp/rendered.yaml"
"$venv/bin/python" "$stage/scripts/validate-deployment.py" \
    "$tmp/rendered.yaml" skein skein skein-frontend frontend
grep -Fq 'readOnlyRootFilesystem: true' "$tmp/rendered.yaml"

printf -v PIP_CONFIG_CONTENT '[global]\nindex-url = %s\n' \
    "${PIP_INDEX_URL:-https://pypi.org/simple}"
printf -v NPM_CONFIG_CONTENT 'registry=%s\nreplace-registry-host=npmjs\n' \
    "${NPM_CONFIG_REGISTRY:-https://registry.npmjs.org/}"
export PIP_CONFIG_CONTENT NPM_CONFIG_CONTENT
backend_image="skein-atlas-contract-backend:${run_id}"
frontend_image="skein-atlas-contract-frontend:${run_id}"
for image in "$backend_image" "$frontend_image"; do
    if docker image inspect "$image" >/dev/null 2>&1; then
        echo "local-contract: A contract image name already exists." >&2
        exit 1
    fi
done
(
    cd "$artifacts"
    sha256sum -c "$tmp/artifacts.sha256" >/dev/null
)
DOCKER_BUILDKIT=1 docker build \
    --secret id=pip-config,env=PIP_CONFIG_CONTENT \
    -f "$stage/deployment/Dockerfile" -t "$backend_image" "$stage" >/dev/null
images+=("$backend_image")
DOCKER_BUILDKIT=1 docker build \
    --secret id=npm-config,env=NPM_CONFIG_CONTENT \
    --build-arg "NEXT_PUBLIC_API_URL=http://127.0.0.1:$api_port" \
    --build-arg "NEXT_PUBLIC_SITE_URL=http://127.0.0.1:$app_port" \
    -f "$stage/deployment/Frontend.Dockerfile" -t "$frontend_image" "$stage" >/dev/null
images+=("$frontend_image")
(
    cd "$artifacts"
    sha256sum -c "$tmp/artifacts.sha256" >/dev/null
)

wait_for_container() {
    container="$1"
    url="$2"
    label="$3"
    for _attempt in $(seq 1 120); do
        if [ "$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null || true)" != true ]; then
            echo "local-contract: The $label stopped before it was ready." >&2
            docker logs "$container" >&2 || true
            return 1
        fi
        if curl --fail --silent --max-time 2 "$url" >/dev/null; then
            return 0
        fi
        sleep 0.5
    done
    echo "local-contract: The $label was not ready." >&2
    docker logs "$container" >&2 || true
    return 1
}

anchor_container="atlas-contract-network-$run_id"
docker create --name "$anchor_container" \
    -p "127.0.0.1:$invalid_port:$invalid_port" \
    -p "127.0.0.1:$idp_port:$idp_port" \
    -p "127.0.0.1:$api_port:$api_port" \
    -p "127.0.0.1:$app_port:$app_port" \
    --read-only --tmpfs /tmp:rw,nosuid,nodev,noexec,size=16m \
    --security-opt no-new-privileges --cap-drop ALL \
    "$backend_image" sleep infinity >/dev/null
containers+=("$anchor_container")
docker start "$anchor_container" >/dev/null

invalid_db="skein_atlas_contract_${run_id}_invalid"
db_helper create "$invalid_db"
databases+=("$invalid_db")
invalid_container="atlas-contract-invalid-$run_id"
db_helper run-docker "$invalid_db" \
    docker create --name "$invalid_container" --network "container:$anchor_container" \
    --user 1001230000:0 --read-only \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m \
    --tmpfs /data:rw,nosuid,nodev,size=64m \
    --security-opt no-new-privileges --cap-drop ALL \
    -e SKEIN_DATABASE_URL -e SKEIN_MODEL_PROVIDER=mock -e SKEIN_SCHEDULER=0 \
    -e SKEIN_AUTH_MODE=oidc "$backend_image" \
    uvicorn atlas_skein.app:app --host 0.0.0.0 --port "$invalid_port" >/dev/null
containers+=("$invalid_container")
docker start "$invalid_container" >/dev/null
wait_for_container "$invalid_container" "http://127.0.0.1:$invalid_port/health" \
    "invalid-auth backend"
invalid_status="$(curl --silent --max-time 5 --output "$tmp/invalid-ready.json" \
    --write-out '%{http_code}' "http://127.0.0.1:$invalid_port/ready")"
[ "$invalid_status" = 503 ]
"$venv/bin/python" - "$tmp/invalid-ready.json" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1]))
assert payload["ok"] is False
assert payload["auth_mode"] == "oidc"
assert payload["auth_error"]
PY
docker rm -f "$invalid_container" >/dev/null

runtime_db="skein_atlas_contract_${run_id}_runtime"
db_helper create "$runtime_db"
databases+=("$runtime_db")
groups='{"ava":["skein-admins"],"nina":["skein-admins","atlas-integrations"],"mira":["skein-admins","atlas-delivery-managers"]}'
idp_container="atlas-contract-idp-$run_id"
docker create --name "$idp_container" --network "container:$anchor_container" \
    --user 1001230000:0 --read-only \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=32m \
    --security-opt no-new-privileges --cap-drop ALL \
    -e SKEIN_STUB_IDP_BIND=0.0.0.0 \
    -v "$stage/scripts:/contract:ro" "$backend_image" \
    python /contract/stub-idp.py "$idp_port" skein "$groups" >/dev/null
containers+=("$idp_container")
docker start "$idp_container" >/dev/null
wait_for_container "$idp_container" "http://127.0.0.1:$idp_port/jwks" \
    "identity provider"

backend_container="atlas-contract-backend-$run_id"
frontend_container="atlas-contract-frontend-$run_id"
db_helper run-docker "$runtime_db" \
    docker create --name "$backend_container" --network "container:$anchor_container" \
    --user 1001230000:0 --read-only \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m \
    --tmpfs /data:rw,nosuid,nodev,size=64m \
    --security-opt no-new-privileges --cap-drop ALL \
    -e SKEIN_DATABASE_URL -e SKEIN_MODEL_PROVIDER=mock -e SKEIN_SCHEDULER=0 \
    -e SKEIN_AUTH_MODE=oidc \
    -e "SKEIN_OIDC_ISSUER=http://127.0.0.1:$idp_port" \
    -e SKEIN_OIDC_AUDIENCE=skein -e SKEIN_OIDC_CLIENT_ID=skein-web \
    -e SKEIN_OIDC_ADMIN_GROUP=skein-admins \
    -e "SKEIN_CORS_ORIGINS=http://127.0.0.1:$app_port" \
    "$backend_image" uvicorn atlas_skein.app:app \
    --host 0.0.0.0 --port "$api_port" >/dev/null
containers+=("$backend_container")
docker start "$backend_container" >/dev/null
wait_for_container "$backend_container" "http://127.0.0.1:$api_port/health" \
    "OIDC backend"
curl --fail --silent --max-time 5 "http://127.0.0.1:$api_port/ready" >"$tmp/ready.json"
"$venv/bin/python" - "$tmp/ready.json" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1]))
assert payload == {"ok": True, "auth_mode": "oidc", "auth_error": ""}
PY

docker create --name "$frontend_container" --network "container:$anchor_container" \
    --user 1001230000:0 --read-only \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m \
    --security-opt no-new-privileges --cap-drop ALL \
    -e "PORT=$app_port" -e HOSTNAME=0.0.0.0 "$frontend_image" >/dev/null
containers+=("$frontend_container")
docker start "$frontend_container" >/dev/null
wait_for_container "$frontend_container" "http://127.0.0.1:$app_port/" \
    "workplace frontend"
for container in "$backend_container" "$frontend_container"; do
    [ "$(docker inspect -f '{{.Config.User}}' "$container")" = "1001230000:0" ]
    [ "$(docker inspect -f '{{.HostConfig.ReadonlyRootfs}}' "$container")" = true ]
done

browser_path="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"
test -d "$browser_path" || {
    echo "local-contract: Install the Playwright Chromium browser first." >&2
    exit 1
}
(
    cd "$stage"
    "${node_command[@]}" \
    PLAYWRIGHT_BROWSERS_PATH="$browser_path" \
    SKEIN_CONTRACT_IDP_URL="http://127.0.0.1:$idp_port" \
    SKEIN_CONTRACT_API_URL="http://127.0.0.1:$api_port" \
    SKEIN_CONTRACT_APP_URL="http://127.0.0.1:$app_port" \
        npx playwright test --config playwright.config.ts
)

echo "local-contract: exact local packages, final images, PostgreSQL, OIDC, and browser gates passed"
