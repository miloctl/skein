#!/usr/bin/env bash
# Build the core images and final Atlas-owned images from exact packages.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v docker >/dev/null || {
    echo "reference-images-contract: docker is required" >&2
    exit 1
}

tmp="$(mktemp -d)"
suffix="$$"
core_backend_image="skein-core-backend-contract:$suffix"
core_frontend_image="skein-core-frontend-contract:$suffix"
backend_image="atlas-skein-extension-contract:$suffix"
frontend_image="atlas-skein-frontend-contract:$suffix"
core_backend_container="skein-core-backend-contract-$suffix"
core_frontend_container="skein-core-frontend-contract-$suffix"
backend_container="atlas-skein-backend-contract-$suffix"
frontend_container="atlas-skein-frontend-contract-$suffix"
core_runtime_volume="skein-core-runtime-data-$suffix"
core_volume="atlas-skein-core-data-$suffix"
db_container="atlas-skein-db-contract-$suffix"
network="atlas-skein-contract-$suffix"
db_admin="skein_bootstrap"
db_password="contract-admin-$suffix"
app_user="skein_app"
app_password="contract-app-$suffix"
cleanup() {
    status=$?
    if [ "$status" -ne 0 ]; then
        docker logs "$db_container" >&2 || true
        docker logs "$core_backend_container" >&2 || true
        docker logs "$core_frontend_container" >&2 || true
        docker logs "$backend_container" >&2 || true
        docker logs "$frontend_container" >&2 || true
    fi
    docker container rm -f "$frontend_container" "$backend_container" \
        "$core_frontend_container" "$core_backend_container" \
        "$db_container" >/dev/null 2>&1 || true
    docker volume rm -f "$core_volume" "$core_runtime_volume" >/dev/null 2>&1 || true
    docker image rm -f "$frontend_image" "$backend_image" \
        "$core_frontend_image" "$core_backend_image" >/dev/null 2>&1 || true
    docker network rm "$network" >/dev/null 2>&1 || true
    rm -rf "$tmp"
    exit "$status"
}
trap cleanup EXIT

tar --exclude=node_modules --exclude=dist --exclude=.skein -cf - \
    -C examples/workplace-extension . | tar -xf - -C "$tmp"
extension="$tmp"
mkdir -p "$extension/dist"
if [ -n "${SKEIN_RELEASE_DIST:-}" ]; then
    cp "$SKEIN_RELEASE_DIST/skein_agents-0.3.0-py3-none-any.whl" "$extension/dist/"
    cp "$SKEIN_RELEASE_DIST/skein-extension-api-1.0.0.tgz" "$extension/dist/"
    cp "$SKEIN_RELEASE_DIST/skein-frontend-host-0.3.0.tgz" "$extension/dist/"
else
    UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" \
        uv build --quiet --wheel --out-dir "$extension/dist" backend
    npm pack --silent --pack-destination "$extension/dist" ./frontend/packages/extension-api >/dev/null
    npm pack --silent --pack-destination "$extension/dist" ./frontend >/dev/null
fi
UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" \
    uv build --quiet --wheel --out-dir "$extension/dist" "$extension"

shopt -s nullglob
core_wheels=("$extension/dist"/skein_agents-*.whl)
atlas_wheels=("$extension/dist"/atlas_skein_extension-*.whl)
api_packages=("$extension/dist"/skein-extension-api-*.tgz)
host_packages=("$extension/dist"/skein-frontend-host-*.tgz)
shopt -u nullglob
[ "${#core_wheels[@]}" -eq 1 ]
[ "${#atlas_wheels[@]}" -eq 1 ]
[ "${#api_packages[@]}" -eq 1 ]
[ "${#host_packages[@]}" -eq 1 ]

docker build --quiet -t "$core_backend_image" backend >/dev/null
docker build --quiet \
    --build-arg NEXT_PUBLIC_API_URL=https://core-api.contract.invalid \
    --build-arg NEXT_PUBLIC_SITE_URL=https://core-site.contract.invalid \
    --build-arg NEXT_PUBLIC_API_TOKEN=core-browser-token \
    -t "$core_frontend_image" frontend >/dev/null
docker build --quiet -f "$extension/deployment/Dockerfile" \
    -t "$backend_image" "$extension" >/dev/null
docker build --quiet \
    --build-arg NEXT_PUBLIC_API_URL=https://api.contract.invalid \
    --build-arg NEXT_PUBLIC_SITE_URL=https://site.contract.invalid \
    --build-arg NEXT_PUBLIC_API_TOKEN=contract-browser-token \
    -f "$extension/deployment/Frontend.Dockerfile" \
    -t "$frontend_image" "$extension" >/dev/null

docker image inspect "$core_backend_image" "$core_frontend_image" \
    "$backend_image" "$frontend_image" >/dev/null
docker volume create "$core_runtime_volume" >/dev/null
docker volume create "$core_volume" >/dev/null
docker network create "$network" >/dev/null
docker run --detach --name "$db_container" --network "$network" \
    -e POSTGRES_USER="$db_admin" \
    -e POSTGRES_PASSWORD="$db_password" \
    -e POSTGRES_DB=skein \
    postgres:17-alpine >/dev/null
ready_count=0
for _attempt in $(seq 1 120); do
    if docker exec "$db_container" psql -U "$db_admin" -d skein -qtAc "SELECT 1" \
        >/dev/null 2>&1; then
        ready_count=$((ready_count + 1))
        if [ "$ready_count" -eq 3 ]; then
            break
        fi
    else
        ready_count=0
    fi
    sleep 1
done
[ "$ready_count" -eq 3 ]

