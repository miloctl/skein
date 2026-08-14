#!/usr/bin/env bash
# Dependency advisories for the code that actually ships.
#
#   ./scripts/audit-deps.sh [backend|frontend|all]
#
# Both halves audit the PRODUCTION dependency set only. A dev-tool advisory
# is Renovate's business, not a blocked merge: this pipeline is push-only
# (.gitea/workflows/ci.yml), so a failing audit blocks whatever unrelated
# commit happens to land next. That trade was already made for npm; this
# script is where the same decision lives for both.
set -euo pipefail
cd "$(dirname "$0")/.."

mode="${1:-all}"
case "$mode" in
    backend | frontend | all) ;;
    *)
        echo "audit-deps.sh: unknown argument. Use backend, frontend, or all." >&2
        exit 2
        ;;
esac

if [ -d backend/.venv/bin ]; then
    export PATH="$PWD/backend/.venv/bin:$PATH"
fi

if [ "$mode" != "frontend" ]; then
    echo "== pip-audit (production dependencies) =="
    # Resolved from pyproject WITHOUT the dev extra, not read from the
    # installed environment: CI and every developer install `-e ".[dev]"`,
    # so auditing what is installed audits pytest and ruff too.
    requirements="$(mktemp)"
    trap 'rm -f "$requirements"' EXIT
    uv pip compile backend/pyproject.toml --quiet -o "$requirements"
    # --no-deps because the compile above already pinned the full closure;
    # without it pip-audit re-resolves and can pull in newer transitives
    # that this deployment would never install.
    pip-audit --requirement "$requirements" --no-deps
fi

if [ "$mode" != "backend" ]; then
    echo "== npm audit (production dependencies) =="
    (cd frontend && npm audit --omit=dev)
fi

echo "dependency audit passed ($mode)"
