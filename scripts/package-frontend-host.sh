#!/usr/bin/env bash
# Build a versioned source artifact for trusted build-time frontend composition.
set -euo pipefail
cd "$(dirname "$0")/.."

version="${1:-}"
output="${2:-}"
source_dir="${SKEIN_FRONTEND_SOURCE:-frontend}"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || [ -z "$output" ]; then
    echo "usage: $0 <version> <output.tar.gz>" >&2
    exit 2
fi

tmp="$(mktemp -d)"
cleanup() { rm -rf "$tmp"; }
trap cleanup EXIT

# The version argument names the artifact IDENTITY that gates every
# extension's minimumCore. Stamping a version the source tree does not claim
# would falsify it, so the argument must match the tree.
tree_version="$(sed -n 's/.*"version": "\([0-9.]*\)".*/\1/p' "$source_dir/package.json" | head -1)"
if [ "$version" != "$tree_version" ]; then
    echo "package-frontend-host: the source tree claims $tree_version, not $version" >&2
    exit 1
fi

mkdir -p "$tmp/frontend"
tar \
    --exclude=.git \
    --exclude='.env*' \
    --exclude=.npmrc \
    --exclude=node_modules \
    --exclude=.next \
    --exclude=.next-e2e \
    --exclude=test-results \
    --exclude=playwright-report \
    --exclude=blob-report \
    --exclude=__tests__ \
    --exclude=e2e \
    --exclude='*.tsbuildinfo' \
    --exclude='playwright*.config.ts' \
    --exclude=extensions/generated.ts \
    -cf - -C "$source_dir" . | tar -xf - -C "$tmp/frontend"
sed -i -E "0,/\"version\": \"[0-9]+\.[0-9]+\.[0-9]+\"/s//\"version\": \"$version\"/" \
    "$tmp/frontend/package.json"
sed -i -E "1,12 s/\"version\": \"[0-9]+\.[0-9]+\.[0-9]+\"/\"version\": \"$version\"/" \
    "$tmp/frontend/package-lock.json"
printf '{"artifact":"skein-frontend-host","version":"%s","extension_api":"1.0"}\n' \
    "$version" > "$tmp/frontend/host-artifact.json"
mkdir -p "$(dirname "$output")"
tar -czf "$output" -C "$tmp" frontend