docker cp "$extension/deployment/10-app-role.sh" "$db_container:/tmp/10-app-role.sh"
docker cp "$extension/deployment/20-atlas-schema.sh" "$db_container:/tmp/20-atlas-schema.sh"
docker exec \
    -e POSTGRES_USER="$db_admin" -e POSTGRES_DB=skein \
    -e SKEIN_APP_USER="$app_user" -e SKEIN_APP_PASSWORD="$app_password" \
    "$db_container" bash /tmp/10-app-role.sh
docker exec \
    -e POSTGRES_USER="$db_admin" -e POSTGRES_DB=skein \
    -e SKEIN_APP_USER="$app_user" \
    "$db_container" bash /tmp/20-atlas-schema.sh
[ "$(docker exec "$db_container" psql -U "$db_admin" -d skein -qtAc \
    "SELECT rolsuper FROM pg_roles WHERE rolname = '$app_user'")" = "f" ]

wait_backend() {
    local container="$1" label="$2" ready=""
    for _attempt in $(seq 1 60); do
        if docker exec "$container" python -c \
            "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)" \
            >/dev/null 2>&1; then
            ready=1
            break
        fi
        sleep 1
    done
    if [ -z "$ready" ]; then
        echo "reference-images-contract: the $label backend image never served /health" >&2
        docker logs "$container" >&2
        exit 1
    fi
}

wait_frontend() {
    local container="$1" label="$2" ready=""
    for _attempt in $(seq 1 30); do
        if docker exec "$container" wget -qO- http://127.0.0.1:3000/ \
            >/dev/null 2>&1; then
            ready=1
            break
        fi
        sleep 1
    done
    if [ -z "$ready" ]; then
        echo "reference-images-contract: the $label frontend image never served /" >&2
        docker logs "$container" >&2
        exit 1
    fi
}

docker run --detach --name "$core_backend_container" \
    --network "$network" \
    --user 1000710000:0 \
    --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,size=64m \
    --mount "type=volume,source=$core_runtime_volume,target=/data" \
    -e SKEIN_DB_HOST="$db_container" \
    -e SKEIN_DB_PORT=5432 \
    -e SKEIN_DB_USER="$app_user" \
    -e SKEIN_DB_PASSWORD="$app_password" \
    -e SKEIN_DB_NAME=skein \
    -e SKEIN_AUTH_MODE=trusted-header \
    -e SKEIN_MODEL_PROVIDER=mock \
    -e SKEIN_SCHEDULER=0 \
    "$core_backend_image" >/dev/null
wait_backend "$core_backend_container" core
docker exec "$core_backend_container" python -c \
    "from pathlib import Path; Path('/data/write-check').write_text('ok')"
[ "$(docker exec "$core_backend_container" id -u)" -eq 1000710000 ]
docker container rm -f "$core_backend_container" >/dev/null

docker run --detach --name "$core_frontend_container" \
    --user 1000710000:0 \
    --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,size=64m \
    "$core_frontend_image" >/dev/null
wait_frontend "$core_frontend_container" core
[ "$(docker exec "$core_frontend_container" id -u)" -eq 1000710000 ]
docker exec "$core_frontend_container" sh -c \
    "grep -R -q 'https://core-api.contract.invalid' .next \
        && grep -R -q 'https://core-site.contract.invalid' .next \
        && grep -R -q 'core-browser-token' .next"
docker container rm -f "$core_frontend_container" >/dev/null

docker run --rm \
    --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,size=64m \
    --mount "type=volume,source=$core_volume,target=/data" \
    "$backend_image" python -c \
    "from pathlib import Path; Path('/data/default-user-write').write_text('ok')"

docker run --detach --name "$backend_container" \
    --network "$network" \
    --user 1000710000:0 \
    --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,size=64m \
    --mount "type=volume,source=$core_volume,target=/data" \
    -e SKEIN_DB_HOST="$db_container" \
    -e SKEIN_DB_PORT=5432 \
    -e SKEIN_DB_USER="$app_user" \
    -e SKEIN_DB_PASSWORD="$app_password" \
    -e SKEIN_DB_NAME=skein \
    -e SKEIN_AUTH_MODE=trusted-header \
    -e SKEIN_MODEL_PROVIDER=mock \
    -e SKEIN_SCHEDULER=0 \
    "$backend_image" >/dev/null
wait_backend "$backend_container" Atlas
docker exec -i "$backend_container" python - <<'PY'
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

assert version("skein-agents") == "0.3.0"
assert version("atlas-skein-extension") == "2.0.0"
try:
    version("skein")
except PackageNotFoundError:
    pass
else:
    raise AssertionError("the unrelated skein distribution is installed")
Path("/data/write-check").write_text("ok")
PY
[ "$(docker exec "$backend_container" id -u)" -ne 0 ]
[ "$(docker exec "$db_container" psql -U "$db_admin" -d skein -qtAc \
    "SELECT schema_owner FROM information_schema.schemata WHERE schema_name = 'ext_atlas_extension'")" = "$app_user" ]

docker run --detach --name "$frontend_container" \
    --user 1000710000:0 \
    --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,size=64m \
    "$frontend_image" >/dev/null
wait_frontend "$frontend_container" Atlas
[ "$(docker exec "$frontend_container" id -u)" -ne 0 ]
docker exec "$frontend_container" sh -c \
    "grep -R -q 'https://api.contract.invalid' .next \
        && grep -R -q 'https://site.contract.invalid' .next \
        && grep -R -q 'contract-browser-token' .next"

echo "reference-images-contract: core and package-built Atlas images started with restricted PostgreSQL and non-root runtimes"
