#!/usr/bin/env bash
# Run the real role and pre-boot recovery contracts without local PG client shims.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
if [ "$#" -ne 1 ]; then
    printf 'Usage: %s <local-backend-image>\n' "$0" >&2
    exit 2
fi
image="$1"
docker image inspect "$image" >/dev/null
suffix="$(python3 -c 'import secrets; print(secrets.token_hex(8))')"
password="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
label="skein.recovery-contract=$suffix"
postgres_image="postgres:17-alpine@sha256:d4bb0a8c1b7bb2e29f976d099e7bfb9a5d8858cffe9e46b35cd302cd1f1f8168"
network=""; database=""; runner=""
cleanup() {
    status=$?
    trap - EXIT
    # IDs are captured only after successful creation. A name collision owns nothing.
    for id in "$runner" "$database"; do
        if [ -n "$id" ]; then
            docker container rm --force --volumes "$id" >/dev/null || true
        fi
    done
    if [ -n "$network" ]; then docker network rm "$network" >/dev/null || true; fi
    printf 'recovery-contract: exit=%s\n' "$status"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
network="$(docker network create --label "$label" "skein-recovery-$suffix")"
database="$(docker run --detach --name "skein-recovery-db-$suffix" --label "$label" \
    --network "$network" --network-alias database \
    -e POSTGRES_USER=skein_bootstrap -e POSTGRES_PASSWORD="$password" -e POSTGRES_DB=skein \
    "$postgres_image")"
ready=0
for _ in $(seq 1 90); do
    if docker exec "$database" pg_isready -h 127.0.0.1 -U skein_bootstrap -d skein >/dev/null 2>&1; then
        ready=$((ready + 1))
        if [ "$ready" -eq 3 ]; then break; fi
    else
        ready=0
    fi
    sleep 1
done
if [ "$ready" -ne 3 ]; then
    printf 'PostgreSQL did not start. Read the disposable container log.\n' >&2
    docker logs "$database" >&2
    exit 1
fi
printf 'recovery-contract: docker=%s\n' "$(docker version --format '{{.Server.Version}}')"
docker image inspect "$image" "$postgres_image" --format '{{.Id}} {{json .RepoDigests}}'
docker exec "$database" psql -U skein_bootstrap -d skein -Atc 'SHOW server_version'
# config.STOCK_DIR prefers /usr/local/skein_stock. Old cards with new predicates fail boot.
runner="$(docker run --detach --name "skein-recovery-runner-$suffix" --label "$label" \
    --network "$network" --user "$(id -u):$(id -g)" \
    --read-only --tmpfs /tmp:rw,nosuid,size=512m --shm-size=128m \
    --mount "type=bind,source=$root,target=/contract,readonly" \
    --mount "type=bind,source=$root/backend,target=/usr/local/skein_stock,readonly" \
    --workdir /contract/backend \
    -e HOME=/tmp -e PYTHONPATH=/contract/backend:/tmp/test-deps \
    -e SKEIN_DATABASE_URL="host=database port=5432 dbname=skein user=skein_bootstrap password=$password" \
    -e SKEIN_ROLE_CONTRACT=1 -e PYTHONDONTWRITEBYTECODE=1 -e PYTHON_DOTENV_DISABLED=1 \
    --entrypoint python "$image" -c 'import time; time.sleep(3600)')"
# Test dependencies exist only in this owned container, never in the serving image.
docker exec "$runner" python -m pip install --quiet --no-cache-dir --target /tmp/test-deps \
    pytest==9.1.1 pytest-xdist==3.8.0
docker exec "$runner" sh -c 'python --version; pg_dump --version; pg_restore --version'
docker exec "$runner" python -m pytest -q -n0 -p no:cacheprovider \
    tests/test_database_role.py tests/test_recovery_runbook.py \
    tests/test_admin_backup.py::test_restore_drill_recovers_one_database_unit_and_requires_artifact_volume \
    tests/test_admin_backup.py::test_mirror_only_recovery_is_explicitly_partial \
    tests/test_admin_backup.py::test_backup_digest_rides_the_anchor_log \
    -k 'not upgrade_card'
