#!/usr/bin/env bash
# Build the two derivative workplace images from staged release artifacts.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v docker >/dev/null || {
    echo "reference-images-contract: docker is required" >&2
    exit 1
}

tmp="$(mktemp -d)"
suffix="$$"
core_image="skein-extension-contract-core:$suffix"
host_image="skein-extension-contract-frontend-host:$suffix"
backend_image="atlas-skein-extension-contract:$suffix"
frontend_image="atlas-skein-frontend-contract:$suffix"
backend_container="atlas-skein-backend-contract-$suffix"
frontend_container="atlas-skein-frontend-contract-$suffix"
core_volume="atlas-skein-core-data-$suffix"
extension_volume="atlas-skein-extension-data-$suffix"
db_container="atlas-skein-db-contract-$suffix"
network="atlas-skein-contract-$suffix"
# Ephemeral by construction: this server lives on an isolated network for the
# length of one run and is removed below, so the value is a label rather than
# a secret.
db_password="contract-$suffix"
cleanup() {
    status=$?
    docker container rm -f "$frontend_container" "$backend_container" \
        "$db_container" >/dev/null 2>&1 || true
    docker volume rm -f "$extension_volume" "$core_volume" \
        >/dev/null 2>&1 || true
    docker image rm -f \
        "$frontend_image" "$backend_image" "$host_image" "$core_image" \
        >/dev/null 2>&1 || true
    docker network rm "$network" >/dev/null 2>&1 || true
    rm -rf "$tmp"
    exit "$status"
}
trap cleanup EXIT

cp -R examples/workplace-extension "$tmp/extension"
mkdir -p "$tmp/extension/dist" "$tmp/tarballs"
UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" \
    uv build --quiet --wheel --out-dir "$tmp/extension/dist" "$tmp/extension"
# prepack recompiles dist from source; the devDependencies carry the
# compiler, and the packed public API supplies the contract types.
npm pack --silent --pack-destination "$tmp/tarballs" \
    frontend/packages/extension-api >/dev/null
api_tar=("$tmp/tarballs"/skein-extension-api-*.tgz)
(cd "$tmp/extension/frontend" && npm install --silent --no-audit --no-fund \
    --package-lock=false --legacy-peer-deps >/dev/null)
mkdir -p "$tmp/extension/frontend/node_modules/@skein/extension-api"
tar -xzf "${api_tar[0]}" --strip-components=1 \
    -C "$tmp/extension/frontend/node_modules/@skein/extension-api"
npm pack --silent --pack-destination "$tmp/extension/dist" \
    "$tmp/extension/frontend" >/dev/null
rm -rf "$tmp/extension/frontend/node_modules"

docker build --quiet -t "$core_image" backend >/dev/null
docker build --quiet --target host -t "$host_image" frontend >/dev/null
docker build --quiet \
    --build-arg "SKEIN_IMAGE=$core_image" \
    -f "$tmp/extension/deployment/Dockerfile" \
    -t "$backend_image" "$tmp/extension" >/dev/null
docker build --quiet \
    --build-arg "SKEIN_FRONTEND_HOST=$host_image" \
    -f "$tmp/extension/deployment/Frontend.Dockerfile" \
    -t "$frontend_image" "$tmp/extension" >/dev/null

docker image inspect "$backend_image" "$frontend_image" >/dev/null
docker volume create "$core_volume" >/dev/null
docker volume create "$extension_volume" >/dev/null

# The app has been PostgreSQL-only since 0.2.3, so a derivative image cannot
# reach startup without a server — this gate ran for a week reporting only
# "container is not running" because nothing gave it one.
#
# Its OWN server on its OWN network, never the caller's SKEIN_DATABASE_URL:
# this starts a real deployment image, and pointing it at a developer's
# database would run core migrations against it. `--network host` would find
# CI's service container, but it collides with a local server on 5432 and
# is not portable off Linux. The major is pinned for the reason
# docker-compose.yml gives: backend/Dockerfile carries a pg_dump of the same
# major, and pg_dump refuses a server newer than itself.
docker network create "$network" >/dev/null
docker run --detach --name "$db_container" --network "$network" \
    -e POSTGRES_USER=skein \
    -e POSTGRES_PASSWORD="$db_password" \
    -e POSTGRES_DB=skein \
    postgres:17-alpine >/dev/null
for _attempt in $(seq 1 60); do
    if docker exec "$db_container" pg_isready -U skein -d skein >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
docker exec "$db_container" pg_isready -U skein -d skein >/dev/null

docker run --detach --name "$backend_container" \
    --network "$network" \
    --mount "type=volume,source=$core_volume,target=/data" \
    --mount "type=volume,source=$extension_volume,target=/atlas-data" \
    -e SKEIN_DATABASE_URL="postgresql://skein:$db_password@$db_container:5432/skein" \
    -e SKEIN_AUTH_MODE=trusted-header \
    -e SKEIN_MODEL_PROVIDER=mock \
    -e SKEIN_SCHEDULER=0 \
    "$backend_image" >/dev/null
ready=""
for _attempt in $(seq 1 30); do
    if docker exec "$backend_container" python -c \
        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)" \
        >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 1
done
# The container's own log, not "container is not running". A startup that
# aborts (a missing setting, a failed migration) leaves the readiness loop to
# time out, and every later docker exec then reports the corpse rather than
# the cause.
if [ -z "$ready" ]; then
    echo "reference-images-contract: the backend image never served /health" >&2
    docker logs "$backend_container" 2>&1 | tail -30 >&2
    exit 1
fi
docker exec "$backend_container" python -c \
    "from pathlib import Path; Path('/data/write-check').write_text('ok'); Path('/atlas-data/write-check').write_text('ok')"
[ "$(docker exec "$backend_container" id -u)" -ne 0 ]
docker exec "$backend_container" python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)" \
    >/dev/null

docker run --detach --name "$frontend_container" "$frontend_image" >/dev/null
ready=""
for _attempt in $(seq 1 30); do
    if docker exec "$frontend_container" wget -qO- http://127.0.0.1:3000/ \
        >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 1
done
if [ -z "$ready" ]; then
    echo "reference-images-contract: the frontend image never served /" >&2
    docker logs "$frontend_container" 2>&1 | tail -30 >&2
    exit 1
fi
docker exec "$frontend_container" wget -qO- http://127.0.0.1:3000/ >/dev/null
[ "$(docker exec "$frontend_container" id -u)" -ne 0 ]
echo "reference-images-contract: derivative images built and started as non-root"
