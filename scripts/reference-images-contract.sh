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
cleanup() {
    status=$?
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
echo "reference-images-contract: backend and frontend derivative images built"
