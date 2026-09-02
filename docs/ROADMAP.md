# Feature Roadmap — Skein

> Shipped work is documented in `docs/FEATURES.md`. The ideation
> transcripts behind rounds 1–3 (all shipped or deliberately skipped) and
> the 2026-07-24 backlog burn-down are archived in `docs/reviews/`.
> **This file holds only un-shipped work**: the open backlog, the
> decisions still to make, and the refusals with their re-entry triggers.

## Posture (2026-08-21): portfolio frozen, agent loop funded

The portfolio pillar (engagements, health, forecasts, intake, readouts) is
complete enough to freeze: every read surface has its write half, and what
remains below is refinement. The agent pillar — the reason Skein is not an
ordinary PM tool — has never completed one real trust loop. Until it has run
for a season with the review gate on (the default since 2026-08-21):

- No new portfolio surfaces. The portfolio items below stay listed and stay
  un-worked; refinement resumes after the season.
- Investment order: delegation contracts from the deferred specs.
  (The first slice shipped 2026-08-21: `acceptance_criteria` and
  `check_in_at` on `delegate_task`, surfaced in the agent inbox and the
  acceptance verdict, with the sweep nagging a missed check-in. The
  full contract entity stays in the cut table below.)
  (The durable first-wake queue shipped 2026-08-24: a human delegation now
  queues one bounded agent turn, while worklog and sponsor acceptance remain
  the response path.)
  (Engagement-level cost attribution shipped with this posture note: the
  agent runner attributes a turn when every open delegated task resolves to
  one engagement, and chat turns keep the retroactive thread link.)
- Exit trigger, cut-table style: if after one season with the gate on the
  trust rows, promotions, and delegations still read zero, that is the
  evidence to NARROW the agent surface instead — the decision is then a
  read, not a debate.

# Open backlog (consolidated 2026-08-02)

This is the only home for un-shipped work. Before this date the backlog was in
four places: this file, the 2026-07-24 implementation plan (now
`docs/reviews/2026-07-24-implementation-plan.md`), and two review transcripts.
Every item below was checked against the code on 2026-08-02, and again on
2026-08-09 after the product-gap ships. The items that were already built were
dropped, not carried forward.

The ID tags are kept because source comments cite them. `TD1` and `TP5` and
their neighbours are named in `frontend/app/globals.css`, `frontend/lib/theme.ts`,
and `frontend/lib/whimsy.ts`.

## Extension surfaces to add only on demand

The first workplace extension API ships routes, jobs, policy, identity,
governed tools, specialists, events, isolated data, workflows, frontend
navigation, and manager dashboard cards. These additions remain deferred until
a real extension needs them:

- Frontend detail panels, forms, general actions, notification renderers,
  theme packages, and terminology packages
- Durable pause and resume for long-running workflow approvals (the strands
  SDK now ships an interrupt primitive — `strands.interrupt`, stop reason
  `interrupt` — which is the likely mechanism)
- Public commands for core entities other than task, blocker, and promise
  work (0.2.2 shipped those three)
- A supported alternative core database adapter
- Remote or untrusted extension execution
- A startup and shutdown lifecycle hook (removed pre-release: no consumer,
  and unlike routes, jobs, events, and migrations it ran trusted code with
  no declared identity, policy action, or timeout)
- Module dependency ordering (`SkeinModule.requires`, removed pre-release:
  composition order is core first, then the allowlist order)
- Durable per-subscriber event targets. Delivery now survives a composition
  with ZERO subscribers (2026-08-13), but an event whose type matches one
  composed extension and not a second, disabled one is still finalized
  without the disabled subscriber. Durable targets snapshot the intended
  subscribers at emission; they also carry per-target leases, backoff, and
  an operator redrive.
- A field-guide contribution slot, so a private extension's user-visible
  navigation and cards can ship a discovery card the way every core
  feature must (`backend/fieldguide/knots.yaml`). Version 1 has no such
  slot, and a core knot for a surface only composed deployments have would
  be wrong in the default app.

Do not add these as empty slots. Add one narrow contract with one core use and
one private-package use when the requirement appears.

## Core convergence, deferred from the 0.2.2 release

