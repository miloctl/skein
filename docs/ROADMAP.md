# Feature Roadmap — Skein

> Shipped work is documented in `docs/FEATURES.md`. The ideation
> transcripts behind rounds 1–3 (all shipped or deliberately skipped) and
> the 2026-07-24 backlog burn-down are archived in `docs/reviews/`.
> **This file holds only un-shipped work**: the open backlog, the
> decisions still to make, and the refusals with their re-entry triggers.

# Open backlog (consolidated 2026-08-02)

This is the only home for un-shipped work. Before this date the backlog was in
four places: this file, `docs/PLAN.md`, and two review transcripts. Every item
below was checked against the code on 2026-08-02. The items that were already
built were dropped, not carried forward.

The ID tags are kept because source comments cite them. `TD1` and `TP5` and
their neighbours are named in `frontend/app/globals.css`, `frontend/lib/theme.ts`,
and `frontend/lib/whimsy.ts`.

## Visibility tiers and crews

Designed in `docs/VISIBILITY.md`, phases 3 to 6 un-shipped. A `private` /
`crew` / `workspace` tier on the content tables, plus crew membership.
Private is structural (never enters FTS, packs, digests, findings, the ICS
feed, or an export), so only the crew tier is filtered. A non-workspace row
is read only by a `StrongUser`. Phases 0 to 2 have shipped: every content read resolves a caller,
`POST /api/chat` claims the thread id, MCP `get_my_day` answers only for
its own identity, `crews` and `crew_members` carry the membership, and
`services/scope.py` holds the filter plus a table-classification
inventory that CI checks.

## Bounded-input census (from the 2026-08-03 holistic review)

CORRECTIONS rule 5 names three bounds. Only the PATCH-vs-create parity check
is enforced by a test. The other two are review obligations, and a review
found real gaps behind both:

- **Rate caps.** About 46 mutating routes never call `ratelimit.check`,
  including `PATCH /api/tasks/{id}`, `PATCH /api/questions/{id}`, the
  `/api/private/*` writes, and the chat folder/thread writes. Needed: a
  structural test that reflects over the route table and asserts every
  mutating route either calls the check or carries an `# unbounded:` marker
  with a row in the exemptions table.
- **Unbounded lists.** `private_notes.list_notes` has no LIMIT on its
  `if person:` branch, and `chat_threads.list_threads`, `get_messages`,
  `api_keys.list_keys` and `list_all_keys` have none at all.
- **Uncapped-on-both-sides fields.** The parity test compares a PATCH to its
  create model, so a field left uncapped on BOTH passes. Four were found and
  capped; a census would prove there are no more.
- **The service layer is uncapped where the route is capped.** `create_task`
  and `create_milestone` bound only non-emptiness, so the agent and MCP paths
  write unbounded LLM-authored titles that the now-capped PATCH route then
  refuses — a row the system wrote that its own UI cannot edit. Either cap at
  the service, or have the route accept what the service already stored.

## Self-serve UX (from the 2026-07-24 fresh-user review)

Sized S or M by that review. Items 6 and 8 of the original list are gone. The
engagement-close conclusion select shipped (`app/dashboard/page.tsx`). The
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
7. [S] Browse hides done tasks entirely (`status !== "done"`), so a finished
   task is reachable only if it was COMMITTED to a week — Health's week plan
   is the one surface that lists done work. A done filter on Browse, or a
   "recently shipped" strip that does not depend on the commitment line.
8. [S] A mistyped task is permanent. `docs/CORRECTIONS.md` rule 2 says
   records that carry history get a terminal state instead of a delete, and
   Task already has `done` — so this is not a contract gap. It is an
   ergonomics one: nothing distinguishes "finished" from "never should have
   existed", which is why demo and validation rows accumulate. A `void`
   disposition, or accept it and say so in CORRECTIONS.

## Manager and workflow (from the 2026-07-25 ideation run)

C1 (week rituals) and P2 (absences) shipped. P5 shipped its web half — My Day
prefills a standup suggestion from your activity — and its CLI half did not:
`skein standup` has no `--draft` flag and never reads the suggestion. These
did not ship:

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
  `events.attendees` for names outside the team. A morning rule attaches the
  brief to meetings with external attendees.
- **C5 decision links and cascade** — a `decision_links` table, populated at
  record time and by scanning references. Consumed by scoped context packs,
  supersede notifications, and handoffs.
