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
grep -q "name: ATLAS_SKEIN_DATA" "$rendered"
grep -q "name: ATLAS_API_URL" "$rendered"
grep -q "name: atlas-skein-data" "$rendered"
grep -q "claimName: atlas-skein-data" "$rendered"
grep -q "fsGroup: 1000" "$rendered"
grep -q "fsGroupChangePolicy: OnRootMismatch" "$rendered"
grep -q "runAsNonRoot: true" "$rendered"
echo "reference-deployment-contract: standard Kustomize render passed"