Both were planned for 0.2.2 and dropped: neither buys a workplace anything it
cannot already do, and each carries a risk that wants its own release.

- **Stock tools on the `ToolContribution` harness.** Core tools bypass it, so
  they carry no pydantic schemas, no per-tool timeout, and a different
  review-proposal shape, which leaves two review disciplines to keep aligned.
  Migrate the four `SPECIALIZED_WRITE_TOOLS` first: they already run through
  `GovernedCoreTool` with review proposals. Pending old-shape reviews must
  stay approvable, so read both shapes at verdict time the way migration
  017's contract-version field already does, and seed that test from a
  running older instance rather than a hand-written row. Model-facing tool
  names must not change: persona allowlists and session history reference
  them. Do it a tranche at a time, whenever a stock tool is next touched for
  another reason.
- **Notification delivery through the outbox.** Channels are hardcoded to
  in-app rows and one Slack webhook, so a second channel is a core change.
  The design tension to resolve first: an outbox envelope is content-free by
  contract and a delivery channel needs the body. The likely shape is a
  notification event carrying source references and the saved policy context,
  with the subscriber reading the body under its own service identity, and
  core's own Slack post becoming the first subscriber. Settle that before
  writing code.

## Bounded-input census (from the 2026-08-03 holistic review)

The rate-cap ratchet and the unbounded list reads shipped 2026-08-09
(`tests/test_bounded_routes.py`). What is left:

- **Uncapped-on-both-sides fields.** The parity test compares a PATCH to its
  create model, so a field left uncapped on BOTH passes. Four were found and
  capped; a census would prove there are no more. This is the one bound of
  the four that still has no structural test.
- **The service layer is uncapped where the route is capped, for five more
  entities.** `work.py` (task and milestone title/description) and
  `intake.py` (request detail) took their bounds 2026-08-09, and
  `review.propose_change` refuses a proposal those two would reject at apply
  time. The same asymmetry is still live for **blockers, questions,
  decisions, notes and promises**: `raise_blocker` bounds nothing while
  `BlockerEditIn.detail` caps at 4000, so `PATCH /api/blockers/{id}` refuses
  to edit a blocker quick capture itself filed. Bound them in the service and
  add them to `review.unappliable`, which is keyed by entity and today knows
  three.
- **Four routes the ratchet exempts as UNCAPPED, with their cost named.**
  They are listed in `test_bounded_routes.py::EXEMPT` so nothing new can join
  them silently, and each wants a cap or a written reason it does not need
  one: `POST /api/findings/run` (a full rule-engine sweep on demand),
  `POST /api/context-pack/publish` (rebuilds and versions a pack),
  `POST /api/playbooks/instantiate` (writes an engagement, milestones and
  tasks), `POST /api/intake/{request_id}/what-if` (a projection, no write).

## Self-serve UX (from the 2026-07-24 fresh-user review)

- **The weekly check-in tally never fills** [S, decision first] — months of
  simulated use left it at "No votes yet", and real months would too: the
  👍/👎 pair lives on My Day where nothing prompts it at a moment that
  matters. Decide the prompt (the Friday close-out ritual is the natural
  one — the week is over and the digest already reaches everyone) before
  building anything, or retire the card with the season.

Sized S or M by that review. Items 6 and 8 of the original list are gone. The
engagement-close conclusion select shipped (`app/dashboard/page.tsx`). The
portfolio commitments card now names its two audiences in the card title.
Items 1 (nav search), 3 (delegate control in the task peek), 5 (the
allocation form on the Capacity card), 7 (the Recently shipped strip), 8 (the
task `void` status), and 9 (phone-width search results) shipped and were
dropped. The remaining numbers stay as the review transcripts cite them.

6. [S] `?` tooltips: ISO week format on the commitment card, season definition
   on the pulse banner. (The origin glossary this item also named shipped
   2026-08-09 as the origin chip on each Approvals row.)
## Manager and workflow (from the 2026-07-25 ideation run)

Playbooks learn from ONE engagement: the plan is snapshot at kickoff, the
close diffs planned against actual, and an approved lesson reaches the
next kickoff of that class. Three pieces stayed behind, and the first is
what makes the other two worth anything:

