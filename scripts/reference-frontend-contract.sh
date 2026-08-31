#!/usr/bin/env bash
# Build one clean workplace frontend from the exact packed host/API artifacts.
set -euo pipefail
cd "$(dirname "$0")/.."
root="$PWD"
db_python="$root/backend/.venv/bin/python"
test -x "$db_python"
command -v docker >/dev/null || {
    echo "reference-frontend-contract: docker is required." >&2
    exit 1
}
if [ -z "${SKEIN_DATABASE_URL:-}" ]; then
    echo "reference-frontend-contract: SKEIN_DATABASE_URL is not set." >&2
    exit 1
fi
admin_database_url="$SKEIN_DATABASE_URL"
release_dist="${SKEIN_RELEASE_DIST:-}"
run_label="${SKEIN_CONTRACT_RUN_ID:-frontend}"
# shellcheck source=lib/hermetic-env.sh
. "$(dirname "$0")/lib/hermetic-env.sh"
skein_hermetic_env
unset SKEIN_DATABASE_URL NEXT_PUBLIC_API_TOKEN
if [[ ! "$run_label" =~ ^[a-z0-9_]{1,16}$ ]]; then
    echo "reference-frontend-contract: SKEIN_CONTRACT_RUN_ID is not safe." >&2
    exit 1
fi
run_id="${run_label}_$$"
read -r api_port app_port idp_port < <(
    "$db_python" - <<'PY'
import socket

sockets = []
for _ in range(3):
    item = socket.socket()
    item.bind(("127.0.0.1", 0))
    sockets.append(item)
print(*(item.getsockname()[1] for item in sockets))
PY
)
role_name="skein_atlas_role_${run_id}"
role_password="$($db_python -c 'import secrets; print(secrets.token_hex(24))')"
runtime_db="skein_contract_frontend_${run_id}"
node_image="node:22-bookworm@sha256:8a34c4ab3ea2c5cd194f07e317b2a8f09461d3c8b05c4e34c8ccd56d56024c4d"
role_created=""
database_created=""
db_helper() {
    SKEIN_DATABASE_URL="$admin_database_url" \
    SKEIN_CONTRACT_ROLE_NAME="$role_name" \
    SKEIN_CONTRACT_ROLE_PASSWORD="$role_password" \
        "$db_python" "$root/examples/workplace-extension/scripts/contract-db.py" "$@"
}

tmp="$(mktemp -d)"
export npm_config_cache="$tmp/npm-cache"
server_pid=""
backend_pid=""
idp_pid=""
node_container=""
cleanup() {
    status=$?
    trap - EXIT
    pids=("$server_pid" "$backend_pid" "$idp_pid")
    for pid in "${pids[@]}"; do
        [ -z "$pid" ] || kill "$pid" >/dev/null 2>&1 || true
    done
    for _attempt in $(seq 1 100); do
        alive=""
        for pid in "${pids[@]}"; do
            if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1; then
                alive=1
            fi
        done
        [ -n "$alive" ] || break
        sleep 0.1
    done
    for pid in "${pids[@]}"; do
        if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1; then
            kill -KILL "$pid" >/dev/null 2>&1 || true
        fi
        [ -z "$pid" ] || wait "$pid" >/dev/null 2>&1 || true
    done
    if [ -n "$node_container" ]; then
        docker rm -f "$node_container" >/dev/null 2>&1 || true
    fi
    if [ -n "$database_created" ]; then
        db_helper drop "$runtime_db" >/dev/null 2>&1 || true
    fi
    if [ -n "$role_created" ]; then
        db_helper drop-role >/dev/null 2>&1 || true
    fi
    rm -rf "$tmp"
    exit "$status"
}
trap cleanup EXIT
node_home="$tmp/node22"
mkdir -p "$tmp/tarballs" "$tmp/consumer/dist" "$tmp/run" \
    "$node_home/bin" "$node_home/lib"
node_container="$(docker create "$node_image")"
docker cp "$node_container:/usr/local/bin/node" "$node_home/bin/node"
docker cp "$node_container:/usr/local/lib/node_modules" "$node_home/lib/node_modules"
docker rm "$node_container" >/dev/null
node_container=""
ln -s ../lib/node_modules/npm/bin/npm-cli.js "$node_home/bin/npm"
ln -s ../lib/node_modules/npm/bin/npx-cli.js "$node_home/bin/npx"
export PATH="$node_home/bin:$PATH"
[[ "$(node --version)" == v22.* ]] || {
    echo "reference-frontend-contract: Node 22 is required." >&2
    exit 1
}

