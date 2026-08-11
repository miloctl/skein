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
cleanup() {
    status=$?
    docker container rm -f "$frontend_container" "$backend_container" \
        >/dev/null 2>&1 || true
    docker volume rm -f "$extension_volume" "$core_volume" \
        >/dev/null 2>&1 || true
    docker image rm -f \
        "$frontend_image" "$backend_image" "$host_image" "$core_image" \
        >/dev/null 2>&1 || true
    rm -rf "$tmp"
    exit "$status"
}
trap cleanup EXIT

cp -R examples/workplace-extension "$tmp/extension"
mkdir -p "$tmp/extension/dist"
UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" \
    uv build --quiet --wheel --out-dir "$tmp/extension/dist" "$tmp/extension"
npm pack --silent --pack-destination "$tmp/extension/dist" \
    "$tmp/extension/frontend" >/dev/null

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
docker run --detach --name "$backend_container" \
    --mount "type=volume,source=$core_volume,target=/data" \
    --mount "type=volume,source=$extension_volume,target=/atlas-data" \
    -e SKEIN_MODEL_PROVIDER=mock \
    -e SKEIN_SCHEDULER=0 \
    "$backend_image" >/dev/null
for _attempt in $(seq 1 30); do
    if docker exec "$backend_container" python -c \
        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)" \
        >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
docker exec "$backend_container" python -c \
    "from pathlib import Path; Path('/data/write-check').write_text('ok'); Path('/atlas-data/write-check').write_text('ok')"
[ "$(docker exec "$backend_container" id -u)" -ne 0 ]
docker exec "$backend_container" python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)" \
    >/dev/null

docker run --detach --name "$frontend_container" "$frontend_image" >/dev/null
for _attempt in $(seq 1 30); do
    if docker exec "$frontend_container" wget -qO- http://127.0.0.1:3000/ \
        >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
docker exec "$frontend_container" wget -qO- http://127.0.0.1:3000/ >/dev/null
[ "$(docker exec "$frontend_container" id -u)" -ne 0 ]
echo "reference-images-contract: derivative images built and started as non-root"
