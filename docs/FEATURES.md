# Skein — feature reference

Everything shipped, grouped by area. Every feature has a deterministic,
keyless core; rows marked **LLM-upgradable** get better with a model provider
but never require one. All writes flow through `backend/app/services/` from
both write paths (human REST, agent tools) and carry provenance
(`origin`, `created_by`) plus an activity-log entry.

**Navigation (since 2026-07-26):** five top-level destinations — My Day ·
Chat · **Work** (Health `/portfolio` · Browse `/dashboard` · Insights
`/insights`) · **Inbox** (Approvals `/review` · Requests `/intake` · Paste
notes `/ingest`) · **Team** (Agents `/agents` · 1:1s `/people` · Charter
`/charter`). All URLs are unchanged; the groups are tabs. Manager-grade
controls (request triage, exec readout, authority editing, commitment
verdicts) sit behind a per-browser "manager controls" toggle so developers
never carry the manager cockpit by default. The toggle is scope control, not
authorization: the endpoints behind it are ordinary `CurrentUser` writes and
stay reachable by any LAN caller — only authority editing requires a personal
key (StrongUser).

## Work tracking

| Feature | How | Surface |
|---|---|---|
| Milestones & tasks | statuses, priorities, due dates, assignees | `GET/POST/PATCH /api/milestones`, `/api/tasks` · dashboard · tools |
| Questions & answers | assignment + notification to assignee | `/api/questions`, `/api/questions/{id}/answer` |
| Decisions with half-life | `review_by` date; daily sweep flips overdue ones to `stale`; supersede chains (`superseded_by`) instead of contradicting; reconfirm to reset; `category=charter` marks team-charter/decision-rights entries (review_by required, category survives supersession). A **risk register** needs no new feature: a risk IS a decision (`risk: <what> — mitigation: <how>`) with an owner and a `review_by`, riding the same stale sweep | `POST /api/decisions` (accepts `review_by`, `category`), `/api/decisions/{id}/supersede`, `/{id}/reconfirm`, `GET /api/decisions?status=&category=` · `/charter` page |
| Waiting-on edges | a task records what it's stuck behind (`waiting_on: task:12 \| blocker:3 \| commitment:7`, `-` clears) — surfaced as health receipts and slip-forecast annotations; satisfied targets (done/resolved/kept) stop counting automatically. Deliberately not Gantt | `PATCH /api/tasks/{id}` · agent tool `update_task` |
| `/ask` with receipts | Q&A over the index: every answer is snippets citing `entity #id` — keyless FTS, degrades honestly | `GET /api/ask?q=` |
| Growth interests | self-declared per person (🌱 on My Day); shown in what-if staffing projections so growth fit is visible when staffing — display-only, no matching, no scores; overwrites logged old→new | `POST /api/users/growth-interests` |
| Standups | blocker text auto-files a real blocker | `POST /api/standups` · CLI `strands standup` |
| Knowledge base | notes; `convention: …` topics feed the context pack | `/api/notes` |
| Calendar | events, today's shown in My Day | `/api/events` |
| Quick capture | rule-based routing: `q:`→question, `todo:`→task, `blocked …`→blocker, `decision:`→decision, `promised:`→commitment, `req:`→intake request, `fb: <person> — …`→author-private feedback journal (strong identity required, single-line only, refused in chat), else note. The ⌘K palette affords the grammar: prefix chips + a live "will file as: …" preview mirroring the backend rules | ⌘K palette · `POST /api/capture` · CLI `strands capture` · Slack `/strands` · MCP `capture` |
| Meeting-notes ingestion | paste raw notes; deterministic per-line capture-grammar pass; every hit becomes a review-queue **proposal** (never a direct write); `fb:` lines flagged and skipped; unclassified lines returned for a human skim; batch-approve in `/review` | `/ingest` page · `POST /api/ingest` |
| Full-text search | FTS5 over every entity; optional embeddings (`STRANDS_EMBEDDINGS=1`) | `GET /api/search?q=` · CLI `skein search` |
| My Day briefing | attention items grouped by judgment type (Decide/Unblock/Promise/Review/Notice), each with a "why you're seeing this" reason; the nav badge counts only Inbox work (pending proposals + requests to triage) so it never promises more than its destination shows | `GET /api/briefing` · `GET /api/attention` · CLI `skein my-day` |
| People (private) | per-report 1:1 prep: deterministic "since last time" brief from team-visible data + author-private notes/feedback journal in a separate `private.db` (0600) that backup/export/FTS/MCP/agents structurally never open; read-time-only feedback-gap nudge; author-only delete with tombstone; author-scoped audit trail (adds/reads/briefs/deletes); strong identity (personal key) required — X-User is 403 | `/people` page · `/api/private/*` |
| Authority half-life | `autonomous`/`notify` grants expire to a findings nudge after 90 days (reconfirm = re-grant); `forbidden`/`review` never expire. "Nothing in Skein is trusted forever — not decisions, not agents" | `POST /api/agents/authority` · `authority_stale` rule |
| Rate caps | per-user sliding window on flood-prone writes: capture 30/min (REST, chat, and MCP), ingest 6/min, key requests 3/min — a DoS-annoyance guard, not a security control | `app/ratelimit.py` |
| ICS calendar feed | events + milestone/commitment due dates; dedicated `STRANDS_ICS_TOKEN` (never the API token); fail-closed when the API is token-locked | `GET /api/calendar.ics` |
| First-run onboarding | checklist computed from real state, personal steps (name, capture, standup, key) separated from team facts (engagement, teammate); dismissible card on My Day (restorable from Settings); anonymous visitors get a single "Who are you?" gate | `GET /api/onboarding` |
| Key self-request | "Request a key" on Settings/1:1s files a team-visible nudge with the exact mint command (idempotent while unread) — the operator ceremony stays, finding the operator doesn't | `POST /api/keys/request` |
| Agent status strip | provider (mock = "deterministic mode"), model, review-gate state in plain words on Team → Agents | `GET /api/agents/status` |
| Derived standup "yesterday" | the My Day composer prefills yesterday from the user's own activity log — asked-for input becomes derived | `GET /api/briefing` (`your_work.standup_suggestion`) |
| Themes | Settings shows Mode + a 4-card theme gallery (Loom/Ledger/Phosphor/High-contrast, live fabric-preview tiles; one click sets pack + signature accent) + a Customize disclosure (accent presets + custom two-hue dials, AA at every hue). Underneath: 3 orthogonal token axes (pack × colorway × appearance); pack surfaces respect a brightness invariant so every accent stays AA on every pack. Tokens use CSS `light-dark()` + OKLCH — needs Chrome 123+/Safari 17.5+/Firefox 120+ (older browsers render unstyled) | Settings · `frontend/lib/theme.ts` |
| Artifacts & digest | handoffs, readouts, digests archived under `data/artifacts/` | `GET /api/artifacts` · `POST /api/digest` · `GET /api/users` |

