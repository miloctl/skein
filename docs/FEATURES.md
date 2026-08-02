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
| `/ask` with receipts | Q&A over the index: every answer is snippets citing `entity #id` — keyless FTS, hybrid with semantic hits when embeddings are on, degrades honestly | `GET /api/ask?q=` |
| Growth interests | self-declared per person (🌱 on My Day); shown in what-if staffing projections so growth fit is visible when staffing — display-only, no matching, no scores; overwrites logged old→new | `GET/POST /api/users/growth-interests` (prefilled; empty save clears) |
| Standups | blocker text auto-files a real blocker | `POST /api/standups` · CLI `skein standup` |
| Knowledge base | notes; `convention: …` topics feed the context pack | `/api/notes` |
| Calendar | events, today's shown in My Day | `/api/events` |
| Quick capture | rule-based routing: `q:`→question, `todo:`→task, `blocked …`→blocker, `decision:`→decision, `promised:`→commitment, `req:`→intake request, `fb: <person> — …`→author-private feedback journal (strong identity required, single-line only, refused in chat), else note. The ⌘K palette affords the grammar: prefix chips + a live "will file as: …" preview mirroring the backend rules | ⌘K palette · `POST /api/capture` · CLI `skein capture` · Slack `/skein` · MCP `capture` |
| Meeting-notes ingestion | paste raw notes; deterministic per-line capture-grammar pass; every hit becomes a review-queue **proposal** (never a direct write); `fb:` lines flagged and skipped; unclassified lines returned for a human skim; batch-approve in `/review` | `/ingest` page · `POST /api/ingest` |
| Full-text search | FTS5 over every entity; optional embeddings (`SKEIN_EMBEDDINGS=1`; provider via `SKEIN_EMBED_PROVIDER` — openai, openai_compatible, or keyless ollama; backfill existing records with `python -m app.backfill_embeddings`) | `GET /api/search?q=` · CLI `skein search` |
| My Day briefing | attention items grouped by judgment type (Decide/Unblock/Promise/Review/Notice), each with a "why you're seeing this" reason; the nav badge counts only Inbox work (pending proposals + requests to triage) so it never promises more than its destination shows | `GET /api/briefing` · `GET /api/attention` · CLI `skein my-day` |
| People (private) | per-report 1:1 prep: deterministic "since last time" brief from team-visible data + author-private notes/feedback journal in a separate `private.db` (0600) that backup/export/FTS/MCP/agents structurally never open; read-time-only feedback-gap nudge; author-only delete with tombstone; author-scoped audit trail (adds/reads/briefs/deletes); strong identity (personal key) required — X-User is 403 | `/people` page · `/api/private/*` |
| Authority half-life | `autonomous`/`notify` grants expire to a findings nudge after 90 days (reconfirm = re-grant); `forbidden`/`review` never expire. "Nothing in Skein is trusted forever — not decisions, not agents" | `POST /api/agents/authority` · `authority_stale` rule |
| Rate caps | per-user sliding window on flood-prone writes: capture 30/min (REST, chat, and MCP), ingest 6/min, key requests 3/min — a DoS-annoyance guard, not a security control | `app/ratelimit.py` |
| ICS calendar feed | events + milestone/commitment due dates; dedicated `SKEIN_ICS_TOKEN` (never the API token); fail-closed when the API is token-locked | `GET /api/calendar.ics` |
| First-run onboarding | checklist computed from real state, personal steps (name, capture, standup, key) separated from team facts (engagement, teammate); dismissible card on My Day (restorable from Settings); anonymous visitors get a single "Who are you?" gate | `GET /api/onboarding` |
| Field guide ("knots") | first-use feature discovery: 27 cards from `backend/fieldguide/knots.yaml`, tied/untied per person, untied cards carry the how-to + deep link; state is SELF-visible only (anti-surveillance rule — no per-person grid, no unlock feed, no activity rows); silent retroactive seeding from existing data; one weekly rotating My Day suggestion (dismiss = permanent); builders' signal is the `feature_unadopted` findings rule (zero-adoption, nameless). Spec + non-negotiables: docs/FIELD-GUIDE.md | `/guide` page · 👤 identity menu (top bar: Settings · Field guide + tied count · identity-strength line — replaces the separate gear button; renaming lives in Settings only) · `GET /api/field-guide` · `/hint` · `POST /api/field-guide/dismiss` (rate-capped) |
| Key self-request | "Request a key" on Settings/1:1s files a team-visible nudge with the exact mint command (idempotent while unread) — the operator ceremony stays, finding the operator doesn't | `POST /api/keys/request` |
| Agent status strip | three states in plain words on Team → Agents: live model, mock ("deterministic mode"), or misconfigured (red dot + the reason from `provider_error`) — plus review-gate state | `GET /api/agents/status` |
| Derived standup "yesterday" | the My Day composer prefills yesterday from the user's own activity log — asked-for input becomes derived | `GET /api/briefing` (`your_work.standup_suggestion`) |
| Themes | 7-pack theme gallery (Loom/Ledger/Phosphor/Atelier/Claw/Hermes/High-contrast) — packs re-weave type, radii, shadows, textures, selvage, and empty-state voice, not just colors; colorway accents + custom two-hue dials (AA at every hue, checker-enforced); theme (incl. custom hues) auto-saves to the profile and follows the person to any browser; shareable theme code (copy/paste JSON under Customize & share); operator-set team default adopted by fresh browsers and anonymous visitors (StrongUser, a default never an override) | Settings · `frontend/lib/theme.ts` · `GET/POST /api/users/theme` · `POST /api/users/theme/default` |
| Availability ledger | PTO/on-call/focus windows (absences table): capacity marks people away, the weekly draft skips anyone away 3+ weekdays (listed as skipped, never silently), what-if staffing shows upcoming PTO; manage on Work → Browse Time-away card | `GET/POST/DELETE /api/absences` · tools `add_absence`/`list_absences` (agent create is ALWAYS a proposal) · CLI `skein absences add/list/rm` |
| Delegation work loop | claim → report_progress (worklog) → submit_for_acceptance: acceptance is ALWAYS a proposal bound to the sponsor (others need a reason on record; only key-authenticated sponsor verdicts feed trust); worklog readable pre-verdict | tools `claim_delegated_task`/`report_progress`/`submit_for_acceptance` · `GET /api/tasks/{id}/worklog` · CLI `skein worklog`/`skein inbox` |
| Authority review job | weekly scan turns strong-verdict streaks into FILED proposals: 5 straight key-authenticated sponsor/reviewer approvals at review → propose notify; 3 straight rejections likewise → propose demotion to review (weak-header and override verdicts never count); agents can never approve, so no self-promotion | job `authority-review` · registry entity `authority` |
| Week rituals | Monday brief (each person's own promises, stale decisions, questions, due tasks → personal notification + artifact) and Friday close-out (due promises, stuck-closing engagements, stale proposals, open questions → team notification + artifact); scheduler-run weekly, manual buttons under manager controls on Work → Health | jobs `week-open`/`week-close` · `POST /api/rituals/week-open|week-close` |
| Artifacts & digest | handoffs, readouts, digests archived under `data/artifacts/` | `GET /api/artifacts` · `POST /api/digest` |

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
| **Commitment ledger** | promises with `audience: external\|team` — external ones feed the exec readout and findings; `team` ones are the manager's own promises to the team, visible so they get kept; open→kept/missed/withdrawn (terminal) | `/api/commitments…` · capture `promised: …` · CLI `skein commitments [settle]` |
| Lessons | retro lessons tagged by class, surfaced at next kickoff | `/api/lessons` |
| Handoff packages | deterministic markdown artifact of everything the incoming roster needs | `POST /api/engagements/{id}/handoff` |

## Weekly operating rhythm

| Feature | How |
|---|---|
| **Commitment line** | `committed_week` (`2026-W31`) on tasks; `/portfolio` shows done/committed and kept-% |
| **Auto-drafted Monday plan** | scheduler drafts per-person top tasks (priority, due date, ≤5 each) and files it as a `weekly_plan` proposal in Inbox → Approvals — approved, not imposed |
| CLI | `skein week` (show) · `skein week draft` · `skein week commit 12 14 …` |
| Daily digest | 07:00 UTC deterministic digest; date-seeded opener suppressed during escalations; `_narrate` hook for LLM polish |
| Blocker escalation | impact-based deadlines (`critical` 2h → `low` 72h), hourly sweep, funerals for 3-day-old blockers at resolution |
| Notification tiers | immediate (in-app + Slack now) / digest (batched twice daily) / passive (activity log only) |
| Team pulse | 6-week seasons, whole-team standup chain, blocker speedruns, season totals — no individual leaderboards (`GET /api/pulse`) |

## Agents as teammates

| Feature | How |
|---|---|
| Chief-of-Staff chat | streaming SSE; freeform text goes to the agent (`mock` = keyless smart-capture; `ollama` free via local daemon or Ollama Cloud; `openai_compatible` for any OpenAI-shaped endpoint — vLLM, LM Studio, OpenRouter, Groq, Azure; `bedrock` via the ambient AWS chain; anthropic/openai paid) |
| Slash commands | `/plan`, `/briefing`, `/search`, `/remember`, `/playbooks`, `/personas`, `/help` run deterministically for EVERY provider (`/as` resolves its persona deterministically, then hands off to the agent) (`app/agents/commands.py` — no model call, no tokens; on live providers the exchange is bridged into the thread's model session via `app/agents/session_log.py` so agent follow-ups have the context); composer autocomplete driven by `GET /api/chat/commands` (type `/h`, ↵ runs, tab completes); unknown `/cmd` gets a did-you-mean instead of a model trip |
| **Long-chat memory** | selectable per deployment: `SKEIN_CONTEXT_STRATEGY=sliding` (default — drop the oldest messages, free) or `summarize` (condense them, costs one extra model call when it fires). `SKEIN_CONTEXT_PIN_FIRST=N` asks the SDK to hold the opening N messages — **but only within a turn.** Skein's chats are file-backed sessions, and session restore replays from `offset=removed_message_count`, which skips exactly those leading messages; the pin does not survive a turn boundary. Treat the knob as inert until that changes. Nothing currently keeps the top of a long chat alive across turns. `SKEIN_CONTEXT_PROACTIVE=1` compresses before an overflow instead of after — the threshold is 70% of the MODEL's token context window, not of `SKEIN_CONTEXT_WINDOW`, and Skein sets no `context_window_limit`, so the SDK assumes 200k tokens and the knob does nothing on a smaller local model. **Changing the strategy rewrites each thread's stored manager state on its next turn** (Strands validates the manager class name on restore and would otherwise fail every open thread with `Invalid conversation manager state.`). The replay offset is carried, aligned back to a user turn, so the restored history stays the size the outgoing manager held and never begins with an assistant message. Leaving `summarize` drops the summary text; the messages it stood for stay out of the replay. That loss is deliberate — resetting the offset instead replays a whole thread into the next model call, which overflows, and the recovery overflows again, failing several turns in a row. **The summarizer's own tokens are NOT in `usage_log`**: the SDK calls the model outside the agent's metrics, so `/api/usage` under-reports a summarize deployment. The summarizer runs with no tools and its own hardened prompt, because its output is re-inserted as a `user` message and persisted — the one place pasted third-party text can be laundered into something resembling a standing instruction. Real providers only: mock builds no Strands agent, so the strip shows no strategy rather than claiming one. A bad value degrades to sliding and surfaces `context_error` — the same discipline as `MODEL_PROVIDER_ERROR`, never a failed import. **Settings → Long chats (team)** switches it without a redeploy (StrongUser — it changes what every chat costs); the stored choice overrides the env value, clearing it returns to the env value, and a stored value that stops being valid falls back rather than selecting something else | `_conversation_manager()` · `services/settings.py` · `GET/POST /api/settings/context-strategy` · `GET /api/agents/status` · `/health` |
| **Turn guard** | a chat turn that answers a capture-prefixed message (`todo:`, `q:`, `decision:`, `promised:`, `blocked:`, `req:`) and writes NOTHING says so, as a receipt in the same stream: "Nothing was filed". Prefix match only — the content heuristics in `capture.PATTERNS` treat any message ending in "?" as a question, and a guard that nags on every question mark gets ignored. Any receipt at all (including `refused`/`failed`) silences it: those already stated the truth, and only total silence is the gap. Keyless and model-free. `SKEIN_TURN_GUARD=1` additionally spends ONE model round trip giving the agent a chance to file it (off by default, never on mock, budget of exactly one) | `app/agents/turn_guard.py` |
| Chat history + folders | Sidebar on /chat: every conversation is saved (provider-agnostic transcript in `chat_threads`/`chat_messages`, written by the chat route — works keyless), auto-titled from the first message, rename/move-to-folder/delete per thread, folders are first-class (create empty via 📁+, drag chats in/out, delete unfiles); switching chats rehydrates the transcript; deleting removes transcript AND model session files; threads are owner-scoped by trusted-LAN identity (not private — fb:-grade content belongs in ⌘K/People, and the route refuses it); transcripts live in platform.db, so daily backups retain deleted chats up to 14/30 days; JSON export excludes chat tables |
| The bench (personas) | 10 curated specialist personas (`backend/personas/*.md`, incl. career-growth: growth-mentor, training-designer) — `/as <persona> <message>` in chat, `/personas` lists them, bench cards on /agents deep-link with a prefilled composer. Same tools + review gate; each persona is its own agent identity (contextvar-threaded), earning separate authority rows and trust scores. Works keyless (mock masthead + deterministic routing). Spec: docs/PERSONAS.md |
| Agent write path | 40+ Strands `@tool` wrappers over the same services humans use (plus planner, memory, and opt-in extra tools on the chat agent) |
| Review gate | `SKEIN_AGENT_REVIEW=1` routes mutating agent tools through `pending_changes`; approval applies via the service registry as `origin=agent_verified` |
| **Authority matrix** | per (agent, entity): `autonomous` (direct), `notify` (direct + team ping), `review` (default — proposal), `forbidden` (refused). Enforced in the shared tool gate (chat AND MCP); only humans can set levels | `GET/POST /api/agents/authority` · `GET /api/agents/entities` · `/agents` page |
| **Delegation** | tasks get `delegated_agent` + a human `sponsor` (required); sponsor is notified | `POST /api/tasks/{id}/delegate` · tool `delegate_task` |
| **Mission control** | per-agent open tasks, pending proposals, last-seen, authority chips | `GET /api/agents` · `/agents` page |
| **Agent inbox** | ambient wake-up view: delegated tasks, assigned questions, rejected proposals *with reviewer notes*, unread notifications | `GET /api/agents/{agent}/inbox` · MCP `my_inbox` |
| **Trust scores** | approval rate + recent streak per (proposer, entity) from review verdicts; ≥5-streak (key-authenticated, non-override verdicts only) suggests promotion — a human still flips the switch | `GET /api/agents/trust` |
| Agent memory | cross-thread remember/recall, injected into the agent prompt | `/api/memories` · tools · MCP `remember` |

## Review flywheel & evals

| Feature | How |
|---|---|
| Approvals (review queue) | approve/reject with notes; CAS claim prevents double-review; failed applies restore to pending (vanished targets auto-reject) | `/review` page · CLI `skein review [approve|reject]` |
| **Review analytics** | approve/reject/pending + avg review latency per entity and per proposer; recent rejection reasons | `GET /api/review/stats` |
| **Eval corpus** | `POST /api/feedback` records thumbs/corrections on chat, capture, proposals | `kind=chat\|capture\|proposal`, `verdict=up\|down\|corrected` |
| **`skein eval`** | replays the capture classifier against its labeled corpus; exit 1 on regressions — run before changing the rules (or a prompt) | CLI · `GET /api/eval/capture` |

## Context pack (org-brain)

Versioned markdown pack: team roster, engagement health, standing decisions
(with stale warnings), paid-for lessons, conventions, open questions, and how
to plug in. Version bumps only when content changes (hash-deduped); refreshed
daily at 05:00 UTC and written to `data/artifacts/context-pack/`.

- `GET /api/context-pack` · `POST /api/context-pack/publish`
- MCP resource `skein://context-pack` + tool `get_context_pack`
- CLI: `skein context` or `skein context --write AGENTS.md` — Skein
  becomes the context supplier to every other agent the team uses.
- Per-engagement packs: `GET /api/context-pack?engagement=<id>` — a scoped
  subset (outcome, experiment frame, milestones, open tasks + waits, linked
  blockers, class lessons, standing decisions) for delegated agents: cheaper
  tokens, less noise, cleaner blast radius. Generated on demand, unversioned.

## Insights & findings

| Feature | How |
|---|---|
| Findings engine (`GET /api/findings`, `POST /api/findings/run`, `GET /api/insights`) | deterministic rules — 18 distinct rule IDs, spec in docs/INSIGHTS.md over blockers, WIP, commitments, review queue, intake, questions, decisions, tokens, scheduled jobs, experiments, authority grants, ledger integrity; receipts (row IDs + numbers) stored at fire time; dedupe = one fire per (rule, subject, ISO week); daily run 06:50 UTC + startup catch-up; max 3 in the digest, severity-ordered — silence is a valid output |
| Finding dispositions | dismiss / defer / convert-to-work / resolved (`POST /api/findings/{id}/disposition`, `/convert`); suppression keyed (rule_id, subject) so dismissals survive the weekly re-fire; converted work carries `source_finding_id`; dispositioned findings leave the digest; per-rule follow-through stats on /insights |
| `/insights` page | findings feed (receipts on click, disposition buttons) + team-rolled trends: rolling-28d blocker MTTR (median/P85, n shown, verdicts withheld under n=8), automation ratio by month (co-presented with review verdicts), intake funnel, weekly token spend, adoption, rule follow-through |
| Adoption telemetry | `tool_usage` (day × user × surface), `GET /api/adoption`; measures the tool's reach, never people's output |
| Anti-surveillance rule | person-level data only for planning the future (capacity, private nudges, My Day); team aggregates only for judging the past — enforced in the service layer, no person-keyed insight endpoints exist |
| Findings feedback | `POST /api/feedback` with `kind=finding` — the eval corpus extends to the findings engine; rules nobody acts on get retired at season end. Rule follow-through stats include median days-to-disposition |
| Weekly pulse | Monday digest asks the one question telemetry can't: "did Skein reduce coordination effort?" — 👍/👎 on My Day (`POST /api/feedback kind=pulse`); tallied per week as team counts only, never per person |
| Golden-trace evals | scenario suite (`tests/test_golden_traces.py`) pins tool-call sequences → expected DB state through the REAL agent tool layer + gate: playbook planning, capture routing, review-mode queuing, forbidden refusal, waiting-on, and the propose→approve→agent_verified roundtrip. Keyless, runs in CI |
| **Gate coverage assertion** | "provenance on every write" proved, not sampled: `tests/test_gate_coverage.py` instruments the DB seam, invokes EVERY tool in the registry (enumerated from `ALL_TOOLS`, never a hand list — a new tool is covered by default and a tool the arg heuristics cannot call fails the suite until it gets an args entry), and fails any call that mutated the database without leaving a receipt. Receipt kinds are asserted as literal strings so a gate bug cannot hide from both sides. Building it found four ungated writers — the delegation loop (`claim_delegated_task`/`report_progress`/`submit_for_acceptance`) and `generate_handoff` bypass the generic gate on purpose (sponsor-bound verdicts, artifact projection) but reported nothing: submitting a task for acceptance filed a proposal and the chat UI stated nothing. All four now record their own receipts. `context_packs` is the one exempt table: a read tool lazily fills a derived cache, attributed at the service layer |

## Developer loop

| Feature | How |
|---|---|
| Per-teammate API keys | first key per person via `python -m app.bootstrap_key <name>` (out-of-band — minting via `POST /api/keys` requires an existing key, or any LAN caller could become anyone); `sk-skein-…` hashed, shown once; authenticates *and* attributes; the STRONG identity for private surfaces; presented-but-revoked keys hard-401. Admin: `GET /api/admin/keys`, `POST /api/admin/keys/revoke-all` (strong-identity-only) |
| `skein` CLI | stdlib-only; `pipx install ./cli`; config at `~/.config/skein/config.json` (0600, key prompted) |
| Git trailers | `skein install-hooks`; `Closes-Task: #12` / `Refs-Task: #7` sync on commit |
| CI webhook | `POST /api/webhooks/ci` (generic or GitHub Actions payload): red default-branch run files a deduped high-impact blocker; green auto-resolves; cancelled/skipped ignored |
| MCP server | `claude mcp add skein -- env SKEIN_MCP_USER=you backend/.venv/bin/python -m app.mcp_server` — 16 tools + the context-pack resource against the same DB |
| Slack | outbound webhook (`SLACK_WEBHOOK_URL`) + `/skein` slash command (`SLACK_SIGNING_SECRET`, HMAC-verified) |
| Prebuilt agent tools | `SKEIN_EXTRA_TOOLS=calculator,current_time,think,batch` loads allowlisted [strands-agents-tools](https://github.com/strands-agents/tools) into the real agent; research tools (`tavily_search`, `exa_search`) activate with their own keys; shell/file/exec tools, `http_request` (a third write path around the review gate), `use_agent`/`use_llm` (model-chosen provider endpoints), and `workflow`/`diagram` (path traversal/subprocess) are refused by the allowlist — rationale in `app/agents/extra_tools.py` |
| OpenTelemetry | `SKEIN_OTEL_ENDPOINT` traces the agent loop |

## Operations

| Feature | How |
|---|---|
| Migrations | append-only numbered SQL, per-migration `BEGIN IMMEDIATE` transactions, `schema_version` |
| **Tamper-evident provenance** | every `activity` row written since migration 036 carries `seq` + `hash` + `prev_hash`: SHA-256 over its own fields, chained to the row before it. A full walk catches an edited row, a deleted row, a removed head or tail, a re-rooted chain, and rows inserted outside the chain — each at a known seq. The link walk alone proves too little, so three marks live outside the rows: a monotonic **high-water** seq (catches truncation), the last verified **anchor** (catches a re-forge), and the pre-036 **unchained baseline** (catches smuggled rows). The in-DB marks alone do not defeat an attacker who reads `services/activity.py` and rewrites `app_settings` too — that case is covered by the **anchor log**: each successful nightly verification appends the verified tip (`<utc> seq=<n> hash=<hex>`) to `backups/activity-anchors.log` and, independently, to the same file on `SKEIN_BACKUP_MIRROR` (append, never copy — a truncated local file must not shorten the mirror's history). The daily findings rule replays every line ever anchored against the ledger as it exists now, so a re-forge or truncation has to contradict a record made on an earlier day. **The remaining limits, stated plainly:** rows newer than the last nightly line are covered only by the in-DB marks until tonight; an attacker who rewrites the local anchor log too is caught only by comparing it against the mirror copy, which is a human step after the finding fires; an attacker who can also write the mirror is not caught at all. Detection, never prevention. No backfill: pre-036 rows report as **unchained**, never as verified, because a chain computed today proves nothing about yesterday. `GET /api/activity/verify` (rate-capped, `?tail=1` resumes from the anchor); `/health` carries `activity_chain`; nightly 03:30 job advances the anchor; the `activity_chain_broken` rule pays for a full walk daily. `services/activity.py` + `db.activity_hash` |
| Scheduler (UTC) | one `JOBS` registry (`services/jobs.py`) drives cron + startup catch-ups: hourly blocker sweep · 03:00 backup · 03:30 activity-chain verify · 05:00 context pack · 05:15 forecast snapshot · 06:00 Mon weekly plan · 06:15 Mon stale-WIP nudge · 06:30 Mon week-open brief · 06:45 Mon authority review · 15:00 Fri week-close · 06:30 stale decisions · 06:50 findings · 07:00 digest · 07:05/15:05 notification flush · monthly retention prune (1st, 04:00) — all idempotent via `claim_job` CAS or status flips; every run records status/duration to `job_outcomes`; `/health` reports last success + stale flag per job |
| Backups & export | atomic daily SQLite backups (keep 14) + JSON export, `SKEIN_BACKUP_DIR` to relocate, `SKEIN_BACKUP_MIRROR` copies each backup off-box (keep 30) — this deploy mirrors to the NAS mount |
| CI | Gitea Actions (`.gitea/workflows/ci.yml`): pytest + frontend build per push; runner `the runner host` registered repo-level, runs as the `act-runner` systemd user service (docker mode, linger enabled) |
| Docker | multi-stage frontend image, slim non-root backend image, healthchecks, single `SKEIN_HOST` knob, named `skein-data` volume |
| Auth model | trusted `X-User` name picker (LAN model); optional `SKEIN_API_TOKEN` shared bearer; per-user keys for automation. Put a real proxy in front to leave the trusted network |
| Usage accounting | tokens/cycles/latency per thread and model when a real provider is on (`GET /api/usage`) |

## Environment variables

See `backend/.env.example`. Highlights: `SKEIN_MODEL_PROVIDER`
(`mock`/`ollama`/`openai`/`openai_compatible`/`anthropic`/`bedrock`),
`SKEIN_MODEL_ID`, `SKEIN_MODEL_BASE_URL`, `SKEIN_MODEL_API_KEY`,
`SKEIN_MAX_TOKENS`, `SKEIN_MODEL_PARAMS`, `SKEIN_AGENT_REVIEW`,
`SKEIN_SCHEDULER`, `SKEIN_EMBEDDINGS`, `SKEIN_API_TOKEN`,
`SLACK_WEBHOOK_URL`, `SLACK_SIGNING_SECRET`, `SKEIN_MCP_SERVERS`,
`SKEIN_OTEL_ENDPOINT`, `SKEIN_BACKUP_DIR`, `SKEIN_MCP_USER`,
`SKEIN_TURN_GUARD`, `SKEIN_CONTEXT_STRATEGY` (+ `_WINDOW`,
`_SUMMARY_RATIO`, `_PRESERVE_RECENT`, `_PIN_FIRST`, `_PROACTIVE`).
