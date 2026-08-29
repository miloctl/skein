#!/usr/bin/env bash
# The one list of lint gates. .gitea/workflows/ci.yml calls this script with a
# mode argument — add a gate here and it runs locally and on push with no
# second edit. Do not restate a gate in ci.yml.
set -euo pipefail
cd "$(dirname "$0")/.."

mode="${1:-all}"
case "$mode" in
    backend | frontend | all) ;;
    *)
        echo "lint.sh: unknown argument. Use backend, frontend, or all." >&2
        exit 2
        ;;
esac

# CI installs the backend dev tools into the runner's Python, not a venv;
# without this fallback every gate below fails there with "No such file".
if [ -d backend/.venv/bin ]; then
    export PATH="$PWD/backend/.venv/bin:$PATH"
fi

if [ "$mode" != "frontend" ]; then
    echo "== ruff =="
    ruff check backend/app backend/tests backend/seed.py cli/skein_cli.py scripts
    ruff format --check backend/app backend/tests backend/seed.py cli/skein_cli.py scripts

    echo "== mypy =="
    (cd backend && mypy)

    echo "== vulture (dead code) =="
    (cd backend && vulture)

    echo "== versioned content =="
    (cd backend && python -m app.content)

    echo "== license copies =="
    # backend/ carries copies because PEP 639 forbids ../ in license-files;
    # a drifted copy would ship a wheel with the wrong license text.
    cmp LICENSE backend/LICENSE && cmp NOTICE backend/NOTICE
    cmp LICENSE frontend/LICENSE && cmp NOTICE frontend/NOTICE
    cmp LICENSE frontend/packages/extension-api/LICENSE
    cmp NOTICE frontend/packages/extension-api/NOTICE

    echo "== theme contrast =="
    python3 scripts/check_theme_contrast.py

    echo "== simplified english (knots how:) =="
    # needs pyyaml: the PATH fallback above resolves python3 to the backend
    # venv locally, and CI installs the backend deps into the runner's python
    python3 scripts/check_ste.py
fi

if [ "$mode" != "backend" ]; then
    # eslint does not typecheck, so a type error reaches main with every gate
    # green. CLAUDE.md tells a person to run `npm run build`; this makes the
    # gate enforce it, at a fraction of a full build's cost. CI's frontend job
    # sets SKIP_TSC=1 because its `next build` step typechecks the same files —
    # set it anywhere else and type errors reach main unchecked.
    if [ "${SKIP_TSC:-0}" != "1" ]; then
        echo "== typescript =="
        (cd frontend && npx --no-install tsc --noEmit)
    else
        # Announced, never silent: an exported SKIP_TSC from a debug session
        # would otherwise skip the gate locally with no trace in the output.
        echo "== typescript == skipped (SKIP_TSC=1)"
    fi

    echo "== eslint =="
    (cd frontend && npm run --silent lint)

    echo "== knip (dead code) =="
    (cd frontend && npm run --silent knip)
fi

echo "lint checks passed ($mode)"