db_helper create-role
role_created=1
db_helper create "$runtime_db"
database_created=1

if [ -n "$release_dist" ]; then
    cp "$release_dist/miloctl-skein-extension-api-1.0.0.tgz" "$tmp/tarballs/"
    cp "$release_dist/miloctl-skein-frontend-host-0.4.0.tgz" "$tmp/tarballs/"
else
    npm pack --silent --pack-destination "$tmp/tarballs" ./frontend/packages/extension-api >/dev/null
    npm pack --silent --pack-destination "$tmp/tarballs" ./frontend >/dev/null
fi
shopt -s nullglob
api_tar=("$tmp/tarballs"/miloctl-skein-extension-api-*.tgz)
host_tar=("$tmp/tarballs"/miloctl-skein-frontend-host-*.tgz)
shopt -u nullglob
[ "${#api_tar[@]}" -eq 1 ]
[ "${#host_tar[@]}" -eq 1 ]

api_files="$tmp/api-files"
host_files="$tmp/host-files"
tar -tzf "${api_tar[0]}" >"$api_files"
tar -tzf "${host_tar[0]}" >"$host_files"
for files in "$api_files" "$host_files"; do
    grep -qx 'package/LICENSE' "$files"
    grep -qx 'package/NOTICE' "$files"
done
grep -qx 'package/scripts/skein-frontend-build.mjs' "$host_files"
if grep -Eq 'package/(__tests__|node_modules|\.next|packages/extension-api)' "$host_files"; then
    echo "reference-frontend-contract: host tarball contains development-only files" >&2
    exit 1
fi
[ "$(tar -xOzf "${host_tar[0]}" package/package.json | node -e \
    'let s=""; process.stdin.on("data",c=>s+=c).on("end",()=>console.log(JSON.parse(s).name))')" \
    = "@miloctl/skein-frontend-host" ]
[ "$(tar -xOzf "${api_tar[0]}" package/package.json | node -e \
    'let s=""; process.stdin.on("data",c=>s+=c).on("end",()=>console.log(JSON.parse(s).name))')" \
    = "@miloctl/skein-extension-api" ]

tar --exclude=node_modules --exclude=dist --exclude=.skein -cf - \
    -C examples/workplace-extension . | tar -xf - -C "$tmp/consumer"
cp "${api_tar[0]}" "$tmp/consumer/dist/miloctl-skein-extension-api-1.0.0.tgz"
cp "${host_tar[0]}" "$tmp/consumer/dist/miloctl-skein-frontend-host-0.4.0.tgz"

if [ -n "$release_dist" ]; then
    cp "$release_dist"/skein_agents-*.whl "$tmp/consumer/dist/"
else
    UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" \
        uv build --quiet --wheel --out-dir "$tmp/consumer/dist" backend
fi
UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" \
    uv build --quiet --wheel --out-dir "$tmp/consumer/dist" "$tmp/consumer"
shopt -s nullglob
core_wheels=("$tmp/consumer/dist"/skein_agents-*.whl)
extension_wheels=("$tmp/consumer/dist"/atlas_skein_extension-*.whl)
shopt -u nullglob
[ "${#core_wheels[@]}" -eq 1 ]
[ "${#extension_wheels[@]}" -eq 1 ]

