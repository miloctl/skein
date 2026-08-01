# Skein

Internal coordination harness for an AI-enabled strike team (humans + AI
agents). FastAPI + Strands Agents SDK + SQLite backend; Next.js 16 +
assistant-ui frontend. Brand: "Skein — many strands, one formation". The
product is Skein everywhere — `SKEIN_*` env vars, `sk-skein-` key prefix,
`skein://` MCP URIs, the `skein` CLI. The bare name `strands` belongs to the
SDK alone (`from strands import tool`, `strands-agents*` deps) — never use it
for anything of ours.

`docs/FEATURES.md` is the reference for what is already built (surfaces,
endpoints, jobs) — read it first. `docs/SPEC.md` is the original phase plan,
kept for its data model and constraints; `docs/ROADMAP.md` holds the feature
ideation and engineering backlog.

## Hard constraints

- **Provider-agnostic.** `backend/app/config.py::PROVIDERS` is the list of
  model providers; `agents/team_agent.py::_model()` is the ONLY place that
  may branch on a provider name. Everywhere else reads
  `config.EFFECTIVE_PROVIDER` (never `MODEL_PROVIDER`, which keeps the raw
  value for honest reporting) or a capability off the registry. A bad
  provider must degrade to mock and surface `MODEL_PROVIDER_ERROR`, never
  take down the REST API.
- **Keyless-first.** No API keys are assumed. Every feature needs a
  deterministic core (DB + REST + UI). Prefer programmatic solutions (SQL,
  rules, heuristics) over LLM calls; the agent layer is an optional shell.
  `SKEIN_MODEL_PROVIDER=mock` must always work end-to-end.
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
.venv/bin/ruff check app tests seed.py ../cli/skein_cli.py   # lint
.venv/bin/ruff format app tests seed.py ../cli/skein_cli.py  # format
.venv/bin/mypy                                          # type check
.venv/bin/vulture                                       # dead code
.venv/bin/uvicorn app.main:app --port 8000 --reload     # run
.venv/bin/python seed.py                                # demo data
.venv/bin/python -m app.bootstrap_key <name>            # first API key per person
                                                        # (private surfaces need one)

# frontend (from frontend/)
npm run dev     # dev server on :3000
npm run build   # verify compile (run before committing frontend changes)

# app lifecycle (repo root)
./scripts/skein.sh start|stop|restart|status|logs   # detached; survives the terminal
./scripts/skein.sh dev                              # both in the foreground, Ctrl-C stops
./scripts/lint.sh   # all lint gates CI runs: ruff + mypy + vulture + eslint + knip
```

Run `./scripts/lint.sh` before every commit — it is the exact gate CI runs; a commit
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

### User-visible wording

Functional text — errors, helper copy, instructions, refusals, notifications,
command replies — follows ASD-STE100 Simplified Technical English (the
`simple-english` skill, pragmatic mode). Brand voice is exempt and stays as
written: `lib/whimsy.ts` pools, digest openers, mock-agent replies, theme pack
names, the field guide's warmth. Code, identifiers, commands, and quoted
errors are never rewritten.

- Errors state what happened, then the fix as an imperative. No rhetorical
  questions ("is it running?"), no apologies, no "Please".
- No contractions, no semicolons, no `e.g.`/`i.e.`, no `should`/`would`/`may`
  (write `must` or `can`, or restructure). Conditions come first: "If the
  build fails, read the log."
- One word per concept: **check** for the user action (`verify` is reserved
  for provenance, `reconfirm` for charter and decisions), **delete** for
  destruction (`forget` only for memories), **whoever runs the server**.
- One condition, one wording. The backend-unreachable error reads identically
  on every surface; a new surface copies that string rather than inventing one.
- Sentence-form text computes its plurals and verb agreement ("1 promise
  carries", "2 promises carry"). A bare `(s)` is fine in stat rows and labels.
- A rewrite must not change what a sentence claims. A refusal describes what
  the system prevented, never what already happened.
