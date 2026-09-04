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

# npm audit consults a registry endpoint that answers 503 now and then. A
# 503 is not an advisory: retry it, then fail closed. A real advisory (exit 1
# with a report) fails at once — the retry never turns a finding into a pass.
npm_audit() {
    local dir="$1" attempt errors
    errors="$(mktemp)"
    for attempt in 1 2 3; do
        if (cd "$dir" && npm_config_fetch_timeout=60000 npm audit --omit=dev 2>"$errors"); then
            rm -f "$errors"; return 0
        fi
        if ! grep -Eq 'audit endpoint returned an error|Service Unavailable|ECONNRESET|ETIMEDOUT|EAI_AGAIN' "$errors"; then
            cat "$errors" >&2; rm -f "$errors"; return 1
        fi
        echo "npm audit endpoint unavailable (attempt $attempt of 3)" >&2
        [ "$attempt" -lt 3 ] && sleep $((attempt * 20))
    done
    cat "$errors" >&2; rm -f "$errors"
    echo "audit-deps: the npm audit endpoint stayed unavailable. Run the audit again later." >&2
    return 1
}

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
    npm_audit frontend
fi

if [ "$mode" = "workplace" ] || [ "$mode" = "all" ]; then
    echo "== pip-audit (workplace production lock) =="
    pip-audit --requirement examples/workplace-extension/requirements.lock --no-deps
    echo "== npm audit (workplace frontend production lock) =="
    npm_audit examples/workplace-extension
fi

echo "dependency audit passed ($mode)"
