<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/site/img/banner-dark.png">
  <img src="docs/site/img/banner-light.png" alt="Twelve strands running as one formation, one leading, trading places as they go">
</picture>

# Skein

**Many strands. One formation.**

A skein is two things: a coil of yarn — many strands wound together so they
pull as one — and a flock of geese flying in V-formation, where the lead
rotates and every bird's lift carries the one behind it. That's this team:
humans and AI agents drafting off each other, earning turns at the front,
receipts shown at every checkpoint.

Skein is a self-hosted coordination harness for an AI-enabled strike team —
engagements, milestones, tasks, blockers, questions, decisions, standups,
intake triage, a knowledge base, and a team calendar, shared between people
and their agents.

Built on the [Strands Agents SDK](https://github.com/strands-agents/sdk-python)
(backend agents) and [assistant-ui](https://github.com/assistant-ui/assistant-ui)
(chat frontend). **Works fully without API keys** — every feature has a
deterministic core; connecting a model provider (a signed-in Ollama daemon is
enough) upgrades the experience.

## Surfaces

The nav has five destinations. Each one groups the routes under it, and every
URL is directly linkable.

| Nav | Routes | What it is |
|---|---|---|
| **My Day** | `/` | What changed and what needs *you*, in under 30 seconds |
| **Chat** | `/chat` | Chief-of-Staff agent, streaming. The mock provider works keyless. Type `/as <persona>` to switch heads — see [The Bench](docs/PERSONAS.md) |
| **Work** | `/portfolio` | Engagement health (R/Y/G with receipts), weekly commitment line, capacity conflicts, flow metrics, slip forecast, commitments, exec readout |
| | `/dashboard` | Engagements · blockers · capacity · milestones · tasks · Q&A · decisions · standups · calendar · notes |
| | `/insights` | Findings feed with click-through receipts, and team-rolled trends (MTTR, automation ratio, adoption, token spend) |
| **Inbox** | `/review` | Approve or reject proposed changes. This is the agent approval gate |
| | `/intake` | Engagement front door — submit → RICE-lite score → accept/defer/decline → what-if staffing |
| | `/ingest` | Paste meeting notes. A deterministic pass turns them into proposals you batch-approve |
| **Team** | `/agents` | Agents as teammates — mission control, authority matrix, trust scores, agent inboxes |
| | `/people` | Manager layer — 1:1 briefs and the private feedback journal. Needs a personal key, and the records never leave `private.db` |
| | `/charter` | Decisions filtered to the charter category, each with a `review_by` date |
| | `/activity` | The provenance ledger as one sentence per row, hash-chained and tamper-evident |
| — | `/guide` | [Field guide](docs/FIELD-GUIDE.md) — every shipped feature as a card you tie by using it. The "what's new" surface |
| — | `/settings` | Name, theme, API key, growth interests, team roster |
| ⌘K anywhere | — | Quick capture. Freeform text auto-routes to task, question, note, decision, blocker, commitment, request (`req:`) or private feedback (`fb:`) |

## Architecture

```
backend/   FastAPI + Strands Agents + SQLite (WAL, migrations, FTS5)
  ├─ app/services/   ALL business logic — the single write path
  ├─ app/routes/     REST (human writes) + /api/chat SSE (agent writes)
  ├─ app/tools/      55 Strands @tool wrappers over the same services
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
- **A tamper-evident ledger** — each activity row commits to its own content
  and to the row before it (SHA-256, migration 036). A nightly job verifies
  the whole chain and appends the verified tip to an anchor log mirrored
  off-box. A later rewrite must contradict a record made on an earlier day.
  Detection, never prevention — the limits are stated plainly in
  [docs/FEATURES.md](docs/FEATURES.md).
- **Approval gate** — with `SKEIN_AGENT_REVIEW=1`, agent writes become
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
  (`SKEIN_EMBEDDINGS=1` — keyless via `SKEIN_EMBED_PROVIDER=ollama`, or
  openai/openai_compatible), token usage accounting per thread/model.

## Run with Docker (recommended for the team)

```bash
docker compose up --build -d     # backend :8000 + frontend :3000, data in a named volume
docker compose exec backend python seed.py   # optional demo data
docker compose logs -f backend               # watch migrations + scheduler start
# private surfaces (People page, admin export) need a personal API key —
# mint each person's FIRST key out-of-band, then paste via the 🔑 button:
docker compose exec backend python -m app.bootstrap_key <name>
```

If another app already uses port 3000 on the host, pick another frontend
port:

```bash
SKEIN_FRONTEND_PORT=3100 docker compose up --build -d   # UI at :3100
```

The SQLite database, chat sessions, artifacts, and daily backups live in the
`skein-data` volume. Configure via `backend/.env` (picked up automatically;
rebuild not needed for backend env changes — `docker compose up -d` again is
enough. `SKEIN_HOST`/`SKEIN_API_TOKEN` are baked into the frontend bundle
and DO need `--build`).

**Ollama in Docker:** a container's `localhost` is not the host, so compose
overrides `SKEIN_OLLAMA_HOST` to `http://host.docker.internal:11434`
(mapped to the host gateway) — the host's signed-in Ollama daemon, including
`*-cloud` models, works from inside the container. **But default Ollama
installs bind 127.0.0.1 only**, which the gateway can't reach. Either fix the
daemon (`sudo systemctl edit ollama` → `Environment="OLLAMA_HOST=0.0.0.0"`)
or, without root, run the bundled bridge (`ops/ollama-bridge.py`, installable
as a user systemd service) and point at port 11435.

A combined example — frontend port in use AND a loopback-only Ollama daemon
behind the bridge:

```bash
SKEIN_FRONTEND_PORT=3100 \
SKEIN_OLLAMA_HOST=http://host.docker.internal:11435 \
docker compose up --build -d
# UI: http://localhost:3100 · API: http://localhost:8000
```

**Backup mirror in Docker:** the mirror path from `backend/.env` doesn't
exist inside the container; uncomment the `/backup-mirror` volume + env lines
in `docker-compose.yml` to mount your NAS path and re-enable it.

For a ~10-person team this single-box setup is deliberate: SQLite in WAL mode
handles this write volume easily, and one backend container means exactly one
scheduler.

**Security model, stated plainly:** `SKEIN_AUTH_MODE` picks it. The default,
`trusted-header`, is a trusted name picker (`X-User`) — teammates, not
strangers; `SKEIN_API_TOKEN` adds a shared bearer token there, but it is
baked into the frontend's public JS bundle, so anyone who can load the UI can
read it — it keeps out network scanners, not people who can reach port 3000.
`api-key` mode demands a personal `sk-skein-` key on every request. `oidc`
mode validates IdP-issued JWTs in-process (the web UI's sign-in flow is still
to come — until then oidc serves API/automation callers). Admin surfaces
(roster, key visibility, authority, backups, export) are held to
`SKEIN_ADMINS` / an IdP admin group. To expose this beyond a trusted network
today, put both services behind an authenticating reverse proxy (Tailscale,
Caddy + SSO, etc.). Copy `data/backups/` off the box on a schedule (or set
`SKEIN_BACKUP_MIRROR`) — backups otherwise live on the same volume as the
database.

Known-and-accepted within that model (documented so nobody rediscovers them
as surprises): the REST write path does not pass through the agent review
gate or authority matrix — only the tool/MCP paths do — so issue `sk-` keys
to humans, not to agent processes you want gated; the CI webhook
(`/api/webhooks/ci`) inherits only whatever the shared token provides; and
if you register a CI runner with docker access on the host that runs Skein,
keep it push-only — untrusted `pull_request` code must never execute on a
production host. Authority levels can only be set by human identities
(self-service by an agent is refused).

## Optional integrations — built in, off until configured

| Integration | Turns on when | What you get |
|---|---|---|
| Slack outbound | `SLACK_WEBHOOK_URL` | Immediate pings + twice-daily notification digests |
| Slack commands | `SLACK_SIGNING_SECRET` | `/skein …` slash command (capture, briefing, search, plan) with signature verification |
| MCP tools | `SKEIN_MCP_SERVERS` (JSON) | GitHub/Linear/etc. tools attached to the real agent |
| Prebuilt tools | `SKEIN_EXTRA_TOOLS` | Allowlisted [strands-agents-tools](https://github.com/strands-agents/tools) for the real agent (keyless: `calculator,current_time,think,batch,sleep,rss`; key-gated: tavily/exa research tools — full allowlist in `app/agents/extra_tools.py`). Shell/file/exec tools **and** `http_request`/`use_agent`/`workflow` are deliberately not loadable — see `app/agents/extra_tools.py` for the security rationale |
| Semantic search | `SKEIN_EMBEDDINGS=1` + `SKEIN_EMBED_PROVIDER` | openai (key) · openai_compatible (base URL) · ollama (keyless) — vectors tagged per model |
| OpenTelemetry | `SKEIN_OTEL_ENDPOINT` | Agent traces to Jaeger/Langfuse |
| API auth | `SKEIN_AUTH_MODE` | `trusted-header` (default) · `api-key` (a personal key on every request) · `oidc` (IdP tokens validated in-process). Admin surfaces are held to `SKEIN_ADMINS` / `SKEIN_OIDC_ADMIN_GROUP` |
| Shared token | `SKEIN_API_TOKEN` | Perimeter bearer token, `trusted-header` mode only |

Notification tiers (immediate / digest / passive) and cross-thread agent
memory (`/remember`, `remember`/`recall_memories` tools, auto-injected into
the agent's prompt) work keyless, in-app.

## The developer loop

- **Per-teammate API keys** — your FIRST key is minted out-of-band, on the
  box: `python -m app.bootstrap_key <you>` (or
  `docker compose exec backend python -m app.bootstrap_key <you>`). Minting
  via the API requires an *existing* key, deliberately — on X-User identity
  alone any LAN caller could become anyone. Once you hold one, later keys
  come from `curl -X POST $URL/api/keys -H 'Authorization: Bearer sk-skein-…'
  -H 'Content-Type: application/json' -d '{"label":"cli"}'`. Store the
  `sk-skein-…` once — it is never shown again; keys authenticate and
  *attribute* automation, and satisfy the shared token gate.
- **`skein` CLI** (stdlib-only): `pipx install ./cli`, then
  `skein config --url … --key …` and
  `skein capture|standup|my-day|tasks|blockers|search|week|eval|context`.
- **`skein eval`** — replays the capture classifier against its labeled
  feedback corpus (`POST /api/feedback`); exits 1 on regressions.
- **`skein context --write AGENTS.md`** — emits the versioned team context
  pack (decisions, health, lessons, conventions) for any agent to load; also
  an MCP resource (`skein://context-pack`).
- **Git trailers** — `skein install-hooks`; commits with `Closes-Task: #12`
  auto-close the task and log the SHA.
- **CI webhook** — point GitHub Actions (or POST `{repo, branch, status, run_url}`)
  at `/api/webhooks/ci`: a red default-branch build files a deduped high-impact
  blocker; green auto-resolves it.
- **MCP server** — your *other* AI agents join the platform:
  `claude mcp add skein -- env SKEIN_MCP_USER=you-mcp /abs/path/to/backend/.venv/bin/python -m app.mcp_server`
  (a DISTINCT name, never your own — the server reserves it as an agent
  identity, and an agent identity cannot use REST or the private surfaces)
  (needs the local backend install — Docker-only deployments should `uv venv`
  the backend once on the host for MCP use; run `skein install-hooks` inside
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
npm install                                # backend URL defaults to :8000
npm run dev                                # http://localhost:3000

# or both: ./scripts/skein.sh dev      (detached: ./scripts/skein.sh start)
# tests:   cd backend && .venv/bin/pytest
```

Model provider in `backend/.env`:

| Variable | Values | Default |
|---|---|---|
| `SKEIN_MODEL_PROVIDER` | `mock` \| `ollama` \| `openai` \| `openai_compatible` \| `anthropic` \| `bedrock` | `mock` (no keys needed) |
| `SKEIN_MODEL_ID` | any model ID, never allowlisted | per provider (below) |
| `SKEIN_MODEL_BASE_URL` | endpoint for `openai_compatible`; refused on every other provider | — |
| `SKEIN_MODEL_API_KEY` | explicit key, overriding the provider-native one | — |
| `SKEIN_MAX_TOKENS` | output cap — reaches anthropic/ollama/bedrock, **not** the OpenAI family | `4096` |
| `SKEIN_MODEL_PARAMS` | JSON merged into the provider's params (`temperature`, `max_completion_tokens`, …) | — |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | credential for the chosen provider | — |
| `SKEIN_OLLAMA_HOST` | local daemon or `https://ollama.com` | `http://localhost:11434` |
| `OLLAMA_API_KEY` | only for direct Ollama Cloud (no local daemon) | — |
| `SKEIN_AGENT_REVIEW` | `1` routes agent writes through /review | `0` |

Default model per provider: `gpt-oss:120b-cloud` (ollama) · `gpt-5` (openai) ·
`claude-opus-4-8` (anthropic). **`openai_compatible` and `bedrock` have no
default** and require `SKEIN_MODEL_ID` — the OpenAI-shaped server decides what
it serves, and Bedrock's Claude ids need a region-dependent inference-profile
prefix. `bedrock` needs no key; it uses the ambient AWS credential chain.

`SKEIN_MODEL_BASE_URL` is **refused** on any provider but `openai_compatible`,
and `openai_compatible` never falls back to `OPENAI_API_KEY` — set
`SKEIN_MODEL_API_KEY` explicitly. Both rules exist so a leftover endpoint can
never redirect a paid provider's key to a third-party host.

**`openai_compatible` covers anything speaking the OpenAI wire format** — vLLM,
LM Studio, llama.cpp, OpenRouter, Together, Groq, Azure OpenAI, or a LiteLLM
Proxy in front of everything else:

```bash
SKEIN_MODEL_PROVIDER=openai_compatible
SKEIN_MODEL_BASE_URL=http://localhost:8001/v1
SKEIN_MODEL_ID=whatever-the-server-serves
```

**Why `SKEIN_MAX_TOKENS` skips OpenAI:** the SDK passes params straight into
`chat.completions.create`, and reasoning models (including the `gpt-5` default)
reject `max_tokens` in favour of `max_completion_tokens`. Sending it would turn
a working provider into a hard 400, so cap OpenAI output through
`SKEIN_MODEL_PARAMS={"max_completion_tokens": 2000}` instead.

A misconfigured provider never takes the app down: it degrades to the
deterministic core, `/health` reports `provider_error`, and Team → Agents shows
a red dot with the reason.

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

Every phase of [docs/SPEC.md](docs/SPEC.md), plus the synthesized picks from
four ideation rounds, is **built**: the keyless operating system, the
integrations (Slack, MCP both ways, CI, keys, CLI), delight and pulse, the
round-3 operating-system layer (portfolio health, weekly commitment line, flow
metrics, agent delegation and the authority matrix, review analytics and the
eval corpus, decision half-life, commitment ledger, context pack), the manager
layer (private 1:1 notes, meeting ingestion, experiments, finding
dispositions), and the round-4 buzz layer (tamper-evident ledger, activity
feed, turn guard, persona bench, per-turn cost and a budget rule).

| Doc | What it is for |
|---|---|
| [docs/FEATURES.md](docs/FEATURES.md) | What exists. The reference — read this first |
| [docs/ROADMAP.md](docs/ROADMAP.md) | What is next. The only backlog |
| [TODO.md](TODO.md) | Debts taken on purpose, each with the condition that repays it |
| [docs/CORRECTIONS.md](docs/CORRECTIONS.md) | The correction contract every entity must meet |
| [docs/INSIGHTS.md](docs/INSIGHTS.md) | The findings rules and the small-n discipline behind them |
| [docs/FIELD-GUIDE.md](docs/FIELD-GUIDE.md) | The field guide ("knots") and its design constraints |
| [docs/PERSONAS.md](docs/PERSONAS.md) | The Bench — the persona spec |
| [docs/SPEC.md](docs/SPEC.md) | The original phase plan. Superseded, kept for the data model |
| [docs/PLAN.md](docs/PLAN.md) | The 2026-07-24 wave plan, executed. Kept for the recorded deviations |
| [docs/reviews/](docs/reviews/) | Design rationale — the alternatives that lost, and why |

## License

Apache-2.0 — see [LICENSE](LICENSE). [NOTICE](NOTICE) carries the
attribution for the persona definitions adapted from
[agency-agents](https://github.com/msitarzewski/agency-agents) (MIT).
