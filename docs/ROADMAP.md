# Feature Roadmap — Strands Team Platform

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

1. **`strands` CLI + per-teammate API keys** — capture/standup/blockers from
   the terminal; the substrate for git hooks and CI. *(DX #1, #3)*
2. **Strands MCP server** — expose the platform's own API as MCP tools so the
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

1. **`strands` CLI** — Typer over the existing REST API; pipx/brew.
2. **Strands MCP Server** — FastMCP wrapping the REST routes (`get_my_day`,
   `create_task`, `log_decision`, `search_knowledge`); `claude mcp add strands`.
3. **Per-teammate API keys** — hashed keys + scopes table; prerequisite for
   1, 2, 4, 5; keeps attribution honest.
4. **Git commit trailers** — `Closes-Task: #12` via hook or CI merge parse;
   links SHAs into task timelines.
5. **CI webhook → blockers** — failed default-branch run opens a blocker
   (deduped by repo+branch), green resolves it.
6. **Editor touchpoints** — VS Code capture command + `TODO(strands):`
   promotion via `strands scan-todos`.
7. **Public-quality /docs** — polish the free OpenAPI: examples, quickstart,
   embedded Scalar viewer.
8. **Weekly changelog auto-post** — rides digest + Slack infra.
   *Adoption metric: % of captures/closures originating outside the web UI;
   >50% means the platform joined the development loop.*
