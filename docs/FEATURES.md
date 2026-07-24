# Skein — feature reference

Everything shipped, grouped by area. Every feature has a deterministic,
keyless core; rows marked **LLM-upgradable** get better with a model provider
but never require one. All writes flow through `backend/app/services/` from
both write paths (human REST, agent tools) and carry provenance
(`origin`, `created_by`) plus an activity-log entry.

## Work tracking

| Feature | How | Surface |
|---|---|---|
| Milestones & tasks | statuses, priorities, due dates, assignees | `GET/POST/PATCH /api/milestones`, `/api/tasks` · dashboard · tools |
| Questions & answers | assignment + notification to assignee | `/api/questions`, `/api/questions/{id}/answer` |
| Decisions with half-life | `review_by` date; daily sweep flips overdue ones to `stale`; supersede chains (`superseded_by`) instead of contradicting; reconfirm to reset | `POST /api/decisions` (accepts `review_by`), `/api/decisions/{id}/supersede`, `/{id}/reconfirm`, `GET /api/decisions?status=` |
| Standups | blocker text auto-files a real blocker | `POST /api/standups` · CLI `strands standup` |
| Knowledge base | notes; `convention: …` topics feed the context pack | `/api/notes` |
| Calendar | events, today's shown in My Day | `/api/events` |
| Quick capture | rule-based routing: `q:`→question, `todo:`→task, `blocked …`→blocker, `decision:`→decision, `promised:`→commitment, else note | ⌘K palette · `POST /api/capture` · CLI `strands capture` · Slack `/strands` · MCP `capture` |
| Full-text search | FTS5 over every entity; optional embeddings (`STRANDS_EMBEDDINGS=1`) | `GET /api/search?q=` · CLI `skein search` |
| My Day briefing | what needs *you* + your work + team happenings; attention count feeds the nav badge | `GET /api/briefing` · `GET /api/attention` · CLI `skein my-day` |
| First-run onboarding | checklist computed from real state (name, engagement, capture, standup, teammate, key); dismissible card on My Day | `GET /api/onboarding` |
| Artifacts & digest | handoffs, readouts, digests archived under `data/artifacts/` | `GET /api/artifacts` · `POST /api/digest` · `GET /api/users` |

## Engagements & portfolio

| Feature | How | Surface |
|---|---|---|
| Engagements | the strike-team unit of work; classes (prototype/incident/migration/…) | `/api/engagements` · dashboard |
| Playbooks | YAML templates → engagement + milestones + tasks + rituals, lessons from the same class surfaced at kickoff | `backend/playbooks/*.yaml` · `POST /api/playbooks/instantiate` · chat `/plan` |
| Intake triage | submit → RICE-lite score (`reach*impact*confidence/effort`) → accept/defer/decline with a reason; accept creates the engagement; dispositions are terminal | `/intake` page · `/api/intake…` |
| **What-if staffing** | project capacity if a request is accepted ("puts Dana at 130%") | `POST /api/intake/{id}/what-if` · tool `what_if_staffing` |
| Allocations & capacity | percent per person; >100% flagged | `POST /api/engagements/{id}/allocate` · `GET /api/capacity` |
| **Allocation conflicts** | window-aware (>100% across engagements covering today) | `GET /api/portfolio/conflicts` · `/portfolio` |
| **Engagement health** | R/Y/G with receipts: overdue milestones, linked open/escalated blockers, stale WIP, silence | `GET /api/portfolio/health` · `/portfolio` · tool/MCP |
| **Slip forecast** | avg slip from the team's own completed milestones projected onto open ones — labeled heuristic, basis shown | `GET /api/portfolio/forecast` |
| **Flow metrics** | cycle time (created→`completed_at`), weekly throughput, WIP per person, stale-WIP list; Monday nudges to owners | `GET /api/portfolio/flow` · `/portfolio` |
| **Exec readout** | curated markdown projection (health, ships, risks, commitments, flow), saved as an artifact | `POST /api/portfolio/readout` |
| **Commitment ledger** | promises to people outside the team; open→kept/missed/withdrawn (terminal); due-soon ones appear in the readout | `/api/commitments…` · capture `promised: …` |
| Lessons | retro lessons tagged by class, surfaced at next kickoff | `/api/lessons` |
| Handoff packages | deterministic markdown artifact of everything the incoming roster needs | `POST /api/engagements/{id}/handoff` |

## Weekly operating rhythm

