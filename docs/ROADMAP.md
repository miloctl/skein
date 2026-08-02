# Feature Roadmap — Skein

> **Status markers (2026-07):** Round 1's synthesized top-10 — all shipped.
> Round 2 — shipped: CLI, MCP server, API keys, git trailers, CI webhook,
> whimsy pack, team pulse, digest voice; not built: editor touchpoints,
> weekly changelog, public-quality /docs, knowledge-flywheel meter, NPC
> levels, clean-handoff streaks, inbox-zero runs, RICE calibration.
> Round 3's suggested build order — all shipped.
> **Round 4 (2026-08, the buzz design-study): all seven items shipped** —
> see "Residuals from the buzz adoption" at the bottom for what was
> deliberately not built and why. Current reference: `docs/FEATURES.md`.
> **All un-shipped work lives in "Open backlog (consolidated 2026-08-02)" at
> the bottom of this file.** Before that date it was spread across this file,
> `docs/PLAN.md` and two review transcripts.

Ideation produced by a 4-agent panel (Product Manager, Workflow Architect,
AI Engineer, UX Researcher), each reviewing the current platform from their
own lens for an AI-enabled strike team working varied project classes across
the company. Below is the synthesized roadmap, followed by each agent's full
output.

## Synthesized top 10 (cross-agent consensus, roughly in build order)

1. **Semantic team memory (RAG over the workspace)** — embed milestones, Q&A,
   decisions, standups, and notes on write (`sqlite-vec` + FTS5 hybrid) and
   give the Chief of Staff a `search_workspace` tool. "Have we solved this
   before?" is the compounding advantage of an elite team. *(AI #1, PM #4)*
