#!/usr/bin/env bash
# Compile the private frontend from TypeScript against the packed public API,
# then load both packed artifacts from a clean consumer directory.
set -euo pipefail
cd "$(dirname "$0")/.."

tmp="$(mktemp -d)"
export npm_config_cache="$tmp/npm-cache"
cleanup() {
    status=$?
    trap - EXIT
    rm -rf "$tmp"
    exit "$status"
}
trap cleanup EXIT
mkdir -p "$tmp/tarballs" "$tmp/build/node_modules/@skein" "$tmp/build/node_modules/@types"
mkdir -p "$tmp/current-source/frontend"
tar --exclude=node_modules --exclude=.next --exclude='*.tsbuildinfo' \
    -cf - -C frontend . | tar -xf - -C "$tmp/current-source/frontend"
cp -a --reflink=auto frontend/node_modules "$tmp/current-source/frontend/node_modules"
installed="$tmp/current-source/frontend/node_modules/@atlas/skein-extension"
[ ! -e "$installed" ] || { echo "temporary Atlas install path already exists" >&2; exit 1; }

npm pack --silent --pack-destination "$tmp/tarballs" frontend/packages/extension-api >/dev/null
api_tar=("$tmp/tarballs"/skein-extension-api-*.tgz)
[ "${#api_tar[@]}" -eq 1 ]
mkdir -p "$tmp/build/node_modules/@skein/extension-api"
tar -xzf "${api_tar[0]}" --strip-components=1 -C "$tmp/build/node_modules/@skein/extension-api"
ln -s "$(pwd)/frontend/node_modules/react" "$tmp/build/node_modules/react"
ln -s "$(pwd)/frontend/node_modules/@types/react" "$tmp/build/node_modules/@types/react"
ln -s "$(pwd)/frontend/node_modules/csstype" "$tmp/build/node_modules/csstype"
cp examples/workplace-extension/frontend/index.tsx "$tmp/build/index.tsx"
cp examples/workplace-extension/frontend/tsconfig.json "$tmp/build/tsconfig.json"
cp examples/workplace-extension/frontend/package.json "$tmp/build/package.json"
frontend/node_modules/.bin/tsc --project "$tmp/build/tsconfig.json"
cmp "$tmp/build/dist/index.js" examples/workplace-extension/frontend/dist/index.js
cmp "$tmp/build/dist/index.d.ts" examples/workplace-extension/frontend/dist/index.d.ts

npm pack --silent --pack-destination "$tmp/tarballs" examples/workplace-extension/frontend >/dev/null
atlas_tar=("$tmp/tarballs"/atlas-skein-extension-*.tgz)
[ "${#atlas_tar[@]}" -eq 1 ]
mkdir -p "$tmp/consumer/node_modules/@skein/extension-api"
mkdir -p "$tmp/consumer/node_modules/@atlas/skein-extension"
tar -xzf "${api_tar[0]}" --strip-components=1 -C "$tmp/consumer/node_modules/@skein/extension-api"
tar -xzf "${atlas_tar[0]}" --strip-components=1 -C "$tmp/consumer/node_modules/@atlas/skein-extension"
ln -s "$(pwd)/frontend/node_modules/react" "$tmp/consumer/node_modules/react"
(
    cd "$tmp/consumer"
    node --input-type=module - <<'JS'
import extension from "@atlas/skein-extension";
import { FRONTEND_EXTENSION_API } from "@skein/extension-api";

if (extension.extensionApi !== FRONTEND_EXTENSION_API) throw new Error("API mismatch");
if (extension.minimumCore !== "0.2.0") throw new Error("core range mismatch");
if (extension.navigation[0].policyAction !== "atlas.dashboard.view") {
  throw new Error("Atlas navigation policy is absent");
}
JS
)

mkdir -p "$installed"
tar -xzf "${atlas_tar[0]}" --strip-components=1 -C "$installed"
SKEIN_FRONTEND_EXTENSIONS=@atlas/skein-extension \
    npm --prefix "$tmp/current-source/frontend" run build >/dev/null

fixture="scripts/fixtures/frontend-host-0.2.0.txt"
previous_commit="$(sed -n 's/^commit=//p' "$fixture")"
previous_tree="$(sed -n 's/^tree=//p' "$fixture")"
[ "$(git rev-parse "$previous_commit:frontend")" = "$previous_tree" ]
current_tree="$(git rev-parse HEAD:frontend)"
[ "$previous_tree" != "$current_tree" ] || {
    echo "frontend host revisions have the same source tree" >&2
    exit 1
}
mkdir -p "$tmp/previous-source"
git archive "$previous_commit" frontend \
    | tar -x -C "$tmp/previous-source"

build_host() {
    version="$1"
    source="$2"
    host="$tmp/host-$version"
    archive="$tmp/tarballs/skein-frontend-host-$version.tar.gz"
    SKEIN_FRONTEND_SOURCE="$source" \
        scripts/package-frontend-host.sh "$version" "$archive"
    mkdir -p "$host"
    tar -xzf "$archive" -C "$host"
    [ "$(sed -n '1,12p' "$host/frontend/package-lock.json" \
        | grep -c "\"version\": \"$version\"")" -eq 2 ]
    [ ! -d "$host/frontend/__tests__" ]
    [ ! -e "$host/frontend/tsconfig.tsbuildinfo" ]
    cp -a --reflink=auto frontend/node_modules "$host/frontend/node_modules"
    mkdir -p "$host/frontend/node_modules/@atlas/skein-extension"
    tar -xzf "${atlas_tar[0]}" --strip-components=1 \
        -C "$host/frontend/node_modules/@atlas/skein-extension"
    SKEIN_FRONTEND_EXTENSIONS=@atlas/skein-extension \
        npm --prefix "$host/frontend" run build >/dev/null
    grep -q '@atlas/skein-extension' "$host/frontend/extensions/generated.ts"
    if [ "$version" = "0.2.1" ]; then
        mkdir -p "$host/frontend/__tests__"
        cp frontend/__tests__/setup.ts "$host/frontend/__tests__/setup.ts"
        cp scripts/fixtures/reference-frontend-runtime.test.tsx \
            "$host/frontend/__tests__/reference-frontend-runtime.test.tsx"
        npm --prefix "$host/frontend" test -- \
            --run __tests__/reference-frontend-runtime.test.tsx >/dev/null
    fi
    rm -rf "$host"
}

build_host 0.2.0 "$tmp/previous-source/frontend"
build_host 0.2.1 "$tmp/current-source/frontend"

echo "reference-frontend-contract: unchanged Atlas package built on distinct 0.2.0 and 0.2.1 frontend implementations"
