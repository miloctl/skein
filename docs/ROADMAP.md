# Feature Roadmap — Skein

> Shipped work is documented in `docs/FEATURES.md`. The ideation
> transcripts behind rounds 1–3 (all shipped or deliberately skipped) and
> the 2026-07-24 backlog burn-down are archived in `docs/reviews/`.
> **This file holds only un-shipped work**: the open backlog, the
> decisions still to make, and the refusals with their re-entry triggers.

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
- **Unbounded lists.** `chat_threads.list_threads`, `get_messages`,
  `api_keys.list_keys` and `list_all_keys` have no LIMIT.
  (`private_notes.list_notes` got its LIMIT on the `if person:` branch.)
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
Items 1 (nav search), 3 (delegate control in the task peek) and 7 (the
Recently shipped strip) shipped 2026-08-08/09 and were dropped. The numbers
below stay as the review transcripts cite them.

2. [M] What-if staffing button on scored intake rows. This closes the dangling
   "shown in staffing what-ifs" reference in Settings.
4. [M] Generate-handoff button on closing engagements and closed engagements.
5. [S] Allocation inline form on the Capacity card, or an honest empty state.
6. [S] `?` tooltips: ISO week format on the commitment card, season definition
   on the pulse banner, origin glossary beside Review.
8. [S] A mistyped task is permanent. `docs/CORRECTIONS.md` rule 2 says
   records that carry history get a terminal state instead of a delete, and
   Task already has `done` — so this is not a contract gap. It is an
   ergonomics one: nothing distinguishes "finished" from "never should have
   existed", which is why demo and validation rows accumulate. A `void`
   disposition, or accept it and say so in CORRECTIONS.

## Manager and workflow (from the 2026-07-25 ideation run)

C1 (week rituals), P2 (absences), P5 (both halves — the My Day prefill and
`skein standup --draft`), and P1 (the planning cockpit, `/planning`) shipped.
These did not ship:

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
- **P3 shared 1:1 loop** — a pairwise-visible agenda scope and a `1:1:` capture
  prefix. Deferred until reports ask for it. The visibility tier it waited on
  has shipped (`docs/VISIBILITY.md`: `private` / `crew` / `workspace` plus
  crew membership), but a pairwise scope is a fourth case that design does
  not cover, so it still gets designed at the time.
- **P4's findings rule** — the interrupt ledger itself shipped: a task created
  after the week line locked and finished in the same week counts as
  unplanned, and the team-level ratio is in the cockpit
  (`portfolio.py::interrupts`). No findings rule reads it yet.

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

- **D2 attention count in the shell prompt** — `skein attention --porcelain`
  reads a 60-second cache at mode 0600, never blocks and never errors.
  `skein install-prompt` writes the starship or PS1 snippet, on the
  `install-hooks` precedent.
- **D3 branch-aware git flow** — `skein task start 42` makes the branch
  `task/42-slug` and sets in_progress. A prepare-commit-msg hook injects the
  trailer from the branch name. `skein pr-body` composes task, engagement pack
  and commits for `gh pr create`.
- **D4 MCP mid-task parity** — `claim`, `report` and `submit` landed, and the
  read side (`read_worklog`, from G3) landed 2026-08-08 with MCP parity.
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
  Ops work on the runner host, not a repo change. (The proxy-aware client
  addresses item that lived beside this one shipped: `SKEIN_TRUST_PROXY_HOPS`.)

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
  shipped: the Model spend card on Work → Health.)

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
the Model spend card (G7), the unattended runner under its ceilings
(G4, G5, A3), and the trust-blocked line (G6's honesty half).

Still open from that review:

- **G6's evidence pack** — trigger unchanged: worth building when a
  real promotion decision is near, and that needs verdict volume the
  runner has not produced yet (`SKEIN_AGENT_RUNNER` is empty by
  default, so no deployment runs unattended turns). A promotion
  decision needs what was approved, not how many times. [M]
- **G7's last clause** — per-model spend rows exist on Work → Health,
  and the Settings model menu shows no cost beside a model, so the
  menu is not yet comparative. [S]
- **P4's findings rule** — see "Manager and workflow" above. [S]
- **F8 `skein ask`** — the browser half of "one input, a `?` prefix"
  shipped; the terminal has `skein search` and no ask. Lives in
  "Developer loop" above. [S]

Suggested order for what is left, now that the deliver-what-is-computed
arc and the agent motor have landed: the manager frame next — C2, C3,
C4 and P4's rule land as cards in the cockpit that now exists — then
the CLI arc (F8, D2, D3, D5, F6, F7), which the web half has outrun,
and the bounded-input census, which is the highest safety value per
hour in this file.

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
