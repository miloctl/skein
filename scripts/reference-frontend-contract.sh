#!/usr/bin/env bash
# Compile the private frontend from TypeScript against the packed public API,
# then load both packed artifacts from a clean consumer directory.
set -euo pipefail
cd "$(dirname "$0")/.."

tmp="$(mktemp -d)"
export npm_config_cache="$tmp/npm-cache"
installed="frontend/node_modules/@atlas/skein-extension"
cleanup() {
    status=$?
    trap - EXIT
    rm -rf "$tmp"
    rm -rf "$installed"
    npm --prefix frontend run --silent compose:extensions >/dev/null 2>&1 || true
    exit "$status"
}
trap cleanup EXIT
mkdir -p "$tmp/tarballs" "$tmp/build/node_modules/@skein" "$tmp/build/node_modules/@types"
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
SKEIN_FRONTEND_EXTENSIONS=@atlas/skein-extension npm --prefix frontend run build >/dev/null

echo "reference-frontend-contract: source, packed artifacts, and production build passed"
