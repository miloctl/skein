# Implementation Spec — Skein

> **Status (2026-07):** all phases 0–4 are BUILT and superseded by reality —
> this document is the original build plan, kept for its rationale, data
> model, and constraints. `docs/FEATURES.md` is the current reference for
> what exists. As-built deviations from this plan: embeddings are OpenAI
> vectors stored as JSON with Python cosine similarity (not sqlite-vec), and
> the approval gate is the pending_changes registry (not Strands interrupts).

Working constraint: **no API keys for now**. Every feature is designed with a
deterministic core (DB + REST + UI) that works without a model; the agent layer
sits on top and lights up when keys arrive. Nothing in Phases 0–2 requires an
LLM call.

## Design principles

1. **Deterministic core, agentic shell.** Each feature ships as schema + REST +
   UI first. Agent tools are thin wrappers over the same service functions, so
   the agent gains the capability "for free" later.
2. **Two write paths, one service layer.** Humans mutate via REST (forms/UI);
   agents mutate via tools. Both call shared functions in `app/services/` so
   validation, provenance, and activity logging happen once.
3. **Provenance everywhere.** Every mutating write records `origin`
   (`human | agent | agent_verified`) and `created_by`. This is cheap now and
   impossible to retrofit later.
4. **Agent writes become proposals.** Once keys exist, mutating agent actions
   go to `pending_changes` for human review instead of writing directly
   (roadmap #2/#6). The table and review UI are built keyless in Phase 1 —
   humans can also use it for peer review.

## Keyless dev strategy

- **`SKEIN_MODEL_PROVIDER=mock`** — a third provider: a scripted model that
  needs no network. It pattern-matches simple intents ("plan…", "standup…") to
  tool calls and otherwise echoes. Lets us exercise the full chat → tool →
  stream → UI pipeline in dev and tests with zero credentials.
- **Full REST write path** — POST/PATCH endpoints for every entity so the
  dashboard is a working tool (not read-only) without the agent.
- **Seed script** (`backend/seed.py`) — realistic demo data for UI work.

## Persistence plan

### Database (SQLite)

| Concern | Decision |
|---|---|
| Durability | `PRAGMA journal_mode=WAL` + `synchronous=NORMAL`; `busy_timeout` for concurrent writers |
| Migrations | Numbered SQL files in `backend/migrations/`, applied at startup, tracked in a `schema_version` table. No more edit-schema-in-place. |
| Backups | `POST /api/admin/backup` + on-startup daily copy to `data/backups/platform-YYYY-MM-DD.db` (SQLite `.backup` API), keep last 14 |
| Export | `GET /api/admin/export` → JSON dump of all tables (portability / disaster recovery) |
| Search | FTS5 virtual tables from day one (keyless full-text search); embeddings column added in Phase 3 without schema breakage |

### New tables (by phase)

- **Phase 1:** `users` (id, name, kind human|agent, active) · `blockers` (owner,
  impact, status, escalation_at, source_standup_id) · `intake_requests`
  (requester, summary, project_class, score fields, disposition, reason) ·
  `pending_changes` (entity, entity_id, proposed_by, diff JSON, status,
  reviewed_by) · provenance columns (`origin`, `created_by`) added to all
  existing tables via migration
- **Phase 2:** `playbooks` + instantiation metadata (see files below) ·
  `engagements` (the strike-team unit of work; project column graduates to FK) ·
  `allocations` (person/agent ↔ engagement, percent, dates) · `lessons`
  (retro output, tagged by project_class) · `handoffs` (generated package ref,
  acknowledged_by)
- **Phase 3:** `memories` (cross-thread agent memory) · `usage` (tokens/cost
  per thread/agent/model) · `notification_prefs`

### Files (useful file persistence)

| What | Where | Why files not DB |
|---|---|---|
| Playbook templates | `backend/playbooks/*.yaml` | Reviewable/diffable in git; the team edits them like code; DB only tracks instantiations |
| Generated artifacts (handoff packages, digests, retro docs) | `backend/data/artifacts/<engagement>/<date>-<type>.md` + an `artifacts` index table | Documents people read/share; markdown in git-friendly form |
| Chat sessions | `backend/data/sessions/` (already) | Strands FileSessionManager |
| DB backups | `backend/data/backups/` | See above |
| Export dumps | `backend/data/exports/` | Point-in-time JSON snapshots |

## Phases

### Phase 0 — Foundation (do first, ~small)
1. `git init` + initial commit (this lives under `~/gitea/` — push to Gitea when ready)
2. Migration runner + move current schema to `migrations/001_init.sql`; WAL pragmas
3. `users` table + `X-User` header convention (single-box trust model for now — see Open Questions)
4. Mock model provider; REST write endpoints; shared `app/services/` layer
5. pytest suite (services + API via TestClient, mock provider for chat) + seed script
6. Backup/export endpoints

### Phase 1 — Keyless operating system
- Blocker & escalation register (roadmap #7): CRUD + age-based `escalation_at`
  computed by a background sweep (APScheduler, no LLM); dashboard banner
- Intake & triage queue (#3): form + scored pipeline + disposition reasons
- `pending_changes` review inbox (#6's substrate): diff-style cards,
  approve/edit/reject; humans use it now, agents route through it later
- Provenance badges (#10a) across dashboard
- **My Day / Attention Inbox** (UX #1/#3): pure SQL — blocked-on-you items,
  due-soon, open questions assigned to you, today's calendar; nav badge count
- Quick capture (UX #5): Cmd+K → `POST /api/capture` with rule-based
  classification (keyword heuristics) — agent classifier swaps in later

### Phase 2 — Compounding structure
- Engagements + project classes as first-class records
- Playbooks: YAML templates + `instantiate` service (deterministic); planner
  agent later *adapts* rather than replaces
- Capacity & allocation board (PM #6)
- Retro ritual: scheduled calendar event + structured `lessons` capture wired
  back to playbooks (WF #7)
- Handoff package generator (WF #5/PM #7): deterministic assembly of open
  blockers, pending gates, decisions, unanswered Q&A into a markdown artifact;
  LLM narrative polish is an optional enhancement later

### Phase 3 — Agentic layer (needs API keys)
- Approval gates via Strands interrupts feeding `pending_changes`
- Semantic memory: embeddings into `sqlite-vec`, hybrid with existing FTS5
- Daily digest + blocker-detector agent (reuses Phase 1 sweep infra)
- Specialist sub-agents; cross-thread memory; usage/cost tracking hooks
- Mock provider stays for CI

### Phase 4 — Surfaces & integrations
- MCP (GitHub/Slack/calendar), Slack bot surface, notification tiers,
  OpenTelemetry observability, eval harness

## Gaps identified beyond the roadmap (now covered above)

1. **Identity/multi-user** — the platform assumed one anonymous user; a strike
   team needs `users` + attribution (Phase 0). Real authn deferred.
2. **No git repo / no tests / no migrations** — Phase 0.
3. **No mock mode** — without it, nothing is testable keyless (Phase 0).
4. **Dashboard is read-only and static** — REST writes (Phase 0) + polling
   refresh now; SSE push later.
5. **Backups/export** — SQLite is one `rm` away from total loss (Phase 0).
6. **Background job infra** (APScheduler) — needed by blockers sweep, retro
   scheduling, digests; introduced keyless in Phase 1.
7. **Deployment story** — Dockerfile + compose, deferred until it matters.

## Accepted tradeoffs

- **No cross-operation transactions in compound services** (e.g.
  `playbooks.instantiate` performs many single-connection writes). A crash
  mid-sequence can leave partial state (engagement without all its tasks).
  Accepted for now given the one-connection-per-op design; revisit if it
  bites — the fix is a shared-connection context manager in `db.py`.

## Open questions (defaults chosen; flag if wrong)

1. **Identity**: default = simple `users` table + `X-User` header the frontend
   sets from a name picker (trusted LAN model). Fine until the tool leaves
   your network; SSO/passwords are a later swap.
2. **Engagement vs. project**: default = rename/graduate the current free-text
   `project` field into an `engagements` table in Phase 2.
3. **Team roster**: seed with you + placeholder agents; give me real
   names/roles when convenient.
