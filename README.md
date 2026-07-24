# 🧵 Strands Team Platform

An internal coordination harness for an AI-enabled strike team — humans and AI
agents sharing engagements, milestones, tasks, blockers, questions, decisions,
standups, intake triage, a knowledge base, and a team calendar.

Built on the [Strands Agents SDK](https://github.com/strands-agents/harness-sdk)
(backend agents) and [assistant-ui](https://github.com/assistant-ui/assistant-ui)
(chat frontend). **Works fully without API keys** — every feature has a
deterministic core; connecting a model provider upgrades the experience.

## Surfaces

| Route | What it is |
|---|---|
| `/` | **My Day** — what changed and what needs *you*, in under 30 seconds |
| `/chat` | Chief-of-Staff agent (streaming; mock provider works keyless) |
| `/dashboard` | Engagements · blockers · capacity · milestones · tasks · Q&A · decisions · standups · calendar · notes · activity |
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
  ├─ app/tools/      39 Strands @tool wrappers over the same services
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
docker compose up --build        # backend :8000 + frontend :3000, data in a named volume
```

The SQLite database, chat sessions, artifacts, and daily backups live in the
`strands-data` volume. Configure via `backend/.env` (picked up automatically).
For a ~10-person team this single-box setup is deliberate: SQLite in WAL mode
handles this write volume easily, and one backend container means exactly one
scheduler.

**Security model, stated plainly:** identity is a trusted name picker
(`X-User`) — teammates, not strangers. `STRANDS_API_TOKEN` adds a shared
bearer token, but it is baked into the frontend's public JS bundle, so anyone
who can load the UI can read it — it keeps out network scanners, not people
who can reach port 3000. To actually expose this beyond a trusted network,
put both services behind an authenticating reverse proxy (Tailscale, Caddy +
SSO, etc.). Copy `data/backups/` off the box on a schedule — backups live on
the same volume as the database.

## Optional integrations — built in, off until configured

| Integration | Turns on when | What you get |
|---|---|---|
| Slack outbound | `SLACK_WEBHOOK_URL` | Immediate pings + twice-daily notification digests |
| Slack commands | `SLACK_SIGNING_SECRET` | `/strands …` slash command (capture, briefing, search, plan) with signature verification |
| MCP tools | `STRANDS_MCP_SERVERS` (JSON) | GitHub/Linear/etc. tools attached to the real agent |
| Prebuilt tools | `STRANDS_EXTRA_TOOLS` | Allowlisted [strands-agents-tools](https://github.com/strands-agents/tools) for the real agent (`calculator,current_time,think,batch` keyless; `tavily_search`/`exa_search` with their keys). Shell/file/exec tools **and** `http_request`/`use_agent`/`workflow` are deliberately not loadable — see `app/agents/extra_tools.py` for the security rationale |
| Semantic search | `STRANDS_EMBEDDINGS=1` + OpenAI key | Embeddings alongside FTS5 |
| OpenTelemetry | `STRANDS_OTEL_ENDPOINT` | Agent traces to Jaeger/Langfuse |
| API auth | `STRANDS_API_TOKEN` | Shared bearer token on every endpoint |

Notification tiers (immediate / digest / passive) and cross-thread agent
memory (`/remember`, `remember`/`recall_memories` tools, auto-injected into
the agent's prompt) work keyless, in-app.

## The developer loop

- **Per-teammate API keys** — `POST /api/keys` (label it, store the `sk-strands-…`
  once); keys authenticate and *attribute* automation, and satisfy the shared
  token gate.
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
  `claude mcp add strands -- env STRANDS_MCP_USER=you backend/.venv/bin/python -m app.mcp_server`
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

Phases 0–2 of [docs/SPEC.md](docs/SPEC.md) plus three ideation rounds are
**built**: the keyless operating system, integrations (Slack, MCP both ways,
CI, keys, CLI), delight & pulse, and the round-3 operating-system layer
(portfolio health, weekly commitment line, flow metrics, agent delegation +
authority matrix, review analytics + eval corpus, decision half-life,
commitment ledger, context pack). The full feature reference is
[docs/FEATURES.md](docs/FEATURES.md); remaining ideas live in
[docs/ROADMAP.md](docs/ROADMAP.md).