- **Playbook variance across engagements** [S] — the same milestone
  slipping in three incidents running is a fact about the TEMPLATE, not
  about any of the three, and nothing totals it. One close teaches
  almost nothing; the third one is the argument. `close_out_diff` is the
  seam and `lessons.project_class` already groups them.
- **The kickoff half of the loop has no reader** [S] —
  `playbooks._instantiate` attaches past-class lessons as a note under
  `kickoff-lessons-<engagement>`, and nothing points at it: `/plan`
  reports counts only, `instantiate` does not return it, and playbooks
  have no UI. The lesson arrives and no one is standing there.
- **Nothing tracks whether an approved lesson reached the YAML** [S] —
  the drafted recommendation names `playbooks/<slug>.yaml` and a human
  edits it by hand, which is right, because playbooks are code. But the
  review's own measure for R6 was "lessons per close AND playbook YAML
  edits per quarter", and the second half is unmeasurable in-product. A
  findings rule over approved close-out lessons per class, against the
  playbook file's mtime, is the smallest honest answer.

Also, and smaller: a close-out proposal files under
`proposed_by="system"` with `origin="agent"`. It is deterministic SQL
arithmetic, which is neither of the two things the origin chip teaches a
reviewer to read (`docs/FEATURES.md`), and `_trust_by_pair`'s `is_agent`
filter drops it — so the R3 record chip renders nothing beside an
agent-labelled row. Either the glossary grows a third shape or the draft
stops claiming to be an agent.

- **C5 decision links and cascade** — a `decision_links` table, populated at
  record time and by scanning references. Consumed by scoped context packs,
  supersede notifications, and handoffs.
- **P3 shared 1:1 loop** — a pairwise-visible agenda scope and a `1:1:` capture
  prefix. Deferred until reports ask for it. The visibility tier it waited on
  has shipped (`docs/VISIBILITY.md`: `private` / `crew` / `workspace` plus
  crew membership), but a pairwise scope is a fourth case that design does
  not cover, so it still gets designed at the time.

## Agent layer (2026-07-25)

A1 (delegation work loop), A2 (system-filed authority proposals) and A3 (the
morning sweep, which notifies each delegated task's sponsor rather than filing
`nudge` proposals) shipped.

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
- Rejected proposals nag agent inboxes forever. Superseded completion rework
  now drops out — a rejected `task_completion` disappears once a later one for
  the same task is approved — so what remains is every other entity, and every
  rejection nobody re-submits. An `acked_at` column ends those.
- Notify-tier writes link to an empty `/review`.
- The review registry has no registration-time assertion on apply-handler
  signatures — a mismatched handler surfaces at apply time as a caught
  runtime error, not at startup. Assert the signatures when the registry is
  built, or record the runtime guard as the accepted answer.

## Developer loop (2026-07-25)

D1 (`skein review`/`inbox`/`answer`/`worklog`) shipped, without the proposed
`--all-from <agent>` batch flag.

- **D4 MCP parity** — landed through `week` on 2026-09-02. Review approval
  over MCP stays deliberately absent, because an agent must not launder its
  own proposal.
- **F6** CLI argument grammar normalization. The commands that take an
  action word still validate their own combinations by hand in `main()`.

## Ops (from the 2026-07-24 architecture review)

- **Runner isolation** — move CI to an ephemeral sandboxed host before ever
  re-adding a pull_request trigger; consider rootless docker for the runner.
  Ops work on the runner host, not a repo change. (The proxy-aware client
  addresses item that lived beside this one shipped: `SKEIN_TRUST_PROXY_HOPS`.)
- **OIDC BFF session before production** — replace browser `localStorage`
  access and refresh tokens with a server-held session and an `HttpOnly`,
  `Secure`, `SameSite` cookie. Add CSRF protection to cookie-authenticated
  writes. Keep personal API keys as the automation path. Remove personal-key
  persistence and fallback from the OIDC browser. If browser key sign-in stays,
  exchange the key once for the same server-held session. Browser JavaScript
  must receive identity metadata, never provider tokens or personal keys.

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
- **W5 the Bolt** — the selvage permanently gains one repeat per ship. (A
  transient 700ms celebration shipped; the permanent repeat did not.)