(
    cd "$tmp/consumer"
    export NEXT_PUBLIC_API_URL="http://127.0.0.1:$api_port"
    export NEXT_PUBLIC_SITE_URL="http://127.0.0.1:$app_port"
    export NEXT_PUBLIC_API_TOKEN=
    # A source-built host keeps the release version but has new bytes. Refresh
    # only the copied lock, or npm ci rejects the exact tarball under test.
    npm update @miloctl/skein-frontend-host \
        --package-lock-only --ignore-scripts --no-audit --no-fund >/dev/null
    npm ci --ignore-scripts --no-audit --no-fund >/dev/null
    npm ls @miloctl/skein-extension-api react react-dom --all >/dev/null
    node - <<'JS'
const lock = require("./package-lock.json");
for (const name of ["@miloctl/skein-extension-api", "@miloctl/skein-frontend-host"]) {
  const entry = lock.packages[`node_modules/${name}`];
  if (!entry?.integrity || entry.link) throw new Error(`${name} is not an integrity-locked tarball`);
}
JS

    expect_build_refusal() {
        label="$1"
        expected="$2"
        if node_modules/.bin/skein-frontend-build @atlas/skein-extension \
            >"$tmp/refusal-$label.log" 2>&1; then
            echo "reference-frontend-contract: the $label build was not refused" >&2
            exit 1
        fi
        grep -Fq "$expected" "$tmp/refusal-$label.log"
        test ! -d .skein
    }

    mv package-lock.json package-lock.saved
    expect_build_refusal missing-lock "The workplace package lock is absent."
    mv package-lock.saved package-lock.json

    for dependency in @miloctl/skein-frontend-host @miloctl/skein-extension-api; do
        cp package.json package.saved
        DEPENDENCY="$dependency" node - <<'JS'
const fs = require("node:fs");
const manifest = JSON.parse(fs.readFileSync("package.json", "utf8"));
const dependency = process.env.DEPENDENCY;
if (!manifest.dependencies?.[dependency]) throw new Error(`${dependency} was not direct before the test`);
delete manifest.dependencies[dependency];
fs.writeFileSync("package.json", `${JSON.stringify(manifest, null, 2)}\n`);
JS
        expect_build_refusal missing-direct "is not a direct workplace dependency."
        mv package.saved package.json
    done

    # Overrides declared by a dependency package do NOT apply to the consumer
    # root. Pikachu's clean registry install resolved vulnerable PostCSS and
    # Sharp versions until the workplace-owned pair was present (2026-08-31).
    # Pin both halves: the manifest is the admin's decision, and the lock is
    # the bytes npm will actually install.
    for override in postcss sharp; do
        cp package.json package.saved
        OVERRIDE="$override" node - <<'JS'
const fs = require("node:fs");
const manifest = JSON.parse(fs.readFileSync("package.json", "utf8"));
const name = process.env.OVERRIDE;
if (!manifest.overrides?.[name]) throw new Error(`${name} override was not present before the test`);
delete manifest.overrides[name];
fs.writeFileSync("package.json", `${JSON.stringify(manifest, null, 2)}\n`);
JS
        expect_build_refusal "missing-$override-override" \
            "$override override must be"
        mv package.saved package.json
    done

    for override in postcss sharp; do
        cp package-lock.json package-lock.saved
        OVERRIDE="$override" node - <<'JS'
const fs = require("node:fs");
const lock = JSON.parse(fs.readFileSync("package-lock.json", "utf8"));
const name = process.env.OVERRIDE;
let changed = 0;
for (const [packagePath, entry] of Object.entries(lock.packages ?? {})) {
  if (packagePath.endsWith(`node_modules/${name}`)) {
    entry.version = "0.0.0-test-mismatch";
    changed++;
  }
}
if (!changed) throw new Error(`${name} had no locked entry before the test`);
fs.writeFileSync("package-lock.json", `${JSON.stringify(lock, null, 2)}\n`);
JS
        expect_build_refusal "mismatched-$override-lock" \
            "$override does not match the required override. Regenerate the package lock."
        mv package-lock.saved package-lock.json
    done

    cp dist/miloctl-skein-frontend-host-0.4.0.tgz host-tarball.saved
    printf '\nchanged-after-lock\n' >>dist/miloctl-skein-frontend-host-0.4.0.tgz
    expect_build_refusal changed-package-bytes "package bytes do not match the workplace lock."
    mv host-tarball.saved dist/miloctl-skein-frontend-host-0.4.0.tgz

    manifests_before="$(sha256sum package.json package-lock.json)"
    modules_before="$(find node_modules -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum)"
    mkdir -p dist/frontend
    printf 'stale\n' > dist/frontend/stale-sentinel
    printf '%s\n' \
        'SKEIN_CONTRACT_SECRET=frontend-contract-secret' \
        'NEXT_PUBLIC_API_TOKEN=frontend-contract-browser-secret' >.env.production
    NEXT_DIST_DIR=.hostile-next SKEIN_FRONTEND_EXTENSIONS=unapproved-package \
        npm run build:frontend >/dev/null
    [ "$manifests_before" = "$(sha256sum package.json package-lock.json)" ]
    [ "$modules_before" = "$(find node_modules -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum)" ]
    test ! -e dist/frontend/stale-sentinel
    test -f dist/frontend/server.js
    test -d dist/frontend/.next/static
    test -d dist/frontend/public
    test -f dist/frontend/LICENSE
    test -f dist/frontend/NOTICE
    test ! -e dist/frontend/.env
    test ! -e dist/frontend/.env.production
    for secret in frontend-contract-secret frontend-contract-browser-secret; do
        if grep -Frq "$secret" dist/frontend; then
            echo "reference-frontend-contract: a workplace environment value reached the runtime output" >&2
            exit 1
        fi
    done
    test ! -d .skein
    grep -Frq 'mt-\[7px\]' dist/frontend/.next/static || {
        echo "reference-frontend-contract: extension Tailwind utility is absent" >&2
        exit 1
    }
    grep -Frq 'Atlas delivery' dist/frontend/.next || {
        echo "reference-frontend-contract: extension text is absent" >&2
        exit 1
    }
    grep -Frq 'atlas.workplace.manager-nav' dist/frontend/.next || {
        echo "reference-frontend-contract: extension navigation is absent" >&2
        exit 1
    }
    grep -Frq 'atlas.dashboard.view' dist/frontend/.next || {
        echo "reference-frontend-contract: extension policy action is absent" >&2
        exit 1
    }

    cancel_pid=""
    cancel_build() {
        if [ -n "$cancel_pid" ]; then
            kill -TERM "$cancel_pid" >/dev/null 2>&1 || true
            wait "$cancel_pid" >/dev/null 2>&1 || true
        fi
    }
    cancel_at() {
        phase="$1"
        target="$2"
        cancel_pid=""
        output_before="$(find dist/frontend -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum)"
        trap cancel_build EXIT
        node_modules/.bin/skein-frontend-build @atlas/skein-extension \
            >"$tmp/cancelled-$phase.log" 2>&1 &
        cancel_pid=$!
        reached=""
        for _attempt in $(seq 1 9000); do
            if ! kill -0 "$cancel_pid" >/dev/null 2>&1; then
                echo "reference-frontend-contract: the $phase cancellation point was not reached. Read the log and fix the build." >&2
                while IFS= read -r line; do echo "$line" >&2; done <"$tmp/cancelled-$phase.log"
                exit 1
            fi
            if compgen -G "$target" >/dev/null; then
                reached=1
                break
            fi
            sleep 0.02
        done
        [ -n "$reached" ] || {
            echo "reference-frontend-contract: the $phase cancellation point did not appear. Read the log and fix the build." >&2
            exit 1
        }
        kill -TERM "$cancel_pid"
        if wait "$cancel_pid"; then
            echo "reference-frontend-contract: the $phase cancelled build returned success" >&2
            exit 1
        fi
        cancel_pid=""
        trap - EXIT
        test ! -d .skein
        test -f dist/frontend/server.js
        [ "$output_before" = "$(find dist/frontend -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum)" ] || {
            echo "reference-frontend-contract: the $phase cancelled build changed the prior runtime" >&2
            exit 1
        }
        if compgen -G 'dist/.frontend-*' >/dev/null \
            || compgen -G 'dist/.frontend-previous-*' >/dev/null; then
            echo "reference-frontend-contract: the $phase cancelled build left temporary output" >&2
            exit 1
        fi
    }

    cancel_at early-stage '.skein/frontend-host-*'
    cancel_at output-promotion 'dist/.frontend-*'
)

