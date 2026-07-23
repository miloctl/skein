#!/usr/bin/env bash
# Run backend (:8000) and frontend (:3000) together for local dev.
set -euo pipefail
cd "$(dirname "$0")"

trap 'kill 0' EXIT

(cd backend && .venv/bin/uvicorn app.main:app --port 8000 --reload) &
(cd frontend && npm run dev) &

wait