| Feature | How |
|---|---|
| **Commitment line** | `committed_week` (`2026-W31`) on tasks; `/portfolio` shows done/committed and kept-% |
| **Auto-drafted Monday plan** | scheduler drafts per-person top tasks (priority, due date, ≤5 each) and files it as a `weekly_plan` proposal in the review inbox — approved, not imposed |
| CLI | `strands week` (show) · `strands week draft` · `strands week commit 12 14 …` |
| Daily digest | 07:00 UTC deterministic digest; date-seeded opener suppressed during escalations; `_narrate` hook for LLM polish |
| Blocker escalation | impact-based deadlines (`critical` 2h → `low` 72h), hourly sweep, funerals for 3-day-old blockers at resolution |
| Notification tiers | immediate (in-app + Slack now) / digest (batched twice daily) / passive (activity log only) |
| Team pulse | 6-week seasons, whole-team standup chain, blocker speedruns, season totals — no individual leaderboards (`GET /api/pulse`) |

## Agents as teammates

| Feature | How |
|---|---|
| Chief-of-Staff chat | streaming SSE; `STRANDS_MODEL_PROVIDER=mock` gives a command-driven agent (`/plan`, `/briefing`, `/search`, `/remember`, `/playbooks`, `/help`) keyless; `ollama` gives a real model for free (local daemon or Ollama Cloud via signed-in daemon / `OLLAMA_API_KEY`); anthropic/openai are the paid tiers |
| Agent write path | 40+ Strands `@tool` wrappers over the same services humans use (plus planner, memory, and opt-in extra tools on the chat agent) |
| Review gate | `STRANDS_AGENT_REVIEW=1` routes mutating agent tools through `pending_changes`; approval applies via the service registry as `origin=agent_verified` |
| **Authority matrix** | per (agent, entity): `autonomous` (direct), `notify` (direct + team ping), `review` (default — proposal), `forbidden` (refused). Enforced in the shared tool gate (chat AND MCP); only humans can set levels | `GET/POST /api/agents/authority` · `GET /api/agents/entities` · `/agents` page |
| **Delegation** | tasks get `delegated_agent` + a human `sponsor` (required); sponsor is notified | `POST /api/tasks/{id}/delegate` · tool `delegate_task` |
| **Mission control** | per-agent open tasks, pending proposals, last-seen, authority chips | `GET /api/agents` · `/agents` page |
| **Agent inbox** | ambient wake-up view: delegated tasks, assigned questions, rejected proposals *with reviewer notes*, unread notifications | `GET /api/agents/{agent}/inbox` · MCP `my_inbox` |
| **Trust scores** | approval rate + recent streak per (proposer, entity) from review verdicts; ≥5-streak suggests promotion — a human still flips the switch | `GET /api/agents/trust` |
| Agent memory | cross-thread remember/recall, injected into the agent prompt | `/api/memories` · tools · MCP `remember` |

## Review flywheel & evals

| Feature | How |
|---|---|
| Review inbox | approve/reject with notes; CAS claim prevents double-review; failed applies restore to pending | `/review` page |
| **Review analytics** | approve/reject/pending + avg review latency per entity and per proposer; recent rejection reasons | `GET /api/review/stats` |
| **Eval corpus** | `POST /api/feedback` records thumbs/corrections on chat, capture, proposals | `kind=chat\|capture\|proposal`, `verdict=up\|down\|corrected` |
| **`strands eval`** | replays the capture classifier against its labeled corpus; exit 1 on regressions — run before changing the rules (or a prompt) | CLI · `GET /api/eval/capture` |

## Context pack (org-brain)

Versioned markdown pack: team roster, engagement health, standing decisions
(with stale warnings), paid-for lessons, conventions, open questions, and how
to plug in. Version bumps only when content changes (hash-deduped); refreshed
daily at 05:00 UTC and written to `data/artifacts/context-pack/`.

- `GET /api/context-pack` · `POST /api/context-pack/publish`
- MCP resource `strands://context-pack` + tool `get_context_pack`
- CLI: `strands context` or `strands context --write AGENTS.md` — Skein
  becomes the context supplier to every other agent the team uses.

## Insights & findings

