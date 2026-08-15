#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v kubectl >/dev/null || {
  echo "reference-deployment-contract: kubectl is required" >&2
  exit 1
}

rendered="$(mktemp)"
trap 'rm -f "$rendered"' EXIT
kubectl kustomize "$root/examples/workplace-extension" >"$rendered"
grep -q "name: atlas-skein-content" "$rendered"
grep -q "atlas_delivery.yaml" "$rendered"
grep -q "image: atlas-skein:1.0.0" "$rendered"
grep -q "image: atlas-skein-frontend:1.0.0" "$rendered"
grep -q "name: ATLAS_SKEIN_STORE" "$rendered"
grep -q "name: ATLAS_API_URL" "$rendered"
grep -q "name: SKEIN_DATABASE_URL" "$rendered"
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
grep -q "fsGroup: 1000" "$rendered"
grep -q "fsGroupChangePolicy: OnRootMismatch" "$rendered"
grep -q "runAsNonRoot: true" "$rendered"
echo "reference-deployment-contract: standard Kustomize render passed"

# Core OpenShift overlays (deploy/k8s). Each assertion pins a decision
# whose loss reintroduces a concrete failure (named per line), not a
# style preference — deploy/k8s/README.md carries the reasons.
for overlay in example-prod example-dev; do
  kubectl kustomize "$root/deploy/k8s/overlays/$overlay" >"$rendered"
  grep -q "type: Recreate" "$rendered"          # the data PVC admits one mounter
  grep -q "replicas: 1" "$rendered"             # scheduler, rate caps, _inflight
  grep -q "ReadWriteOnce" "$rendered"           # artifacts/exports are node-local
  grep -q "startupProbe" "$rendered"            # migrations run before /health answers
  # the database: its own StatefulSet and volume, reached through a URL the
  # backend composes. A rendered manifest missing any of these means the
  # backend boots with no store and refuses every request.
  grep -q "kind: StatefulSet" "$rendered"
  grep -q "image: postgres:17" "$rendered"      # matches backend/Dockerfile PG_MAJOR
  grep -q "name: SKEIN_DATABASE_URL" "$rendered"
  grep -q "pg_isready" "$rendered"              # port-open is not cluster-ready
  grep -q "NOSUPERUSER" "$rendered"             # a superuser turns SQL into RCE
  grep -q "SKEIN_APP_USER" "$rendered"          # the app connects as that role
  grep -q "kind: NetworkPolicy" "$rendered"     # the 1:1 notes are on a network port now
  grep -q "timeout: 330s" "$rendered"           # router idle 30s < SSE silences
  grep -q "SKEIN_TRUST_PROXY_HOPS" "$rendered"
  ! grep -q "fsGroup:" "$rendered"              # restricted-v2 rejects any fixed value
done
echo "reference-deployment-contract: core deploy/k8s overlays passed"