- **P1 weekly planning cockpit** — `GET /api/planning` and one page in meeting
  order: kept-% and carryover, then capacity against the draft with conflicts
  inline, then the intake queue, then stale decisions, then one-click commit.
  Pure composition of endpoints that exist.
- **P3 shared 1:1 loop** — a pairwise-visible agenda scope and a `1:1:` capture
  prefix. Deferred until reports ask for it. The visibility tier it waited on
  is now designed in `docs/VISIBILITY.md` (`private` / `crew` / `workspace`
  plus crew membership, un-shipped); a pairwise scope is a fourth case that
  design does not cover, so it still gets designed at the time.
- **P4 interrupt ledger** — derived, with no user action: a task created after
  the week line locked and finished in the same week counts as unplanned. The
  team-level ratio goes in flow metrics and the readout, with a findings rule.

## Agent layer (2026-07-25)

A1 (delegation work loop) and A2 (system-filed authority proposals) shipped.

- **A3 gated agent morning sweep** — SHIPPED 2026-08-08 in a different shape:
  `services/agent_runner.py::sweep` notifies each delegated task's SPONSOR
  rather than filing `nudge` proposals. The proposal-entity design was
  dropped because the sweep's output has one accountable reader by
  construction (the sponsor), and a proposal nobody must approve is a
  notification with extra steps. Deterministic and keyless, as designed.
- **A4 agent-to-agent handoff** — a `handoff_task` tool that keeps the sponsor
  immutable. The hop is itself a proposal the sponsor approves.
- **A5 proposal bundles** — `bundle_id` and `seq` on `pending_changes`, with
  symbolic references (`$1.id`) resolved at apply time, per-bundle approval
  with a per-step untick, and an atomic apply. **Still deferred, and the
  trigger is now precise:** the unattended runner shipped 2026-08-08 and is
  OFF by default, so no deployment has yet produced a week of unattended
  proposals. Build this when one has, and shape the bundle around what that
  week actually filed. Its first named consumer stays the flock synthesis
  step, which is built with no tools and therefore cannot propose anything.
- Rejected proposals nag agent inboxes forever. An `acked_at` column ends it.
- Notify-tier writes link to an empty `/review`.
- The review registry has no registration-time assertion on apply-handler
  signatures — a mismatched handler surfaces at apply time as a caught
  runtime error, not at startup. Assert the signatures when the registry is
  built, or record the runtime guard as the accepted answer.

## Developer loop (2026-07-25)

D1 (`skein review`/`inbox`/`answer`/`worklog`) shipped, without the proposed
`--all-from <agent>` batch flag.

- **P5's CLI half** — `skein standup --draft` prefills from the same
  suggestion My Day shows (`GET /api/briefing` carries it). Own-data-to-self
  only.

- **D2 attention count in the shell prompt** — `skein attention --porcelain`
  reads a 60-second cache at mode 0600, never blocks and never errors.
  `skein install-prompt` writes the starship or PS1 snippet, on the
  `install-hooks` precedent.
- **D3 branch-aware git flow** — `skein task start 42` makes the branch
  `task/42-slug` and sets in_progress. A prepare-commit-msg hook injects the
  trailer from the branch name. `skein pr-body` composes task, engagement pack
  and commits for `gh pr create`.
- **D4 MCP mid-task parity** — `claim`, `report` and `submit` landed.
  `update_task`, `answer_question`, `resolve_blocker`, `ask` and `week` did
  not. Review approval over MCP stays deliberately absent, because an agent
  must not launder its own proposal.
- **D5 offline capture outbox** — a JSONL outbox with an idempotency key that
  auto-flushes on any successful command, plus `my-day --cached`.
- **F6** CLI argument grammar normalization. **F7** `skein context --engagement`.
  **F8** `skein ask`.

## Ops (from the 2026-07-24 architecture review)

- **Runner isolation** — move CI to an ephemeral sandboxed host before ever
  re-adding a pull_request trigger; consider rootless docker for the runner.
  Ops work on the runner host, not a repo change.
- **Proxy-aware client addresses** — `request.client.host` behind the
  OpenShift router is the router pod, so every external caller shares one
  address-keyed bucket (`signin`, `forge_addr`): an unsigned flooder can
  starve the real forge's deliveries, and Gitea does not auto-retry. Needs
  trusted-proxy `X-Forwarded-For` handling (uvicorn `--proxy-headers` +
  `--forwarded-allow-ips`, or middleware) before the prod deploy — flagged
  by the 2026-08-03 net-state review.

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

