#!/usr/bin/env bash
# Every lint gate CI runs. .gitea/workflows/ci.yml duplicates these commands —
# a gate added here without updating ci.yml passes locally and never runs on push.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== ruff =="
backend/.venv/bin/ruff check backend/app backend/tests backend/seed.py cli/skein_cli.py scripts
backend/.venv/bin/ruff format --check backend/app backend/tests backend/seed.py cli/skein_cli.py scripts

echo "== mypy =="
(cd backend && .venv/bin/mypy)

echo "== vulture (dead code) =="
(cd backend && .venv/bin/vulture)

echo "== personas =="
(cd backend && .venv/bin/python -m app.services.personas)

echo "== license copies =="
# backend/ carries copies because PEP 639 forbids ../ in license-files;
# a drifted copy would ship a wheel with the wrong license text.
cmp LICENSE backend/LICENSE && cmp NOTICE backend/NOTICE

echo "== theme contrast =="
python3 scripts/check_theme_contrast.py

echo "== eslint =="
(cd frontend && npm run --silent lint)

echo "== knip (dead code) =="
(cd frontend && npm run --silent knip)

echo "all lint checks passed"
