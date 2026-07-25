# Skein (formerly Strands Team Platform)

Internal coordination harness for an AI-enabled strike team (humans + AI
agents). FastAPI + Strands Agents SDK + SQLite backend; Next.js 16 +
assistant-ui frontend. Brand: "Skein — many strands, one formation"; display
surfaces say Skein, internal identifiers keep the `strands`/`STRANDS_*` names.

`docs/FEATURES.md` is the reference for what is already built (surfaces,
endpoints, jobs) — read it first. `docs/SPEC.md` is the original phase plan,
kept for its data model and constraints; `docs/ROADMAP.md` holds the feature
ideation and engineering backlog.

## Hard constraints

- **Keyless-first.** No API keys are assumed. Every feature needs a
  deterministic core (DB + REST + UI). Prefer programmatic solutions (SQL,
  rules, heuristics) over LLM calls; the agent layer is an optional shell.
  `STRANDS_MODEL_PROVIDER=mock` must always work end-to-end.
- **Two write paths, one service layer.** Humans mutate via REST, agents via
  Strands tools. Both MUST call the shared functions in `backend/app/services/`
  — never write SQL in a route or tool.
- **Provenance on every write.** Services record `origin`
  (`human|agent|agent_verified`) and `created_by`, and log to `activity`.
- **Migrations are append-only.** Schema changes go in a new numbered file in
  `backend/migrations/`; never edit an applied migration or `db.py` schema
  inline.

## Commands

```bash
# backend (from backend/)
uv venv .venv && uv pip install -e ".[dev]" --python .venv/bin/python   # deps
.venv/bin/pytest                                        # tests
.venv/bin/ruff check app tests seed.py ../cli/strands_cli.py   # lint
.venv/bin/ruff format app tests seed.py ../cli/strands_cli.py  # format
.venv/bin/mypy                                          # type check
.venv/bin/vulture                                       # dead code
.venv/bin/uvicorn app.main:app --port 8000 --reload     # run
.venv/bin/python seed.py                                # demo data
.venv/bin/python -m app.bootstrap_key <name>            # first API key per person
                                                        # (private surfaces need one)

# frontend (from frontend/)
npm run dev     # dev server on :3000
npm run build   # verify compile (run before committing frontend changes)

./dev.sh        # both at once (repo root)
./lint.sh       # all lint gates CI runs: ruff + mypy + vulture + eslint + knip
```

Run `./lint.sh` before every commit — it is the exact gate CI runs; a commit
that hasn't passed it will fail on push-to-main.

## Architecture map

- `backend/app/services/` — all business logic + SQL (the only write path)
- `backend/app/tools/` — Strands `@tool` wrappers over services (agent write path)
- `backend/app/routes/` — FastAPI routers: REST wrappers over services + chat SSE
- `backend/app/agents/` — Chief-of-Staff orchestrator, planner sub-agent, mock provider
- `backend/migrations/` — numbered SQL, applied at startup, tracked in `schema_version`
- `backend/playbooks/*.yaml` — project-class templates (edited like code)
- `backend/data/` — gitignored: platform.db, sessions/, artifacts/, backups/, exports/
- `frontend/app/` — pages; `frontend/components/` — UI; `frontend/lib/` — api client/config

## Conventions

- Current user comes from the `X-User` header (frontend name picker, trusted
  LAN model). FastAPI routes take it via the `CurrentUser` dependency.
- Times are UTC ISO-8601 strings (`db.now()`); dates are `YYYY-MM-DD`.
- Tools return JSON strings; services return dicts/lists.
- Keep frontend components small and Tailwind-styled; no extra UI libraries.
- Python: no comments narrating what code does; match existing terse style.