cp -R "$tmp/consumer/dist/frontend" "$tmp/runtime"
port="$(node -e 'const s=require("node:net").createServer(); s.listen(0,"127.0.0.1",()=>{console.log(s.address().port);s.close()})')"
original_dir="$PWD"
cd "$tmp/runtime"
PORT="$port" HOSTNAME=127.0.0.1 NEXT_PUBLIC_API_TOKEN= \
    node server.js >"$tmp/frontend.log" 2>&1 &
server_pid=$!
cd "$original_dir"
ready=""
for _attempt in $(seq 1 60); do
    if ! kill -0 "$server_pid" >/dev/null 2>&1; then
        echo "reference-frontend-contract: the standalone server stopped. Read the log and fix the runtime." >&2
        while IFS= read -r line; do echo "$line" >&2; done <"$tmp/frontend.log"
        exit 1
    fi
    if curl --fail --silent "http://127.0.0.1:$port/" >/dev/null; then
        ready=1
        break
    fi
    sleep 0.5
done
[ -n "$ready" ] || {
    echo "reference-frontend-contract: the standalone server was not ready. Read the log and fix the runtime." >&2
    exit 1
}
kill "$server_pid"
wait "$server_pid" >/dev/null 2>&1 || true
server_pid=""

python="backend/.venv/bin/python"
[ -x "$python" ] || python="$(command -v python3)"
UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" uv venv --quiet --python "$python" "$tmp/venv"
UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" uv pip install --quiet \
    --python "$tmp/venv/bin/python" \
    --require-hashes -r "$tmp/consumer/requirements-test.lock"
UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" uv pip install --quiet \
    --python "$tmp/venv/bin/python" --no-deps \
    "${core_wheels[0]}" "${extension_wheels[0]}"
UV_CACHE_DIR="${UV_CACHE_DIR:-$tmp/uv-cache}" uv pip check --python "$tmp/venv/bin/python"

wait_for_url() {
    pid="$1"
    url="$2"
    label="$3"
    log="$4"
    for _attempt in $(seq 1 120); do
        if ! kill -0 "$pid" >/dev/null 2>&1; then
            echo "reference-frontend-contract: the $label stopped before it was ready. Read the log and fix the runtime." >&2
            while IFS= read -r line; do echo "$line" >&2; done <"$log"
            exit 1
        fi
        if curl --fail --silent "$url" >/dev/null; then
            return
        fi
        sleep 0.5
    done
    echo "reference-frontend-contract: the $label was not ready. Read the log and fix the runtime." >&2
    while IFS= read -r line; do echo "$line" >&2; done <"$log"
    exit 1
}

groups='{"ava":["skein-admins"],"nina":["skein-admins","atlas-integrations"],"mira":["skein-admins","atlas-delivery-managers"]}'
"$tmp/venv/bin/python" scripts/stub-idp.py "$idp_port" skein "$groups" >"$tmp/idp.log" 2>&1 &
idp_pid=$!
wait_for_url "$idp_pid" "http://127.0.0.1:$idp_port/jwks" "stub identity provider" "$tmp/idp.log"

db_helper run-clean "$runtime_db" env -C "$tmp/run" \
    HOME="$tmp/run" \
    PYTHONNOUSERSITE=1 \
    ATLAS_SKEIN_STORE=atlas-contract \
    SKEIN_DATA_DIR="$tmp/data" \
    SKEIN_MODEL_PROVIDER=mock \
    SKEIN_SCHEDULER=0 \
    SKEIN_EMBEDDINGS=0 \
    SKEIN_AUTH_MODE=oidc \
    SKEIN_OIDC_ISSUER="http://127.0.0.1:$idp_port" \
    SKEIN_OIDC_AUDIENCE=skein \
    SKEIN_OIDC_CLIENT_ID=skein-web \
    SKEIN_OIDC_ADMIN_GROUP=skein-admins \
    SKEIN_CORS_ORIGINS="http://127.0.0.1:$app_port" \
    SKEIN_PLAYBOOKS_DIR="$tmp/consumer/content/playbooks" \
    SKEIN_PERSONAS_DIR="$tmp/consumer/content/personas" \
    SKEIN_FLOCKS_DIR="$tmp/consumer/content/flocks" \
    "$tmp/venv/bin/python" -m uvicorn atlas_skein.app:app \
    --host 127.0.0.1 --port "$api_port" >"$tmp/backend.log" 2>&1 &
backend_pid=$!
wait_for_url "$backend_pid" "http://127.0.0.1:$api_port/health" "installed Atlas backend" "$tmp/backend.log"

env -C "$tmp/runtime" PORT="$app_port" HOSTNAME=127.0.0.1 NEXT_PUBLIC_API_TOKEN= \
    node server.js >"$tmp/runtime.log" 2>&1 &
server_pid=$!
wait_for_url "$server_pid" "http://127.0.0.1:$app_port/" "copied workplace frontend" "$tmp/runtime.log"

env -C frontend PW_REUSE=1 SKEIN_WORKPLACE_RUNTIME=1 \
    SKEIN_OIDC_API_URL="http://127.0.0.1:$api_port" \
    SKEIN_OIDC_APP_URL="http://127.0.0.1:$app_port" \
    SKEIN_OIDC_IDP_URL="http://127.0.0.1:$idp_port" \
    npx playwright test --config playwright.oidc.config.ts \
    e2e-oidc/workplace-runtime.spec.ts

echo "reference-frontend-contract: packed packages built and walked the signed Atlas workplace runtime"