## Engagements & portfolio

| Feature | How | Surface |
|---|---|---|
| Engagements | the strike-team unit of work; classes (prototype/incident/migration/…); `kind: delivery\|experiment` — experiments carry a timebox + kill criteria, skip the slip forecast, and auto-draft a lesson at close; closing ANY engagement requires an honest conclusion (achieved/partial/missed/invalidated/unmeasured/stopped) — invalidated-on-time is a success, not a slip | `/api/engagements` · dashboard (close… button) · intake "accept as experiment" |
| Playbooks | YAML templates → engagement + milestones + tasks + rituals, lessons from the same class surfaced at kickoff | `backend/playbooks/*.yaml` · `POST /api/playbooks/instantiate` · chat `/plan` |
| Intake triage | submit → RICE-lite score (`reach*impact*confidence/effort`) → accept/defer/decline with a reason; accept creates the engagement; dispositions are terminal | `/intake` page · `/api/intake…` |
| **What-if staffing** | project capacity if a request is accepted ("puts Dana at 130%") | `POST /api/intake/{id}/what-if` · tool `what_if_staffing` |
| Allocations & capacity | percent per person; >100% flagged | `POST /api/engagements/{id}/allocate` · `GET /api/capacity` |
| **Allocation conflicts** | window-aware (>100% across engagements covering today) | `GET /api/portfolio/conflicts` · `/portfolio` |
| **Engagement health** | R/Y/G with receipts: overdue milestones, linked open/escalated blockers, stale WIP, silence | `GET /api/portfolio/health` · `/portfolio` · tool/MCP |
| **Slip forecast** | avg slip from the team's own completed milestones projected onto open ones — labeled heuristic, basis shown | `GET /api/portfolio/forecast` |
| **Flow metrics** | cycle time (created→`completed_at`), weekly throughput, WIP per person, stale-WIP list; Monday nudges to owners | `GET /api/portfolio/flow` · `/portfolio` |
| **Exec readout** | curated markdown projection (health, ships, risks, commitments, flow), saved as an artifact | `POST /api/portfolio/readout` |
| **Commitment ledger** | promises with `audience: external\|team` — external ones feed the exec readout and findings; `team` ones are the manager's own promises to the team, visible so they get kept; open→kept/missed/withdrawn (terminal) | `/api/commitments…` · capture `promised: …` |
| Lessons | retro lessons tagged by class, surfaced at next kickoff | `/api/lessons` |
| Handoff packages | deterministic markdown artifact of everything the incoming roster needs | `POST /api/engagements/{id}/handoff` |

