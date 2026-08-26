#!/usr/bin/env bash
# Dependency advisories for the code that actually ships.
#
#   ./scripts/audit-deps.sh [backend|frontend|workplace|all]
#
# Every mode audits production dependencies only. Dev-tool advisories stay in
# Renovate rather than blocking an unrelated application release.
set -euo pipefail
cd "$(dirname "$0")/.."

mode="${1:-all}"
case "$mode" in
    backend | frontend | workplace | all) ;;
    *)
        echo "audit-deps.sh: unknown argument. Use backend, frontend, workplace, or all." >&2
        exit 2
        ;;
esac

if [ -d backend/.venv/bin ]; then
    export PATH="$PWD/backend/.venv/bin:$PATH"
fi

if [ "$mode" = "backend" ] || [ "$mode" = "all" ]; then
    echo "== pip-audit (core production dependencies) =="
    requirements="$(mktemp)"
    trap 'rm -f "$requirements"' EXIT
    uv pip compile backend/pyproject.toml --quiet -o "$requirements"
    pip-audit --requirement "$requirements" --no-deps
fi

if [ "$mode" = "frontend" ] || [ "$mode" = "all" ]; then
    echo "== npm audit (core frontend production dependencies) =="
    (cd frontend && npm audit --omit=dev)
fi

if [ "$mode" = "workplace" ] || [ "$mode" = "all" ]; then
    echo "== pip-audit (workplace production lock) =="
    pip-audit --requirement examples/workplace-extension/requirements.lock --no-deps
    echo "== npm audit (workplace frontend production lock) =="
    (cd examples/workplace-extension && npm audit --omit=dev)
fi

echo "dependency audit passed ($mode)"