These are not features. Each one needs a decision.

- **Time zone.** DECIDED and shipped 2026-08-08: `SKEIN_TZ` takes an IANA
  Region/City name (the deployment runs `America/New_York`). The scheduler
  fires in that zone, `db.today()` is the team day, and storage stays UTC.
  A fixed-offset key (`EST`) is refused with its reason, and a host with no
  tzdata degrades to UTC and says so on `/health`.
  **One open deploy note, not a build:** daily and weekly job claim keys
  (`db.claim_job`) changed from the UTC day to the team day, and old keys are
  never reconciled. Deploying between 20:00 and 24:00 local can therefore
  skip exactly one run of a daily job whose next-day key was already claimed
  by that evening's UTC-keyed run. Deploy outside that window, or accept one
  missed digest. `retention-prune`'s month key moved with it, so a zone more
  than 4 hours east of UTC no longer skips a whole month.
- **The OIDC and API-key identity bridge** — DECIDED and shipped 2026-08-02:
  `SKEIN_AUTH_MODE` (`trusted-header | api-key | oidc`), in-process JWT
  validation in `app/oidc.py`, and the `AdminUser` split (`SKEIN_ADMINS` /
  `SKEIN_OIDC_ADMIN_GROUP`). The browser sign-in landed the same day:
  authorization code + PKCE in `frontend/lib/auth.ts`, `/auth/callback`,
  and `GET /api/auth/config` + `POST /api/auth/token`. Nothing is left of
  this item. Open follow-ons, none blocking: RP-initiated logout (sign-out
  is local only, so the IdP session survives), and refresh-token rotation
  if the deployment's IdP issues rotating tokens.
- **`docs/FEATURES.md` claims person-level data never judges the past, and
  `services/portfolio.py` still returns `wip_by_person`.** Narrow the claim or
  aggregate the display. Leaving both is the only wrong answer.
  **Half-settled 2026-08-08.** The egress half shipped: the exec readout
  writes an aggregate WIP line (`readout.py::_wip_summary`) and passes
  `name_assignees=False`, which drops the one `engagement_health` receipt
  that named a person against past inactivity. The rule applied was
  "names on a planning surface that has a viewer, totals in anything built
  to be forwarded". **Still open, and it is the wider door:** the agent tool
  `get_flow_metrics` (`tools/portfolio.py`, in `ALL_TOOLS`, so every persona
  holds it) returns raw `wip_by_person` PLUS `stale_wip` with titles and
  assignees, and an agent's reply is text a manager pastes anywhere. Decide
  whether a model reply counts as egress. If it does, the same
  `name_assignees` treatment applies there and to the MCP twin.
- **`promised:` audience is ambiguous at capture time.**
- **The Slack `fb:` refusal is documented but not stated in the Slack copy.**
  The code fails closed, so this is a documentation gap only.
- **If HMAC is ever added to the activity chain** — changing the preimage
  invalidates every existing chain AND contradicts the append-only anchor
  log. Plan it as a logged genesis reset, never a migration.

## Insights & usage (from the 2026-08-02 buzz review)

- **Per-rule `enabled` flag for findings** — silence a noisy rule by config
  rather than a deploy.
- **A UI surface for `/api/usage`** — the budget finding points people at a
  raw JSON endpoint; engagement costs belong next to engagement health on
  /portfolio.

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

## From the PM Review (2026-08-03)

K1 (forge webhook), K2 (short-id fast path) and K3 (@mentions) shipped
2026-08-03. The open question K1 carried is answered: the webhook
signature is a door, so `verify_forge_signature` lives in
`routes/deps.py` beside the others. Still deferred, and still for the
stated reason: outbound sync and echo suppression (Skein writes nothing
back, so there is no echo), and comment import (`origin` has no
external-author value — that provenance decision must be made
deliberately, not implied by an importer). Issue events move nothing: a
forge issue is a second place to track the work, which is the thing
Skein exists to stop.

- **K4 `?task=` side peek** — a linkable, back-button-safe task panel.
  Named consumers exist today: `/ask` citations, attention items, and
  activity rows all reference tasks with nowhere to land.

## From the product-gap review (2026-08-08)