## Weekly operating rhythm

| Feature | How |
|---|---|
| **Commitment line** | `committed_week` (`2026-W31`) on tasks; `/portfolio` shows done/committed and kept-% |
| **Auto-drafted Monday plan** | scheduler drafts per-person top tasks (priority, due date, ≤5 each) and files it as a `weekly_plan` proposal in Inbox → Approvals — approved, not imposed |
| CLI | `strands week` (show) · `strands week draft` · `strands week commit 12 14 …` |
| Daily digest | 07:00 UTC deterministic digest; date-seeded opener suppressed during escalations; `_narrate` hook for LLM polish |
| Blocker escalation | impact-based deadlines (`critical` 2h → `low` 72h), hourly sweep, funerals for 3-day-old blockers at resolution |
| Notification tiers | immediate (in-app + Slack now) / digest (batched twice daily) / passive (activity log only) |
| Team pulse | 6-week seasons, whole-team standup chain, blocker speedruns, season totals — no individual leaderboards (`GET /api/pulse`) |

## Agents as teammates

| Feature | How |
|---|---|
| Chief-of-Staff chat | streaming SSE; freeform text goes to the agent (`mock` = keyless smart-capture; `ollama` free via local daemon or Ollama Cloud; anthropic/openai paid) |
| Slash commands | `/plan`, `/briefing`, `/search`, `/remember`, `/playbooks`, `/personas`, `/help` run deterministically for EVERY provider (`/as` resolves its persona deterministically, then hands off to the agent) (`app/agents/commands.py` — no model call, no tokens); composer autocomplete driven by `GET /api/chat/commands` (type `/h`, ↵ runs, tab completes); unknown `/cmd` gets a did-you-mean instead of a model trip |
| Chat history + folders | Sidebar on /chat: every conversation is saved (provider-agnostic transcript in `chat_threads`/`chat_messages`, written by the chat route — works keyless), auto-titled from the first message, rename/move-to-folder/delete per thread, folders are first-class (create empty via 📁+, drag chats in/out, delete unfiles); switching chats rehydrates the transcript; deleting removes transcript AND model session files; threads are owner-scoped by trusted-LAN identity (not private — fb:-grade content belongs in ⌘K/People, and the route refuses it); transcripts live in platform.db, so daily backups retain deleted chats up to 14/30 days; JSON export excludes chat tables |
| The bench (personas) | 10 curated specialist personas (`backend/personas/*.md`, incl. career-growth: growth-mentor, training-designer) — `/as <persona> <message>` in chat, `/personas` lists them, bench cards on /agents deep-link with a prefilled composer. Same tools + review gate; each persona is its own agent identity (contextvar-threaded), earning separate authority rows and trust scores. Works keyless (mock masthead + deterministic routing). Spec: docs/PERSONAS.md |
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
| Approvals (review queue) | approve/reject with notes; CAS claim prevents double-review; failed applies restore to pending | `/review` page |
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
- Per-engagement packs: `GET /api/context-pack?engagement=<id>` — a scoped
  subset (outcome, experiment frame, milestones, open tasks + waits, linked
  blockers, class lessons, standing decisions) for delegated agents: cheaper
  tokens, less noise, cleaner blast radius. Generated on demand, unversioned.

