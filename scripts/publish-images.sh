#!/usr/bin/env bash
# Build and push the container images for a release.
#
# Usage:
#   SKEIN_REGISTRY=registry.example.com/skein \
#     ./scripts/publish-images.sh 0.3.2 prod=API_URL,SITE_URL [dev=API_URL,SITE_URL ...]
#
# One backend image per version. One frontend image per version PER
# ENVIRONMENT: NEXT_PUBLIC_API_URL and NEXT_PUBLIC_SITE_URL are baked into
# the bundle at build time (frontend/Dockerfile), so the tag carries the
# environment name (0.3.2-prod). NEXT_PUBLIC_API_TOKEN is never set here —
# it would bake a shared bearer secret into a registry image, and the
# k8s deployment runs oidc or api-key mode, which do not read it.
#
# Log in to the registry first (docker login / podman login / oc registry
# login). The script binds the build to one clean commit named by the trusted
# remote's annotated release tag. It refuses a registry tag that already exists. Registry-side tag immutability
# must also be on because only the registry can close a concurrent push race.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tool="${CONTAINER_TOOL:-docker}"
release_remote="${SKEIN_RELEASE_REMOTE:-github}"

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

frontend_tags=()
frontend_api_urls=()
frontend_site_urls=()
declare -A seen_environments=()
for spec in "$@"; do
  env_name="${spec%%=*}"
  urls="${spec#*=}"
  if [[ ! "$env_name" =~ ^[a-z0-9][a-z0-9._-]{0,31}$ ]] \
    || [[ ! "$urls" =~ ^[^,]+,[^,]+$ ]]; then
    echo "publish-images: an environment specification is invalid. Write <env>=<api-url>,<site-url>." >&2
    exit 1
  fi
  if [[ -n "${seen_environments[$env_name]+set}" ]]; then
    echo "publish-images: an environment name occurs more than once. Remove the duplicate." >&2
    exit 1
  fi
  seen_environments[$env_name]=1
  api_url="${urls%%,*}"
  site_url="${urls#*,}"
  frontend_tags+=("$registry/skein-frontend:$version-$env_name")
  frontend_api_urls+=("$api_url")
  frontend_site_urls+=("$site_url")
done

# The version alone does not bind image bytes. The annotated tag and clean
# tree prevent a later working copy from reusing the released identity.
declared="$(sed -n 's/^version = "\(.*\)"/\1/p' "$root/backend/pyproject.toml")"
if [ "$declared" != "$version" ]; then
  echo "publish-images: version $version does not match backend/pyproject.toml ($declared)." >&2
  echo "Run scripts/prepare-release.py before publication." >&2
  exit 1
fi
if [ -n "$(git -C "$root" status --porcelain --untracked-files=all)" ]; then
  echo "publish-images: the release tree has changes. Use a clean release checkout." >&2
  exit 1
fi
release_tag="refs/tags/v$version"
head_sha="$(git -C "$root" rev-parse HEAD)"
if ! remote_refs="$(
  git -C "$root" ls-remote --exit-code --tags \
    "$release_remote" "$release_tag" "$release_tag^{}" 2>/dev/null
)"; then
  echo "publish-images: the trusted release tag is unavailable. Check the Git remote and access." >&2
  exit 1
fi
remote_sha=""
while read -r sha ref; do
  if [ "$ref" = "$release_tag^{}" ]; then
    remote_sha="$sha"
  fi
done <<<"$remote_refs"
if [ "$remote_sha" != "$head_sha" ]; then
  echo "publish-images: the trusted remote tag does not name this release commit." >&2
  exit 1
fi

remote_tag_is_absent() {
  local tag="$1" output
  if output="$("$tool" manifest inspect "$tag" 2>&1)"; then
    echo "publish-images: remote tag $tag already exists. Publish a new version." >&2
    return 1
  fi
  output="${output,,}"
  case "$output" in
    *"no such manifest"*|*"manifest unknown"*|*"name unknown"*) return 0 ;;
  esac
  echo "publish-images: the registry did not report whether $tag exists. Check registry access." >&2
  return 1
}

backend_tag="$registry/skein:$version"
remote_tag_is_absent "$backend_tag"
for tag in "${frontend_tags[@]}"; do
  remote_tag_is_absent "$tag"
done

"$tool" build -t "$backend_tag" "$root/backend"
"$tool" push "$backend_tag"

for index in "${!frontend_tags[@]}"; do
  tag="${frontend_tags[$index]}"
  "$tool" build \
    --build-arg "NEXT_PUBLIC_API_URL=${frontend_api_urls[$index]}" \
    --build-arg "NEXT_PUBLIC_SITE_URL=${frontend_site_urls[$index]}" \
    -t "$tag" "$root/frontend"
  "$tool" push "$tag"
done

echo "publish-images: pushed skein:$version and ${#frontend_tags[@]} frontend image(s) to $registry"
