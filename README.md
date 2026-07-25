# 🧵 Skein

**Many strands. One formation.**

A skein is two things: a coil of yarn — many strands wound together so they
pull as one — and a flock of geese flying in V-formation, where the lead
rotates and every bird's lift carries the one behind it. That's this team:
humans and AI agents drafting off each other, earning turns at the front,
receipts shown at every checkpoint.

Skein is an internal coordination harness for an AI-enabled strike team —
engagements, milestones, tasks, blockers, questions, decisions, standups,
intake triage, a knowledge base, and a team calendar, shared between people
and their agents.

Built on the [Strands Agents SDK](https://github.com/strands-agents/sdk-python)
(backend agents) and [assistant-ui](https://github.com/assistant-ui/assistant-ui)
(chat frontend). **Works fully without API keys** — every feature has a
deterministic core; connecting a model provider (a signed-in Ollama daemon is
enough) upgrades the experience.

> Formerly "Strands Team Platform" — renamed so the product stops colliding
> with its framework. Internal identifiers (`STRANDS_*` env vars, key prefixes,
> data paths) are unchanged; the CLI installs as both `skein` and `strands`.

## Surfaces

| Route | What it is |
|---|---|
| `/` | **My Day** — what changed and what needs *you*, in under 30 seconds |
| `/chat` | Chief-of-Staff agent (streaming; mock provider works keyless) |
| `/dashboard` | Engagements · blockers · capacity · milestones · tasks · Q&A · decisions · standups · calendar · notes · activity |
| `/insights` | Findings feed with click-through receipts + team-rolled trends (MTTR, automation ratio, adoption, token spend) |
| `/portfolio` | Portfolio layer — engagement health (R/Y/G with receipts), weekly commitment line, capacity conflicts, flow metrics, slip forecast, commitments, exec readout |
| `/agents` | Agents as teammates — mission control, authority matrix, trust scores, agent inboxes |
| `/review` | Review inbox — approve/reject proposed changes (agent approval gate) |
| `/intake` | Engagement front door — submit → RICE-lite score → accept/defer/decline → what-if staffing |
| ⌘K anywhere | Quick capture — freeform text auto-routed to task/question/note/decision/blocker/commitment |

## Architecture

```
backend/   FastAPI + Strands Agents + SQLite (WAL, migrations, FTS5)
  ├─ app/services/   ALL business logic — the single write path
  ├─ app/routes/     REST (human writes) + /api/chat SSE (agent writes)
  ├─ app/tools/      40+ Strands @tool wrappers over the same services
  ├─ app/agents/     Chief of Staff + planner sub-agent + keyless mock agent
  ├─ migrations/     numbered SQL, applied at startup (schema_version)
  ├─ playbooks/      YAML project-class templates (prototype, incident, migration)
  └─ data/           gitignored: platform.db, sessions/, artifacts/, backups/, exports/

frontend/  Next.js 16 + @assistant-ui/react + Tailwind
```

Key mechanics:

- **Provenance everywhere** — every record carries `origin`
  (`human | agent | agent_verified`) and `created_by`; every mutation lands in
  the activity log.
- **Approval gate** — with `STRANDS_AGENT_REVIEW=1`, agent writes become
  `pending_changes` proposals that humans approve in `/review`.
- **Programmatic automation** (no LLM): blocker auto-extraction from standups,
  hourly escalation sweep, RICE-lite intake scoring, rule-based quick capture,
  FTS5 workspace search, deterministic daily digest, playbook instantiation
  with lessons surfaced at kickoff, handoff package generation, daily backups,
  engagement health scoring, flow metrics, slip forecasting, auto-drafted
  weekly plans, decision half-life sweeps, versioned context packs.
- **Authority matrix & trust** — per (agent, entity) authority
  (`autonomous | notify | review | forbidden`) enforced in the tool gate;
  trust scores computed from review verdicts suggest (never auto-apply)
  promotions. Tasks delegate to agents with a required human sponsor.
- **LLM layer (connect keys later)** — conversational Chief of Staff, planner
  that adapts playbooks, digest narration, optional semantic search
  (`STRANDS_EMBEDDINGS=1`), token usage accounting per thread/model.

## Run with Docker (recommended for the team)

```bash
docker compose up --build -d     # backend :8000 + frontend :3000, data in a named volume
docker compose exec backend python seed.py   # optional demo data
docker compose logs -f backend               # watch migrations + scheduler start
# private surfaces (People page, admin export) need a personal API key —
# mint each person's FIRST key out-of-band, then paste via the 🔑 button:
docker compose exec backend python -m app.bootstrap_key <name>
```

If something already uses port 3000 on the host (true on this box — a
`serve.js` app owns it), pick another frontend port:

```bash
STRANDS_FRONTEND_PORT=3100 docker compose up --build -d   # UI at :3100
```

The SQLite database, chat sessions, artifacts, and daily backups live in the
`strands-data` volume. Configure via `backend/.env` (picked up automatically;
rebuild not needed for backend env changes — `docker compose up -d` again is
enough. `STRANDS_HOST`/`STRANDS_API_TOKEN` are baked into the frontend bundle
and DO need `--build`).

**Ollama in Docker:** a container's `localhost` is not the host, so compose
overrides `STRANDS_OLLAMA_HOST` to `http://host.docker.internal:11434`
(mapped to the host gateway) — the host's signed-in Ollama daemon, including
`*-cloud` models, works from inside the container. **But default Ollama
installs bind 127.0.0.1 only**, which the gateway can't reach. Either fix the
daemon (`sudo systemctl edit ollama` → `Environment="OLLAMA_HOST=0.0.0.0"`)
or, without root, run the bundled bridge (`ops/ollama-bridge.py`, a user
systemd service on this box already: `ollama-bridge`) and point at port 11435.

**The verified command for THIS box** (port 3000/3100 taken, loopback Ollama
bridged on 11435):

```bash
STRANDS_FRONTEND_PORT=3200 \
STRANDS_OLLAMA_HOST=http://host.docker.internal:11435 \
docker compose up --build -d
# UI: http://localhost:3200 · API: http://localhost:8000
```

**Backup mirror in Docker:** the mirror path from `backend/.env` doesn't
exist inside the container; uncomment the `/backup-mirror` volume + env lines
in `docker-compose.yml` to mount your NAS path and re-enable it.

For a ~10-person team this single-box setup is deliberate: SQLite in WAL mode
handles this write volume easily, and one backend container means exactly one
scheduler.

**Security model, stated plainly:** identity is a trusted name picker
(`X-User`) — teammates, not strangers. `STRANDS_API_TOKEN` adds a shared
bearer token, but it is baked into the frontend's public JS bundle, so anyone
who can load the UI can read it — it keeps out network scanners, not people
who can reach port 3000; treat per-user `sk-strands-` keys and a reverse
proxy as the real credentials. To actually expose this beyond a trusted
network, put both services behind an authenticating reverse proxy (Tailscale,
Caddy + SSO, etc.). Copy `data/backups/` off the box on a schedule (or set
`STRANDS_BACKUP_MIRROR`) — backups otherwise live on the same volume as the
database.

Known-and-accepted within that model (documented so nobody rediscovers them
as surprises): the REST write path does not pass through the agent review
gate or authority matrix — only the tool/MCP paths do — so issue `sk-` keys
to humans, not to agent processes you want gated; the CI webhook
(`/api/webhooks/ci`) inherits only whatever the shared token provides; and
the Actions runner on this box runs push-to-main only — never re-add a
`pull_request` trigger while the runner has docker access on the production
host. Authority levels can only be set by human identities (self-service by
an agent is refused).

## Optional integrations — built in, off until configured

| Integration | Turns on when | What you get |
|---|---|---|
| Slack outbound | `SLACK_WEBHOOK_URL` | Immediate pings + twice-daily notification digests |
| Slack commands | `SLACK_SIGNING_SECRET` | `/strands …` slash command (capture, briefing, search, plan) with signature verification |
| MCP tools | `STRANDS_MCP_SERVERS` (JSON) | GitHub/Linear/etc. tools attached to the real agent |
| Prebuilt tools | `STRANDS_EXTRA_TOOLS` | Allowlisted [strands-agents-tools](https://github.com/strands-agents/tools) for the real agent (keyless: `calculator,current_time,think,batch,sleep,rss`; key-gated: tavily/exa research tools — full allowlist in `app/agents/extra_tools.py`). Shell/file/exec tools **and** `http_request`/`use_agent`/`workflow` are deliberately not loadable — see `app/agents/extra_tools.py` for the security rationale |
| Semantic search | `STRANDS_EMBEDDINGS=1` + OpenAI key | Embeddings alongside FTS5 |
| OpenTelemetry | `STRANDS_OTEL_ENDPOINT` | Agent traces to Jaeger/Langfuse |
| API auth | `STRANDS_API_TOKEN` | Shared bearer token on every endpoint |

Notification tiers (immediate / digest / passive) and cross-thread agent
memory (`/remember`, `remember`/`recall_memories` tools, auto-injected into
the agent's prompt) work keyless, in-app.

## The developer loop

- **Per-teammate API keys** — create yours with
  `curl -X POST $URL/api/keys -H 'X-User: you' -H 'Content-Type: application/json' -d '{"label":"cli"}'`
  (store the `sk-strands-…` once — it is never shown again); keys authenticate
  and *attribute* automation, and satisfy the shared token gate. If
  `STRANDS_API_TOKEN` is set, pass it as the bearer when minting your first key.
- **`strands` CLI** (stdlib-only): `pipx install ./cli`, then
  `strands config --url … --key …` and
  `strands capture|standup|my-day|tasks|blockers|search|week|eval|context`.
- **`strands eval`** — replays the capture classifier against its labeled
  feedback corpus (`POST /api/feedback`); exits 1 on regressions.
- **`strands context --write AGENTS.md`** — emits the versioned team context
  pack (decisions, health, lessons, conventions) for any agent to load; also
  an MCP resource (`strands://context-pack`).
- **Git trailers** — `strands install-hooks`; commits with `Closes-Task: #12`
  auto-close the task and log the SHA.
- **CI webhook** — point GitHub Actions (or POST `{repo, branch, status, run_url}`)
  at `/api/webhooks/ci`: a red default-branch build files a deduped high-impact
  blocker; green auto-resolves it.
- **MCP server** — your *other* AI agents join the platform:
  `claude mcp add strands -- env STRANDS_MCP_USER=you /abs/path/to/backend/.venv/bin/python -m app.mcp_server`
  (needs the local backend install — Docker-only deployments should `uv venv`
  the backend once on the host for MCP use; run `strands install-hooks` inside
  each work repo you want git-trailer sync in)
  exposes `get_my_day`, `capture`, `create_task`, `log_decision`,
  `search_workspace`, `add_blocker`, `remember`, `my_inbox`,
  `portfolio_health`, `get_context_pack`, and more against the same DB.

## Delight & team pulse

Ship an engagement → confetti + recap card + team notification. Long-lived
blockers get a funeral (🪦 "It fought hard. It lost."). The digest opens with
a date-seeded line the whole team shares — and goes straight-faced whenever
something is escalated. The dashboard's **Team pulse** tracks season-scoped,
team-level stats only (standup chain, blocker speedruns, ships, lessons) —
deliberately no individual leaderboards.

## Setup

Prerequisites: Python 3.10+, Node 20+, [uv](https://github.com/astral-sh/uv),
and pipx (for the CLI).

```bash
# backend
cd backend
uv venv .venv && uv pip install -e ".[dev]" --python .venv/bin/python
cp .env.example .env                       # defaults to the keyless mock provider
.venv/bin/python seed.py                   # optional demo data
.venv/bin/uvicorn app.main:app --port 8000 --reload

# frontend
cd frontend
npm install && cp .env.local.example .env.local
npm run dev                                # http://localhost:3000

# or both: ./dev.sh   ·   tests: cd backend && .venv/bin/pytest
```

Model provider in `backend/.env`:

| Variable | Values | Default |
|---|---|---|
| `STRANDS_MODEL_PROVIDER` | `mock` \| `anthropic` \| `openai` \| `ollama` | `mock` (no keys needed) |
| `STRANDS_MODEL_ID` | any model ID | `claude-opus-4-8` / `gpt-5` / `gpt-oss:120b-cloud` |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | credential for the chosen provider | — |
| `STRANDS_OLLAMA_HOST` | local daemon or `https://ollama.com` | `http://localhost:11434` |
| `OLLAMA_API_KEY` | only for direct Ollama Cloud (no local daemon) | — |
| `STRANDS_AGENT_REVIEW` | `1` routes agent writes through /review | `0` |

**Free real-model tier:** `ollama` needs no API key at all when the box has a
signed-in Ollama daemon (`ollama signin`) — `*-cloud` model IDs are proxied to
Ollama Cloud (free tier available), local model IDs run on-box. Verified
end-to-end: streaming chat, tool calls, and usage accounting.

## Try it (keyless)

- Press **⌘K**: `blocked on vendor contract` → lands in the blocker register.
- Chat: `/plan incident Payments outage` → engagement + milestones + tasks +
  rituals from the incident playbook, with past incident lessons attached.
- Chat: `/briefing`, `/search cutover`, `/help`.
- `/intake`: submit a request, score it, accept it → engagement appears.
- `POST /api/engagements/{id}/handoff` → markdown handoff package in
  `backend/data/artifacts/`.

## Status & roadmap

Phases 0–2 of [docs/SPEC.md](docs/SPEC.md) plus the synthesized picks from
three ideation rounds are **built**: the keyless operating system, integrations (Slack, MCP both ways,
CI, keys, CLI), delight & pulse, and the round-3 operating-system layer
(portfolio health, weekly commitment line, flow metrics, agent delegation +
authority matrix, review analytics + eval corpus, decision half-life,
commitment ledger, context pack). The full feature reference is
[docs/FEATURES.md](docs/FEATURES.md); remaining ideas live in
[docs/ROADMAP.md](docs/ROADMAP.md).
