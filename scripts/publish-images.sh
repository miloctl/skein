#!/usr/bin/env bash
# Build and push the container images for a release.
#
# Usage:
#   SKEIN_REGISTRY=registry.example.com/skein \
#     ./scripts/publish-images.sh 0.2.2 prod=API_URL,SITE_URL [dev=API_URL,SITE_URL ...]
#
# One backend image per version. One frontend image per version PER
# ENVIRONMENT: NEXT_PUBLIC_API_URL and NEXT_PUBLIC_SITE_URL are baked into
# the bundle at build time (frontend/Dockerfile), so the tag carries the
# environment name (0.2.2-prod). NEXT_PUBLIC_API_TOKEN is never set here —
# it would bake a shared bearer secret into a registry image, and the
# k8s deployment runs oidc or api-key mode, which do not read it.
#
# Log in to the registry first (docker login / podman login / oc registry
# login). Version tags are immutable: the script refuses to overwrite an
# existing remote tag by never retagging — a moved tag breaks ArgoCD's
# declarative model.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tool="${CONTAINER_TOOL:-docker}"

version="${1:-}"
shift || true
if [ -z "$version" ] || [ "$#" -eq 0 ]; then
  echo "publish-images: no version or no environment given." >&2
  echo "Run: SKEIN_REGISTRY=<registry/path> $0 <version> <env>=<api-url>,<site-url> ..." >&2
  exit 1
fi
registry="${SKEIN_REGISTRY:-}"
if [ -z "$registry" ]; then
  echo "publish-images: SKEIN_REGISTRY is not set. Set it to the registry path (registry.example.com/skein)." >&2
  exit 1
fi

# Same guard as the CI publish job: the tag must name the tree it ships.
declared="$(sed -n 's/^version = "\(.*\)"/\1/p' "$root/backend/pyproject.toml")"
if [ "$declared" != "$version" ]; then
  echo "publish-images: version $version does not match backend/pyproject.toml ($declared)." >&2
  echo "Set the pyproject version first (RELEASING.md section 1)." >&2
  exit 1
fi

"$tool" build -t "$registry/skein:$version" "$root/backend"
"$tool" push "$registry/skein:$version"

for spec in "$@"; do
  env_name="${spec%%=*}"
  urls="${spec#*=}"
  api_url="${urls%%,*}"
  site_url="${urls#*,}"
  if [ -z "$env_name" ] || [ "$api_url" = "$spec" ] || [ -z "$site_url" ]; then
    echo "publish-images: cannot parse '$spec'. Write <env>=<api-url>,<site-url>." >&2
    exit 1
  fi
  tag="$registry/skein-frontend:$version-$env_name"
  "$tool" build \
    --build-arg "NEXT_PUBLIC_API_URL=$api_url" \
    --build-arg "NEXT_PUBLIC_SITE_URL=$site_url" \
    -t "$tag" "$root/frontend"
  "$tool" push "$tag"
done

echo "publish-images: pushed skein:$version and $# frontend image(s) to $registry"
