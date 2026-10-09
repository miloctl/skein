#!/usr/bin/env bash
# The one list of lint gates. .gitea/workflows/ci.yml calls this script with a
# mode argument - add a gate here and it runs locally and on push with no
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

echo "== typography =="
# Authored text uses a hyphen for the en and em dash, and straight quotes.
# Code that must recognize one of these marks names it as an escape instead:
# the capture parsers accept a typed dash (services/capture.py,
# private_notes.py), and wording.quoted turns an interior apostrophe curly so
# a quoted title cannot close early. frontend/AGENTS.md is written by
# `next dev`, so it is not ours to edit.
if git grep -n -e $'\xe2\x80\x93' -e $'\xe2\x80\x94' -e $'\xe2\x80\x98' -e $'\xe2\x80\x99' \
    -e $'\xe2\x80\x9c' -e $'\xe2\x80\x9d' -- ':!frontend/AGENTS.md'; then
    echo "lint.sh: a dash or curly quote is in the files above. Use a hyphen (-) or a straight quote." >&2
    exit 1
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
    # No NOTICE compare for extension-api: that package is published at a
    # frozen 1.0.0, so its NOTICE is the one that shipped with it, and
    # prepare-release.py refuses any change to the package without a
    # version bump. The adapted content the root NOTICE credits (personas,
    # the STE linter) is not in that package.

    echo "== theme contrast =="
    python3 scripts/check_theme_contrast.py

    echo "== simplified english (knots how:) =="
    # needs pyyaml: the PATH fallback above resolves python3 to the backend
    # venv locally, and CI installs the backend deps into the runner's python.
    # The self-test makes a regex regression fail even when today's corpus
    # happens not to exercise the broken rule.
    python3 scripts/check_ste.py --self-test
    python3 scripts/check_ste.py
fi

if [ "$mode" != "backend" ]; then
    # eslint does not typecheck, so a type error reaches main with every gate
    # green. CLAUDE.md tells a person to run `npm run build`; this makes the
    # gate enforce it, at a fraction of a full build's cost. CI's frontend job
    # sets SKIP_TSC=1 because its `next build` step typechecks the same files -
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
