#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v kubectl >/dev/null || {
  echo "reference-deployment-contract: kubectl is required" >&2
  exit 1
}

deployment="$root/examples/workplace-extension/deployment"
test -x "$deployment/10-app-role.sh"
test -x "$deployment/20-atlas-schema.sh"
grep -q -- "--single-transaction" "$deployment/10-app-role.sh"
grep -q "ext_atlas_extension" "$deployment/20-atlas-schema.sh"
grep -q "skein_agents-0.3.2-py3-none-any.whl" "$deployment/Dockerfile"
grep -q "atlas_skein_extension-2.0.0-py3-none-any.whl" "$deployment/Dockerfile"
grep -q "id=pip-config.*required=true" "$deployment/Dockerfile"
grep -q "id=npm-config.*required=true" "$deployment/Frontend.Dockerfile"
grep -q "replace-registry-host=npmjs" "$root/scripts/reference-images-contract.sh"
if grep -q "replace-registry-host=always" "$root/scripts/reference-images-contract.sh"; then
  echo "reference-deployment-contract: npm always-mode rewrites local tarballs as registry URLs" >&2
  exit 1
fi
if grep -Eq '(skein_agents|atlas_skein_extension)-\*\.whl' "$deployment/Dockerfile"; then
  echo "reference-deployment-contract: the backend image accepts an ambiguous wheel name" >&2
  exit 1
fi

rendered="$(mktemp)"
trap 'rm -f "$rendered"' EXIT
kubectl kustomize "$root/examples/workplace-extension" >"$rendered"
grep -q "name: atlas-skein-content" "$rendered"
grep -q "atlas_delivery.yaml" "$rendered"
grep -q "image: atlas-skein:2.0.0" "$rendered"
grep -q "image: atlas-skein-frontend:2.0.0" "$rendered"
grep -q "name: ATLAS_SKEIN_STORE" "$rendered"
grep -q "name: ATLAS_API_URL" "$rendered"
grep -q "name: SKEIN_DB_HOST" "$rendered"
grep -q "name: SKEIN_DB_USER" "$rendered"
grep -q "name: SKEIN_DB_PASSWORD" "$rendered"
# The extension store is a schema inside the Skein database. A PVC or a
# file path here means the example regressed to the file-era wiring, which
# the application no longer reads. An if, not `! grep`: set -e ignores a
# failing negated command, so `! grep` can never fail the script.
for regressed in "atlas-skein-data" "ATLAS_SKEIN_DATA"; do
  if grep -q "$regressed" "$rendered"; then
    echo "reference-deployment-contract: '$regressed' is file-era store wiring the app no longer reads" >&2
    exit 1
  fi
done
grep -q "type: Recreate" "$rendered"
grep -q "progressDeadlineSeconds: 1800" "$rendered"
grep -q "name: skein-secrets" "$rendered"
grep -q "runAsNonRoot: true" "$rendered"
grep -q "type: RuntimeDefault" "$rendered"
grep -q "readOnlyRootFilesystem: true" "$rendered"
grep -q "startupProbe" "$rendered"
grep -q "readinessProbe" "$rendered"
grep -q "livenessProbe" "$rendered"
grep -q "emptyDir: {}" "$rendered"
if grep -q "fsGroup:" "$rendered"; then
  echo "reference-deployment-contract: Atlas fixed fsGroup violates restricted-v2" >&2
  exit 1
fi
echo "reference-deployment-contract: standard Kustomize render passed"

# Core OpenShift overlays (deploy/k8s). Each assertion pins a decision
# whose loss reintroduces a concrete failure (named per line), not a
# style preference — deploy/k8s/README.md carries the reasons.
# ponytail: global greps over the whole render, not per-resource parsing — a
# second resource carrying the same string can mask a regression. Parse by
# kind/name (yq) if a masked regression ever ships.
for overlay in example-prod example-dev; do
  kubectl kustomize "$root/deploy/k8s/overlays/$overlay" >"$rendered"
  grep -q "type: Recreate" "$rendered"          # backend pods never overlap
  grep -q "replicas: 1" "$rendered"             # scheduler, rate caps, _inflight
  grep -q "ReadWriteOnce" "$rendered"           # block storage stays on one node
  grep -q "startupProbe" "$rendered"            # migrations run before /health answers
  # the database: its own StatefulSet and volume, reached through a URL the
  # backend composes. A rendered manifest missing any of these means the
  # backend boots with no store and refuses every request.
  grep -q "kind: StatefulSet" "$rendered"
  grep -q "image: postgres:17" "$rendered"      # matches backend/Dockerfile PG_MAJOR
  grep -q "name: SKEIN_DB_HOST" "$rendered"     # the app composes the conninfo
  grep -q "pg_isready" "$rendered"              # port-open is not cluster-ready
  grep -q "NOSUPERUSER" "$rendered"             # a superuser turns SQL into RCE
  grep -q "CREATE SCHEMA IF NOT EXISTS private AUTHORIZATION" "$rendered"
  grep -q -- "--single-transaction" "$rendered" # a crashed bootstrap leaves nothing
  if grep -q "GRANT CREATE ON DATABASE" "$rendered"; then
    echo "reference-deployment-contract: app role gained database-wide CREATE" >&2
    exit 1
  fi
  grep -q "SKEIN_APP_USER" "$rendered"          # the app connects as that role
  # The backend composes the conninfo from components (config._database_url).
  # A URI built by manifest interpolation breaks on a password holding
  # @ : / % ? or # — refuse the render if one comes back.
  grep -q "SKEIN_DB_PASSWORD" "$rendered"
  if grep -Eq 'postgres(ql)?://\$\(' "$rendered"; then
    echo "reference-deployment-contract: interpolated database URI breaks on special-character passwords" >&2
    exit 1
  fi
  grep -q "kind: NetworkPolicy" "$rendered"     # the 1:1 notes are on a network port now
  grep -q "app: skein-maintenance" "$rendered"  # bounded recovery pod, not Service traffic
  grep -q "timeout: 1000s" "$rendered"          # SSE and bounded manual backup
  grep -q "SKEIN_TRUST_PROXY_HOPS" "$rendered"
  grep -q "name: skein-backup-mirror" "$rendered" # public dump + anchor volume
  grep -q "mountPath: /backup-mirror" "$rendered"
  grep -q "SKEIN_BACKUP_MIRROR: /backup-mirror" "$rendered"
  grep -q "name: skein-data" "$rendered"          # artifact-volume recovery unit
  grep -q "mountPath: /data" "$rendered"
  grep -q "storage: 360Gi" "$rendered"            # local dumps + export + artifacts
  grep -q "storage: 320Gi" "$rendered"            # public mirror retention
  if grep -q "fsGroup:" "$rendered"; then
    echo "reference-deployment-contract: fixed fsGroup violates restricted-v2" >&2
    exit 1
  fi
done
echo "reference-deployment-contract: core deploy/k8s overlays passed"
