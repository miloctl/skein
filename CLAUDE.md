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
- **Input errors are 4xx, by classification.** If a request can produce an
  exception, `app/main.py` maps it to a 4xx. If only our own state can produce
  it, it stays a 500. Add an exception handler when a 500 traces back to
  something a caller sent, not when a new exception class appears. An error
  response is always JSON, and it never echoes the rejected value back.
- **Migrations are append-only.** Schema changes go in a new numbered file in
  `backend/migrations/`; never edit an applied migration or `db.py` schema
  inline. A migration must never UPDATE or DELETE an `activity` row that
  carries a `seq` — those rows are hash-chained, and a bulk rewrite breaks
  verification permanently at the earliest row it touches.
- **A filename names the behavior, not the session that made it.** This is
  what `app/services/` already does: 46 files, each named for its subject.
  Name a test for what it pins (`test_delegation.py`), never for the wave,
  round, audit or review that produced it. Name a doc for its function.
  Dates belong in `docs/reviews/`, which holds closed transcripts only.
  Migrations are the one exception: `db.py` records applied migrations by
  filename in `schema_version`, so renaming one re-runs it on every existing
  database. To rename a migration, add a new migration that UPDATEs
  `schema_version` in the same change.

## Commands

```bash
# backend (from backend/)
uv venv .venv && uv pip install -e ".[dev]" --python .venv/bin/python   # deps
.venv/bin/pytest                                        # tests
.venv/bin/ruff check app tests seed.py ../cli/skein_cli.py ../scripts   # lint
.venv/bin/ruff format app tests seed.py ../cli/skein_cli.py ../scripts # format
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

- Current user resolution is `SKEIN_AUTH_MODE` (`trusted-header` default: the
  `X-User` name-picker header; `api-key`; `oidc`), branched ONLY in
  `routes/deps.py`. Routes take it via `CurrentUser`; strong-identity
  surfaces use `StrongUser`, team-wide/roster surfaces use `AdminUser`.
- Times are UTC ISO-8601 strings (`db.now()`); dates are `YYYY-MM-DD`.
- Tools return JSON strings; services return dicts/lists.
- Keep frontend components small and Tailwind-styled; no extra UI libraries.
- Python: match the existing terse style. Comments follow "Code comments" below.

### User-visible wording

Functional text — errors, helper copy, instructions, refusals, notifications,
command replies — follows ASD-STE100 Simplified Technical English (the
`simple-english` skill, pragmatic mode). Brand voice is exempt and stays as
written: `lib/whimsy.ts` pools, digest openers, mock-agent replies, theme pack
names, and the field guide's `pitch:` lines. In `fieldguide/knots.yaml` the
split is by key, not by feel: `pitch:` says why a knot exists and stays warm,
`how:` tells the reader what to do and follows STE. Code, identifiers,
commands, and quoted errors are never rewritten.

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

### Code comments

A comment records a constraint that the code cannot show. Before you write
one, ask: what does an editor with no memory of this change break here? If
there is no answer, write nothing. An agent is that editor — it reads the
code in slices, with no access to the session that made it.

- Put the comment at the point of temptation, and state the consequence.
  Name the concrete failure, not the rule ("str() on the list matches no
  tool, and every persona gets ZERO tools").
- If the constraint lives in another file, name that file. Point to the
  test, the doc, or the component that enforces it.
- If you omit an item from a list on purpose, record that decision where
  the list lives. An absence with no comment reads as an oversight.
- Put the threat next to a security check. A check with no reason reads as
  redundant validation, and a later edit deletes it.
- If a re-check follows an `await`, name the race that it prevents.
- Never write narration ("loop over the users"), session history ("added
  in round 3"), hedges ("this should probably..."), or bare markers ("do
  not remove"). A marker with no consequence does not survive the next edit.
- If an edit makes a comment false, the edit is wrong or the comment must
  change with it. Never delete only the comment.
- If your own code needs an explanation, restructure the code instead. Keep
  the comment only when an external constraint causes the confusion (an SDK
  rule, a protocol, a cross-file contract).

`services/review.py`, `tools/_gate.py`, and `frontend/lib/theme.ts` show
the style.