2. **Human-in-the-loop approval gates** — agents propose, humans approve.
   Use Strands interrupts to pause mutating tool calls; surface pending
   approvals as cards in assistant-ui. The trust unlock for everything
   autonomous. *(AI #2, WF #4, UX #2, PM #5)*
3. **Engagement intake & triage queue** — a structured front door for requests
   (submitted → scored → accepted/deferred/declined) so the team takes the
   right work and demand vs. capacity is legible to leadership. *(PM #1)*
4. **Project-class playbooks / templates** — per-class milestone skeletons,
   default tasks, rituals, and gates; the planner instantiates and adapts a
   playbook instead of planning cold. Retros feed learnings back in. *(PM #2,
   WF #1, UX #8)*
5. **Autonomous daily digest + blocker detector** — a scheduled headless agent
   sweeps stalled tasks, unanswered questions, and at-risk milestones before
   standup; renders as a "My Day" briefing view and stakeholder digest.
   *(AI #3, PM #3, UX #1)*
6. **Agent work queue with human review** — assign tasks *to* agents that
   execute asynchronously; outputs land in a diff-style review inbox
   (approve / edit / reject / ask-why) with provenance preserved. Converts AI
   from chat interface into headcount. *(PM #5, UX #2, WF #6)*
7. **Blocker & escalation register** — first-class blockers with owner, age,
   and escalation policy (agent-blocked > 2h pings owner; > 1 day escalates);
   auto-extracted from standups. *(WF #3, PM #8)*
8. **Handoff contracts & rotation handoff generator** — schema-defined
   payloads at every human↔agent and agent↔agent boundary, plus an
   agent-drafted close-out/handoff package when the strike team rotates off an
   engagement. *(WF #2, WF #5, PM #7, AI ties into A2A)*
9. **MCP integrations + Slack surface** — GitHub, Slack, and calendar via
   Strands' `MCPClient`; morning briefing, approvals, and quick capture from
   Slack so the platform meets the team where it lives. *(AI #5, UX #7)*
10. **Trust, observability & cost layer** — provenance badges
    (human / agent / agent-verified) on every record, OpenTelemetry tracing of
    agent actions, and per-project/per-agent token cost attribution.
    *(UX #4, AI #7, AI #8)*

Also strongly rated: attention inbox / needs-you triage (UX #3), quick-capture
command palette (UX #5), notification noise budget (UX #6), retrospective
ritual with action tracking (WF #7), capacity & allocation board (PM #6),
specialist sub-agents with per-model routing as a cost lever (AI #4), and
cross-thread agent memory (AI #6).

---

## Product Manager — top 8 by value/effort

1. **Engagement Intake & Triage Queue** — no front door today; a structured
   intake + triage pipeline (submitted → scored → accepted/deferred/declined)
   turns "say no clearly" into a system. *Impl: request/disposition tables, a
   CoS tool drafting a RICE-lite score, dashboard queue view.*
2. **Project-Class Playbooks** — each class (incident response, prototype,
   diligence, migration…) has a repeatable shape currently re-derived from
   scratch. *Impl: template entities; planner selects and instantiates.*
3. **Auto-Generated Stakeholder Digest** — weekly exec-readable digest per
   engagement drafted by the agent from existing data; eliminates status
   meetings. Highest value-per-effort. *Impl: synthesis tool + approve-then-
   send + shareable read-only page.*
4. **Unified Team Memory (semantic search/RAG)** — cross-artifact retrieval as
   both UI and agent tool. *Impl: embeddings on write into sqlite-vec.*
5. **Agent Work Queue with Human Review** — humans assign async work to
   agents; outputs enter a review inbox. *Impl: background runner + pending_review state.*
6. **Capacity & Allocation Board** — live human+agent allocation with
   over/under signals; makes intake honest. *Impl: allocation table + heatmap + CoS tool.*
7. **Engagement Debrief & Handoff Generator** — agent-drafted close-out
   package + retro whose learnings feed playbooks. *Impl: assembly tool over
   decisions/tasks/notes.*
8. **Risk & Blocker Register with Escalation** — owned blockers with age
   thresholds and CoS nudges. *Impl: small table + standup auto-extraction.*

## Workflow Architect — top 8 workflow features

1. **Project Intake & Class Templates** — intake wizard + `instantiate_project(class, params)` tool seeding milestones, rituals, calendar.
2. **Human-Agent Handoff Contracts** — schema-defined inputs, acceptance
   criteria, deadline, return format; orchestrator refuses dispatch until
   complete.
3. **Blocker & Escalation Flow** — first-class blocker state with owner, age,
   escalation policy; background sweep job; CoS drafts escalation messages.
4. **Review & Approval Gates** — per-class gate definitions; approval queue;
   sign-offs auto-write to the decision log.
5. **Rotation Handoff & Offboarding Package** — `generate_handoff` tool
   compiling open blockers, pending gates, decisions, unanswered Q&A;
   incoming-roster acknowledgement checklist.
6. **Agent Failure & Recovery Paths** — explicit run states
   (running/succeeded/failed/needs_human), retry policies, provider fallback
   (Anthropic ↔ OpenAI), partial output preserved for triage.
7. **Retrospective Ritual with Action Tracking** — calendar-triggered retro
   pre-drafted by the CoS from activity/decisions/blockers; lessons tagged by
   project class feed templates; action items become real tasks.
8. **Agent-to-Agent Coordination Protocol** — inter-agent requests brokered
   through the CoS using the handoff-contract schema, with dependency
   sequencing and full audit logging.

## AI Engineer — top 8 agentic capabilities

1. **Semantic Knowledge Retrieval (RAG)** — `sqlite-vec` embeddings + FTS5
   hybrid `search_workspace` tool; populated by FastAPI background task.
2. **Human-in-the-Loop Approval Gates** — Strands interrupts raised from
   mutating tools, persisted via FileSessionManager, rendered as approval
   cards in assistant-ui.
3. **Autonomous Daily Digest + Blocker Detector** — APScheduler job running a
   headless read-only agent; digest saved as a note; flagged blockers routed
   through approval gates.
4. **Specialist Sub-Agents (agents-as-tools)** — researcher, reporter,
   project-analyst alongside the planner; cheap models for specialists,
   frontier model for the orchestrator (main cost lever).
5. **MCP Integrations (GitHub + Slack + Calendar)** — Strands `MCPClient`
   over streamable HTTP; standup collector DMs via Slack MCP and writes to
   the standups table.
6. **Per-User / Per-Project Long-Term Memory** — `SummarizingConversationManager`
   for in-thread compaction + a cross-thread `memories` table injected via a
   `BeforeInvocation` hook (or drop-in `mem0_memory` from strands-tools).
7. **Observability + Eval Harness** — `StrandsTelemetry` (OpenTelemetry) to
   Langfuse/Jaeger; golden Q&A eval suite with LLM-as-judge run in CI against
   both providers.
8. **Cost & Usage Tracking** — `AfterInvocation` hook reading
   `agent.event_loop_metrics` into a usage table keyed by thread/agent/model;
   dashboard aggregates + soft budget alerts.

## UX Researcher — top 8 daily-adoption features

1. **My Day (morning briefing view)** — default route answering "what changed
   and what needs *me*?" in under 30 seconds; `/briefing` endpoint + optional
   2-sentence agent narrative.
2. **Overnight Agent Review Queue** — diff-style cards for each agent action
   with Approve / Edit / Reject / Ask-why; agents write to `pending_changes`
   instead of mutating directly.
3. **Attention Inbox (needs-you triage)** — unified query of things blocking
   others on *you*, with one-tap responses; nav badge count is the daily pull.
4. **Trust & Provenance Signals** — origin (human / agent / agent-verified) +
   confidence + source links rendered as a consistent badge system on every
   record.
5. **Quick Capture (Cmd+K palette)** — freeform text classified by a
   lightweight agent into task/question/note/decision with confirm-or-correct.
6. **Smart Notification Digest (noise budget)** — three tiers: immediate
   (blocking-on-you), 2× daily agent-summarized digest, passive activity log;
   per-project preferences.
7. **Slack Surface for the Chief of Staff** — scheduled briefing, Block Kit
   approve/reject buttons wired to the same approval API, capture by DM.
8. **Project-Class Templates & Lenses** — class-specific dashboard layouts
   (migration vs. incident vs. research spike) over the same data.

*UX validation note: pilot with a 5-user diary study; key metric is
time-to-first-meaningful-action on open (< 30s) and review-queue approval
latency.*

---

# Round 2 ideation — delight, game systems, developer loop (2026-07-23)

Second 3-agent panel (Whimsy Injector, Game Designer, Developer Advocate),
run after Phases 0–2 + integrations shipped. Everything below is deterministic
(no LLM required) unless noted.

## Synthesized picks

1. **`skein` CLI + per-teammate API keys** — capture/standup/blockers from
   the terminal; the substrate for git hooks and CI. *(DX #1, #3)*
2. **Skein MCP server** — expose the platform's own API as MCP tools so the
   team's Claude Code sessions and other agents read/write tasks natively.
   The killer feature for an AI-enabled team. *(DX #2)*
3. **Ship It moment + Blocker Funeral** — confetti + recap card on shipped
   engagements; ceremonial "laid to rest" for long-lived blockers with a
   Blocker Graveyard. Highest delight-per-effort. *(Whimsy #1, #2)*
4. **Git commit trailers + CI webhook** — `Closes-Task: #12` auto-closes;
   red main-branch builds auto-open blockers, green auto-resolves. *(DX #4, #5)*
5. **Team-level game loops with seasons** — blocker speedrun PBs, shared
   standup chain with PTO "shields", knowledge flywheel weighted by *reuse*
   not volume, clean-handoff streaks judged by the receiver; 6-week seasons
   reset everything and produce a report card. Explicitly designed against
   leaderboards, streak dread, and metric gaming. *(Game #1-#6)*
6. **Agent NPC levels** — agents visibly "level up" from validated memories,
   corrections, and playbooks, unlocking real capabilities. *(Game #4)*
7. **Digest with a voice + empty states + mock-agent personality pack** —
   date-seeded deterministic tone so the whole team shares the same joke;
   suppressed when an escalation is active. *(Whimsy #3, #4, #5)*
8. **Weekly changelog auto-post + polished /docs** — the platform announces
   its own improvements; OpenAPI polish so teammates build integrations.
   *(DX #7, #8)*

## Whimsy Injector — 8 delight features (ranked)

1. **Ship It Moment** — confetti (behind prefers-reduced-motion) + SQL-driven
   recap card (duration, blockers survived, decisions) + share-to-Slack.
2. **Blocker Funeral** — resolve after 3+ days → epitaph + Blocker Graveyard.
3. **Digest With a Voice** — ~40 templated openers keyed on computed team
   state, date-seeded for stability.
4. **Empty States That Earn Their Space** — rotating copy per view
   ("Review inbox: zero. The agents fear you.").
5. **Chief-of-Staff Mock Personality Pack** — dry aide-de-camp voice for the
   keyless mock agent; makes demos better too.
6. **Streaks, But For the Team** — team-scoped only, gentle resets.
7. **Handoff Baton** — baton-pass animation + "clean handoff" seal.
8. **Loading lines + seasonal easter egg** — data-driven loaders, Konami-code
   day themes.
   *Guardrails: reduced-motion + calm-mode toggle, frequency caps, whimsy
   suppressed while an escalated blocker is showing.*

## Game Designer — 8 team-level mechanics (ranked)

Pillars: team-as-player, runs-not-grinds, reward the report, count knowledge
*consumed* not produced. Kill-switch signals defined per loop (shorter
standups, later blockers, citation-less playbook spikes = being gamed).

1. **Blocker Speedrun Board** — team median time-to-clear PBs per severity;
   raising a blocker *scores* (never suppresses reporting).
2. **Standup Chain** — one shared streak; 3 "shield" tokens/season auto-cover
   calendar absences.
3. **Knowledge Flywheel Meter** — creation 1x, reuse/citation 3x, unread decay.
4. **Agent NPC Levels** — validated-entry XP; levels unlock real capabilities
   (longer lookback, proactive digest sections); never decrease.
5. **Clean Handoff Streak** — receiver-judged: no clarifying question in 48h;
   failures generate playbook-gap prompts, not blame.
6. **Seasons + Season Report** — 6-week resets aligned to rotations; archives
   for retros. Ship alongside whichever loop goes first.
7. **Inbox Zero Runs** — win = nothing ages past SLA (not throughput; no
   rubber-stamp incentive); reopens void the day.
8. **RICE Calibration Range** — estimated-vs-actual trend per season,
   team-aggregated, bets excludable.
   *All thresholds in a tuning config table, not hardcoded.*

## Developer Advocate — 8 developer-loop features (ranked)

1. **`skein` CLI** — Typer over the existing REST API; pipx/brew.
2. **Skein MCP Server** — FastMCP wrapping the REST routes (`get_my_day`,
   `create_task`, `log_decision`, `search_knowledge`); `claude mcp add skein`.
3. **Per-teammate API keys** — hashed keys + scopes table; prerequisite for
   1, 2, 4, 5; keeps attribution honest.
4. **Git commit trailers** — `Closes-Task: #12` via hook or CI merge parse;
   links SHAs into task timelines.
5. **CI webhook → blockers** — failed default-branch run opens a blocker
   (deduped by repo+branch), green resolves it.
6. **Editor touchpoints** — VS Code capture command + `TODO(skein):`
   promotion via `skein scan-todos`.
7. **Public-quality /docs** — polish the free OpenAPI: examples, quickstart,
   embedded Scalar viewer.
8. **Weekly changelog auto-post** — rides digest + Slack infra.
   *Adoption metric: % of captures/closures originating outside the web UI;
   >50% means the platform joined the development loop.*

---

# Round 3 ideation — operating system maturity (2026-07-23)

Third panel, 5 agents (Chief of Staff, Trend Researcher, Studio Producer,
Sprint Prioritizer, Feedback Synthesizer), run after the developer loop,
integrations, and review hardening shipped.

## Synthesis — the five lenses converge on four themes

**1. Agents as first-class teammates (the 2025-26 table stakes).** Delegation
as a real primitive — tasks with a `delegated_agent` and a human sponsor, a
Mission-Control view of active agent sessions, an authority matrix
(`autonomous | notify | review | forbidden`) per (agent, action type) with a
trust ratchet: N consecutive approvals suggests promotion to autonomous.
Agent identities extend the API-key model with scopes, sponsors, and an
append-only audit trail. *(Trend #1/#4, CoS #4, Sprint #6)*

**2. The review inbox becomes a flywheel.** Every approve/reject/edit is
already a labeled example: rejection-reason analytics auto-tune which
proposal types need gating; thumbs on chat responses build an eval corpus;
`skein eval` replays it before any model/prompt change; capture corrections
refine the classifier rules — every self-tune shipped as a gated proposal.
Nobody mainstream has closed this loop; our labeling cost is already sunk.
*(Feedback #1/#2/#3, Trend #5)*

**3. The portfolio layer.** Engagement health scoring (R/Y/G from blocker
age, milestone slip, standup gaps — receipts shown), cross-engagement
allocation conflicts (SQL over the week calendar), what-if intake planning
("accepting this puts Dana at 130% in W34"), milestone slip forecasting from
the team's own actuals, inter-engagement dependencies, and a `/portfolio`
executive surface — curated projections, never raw tables. Plus the external
commitment ledger: promises made to stakeholders outside the team, extracted
where they already surface. *(Producer #1-6, CoS #1/#2)*

**4. Operating rhythm & record integrity.** Weekly commitment line
(`committed_week` on tasks) + an auto-drafted Monday plan through the review
inbox; flow metrics (cycle time, throughput, WIP) from existing timestamps —
no estimates; stale-WIP nudges and soft per-person WIP limits; decision
half-life (`review_by` dates + superseding chains so nobody cites a dead
decision); stale-knowledge pruning driven by a search-impression log;
meeting preemption (agenda-or-async). *(Sprint #1-5, CoS #3/#5/#6, Feedback #4/#6)*

## Suggested build order

1. Engagement health + allocation conflicts (pure SQL reads, feeds everything)
2. Weekly commitment line + auto-drafted plan (reuses review inbox)
3. Flow metrics rollup (baseline before behavior change)
4. Agent delegation + delegation charter + agent identities
5. Review-inbox analytics → trust scores, eval corpus, `skein eval`
6. Decision half-life; 7. Commitment ledger + exec readout;
8. What-if intake + slip forecasting; 9. Ambient agent inbox
(notify/question/review); 10. Team context pack (versioned org-brain as an
MCP resource + AGENTS.md emitter — the differentiated bet: Skein becomes
the context supplier to every other agent the team uses).

*(Full per-agent output retained in the panel transcripts; key trend sources:
Linear for Agents, GitHub Agent HQ, Asana AI Teammates, Height, Dust,
LangChain ambient agents, CSA agentic identity governance, OWASP Agentic
Top 10.)*

# Engineering backlog — from the full-project architecture review (2026-07-24)

Done in the same review cycle: milestone `engagement_id` link, unified
MCP/tool gate, handoff scoping, export coverage, indexes, allocations
provenance.

Items 1–8 shipped 2026-07-24 (backlog burn-down; tests in
`tests/test_backlog.py`):

1. ~~`db.transaction()` context manager~~ — contextvar ambient connection in
   `db.py`; `playbooks.instantiate` and `intake.disposition_request` converted.
2. ~~Job registry~~ — `services/jobs.py` `JOBS` tuple drives cron + startup
   catch-ups; per-run outcomes in `job_outcomes` (migration 013); last-success
   + stale flag on `/health`; `job_stale` findings rule at 2× period.
3. ~~Name-join migration~~ — health/ship-it/handoff/forecast now join on
   `m.engagement_id`; `create_engagement` adopts orphan milestones created
   under the name before the engagement existed.
4. ~~Retention~~ — `services/retention.py`, monthly (1st, 04:00 UTC):
   forecast_snapshots >1y, read notifications >90d, job_runs/job_outcomes
   >90d. activity is the provenance ledger — kept forever.
5. ~~Staleness SLA constants~~ — `services/slas.py` (3d/7d/14d gradation).
6. ~~Extract `readout.py`~~ — composes portfolio + insights with top-level
   imports; the deferred-import workarounds are gone.
7. ~~`services/usage.py`~~ — chat token logging out of `routes/chat.py`;
   digest narrator inverted (`digest.set_narrator`, registered from
   `agents/narrator.py` at startup) so services never import the agent layer.
8. ~~MCP migration guard~~ — `db.pending_migrations()`; `mcp_server.main`
   exits instead of applying schema from a long-lived side process.

Still open:

9. Runner isolation: move CI to an ephemeral sandboxed host before ever
   re-adding a pull_request trigger; consider rootless docker for the runner.
   (Ops work on the runner host, not a repo change.)

# Residuals from the buzz adoption (2026-08-02)

All seven planned items from the block/buzz design-study shipped (tamper-
evident ledger + off-box anchor, turn guard, activity feed, gate coverage
assertion, persona manifest, turn cost + budget rule, doc honesty patterns —
see docs/FEATURES.md for each). What remains, deliberately unbuilt:

1. **Post-compaction context re-injection — open.** Two earlier rationales
   for dropping it were both wrong (there IS a hook seam:
   `ConversationManager.reduce_context()`; and `pin_first` does NOT survive a
   turn boundary on file-backed sessions — session restore replays from
   `offset=removed_message_count`, which skips exactly the pinned messages).
   Nothing currently keeps the top of a long chat alive across turns. If
   built: subclass the conversation manager, re-inject the scoped
   per-engagement context pack (the thread→engagement link now exists), once
   per session with a token ceiling to avoid a trim/re-inject loop.
2. **Honest tombstones for deleted tasks/chats** — a removed item leaves a
   marker with a sanitized reason, never a silent hole. Skein does this for
   private 1:1 notes and decision supersession only.
3. **Presence as a lease, not a flag** — applies only if Skein ever grows a
   boolean liveness indicator. Today the agents page shows a `last seen`
   timestamp, which is honest forever; buzz's lease idea fixes a live-dot
   outliving a dead process, and no such dot exists here. No action unless
   one is added.

Assessment 2026-08-02: items 1–3 need no work now. 1 waits for a real
long-chat complaint (summarize + the context-pack tool already cover it);
2 is met more strongly by the hash-chained ledger and the loud feed row than
a tombstone would meet it; 3's premise does not apply.
4. **Per-rule `enabled` flag for findings** — silence a noisy rule by config
   rather than a deploy.
5. **A UI surface for `/api/usage`** — the budget finding points people at a
   raw JSON endpoint; engagement costs belong next to engagement health on
   /portfolio.
6. **If HMAC is ever added to the activity chain** — changing the preimage
   invalidates every existing chain AND contradicts the append-only anchor
   log. Plan it as a logged genesis reset, never a migration.

# Open backlog (consolidated 2026-08-02)

This is the only home for un-shipped work. Before this date the backlog was in
four places: this file, `docs/PLAN.md`, and two review transcripts. Every item
below was checked against the code on 2026-08-02. The items that turned out to
be built were dropped, not carried forward.

The ID tags are kept because source comments cite them. `TD1` and `TP5` and
their neighbours are named in `frontend/app/globals.css`, `frontend/lib/theme.ts`,
and `frontend/lib/whimsy.ts`.

## Self-serve UX (from the 2026-07-24 fresh-user review)

Sized S or M by that review. Items 6 and 8 of the original list are gone: the
engagement-close conclusion select shipped (`app/dashboard/page.tsx`), and the
portfolio commitments card now names its two audiences in the card title.

1. [M] Global search box in the nav. `GET /api/search` exists and is invisible.
2. [M] What-if staffing button on scored intake rows. This closes the dangling
   "shown in staffing what-ifs" reference in Settings.
3. [S] Delegate-task affordance. The Agents empty state advertises it and no UI
   does it.
4. [M] Generate-handoff button on closing engagements and closed engagements.
5. [S] Allocation inline form on the Capacity card, or an honest empty state.
6. [S] `?` tooltips: ISO week format on the commitment card, season definition
   on the pulse banner, origin glossary beside Review.

## Manager and workflow (from the 2026-07-25 ideation run)

C1 (week rituals), P2 (absences) and P5 (standup auto-draft) shipped. These did
not:

- **C2 received-promise chaser** — `commitments.direction ('given'|'received')`
  plus `last_nudged_at` in a migration. Capture grammar
  `awaiting: <who> — <what> by <date>`. An hourly rule nudges the creator and
  escalates to the manager after 2 silent cycles. `waiting_on: commitment:N`
  already works.
- **C3 meeting outcome loop** — `events` gains agenda, engagement_id and
  outcome_status. A post-meeting attention item deep-links to `/ingest`. A
  weekly finding names a recurring meeting with no captured outcome for 3 weeks
  and gives the hours-burned receipt.
- **C4 stakeholder open-threads brief** — a read-only union over
  `commitments.to_whom`, `intake.requester`, `questions.asked_by` and
  `events.attendees` for names outside the team.
- **C5 decision links and cascade** — a `decision_links` table, populated at
  record time and by scanning references. Consumed by scoped context packs,
  supersede notifications, and handoffs.
- **P1 weekly planning cockpit** — `GET /api/planning` and one page in meeting
  order: kept-% and carryover, then capacity against the draft with conflicts
  inline, then the intake queue, then stale decisions, then one-click commit.
  Pure composition of endpoints that exist.
- **P3 shared 1:1 loop** — a pairwise-visible agenda scope and a `1:1:` capture
  prefix. Deferred until reports ask for it. The visibility tier gets designed
  then, not before.
- **P4 interrupt ledger** — derived, with no user action: a task created after
  the week line locked and finished in the same week counts as unplanned. The
  team-level ratio goes in flow metrics and the readout, with a findings rule.

## Agent layer (2026-07-25)

A1 (delegation work loop) and A2 (system-filed authority proposals) shipped.

- **A3 gated agent morning sweep** — a new `nudge` registry entity where apply
  means notify. Daily rules over existing reads file 5 proposals per day at
  most, deduped weekly the way findings are. The cheapest authority on-ramp.
- **A4 agent-to-agent handoff** — a `handoff_task` tool that keeps the sponsor
  immutable. The hop is itself a proposal the sponsor approves.
- **A5 proposal bundles** — `bundle_id` and `seq` on `pending_changes`, with
  symbolic references (`$1.id`) resolved at apply time, per-bundle approval
  with a per-step untick, and an atomic apply. Deferred until the simpler
  pieces prove out.
- Rejected proposals nag agent inboxes forever. An `acked_at` column ends it.
- Notify-tier writes link to an empty `/review`.

## Developer loop (2026-07-25)

D1 (`skein review`/`inbox`/`answer`/`worklog`) shipped.

- **D2 attention count in the shell prompt** — `skein attention --porcelain`
  reads a 60-second cache at mode 0600, never blocks and never errors.
  `skein install-prompt` writes the starship or PS1 snippet, on the
  `install-hooks` precedent.
- **D3 branch-aware git flow** — `skein task start 42` makes the branch
  `task/42-slug` and sets in_progress. A prepare-commit-msg hook injects the
  trailer from the branch name. `skein pr-body` composes task, engagement pack
  and commits for `gh pr create`.
- **D4 MCP mid-task parity** — `claim`, `report` and `submit` landed.
  `update_task`, `answer_question` and `resolve_blocker` did not. Review
  approval over MCP stays deliberately absent, because an agent must not
  launder its own proposal.
- **D5 offline capture outbox** — a JSONL outbox with an idempotency key that
  auto-flushes on any successful command, plus `my-day --cached`.
- **F6** CLI argument grammar normalization. **F7** `skein context --engagement`.
  **F8** `skein ask`.

## Delight (2026-07-25)

- **W1 the Skein takes flight** — one goose per ship this season forms a V on
  Team Pulse. A flock of geese in flight is called a skein. Count-based,
  team-level, and it resets each season.
- **W2 onboarding goose takeoff** — the completion moment is a silent no-op
  today. Show the card once more with the goose lifting off.
- **W3 dye-lot season names** — deterministic natural-dye names per season
  index, and a season-close ritual line.
- **W4 `honk`** — a ⌘K easter egg. One goose glides across the viewport,
  nothing is persisted, and reduced-motion is respected.
- **W5 the Bolt** — the selvage permanently gains one repeat per ship.
- **W6 epitaph pool** for blocker funerals, seeded by blocker id.
- **W7 loose threads** — woven 404 and error pages. There is no
  `not-found.tsx` at all today.

## Decisions needed, not builds (2026-07-25)

These are not features. Each one needs a call.

- **Time zone.** Every human rhythm is hardcoded to UTC. A team outside UTC
  gets a digest at the wrong hour. A `SKEIN_TZ` setting has zero hits today.
- **The OIDC and API-key identity bridge is undefined.** `TODO.md` carries two
  accepted debts that both repay when this lands.
- **`docs/FEATURES.md` claims person-level data never judges the past, and
  `services/portfolio.py` still returns `wip_by_person`.** Narrow the claim or
  aggregate the display. Leaving both is the only wrong answer.
- **`promised:` audience is ambiguous at capture time.**
- **The Slack `fb:` refusal is documented but not stated in the Slack copy.**
  The code fails closed, so this is a documentation gap only.

## Theme system (from the 2026-07-27 review)

TD1, TD2, TD6, TP5, TP6 and TP3 shipped. Open by choice:

- **TD3** density dial through `--spacing` (ledger dense, atelier airy).
  **TD4** loom weft, real cloth with warp and weft. **TD5** phosphor dark-mode
  bloom and heading glow.
- **TD7** ledger masthead rule with 20px text-aligned ruling. **TD8** atelier
  laid-paper texture, light mode only. **TD9** high-contrast plain heading face
  with a 3px focus ring. **TD10** phosphor light mode as paper teletype.
- **Vellum pack concept** (drafting grid, blueprint night). Run
  `scripts/check_theme_contrast.py` before shipping it.
- **TP1 named presets.** Revisit only if the custom editor grows.
- TP2 per-appearance packs, TP4 seasonal, TP7 OS accent and TP8 scheduled dark
  are all skipped. TP7 is not buildable.

## Cut, with re-entry triggers (from the 2026-07-24 synthesis)

These are deliberate refusals. Each names the condition that reopens it.

| Cut | Trigger to revisit |
|---|---|
| Shadow authority level | Proposal volume overwhelms the review queue. The review queue *is* shadow mode today. |
| `entity_links` table, registry, thread view | A 4th typed relationship with a named consumer. |
| Attention budget, ack states, dedupe keys | Real duplicate-notification pain. Findings already dedupe weekly. |
| Trust profile partitions by model version | A model swap causes a problem that review stats did not catch. |
| Auto-quiet findings rules | The rule count grows beyond hand-tending. The maintainer retires rules at season end today. |
| Stakeholder signed status pages | Real stakeholder demand AND real auth. Then build it as a push-generated static artifact, never by exposing the app. |
| Coordination-debt and closed-loop-rate metrics registry | Multi-team scale. |
| Playbooks 2.0, delegation contracts, evidence pack, outbox, capability broker | Deferred. Specs are in `docs/reviews/2026-07-24-agent-sol.md`. |
| Employee private-prep sections | Refused until the journal separate-store pattern is proven. |