- **W6 epitaph pool** for blocker funerals, seeded by blocker id.
- **W7 loose threads** — woven 404 and error pages. There is no
  `not-found.tsx` at all today.

## Decisions needed, not builds (2026-07-25)

These are not features. Each one needs a decision. The three that are
settled (time zone, the auth bridge, `wip_by_person` egress) were dropped
2026-08-09 — `docs/FEATURES.md` records what shipped. What survives of them:

- **Time-zone deploy note, not a build:** daily and weekly job claim keys
  (`db.claim_job`) changed from the UTC day to the team day, and old keys are
  never reconciled. Deploying between 20:00 and 24:00 local can therefore
  skip exactly one run of a daily job whose next-day key was already claimed
  by that evening's UTC-keyed run. Deploy outside that window, or accept one
  missed digest.
- **Auth follow-ons, none blocking:** RP-initiated logout (sign-out is local
  only, so the IdP session survives), and refresh-token rotation if the
  deployment's IdP issues rotating tokens.
- **`promised:` audience is ambiguous at capture time.**
- **The Slack `fb:` refusal is documented but not stated in the Slack copy.**
  The code fails closed, so this is a documentation gap only.
- **If HMAC is ever added to the activity chain** — changing the preimage
  invalidates every existing chain AND contradicts the append-only anchor
  log. Plan it as a logged genesis reset, never a migration.

## Insights & usage (from the 2026-08-02 buzz review)

- **Per-rule `enabled` flag for findings** — silence a noisy rule by config
  rather than a deploy. (The usage-UI item that lived beside this one
  shipped: the AI usage and estimated cost card on Work → Health.)
- **job_stale rows dominate the findings feed** [S, decision first] — five
  stale jobs render five high-severity rows at the top of /insights and push
  team findings below the fold, while `digest_findings` already collapses
  job_stale to one line for the digest. The tension is real on both sides:
  /insights says it measures the system, so per-job detail is its job — but
  five red rows are one condition (the scheduler is not running). Decide
  whether the feed folds them like the digest or the OperationsCard link
  absorbs the detail, then build the one chosen.

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
Skein exists to stop. (K4, the `?task=` side peek this section carried,
shipped 2026-08-08.)

## From the product-gap review (2026-08-08)

Three commissioned perspectives — manager, IC, and the agent layer as a
multi-agent system — reviewed the shipped product against this backlog.
Transcripts and rankings: `docs/reviews/2026-08-08-product-gaps.md`,
which is also the definition site for G1–G10. The shared diagnosis: the
backend ran ahead of its surfaces — search, usage, the worklog, and
forecast calibration were computed and never delivered to where a
person looks.