Three commissioned perspectives — manager, IC, and the agent layer as a
multi-agent system — reviewed the shipped product against this backlog.
Transcripts and rankings: `docs/reviews/2026-08-08-product-gaps.md`. The
shared diagnosis: the backend runs ahead of its surfaces — search,
usage, the worklog, and forecast calibration are computed and never
delivered to where a person looks.

Promotions of items that already live above, with the review's reasons:

- **K4** ranks first of everything: briefing attention items, `/ask`
  citations, and activity rows all name a row and land at the top of a
  thirteen-section page. G9 and the delegate affordance land on the
  panel it adds.
- **Self-serve 1 and F8 together**: one nav input, a `?` prefix flips
  search to `/ask`. Neither endpoint has a single frontend consumer.
- **Self-serve 3**, with G9: the Agents empty state advertises
  delegation and no UI does it, in either direction.
- **Self-serve 7**, the strip variant: a merge closes a task and Browse
  hides it the same second — the reward moment deletes its own receipt.
- **P5's CLI half** and **D2**, with G8 as the browser half of D2.
- **P1** as the frame for the manager items: the weekly ritual is the
  product's spine and has no room built for it. G1, G2, G10, C2's
  chaser output and the usage card (G7's UI half) are cards inside it,
  and building them first means bolting more cards onto an already
  six-card `/portfolio`.
- **C2** and **P4** as-is.
- **A3** as the deterministic core of G5.
- **A5**, with the flock synthesis step as its first named consumer: a
  flock ends in N essays because the merge agent is built with no
  tools, and a bundle is the missing work-shaped output. Its deferral
  condition — "until the simpler pieces prove out" — is met.
- **Evidence pack** (cut table): trigger effectively met — the first
  real promotion decision would stand on counts alone. Enters as the
  second half of G6.

New items:

- **G1 health snapshots and the readout delta** — R/Y/G is computed at
  request time and discarded (`portfolio.py::engagement_health`), so a
  readout cannot say "newly yellow". A daily snapshot job on the
  `forecast-snapshot` precedent, plus a "since last readout" section:
  newly red or yellow, newly green, newly shipped. Deterministic SQL. [M]
- **G2 forecast calibration reader** — `snapshot_forecasts` promises
  "measured against actuals later" in its own docstring and nothing
  reads the table. One query joining snapshots to milestone
  `completed_at`; a hit-rate and median-error line on `/insights`
  beside MTTR — medians over means, n shown, verdicts withheld under
  small n, the house style. [S–M]
- **G3 agent worklog read-back** — `report_progress` writes the
  continuity record for multi-day delegated work and no tool in
  `ALL_TOOLS` reads it, so on day 3 an agent restarts from the task
  title. A `read_worklog` tool or worklog rows inlined into
  `my_agent_inbox`, plus MCP parity. Reshapes D4: the read side, not
  only the write side. [S]
- **G4 per-turn budget and circuit breaker** — the head agent's turn
  has no total deadline, cycle cap, or token ceiling; only flock
  members and consults carry the 180s member deadline, and the monthly
  budget finding reports without refusing. A per-turn cycle/deadline
  cap plus a per-agent-per-day spend ceiling, checked where spend is
  already seen (the gate and `_log_usage`), refuse-and-report. Hard
  prerequisite for G5. [M]
- **G5 budgeted worker sweep** — nothing ever runs an agent turn
  unattended: the JOBS registry drives 16 deterministic jobs and zero
  agent turns, so the inbox is an ambient wake-up view with no waker.
  One bounded turn per agent with open delegated work per day, against
  `my_agent_inbox` and the per-engagement context pack (built for
  exactly this), under G4's ceilings; every write still a gated
  proposal. A3's rule-based sweep is the keyless core; the agent run is
  the LLM upgrade. Also the only realistic source of the verdict
  volume the trust flywheel currently starves without. [L]
- **G6 trust-flywheel honesty, then the evidence pack** — with the
  review gate off, review-level writes apply with no verdict; under
  weak identity, verdicts never count toward streaks. The trust card
  reads as "no data" where the truth is "cannot produce data" — state
  the dependency on `/agents`. Then the evidence pack: a promotion
  decision needs what was approved, not how many times. [S, then M]
- **G7 turn-anomaly findings rules, with the usage UI** — reshapes "a
  UI surface for `/api/usage`" above: same tables, one pass.
  Deterministic rules over `usage_log` and `flock_traces` (pathological
  cycle count in one turn, member failure streak, proposal-rejection
  streak outside the weekly authority review), and the usage page
  presents unpriced calls as unpriced. Per-model rows make the Settings
  model menu comparative. [S–M]
- **G8 browser attention signal** — the immediate tier is a My Day
  card, so "immediate" means "next visit". A document-title count or
  nav dot fed from the briefing the nav already polls; own-data-only,
  so anti-surveillance-clean. D2 is the terminal half. [S]
- **G9 worklog panel** — `GET /api/tasks/{id}/worklog` is
  sponsor-readable before the verdict by design and appears nowhere in
  the frontend. Lands on K4's panel. [S]
- **G10 forward capacity view** — `allocation_conflicts` and `what_if`
  bind both window predicates to today, so a request accepted today can
  be a conflict on its real start date and Skein notices when the date
  arrives. A per-week table over the next 4–8 weeks from `allocations`
  plus `absences` — a table, never a Gantt, honoring the existing
  refusal. Person-level data planning the future is the permitted
  direction. [M]

**Shipped 2026-08-08 (G4, G5, A3):** `services/agent_runner.py` is the motor.
`sweep()` is deterministic and keyless — it tells each sponsor what their
delegated work is doing, and is the whole feature on `mock`. `run()` adds one
bounded unattended turn per allowlisted agent per day, under a daily token
ceiling (`SKEIN_AGENT_DAILY_TOKENS`), a wall clock
(`SKEIN_AGENT_RUN_SECONDS`), a per-(agent, team-day) claim key, and the
`forbidden` kill switch. `SKEIN_AGENT_RUNNER` is an allowlist and is empty by
default, so nothing wakes an agent until an operator names it. Every write
still passes the same gate a chat turn's writes pass.

**G6's evidence pack stays open**, and its trigger is unchanged: it is worth
building when a real promotion decision is near, and that needs verdict volume
the runner has not produced yet. G6's honesty half DID ship — `/agents` now
states when trust cannot accrue at all (`delegation.trust_blocked`).

Suggested order: one pass of the S items first (G3, G6's honesty half,
G8, P5's CLI half, self-serve 7, the readout decision below), then
three arcs in parallel or in sequence — deliver-what-is-computed (K4,
then nav search and `/ask`, then G7), the manager frame (P1 hosting G1,
G2, G10, C2, P4), and the agent motor (G4, then G5), which is the
largest bet and the one that makes "agents as teammates" a description
instead of a promise.

## Cut, with re-entry triggers

Deliberate refusals, from the 2026-07-24 synthesis and the 2026-08-02 buzz
review. Each names the condition that reopens it.

| Cut | Trigger to revisit |
|---|---|
| Shadow authority level | Proposal volume overwhelms the review queue. The review queue *is* shadow mode today. |
| `entity_links` table, registry, thread view | A 4th typed relationship with a named consumer. |
| Attention budget, ack states, dedupe keys | Real duplicate-notification pain. Findings already dedupe weekly. |
| Trust profile partitions by model version | A model swap causes a problem that review stats did not catch. |
| Auto-quiet findings rules | The rule count or the noise grows beyond hand-tending. The maintainer retires rules at season end today. |
| Stakeholder signed status pages | Real stakeholder demand AND real auth. Then build it as a push-generated static artifact, never by exposing the app. |
| Coordination-debt and closed-loop-rate metrics registry | Multi-team scale. |
| Playbooks 2.0, delegation contracts, evidence pack, outbox, capability broker | Deferred. Specs are in `docs/reviews/2026-07-24-agent-sol.md`. |
| Employee private-prep sections | Refused until the journal separate-store pattern is proven. |
| Post-compaction context re-injection | A real long-chat complaint — `summarize` plus the scoped context pack cover it today. If built: subclass `ConversationManager.reduce_context()`, re-inject the per-engagement pack once per session with a token ceiling to avoid a trim/re-inject loop. |
| Honest tombstones for deleted tasks/chats | A deletion dispute the hash-chained ledger and the loud feed row do not settle — today they meet the need more strongly than a tombstone would. Private 1:1 notes and decision supersession keep their existing tombstones. |
| Presence as a lease, not a flag | Only if a boolean live-dot is ever added. `last seen` timestamps are honest forever; a lease fixes a live-dot outliving a dead process, and no such dot exists. |
