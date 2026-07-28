#!/usr/bin/env bash
# Run every lint gate CI runs: ruff (backend + CLI), mypy (backend), eslint (frontend).
set -euo pipefail
cd "$(dirname "$0")"

echo "== ruff =="
backend/.venv/bin/ruff check backend/app backend/tests backend/seed.py cli/strands_cli.py
backend/.venv/bin/ruff format --check backend/app backend/tests backend/seed.py cli/strands_cli.py

echo "== mypy =="
(cd backend && .venv/bin/mypy)

echo "== vulture (dead code) =="
(cd backend && .venv/bin/vulture)

echo "== eslint =="
(cd frontend && npm run --silent lint)

echo "== knip (dead code) =="
(cd frontend && npm run --silent knip)

echo "all lint checks passed"