| Feature | How |
|---|---|
| Findings engine (`GET /api/findings`, `POST /api/findings/run`, `GET /api/insights`) | 12 deterministic rules (docs/INSIGHTS.md) over blockers, WIP, commitments, review queue, intake, questions, decisions, tokens; receipts (row IDs + numbers) stored at fire time; dedupe = one fire per (rule, subject, ISO week); daily run 06:50 UTC + startup catch-up; max 3 in the digest, severity-ordered — silence is a valid output |
| `/insights` page | findings feed (receipts on click) + team-rolled trends: rolling-28d blocker MTTR (median/P85, n shown, verdicts withheld under n=8), automation ratio by month (co-presented with review verdicts), intake funnel, weekly token spend, adoption |
| Adoption telemetry | `tool_usage` (day × user × surface), `GET /api/adoption`; measures the tool's reach, never people's output |
| Anti-surveillance rule | person-level data only for planning the future (capacity, private nudges, My Day); team aggregates only for judging the past — enforced in the service layer, no person-keyed insight endpoints exist |
| Findings feedback | `POST /api/feedback` with `kind=finding` — the eval corpus extends to the findings engine; rules nobody acts on get retired at season end |

## Developer loop

| Feature | How |
|---|---|
| Per-teammate API keys | `POST /api/keys` → `sk-strands-…` (hashed, shown once); authenticates *and* attributes; satisfies the shared token gate; presented-but-revoked keys hard-401. Admin: `GET /api/admin/keys`, `POST /api/admin/keys/revoke-all` |
| `strands` CLI | stdlib-only; `pipx install ./cli`; config at `~/.config/strands/config.json` (0600, key prompted) |
| Git trailers | `strands install-hooks`; `Closes-Task: #12` / `Refs-Task: #7` sync on commit |
| CI webhook | `POST /api/webhooks/ci` (generic or GitHub Actions payload): red default-branch run files a deduped high-impact blocker; green auto-resolves; cancelled/skipped ignored |
| MCP server | `claude mcp add strands -- env STRANDS_MCP_USER=you backend/.venv/bin/python -m app.mcp_server` — 13 tools + the context-pack resource against the same DB |
| Slack | outbound webhook (`SLACK_WEBHOOK_URL`) + `/strands` slash command (`SLACK_SIGNING_SECRET`, HMAC-verified) |
| Prebuilt agent tools | `STRANDS_EXTRA_TOOLS=calculator,current_time,think,batch` loads allowlisted [strands-agents-tools](https://github.com/strands-agents/tools) into the real agent; research tools (`tavily_search`, `exa_search`) activate with their own keys; shell/file/exec tools, `http_request` (a third write path around the review gate), `use_agent`/`use_llm` (model-chosen provider endpoints), and `workflow`/`diagram` (path traversal/subprocess) are refused by the allowlist — rationale in `app/agents/extra_tools.py` |
| OpenTelemetry | `STRANDS_OTEL_ENDPOINT` traces the agent loop |

## Operations

| Feature | How |
|---|---|
| Migrations | append-only numbered SQL, per-migration `BEGIN IMMEDIATE` transactions, `schema_version` |
| Scheduler (UTC) | hourly blocker sweep · 03:00 backup · 05:00 context pack · 05:15 forecast snapshot · 06:00 Mon weekly plan · 06:15 Mon stale-WIP nudge · 06:30 stale decisions · 06:50 findings · 07:00 digest · 07:05/15:05 notification flush — all idempotent via `claim_job` CAS or status flips |
| Backups & export | atomic daily SQLite backups (keep 14) + JSON export, `STRANDS_BACKUP_DIR` to relocate, `STRANDS_BACKUP_MIRROR` copies each backup off-box (keep 30) — this deploy mirrors to the NAS mount |
| CI | Gitea Actions (`.gitea/workflows/ci.yml`): pytest + frontend build per push; runner `the runner host` registered repo-level, runs as the `act-runner` systemd user service (docker mode, linger enabled) |
| Docker | multi-stage frontend image, slim non-root backend image, healthchecks, single `STRANDS_HOST` knob, named `strands-data` volume |
| Auth model | trusted `X-User` name picker (LAN model); optional `STRANDS_API_TOKEN` shared bearer; per-user keys for automation. Put a real proxy in front to leave the trusted network |
| Usage accounting | tokens/cycles/latency per thread and model when a real provider is on (`GET /api/usage`) |

## Environment variables

See `backend/.env.example`. Highlights: `STRANDS_MODEL_PROVIDER`
(`mock`/`anthropic`/`openai`/`ollama`), `STRANDS_MODEL_ID`, `STRANDS_AGENT_REVIEW`,
`STRANDS_SCHEDULER`, `STRANDS_EMBEDDINGS`, `STRANDS_API_TOKEN`,
`SLACK_WEBHOOK_URL`, `SLACK_SIGNING_SECRET`, `STRANDS_MCP_SERVERS`,
`STRANDS_OTEL_ENDPOINT`, `STRANDS_BACKUP_DIR`, `STRANDS_MCP_USER`.