**Nearly all of it shipped 2026-08-08/09**, documented in
`docs/FEATURES.md`: the task side peek (K4) carrying the worklog panel
(G9) and the delegate control (self-serve 3), the nav search box with
the `?` ask prefix (self-serve 1), health snapshots and the readout
delta (G1), the forecast calibration reader (G2), the agent worklog
read-back (G3), the tab-title attention count (G8), the forward
capacity view (G10), the planning cockpit (P1), the Recently shipped
strip (self-serve 7), `skein standup --draft` (P5), the interrupt
ratio in the cockpit (P4's ledger half), the turn-anomaly rules and
the AI usage and estimated cost card (G7), the unattended runner under its ceilings
(G4, G5, A3), and the trust-blocked line (G6's honesty half).

Still open from that review:

- **G6's evidence pack** — trigger unchanged: worth building when a
  real promotion decision is near, and that needs verdict volume the
  runner has not produced yet (`SKEIN_AGENT_RUNNER` is empty by
  default, so no deployment runs unattended turns). A promotion
  decision needs what was approved, not how many times. [M]

The suggested order this section carried (manager frame, then CLI,
then census) was revised by the 2026-08-09 product-strategy review —
the current order lives in that section below.

## From the product-strategy review (2026-08-09)

A three-lens review — developer, manager, and the product as a
human-and-AI operating system — of everything shipped. Transcript:
`docs/reviews/2026-08-09-product-review.md`, the definition site for
R1–R7. The diagnosis repeats 2026-08-08 in a smaller radius, plus one
new theme: computed value still fails to reach a reader in places, and
several loops stop at 80% — the trust flywheel has no flow, playbooks
never learn from their own engagements, and waiting-on edges give the
person typing them nothing back.

- **R8 runner wake reads: attention** [S] — the wake turn now reads
  `get_findings`, so an unattended agent sees the rules a chat turn does.
  `get_attention` is still absent, and deliberately: it resolves the
  REQUESTER from the turn's own viewer, and an unattended turn has no
  requester (`tools/portfolio.py` says so rather than returning an empty
  list). Wiring it needs a requester context on the runner first.
- **The per-task unblock count on My Day** [S] — the task peek and the
  cockpit card both show what finishing a task releases; My Day does not,
  so the number is absent from the one surface people open first. The
  `blockers.task_id` half of the original spec stays dropped on purpose —
  `services/work.py::_blocked_by` records why a blocker edge is not
  released work. My Day now leads with the week's commitment and marks it,
  which is the other half of the same complaint.

**Dogfooding.** Not a build, and blocking two triggers: put flow through
the trust loop in our own deployment — `SKEIN_AGENT_REVIEW=1`, real delegations
with real sponsors, one agent named in `SKEIN_AGENT_RUNNER`. A5 and
G6 both wait, by their own stated triggers, on the verdict volume this
produces.

That note is the binding item now. The proposer's record renders from
real verdicts, and in trusted-header mode no verdict counts
at all, so the surface is currently proving itself against an empty
table.

**Left behind by the 2026-08-09 arc, named where each belongs:** R8 and
the per-task unblock count above; playbook variance across engagements,
the unread kickoff half of that loop, and the untracked YAML edit under
"Manager and workflow"; F6 under "Developer
loop"; C4's morning rule under "Manager and workflow"; and the
uncapped-on-both-sides census bullet at the top of this file.

## Left by the pre-merge review (2026-08-09)

Named here because the rest of that review shipped and these did not.

- **The focus ring is marginal in dark mode** [XS] — `--thread-solid`
  against `--surface-raised` measures 2.90 to 2.99 on loom, ledger,
  hermes and phosphor, under the 3:1 floor for a non-text indicator.
  Against the page background it passes everywhere, so only the inner
  edge is short. `scripts/check_theme_contrast.py` sweeps that token
  under white text, not this pairing.
- **`PARTY_CAP` caps parties, not items** [XS] — one party carrying 600
  open threads still reaches the page height the cap was added to fix.

## From the live product study (2026-08-15)

Drained from the study transcripts in `docs/reviews/2026-08-15-product-study-*`
when they closed. Everything else in those files either shipped on
`proposed-features` or was refused there with a reason.

**Reachability — a bounded read with no route to the rest.** Each of these
stores rows that no surface can reach. They share a shape and are worth
sizing together, but they are not one build: the study refused a global
history center, so each surface keeps its own bound.

- **Unread notifications past the first 20** [S] — `services/briefing.py`
  reads `ORDER BY id DESC LIMIT 20` and My Day renders at most five groups.
  Retention prunes only READ rows older than 90 days, so an unread row past
  the window is stored with no count and no route to it. Advances when a live
  reader exceeds 20 unread rows.
- **The Insights findings feed** [S] — `services/insights.py` calls
  `list_findings(weeks=4, limit=50)` with no cursor. (The duplicate half
  shipped: a persisting condition now collapses to one row per (rule,
  subject) with a since-week, so the bound is spent on distinct conditions.)
- **The agent inbox rejection window** [S] — ten rows, and the superseded
  `task_completion` anti-join that shipped clears only one cause. See the
  `acked_at` item under "Agent layer" for the rest.
- **Divergent bounds across surfaces** [M, decision first] — reports, chats,
  findings, notifications, decisions, requests and agent notes each have a
  different reachability limit. Name one rule before building seven cursors.

**Named, blocked on a decision rather than on effort.**

- **Legacy and aggregate review-notification repair** [M] — migration 002
  types new proposal notifications, so pre-002 rows still sit beside their
  proposals untyped. Separately, a batch ingest summary keeps claiming
  "1 proposal awaiting review" after that proposal is rejected. Needs a typed
  batch identity and an idempotent repair, not a second filter.
- **The rest of the verified-trust contract** [S] — the shipped half stopped
  inferring a last verdict from a zero streak. Reviewer name, verdict time and
  identity strength on rejected inbox rows did not ship.

**Daily-surface ergonomics, each with study evidence.**

- **My Day active-task window** [S] — capped at 200 active tasks with no
  overflow count, and the sort carries no final task-id key, so equal-ranked
  rows order unstably between reads. The instability is the smaller half and
  the cheaper fix.
- **Task-panel editing, the second half** [S] — the panel edits status,
  priority, assignee, due date and `waiting_on`. Description, commitment
  week and visibility still have no path there, and visibility needs a
  transition rule before it gets one.
- **Browse task pagination** [M] — the whole open list still renders at
  once (~600 accessibility nodes on the seeded instance). The filter half
  shipped: one needle over title, #id, @assignee, status and priority.
- **Planning action placement** [S] — evidence and the control that
  resolves it sit on different pages for the health draft. The capacity form
  and the intervention queue's inline moves (assign, promise verdicts,
  reconfirm, resolve) shipped 2026-08-20.

## Cut, with re-entry triggers

Deliberate refusals, from the 2026-07-24 synthesis and the 2026-08-02 buzz
review. Each names the condition that reopens it.

| Cut | Trigger to revisit |
|---|---|
| Shadow authority level | Proposal volume overwhelms the review queue. The review queue *is* shadow mode today. |
| `entity_links` table, registry, thread view | A 4th typed relationship with a named consumer. |
| Attention budget, ack states, dedupe keys | Proposal rows now suppress notifications through a typed relation. Revisit the broader system when another repeated-notification class has measured cost. |
| Trust profile partitions by model version | A model swap causes a problem that review stats did not catch. |
| Auto-quiet findings rules | The rule count or the noise grows beyond hand-tending. The maintainer retires rules at season end today. |
| Stakeholder signed status pages | Real stakeholder demand AND real auth. Then build it as a push-generated static artifact, never by exposing the app. |
| Crew context-pack surfaces (UI mention, CLI flag, scheduled publish) | A crew actually asks for a pack scoped tighter than the workspace. The API works today (`?crew=`, membership-gated); only the surfaces were cut. |
| Coordination-debt and closed-loop-rate metrics registry | Multi-team scale. |
| Playbooks 2.0, delegation contracts (full entity), evidence pack, transactional outbox, capability broker | Deferred. Specs are in `docs/reviews/2026-07-24-agent-sol.md`. The contract's acceptance-criteria and check-in slice shipped 2026-08-21; the remaining fields (per-delegation budget, authority scope, escalation conditions) wait on a season of the shipped pair being used. |
| Employee private-prep sections | Refused until the journal separate-store pattern is proven. |
| Post-compaction context re-injection | A real long-chat complaint — `summarize` plus the scoped context pack cover it today. If built: subclass `ConversationManager.reduce_context()`, re-inject the per-engagement pack once per session with a token ceiling to avoid a trim/re-inject loop. |
| Honest tombstones for deleted tasks/chats | A deletion dispute the hash-chained ledger and the loud feed row do not settle — today they meet the need more strongly than a tombstone would. Private 1:1 notes and decision supersession keep their existing tombstones. |
| Presence as a lease, not a flag | Only if a boolean live-dot is ever added. `last seen` timestamps are honest forever; a lease fixes a live-dot outliving a dead process, and no such dot exists. |
| Requirement/Outcome record layer (2026-08-21 control-plane thesis) | A real engagement retro produces a traceability dispute — "which requirement did this work serve" — that `engagements.outcome`, milestones and decisions could not settle. A new entity costs seven gated registries; the dispute is the evidence it repays that. |
| Assumption records (statement, owner, verification plan, expiry) | An engagement fails on an assumption nobody wrote down, and the retro shows `kill_criteria` plus a dated decision could not have held it. |
| Acceptance checks + validation evidence as records | The evidence-gap findings rule (Insights backlog) fires often enough that teams act on it. The rule is the cheap probe; the records are the expensive answer. The display half shipped 2026-08-22: rows an `acceptance_criteria` names now resolve to their current status on the approval card. |
| Authority matrix split by action class (internal edit vs external send) | An agent holds `autonomous` on an entity where one verb is internal and another leaves Skein (mail, webhook, customer surface). Today no tool on a granted entity crosses that line — the split waits for the tool that does. |

## Deferred by the agent-discovery adoption (2026-08-30)

The adoption program refused each of these on purpose. The trigger column
is the evidence that funds the build — none of them is worth building early.

| Deferred item | Trigger |
|---|---|
| Context offloader on wake and allowlist paths | The storage backstop logs every tool result it truncates ("the context offloader did not ride this path", `agents/session_store.py`). A season of that line accumulating is the evidence. The build carries a documented exception to the WAKE_TOOLS contract, because the plugin registers its retrieval tool outside `build_agent`'s filtering. |
| Plaintext-tool-call detection in the turn guard | A keyless operator reports empty turns on a local or `openai_compatible` model — those models can emit tool calls as TEXT, and the turn guard's "Nothing was filed" receipt cannot name the cause. Detect via the known marker grammar and attribute it. Never repair or execute the parsed call: it skipped the model's own tool interface and every gate assumption downstream. |
| Token-budgeted context pack (`?budget=`) | An operator tunes `SKEIN_AGENT_RUN_TOKENS` — a per-run token ceiling is honest only when the wake prompt has a known size. Deterministic whole-section truncation in a declared priority order, reporting what was dropped. The refused version stays refused: no LLM summarizer inside the pack builder (keyless path). |
| Watch subscriptions on tasks | People ask to follow work they neither own nor are named in, more than once. Self-visible ONLY — no watcher list, no "N people watching" — or it fails the anti-surveillance rule. |
| Offload thresholds as admin tunables | An operator has a standing reason to change `SKEIN_OFFLOAD_RESULT_TOKENS` / `_PREVIEW_TOKENS` between deploys (the settings rule's question 4). Until then they stay env-only. |
| `wording.py` STE ring promoted to fatal | The warn count in `scripts/check_ste.py` stays at zero across a few releases (currently zero). Same staging the knots gate went through: warn until clean stays clean, then gate. |
| Live consult-quality eval | A deployment with a few weeks of real consults. The routing eval scores DESCRIPTIONS (TF-IDF), and the 2026-08-30 merge already showed the proxy's failure mode: a description tuned to the fixtures. Tier 1 is deterministic assertions over live traces — consult fired, right slug, count within the turn budget, writes landed as proposals — with cases accruing from the feedback corpus (`kind=finding`), not cold authoring. An LLM judge is NOT the gate: it would add cost, nondeterminism, and a second Goodhart surface to cover only the fuzzy remainder ("framed, not repeated"). At most it becomes an ad-hoc triage labeler once the corpus outgrows human reading. |

## Remote MCP servers (2026-09-01)

The personal tier shipped 2026-09-01 (`services/mcp_servers.py`, Settings →
Connections). The tiers differ in trust, not only in scope: the system tier
is operator-classified, the personal tier is annotation-classified with every
write under review. What is left:

- **Team tier** — an administrator or crew steward registers a server with
  `scope = 'team'` in the same `mcp_servers` table, scoped to one crew or to
  everyone. The credential is a Secret key name (`auth_token_env`) or a
  sealed token. Metadata is derived as for the personal tier, and an
  administrator can tighten a tool's effect or risk, never loosen it. The
  steward check is `crews.assert_steward` inside the write's transaction,
  the route answers strong plus named admin, and the personal-write review
  rule relaxes to the policy engine's verdict because a steward classified
  the server on purpose. Trigger: a second person asks for the same server
  a colleague already registered.
- **Standing approval for one personal write tool** — a reviewer approves
  each personal write today. Once a tool has a run of approvals, a reviewer
  can grant it a bounded standing approval (a count or a date), recorded
  as an authority row keyed on `server_id:tool`, and the review rule reads
  it. Trigger: the same personal tool reviewed and approved ten times.