## Insights & findings

| Feature | How |
|---|---|
| Findings engine (`GET /api/findings`, `POST /api/findings/run`, `GET /api/insights`) | deterministic rules — 16 distinct rule IDs, spec in docs/INSIGHTS.md over blockers, WIP, commitments, review queue, intake, questions, decisions, tokens, scheduled jobs, experiments, authority grants; receipts (row IDs + numbers) stored at fire time; dedupe = one fire per (rule, subject, ISO week); daily run 06:50 UTC + startup catch-up; max 3 in the digest, severity-ordered — silence is a valid output |
| Finding dispositions | dismiss / defer / convert-to-work / resolved (`POST /api/findings/{id}/disposition`, `/convert`); suppression keyed (rule_id, subject) so dismissals survive the weekly re-fire; converted work carries `source_finding_id`; dispositioned findings leave the digest; per-rule follow-through stats on /insights |
| `/insights` page | findings feed (receipts on click, disposition buttons) + team-rolled trends: rolling-28d blocker MTTR (median/P85, n shown, verdicts withheld under n=8), automation ratio by month (co-presented with review verdicts), intake funnel, weekly token spend, adoption, rule follow-through |
| Adoption telemetry | `tool_usage` (day × user × surface), `GET /api/adoption`; measures the tool's reach, never people's output |
| Anti-surveillance rule | person-level data only for planning the future (capacity, private nudges, My Day); team aggregates only for judging the past — enforced in the service layer, no person-keyed insight endpoints exist |
| Findings feedback | `POST /api/feedback` with `kind=finding` — the eval corpus extends to the findings engine; rules nobody acts on get retired at season end. Rule follow-through stats include median days-to-disposition |
| Weekly pulse | Monday digest asks the one question telemetry can't: "did Skein reduce coordination effort?" — 👍/👎 on My Day (`POST /api/feedback kind=pulse`); tallied per week as team counts only, never per person |
| Golden-trace evals | scenario suite (`tests/test_golden_traces.py`) pins tool-call sequences → expected DB state through the REAL agent tool layer + gate: playbook planning, capture routing, review-mode queuing, forbidden refusal, waiting-on, and the propose→approve→agent_verified roundtrip. Keyless, runs in CI |

## Developer loop

| Feature | How |
|---|---|
| Per-teammate API keys | first key per person via `python -m app.bootstrap_key <name>` (out-of-band — minting via `POST /api/keys` requires an existing key, or any LAN caller could become anyone); `sk-strands-…` hashed, shown once; authenticates *and* attributes; the STRONG identity for private surfaces; presented-but-revoked keys hard-401. Admin: `GET /api/admin/keys`, `POST /api/admin/keys/revoke-all` (strong-identity-only) |
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
| Scheduler (UTC) | one `JOBS` registry (`services/jobs.py`) drives cron + startup catch-ups: hourly blocker sweep · 03:00 backup · 05:00 context pack · 05:15 forecast snapshot · 06:00 Mon weekly plan · 06:15 Mon stale-WIP nudge · 06:30 stale decisions · 06:50 findings · 07:00 digest · 07:05/15:05 notification flush · monthly retention prune (1st, 04:00) — all idempotent via `claim_job` CAS or status flips; every run records status/duration to `job_outcomes`; `/health` reports last success + stale flag per job |
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
