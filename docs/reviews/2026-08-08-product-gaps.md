# Product-gap review — three perspectives (2026-08-08)

*Three commissioned reviews of the shipped product against the open
backlog, run the same day: the manager persona (Product Manager agent),
the IC persona (UX Researcher agent), and the agent layer as a
multi-agent system (Multi-Agent Systems Architect agent). Each reviewer
read `docs/FEATURES.md` and `docs/ROADMAP.md` in full and probed the
running instance read-only. Saved verbatim below the synthesis.*

**Status: drained 2026-08-08.** Every surviving proposal moved to
`docs/ROADMAP.md` the same day — new items G1–G10 plus the promotions
recorded in the "From the product-gap review (2026-08-08)" section.
This file keeps the reasoning and the rankings, per this directory's
rule: the roadmap records the what, this records the why.

---

## Synthesis

Each reviewer's diagnosis, in one line:

- **Manager:** the manager rituals are built but the room to run them in
  is not; several shipped features never got their second half.
- **IC:** Skein computes value it never delivers to where the dev is
  looking — the pull is missing, and the click does not land.
- **Agent layer:** the governance half is production-grade, the
  execution half does not exist — "Skein built the leash, the ledger,
  and the review court before building anything that walks."

All three converged, independently, on one structural pattern: **the
backend runs roughly two quarters ahead of its surfaces.** Search,
`/ask`, usage accounting, the delegation worklog, forecast calibration,
and engagement-health history are all computed and either never shown or
shown nowhere a person looks.

The three findings that were genuinely new (nowhere on the roadmap):

1. **Engagement health has no history.** R/Y/G is computed at request
   time and discarded, so a readout cannot say "newly yellow" — while
   forecasts already get a daily snapshot job. (→ G1)
2. **Forecast calibration is write-only.** `snapshot_forecasts` promises
   "measured against actuals later" in its own docstring; nothing ever
   reads the table. (→ G2)
3. **Nothing ever runs an agent turn unattended, and nothing bounds one
   if it did.** The JOBS registry drives 16 deterministic jobs and zero
   agent turns; the head agent's turn has no total deadline, cycle cap,
   or spend ceiling. The delegation loop, inbox, authority matrix and
   trust scores are all waiting on a motor that was never built.
   (→ G4, G5)

Two sharpened facts about known items:

- The anti-surveillance contradiction the roadmap records against
  `/portfolio` is worse than recorded: `services/readout.py` writes
  named per-person WIP counts into the exec readout, a forwardable
  artifact whose purpose is to leave the team.
- The trust flywheel structurally cannot accrue data in the live
  configuration: with the review gate off, review-level writes apply
  with no verdict, and under weak identity no verdict counts toward a
  streak. The UI reads as "no data" where the truth is "cannot produce
  data". (→ G6)

The ranking that survived into the roadmap's suggested order: a week of
S items first (G3, G6's honesty half, G8, P5's CLI half, self-serve 7),
then three arcs — deliver-what-is-computed (K4 → nav search/ask → G7),
the manager frame (P1 hosting G1/G2/C2/P4/G10), and the agent motor
(G4 → G5), the largest bet and the one that makes the product's name
honest.

---

## Review 1 — Manager persona (Product Manager agent)

### Manager-persona gaps

**1. The Monday planning cockpit** — the manager's weekly ritual has no
single surface; running Monday means touring five pages

- Category: `surface-existing` (pure composition of existing endpoints)
- Why a manager cares: Monday morning the manager needs, in meeting
  order: last week's kept-%, capacity vs. the drafted plan, the intake
  queue awaiting triage, and stale decisions — then one commit action.
  Today that is `/portfolio` (two cards plus a manage toggle),
  `/intake`, `/charter`, and the week-draft button, each with its own
  load. The weekly operating rhythm is Skein's spine, and the person who
  runs it gets no room built for it.
- Evidence: `docs/ROADMAP.md` P1 ("Pure composition of endpoints that
  exist"); `frontend/app/portfolio/page.tsx` shows the week plan and
  draft flow embedded as one card among six.
- Size: M
- Overlaps ROADMAP **P1 weekly planning cockpit** — promote as-is. It is
  also the natural landing spot for gaps 5 and 7 below, which
  strengthens the case for building the frame now.

**2. "What changed since last week"** — health is a live computation
with no history, so the exec readout cannot show direction

- Category: `new`
- Why a manager cares: the first question every exec asks is not "is
  Atlas yellow" but "is Atlas *newly* yellow, and which way is it
  moving?" `engagement_health()` computes R/Y/G at request time and
  discards it; two consecutive readouts are two snapshots the manager
  must diff by eye across markdown files. Interestingly the plumbing
  pattern already exists — forecasts get a daily snapshot job — but
  health does not.
- Evidence: `backend/app/services/portfolio.py::engagement_health` (no
  persistence); `backend/app/services/readout.py` (each readout is a
  fresh projection, no delta section); `jobs.py` has `forecast-snapshot`
  but no health snapshot.
- Size: M (a daily `health_snapshots` job on the `forecast-snapshot`
  precedent, plus a "since last readout" section: newly red/yellow,
  newly green, newly shipped). Deterministic SQL, no LLM.
- No ROADMAP overlap. Nearest neighbor is the cut "stakeholder signed
  status pages," but that cut is about *distribution*; this is about
  *content*.

**3. Received-promise chaser** — the promise ledger only faces one way

- Category: `rethink` (the ledger exists but models half the manager's
  exposure)
- Why a manager cares: half of what sinks a strike team's commitments is
  what *other people* owe it — the security review, the data feed, the
  exec sign-off. Today `waiting_on: promise:N` can point at a promise,
  but a promise is always something *we* gave; there is no first-class
  "awaiting X from Y by date" with a nudge loop, so chasing stakeholders
  lives in the manager's head. Missing an inbound dependency then
  surfaces only as a slipped milestone, after the fact.
- Evidence: `docs/FEATURES.md` promise ledger row (`audience:
  external|team`, both directions of *our* giving); `docs/ROADMAP.md` C2
  spells the fix (`commitments.direction`, `awaiting:` capture grammar,
  escalate-to-manager after 2 silent cycles).
- Size: M
- Overlaps ROADMAP **C2** — promote as-is; the design there is already
  right (deterministic hourly rule, existing `waiting_on` integration).

**4. Spend is invisible to the person who owns the budget** —
`/api/usage` has no UI

- Category: `surface-existing`
- Why a manager cares: the manager answers "what is the AI layer costing
  us, and on what?" at budget time. Skein already prices every turn at
  write time, rolls spend per engagement through chat-thread links, and
  fires a budget finding — which then points the manager at raw JSON.
  Engagement cost next to engagement health is exactly the
  per-engagement ROI view an exec conversation needs, and the honest
  `(unlinked)`/`unpriced_calls` framing is already built server-side.
- Evidence: `docs/FEATURES.md` usage accounting row
  (`SKEIN_MONTHLY_BUDGET_USD`); `docs/ROADMAP.md` Insights & usage: "the
  budget finding points people at a raw JSON endpoint; engagement costs
  belong next to engagement health on /portfolio."
- Size: S
- Overlaps ROADMAP **"A UI surface for `/api/usage`"** — promote as-is,
  onto `/portfolio` (or the cockpit from gap 1).

**5. Forecast calibration is collected and never shown** —
`forecast_snapshots` is write-only

- Category: `surface-existing`
- Why a manager cares: the manager forwards slip-forecast dates to
  stakeholders. The daily snapshot job exists *explicitly* "so
  calibration can be measured against actuals later" — but nothing ever
  reads the table, so "later" never comes. A manager who has been
  quoting "likely dates" for a quarter cannot answer "how often were
  those dates right?", which is the difference between a forecast execs
  trust and a decoration.
- Evidence: `backend/app/services/adoption.py::snapshot_forecasts`
  (docstring promises calibration); no reader exists in `portfolio.py`,
  `readout.py`, or the `/insights` trends listed in `docs/FEATURES.md`.
- Size: S–M (one deterministic query joining snapshots to milestone
  `completed_at`, a "forecast hit rate / median error, n shown" line on
  `/insights` beside MTTR — same medians-over-means house style)
- No ROADMAP overlap — this is a shipped feature whose second half was
  never scheduled.

**6. `wip_by_person` in the exec readout contradicts the
anti-surveillance promise** — and the readout makes it worse than the
roadmap knows

- Category: `rethink` (a decision the roadmap already demands, with an
  escalating cost of not making it)
- Why a manager cares: the anti-surveillance rule is what buys the
  team's honest data entry — standups, blockers, stale-WIP confessions.
  The roadmap flags `wip_by_person` on `/portfolio` as an open
  contradiction; what it does not note is that `exec_readout()` writes
  **named per-person WIP counts into a forwardable markdown artifact**
  ("WIP: alice 4, bob 1") whose whole purpose is to leave the team. That
  is person-level data judging the past, in front of exactly the
  audience the rule exists to keep it from. One bad forwarding and the
  team stops filing honest standups.
- Evidence: `backend/app/services/readout.py` (named per-person WIP
  lines); `docs/ROADMAP.md` "Decisions needed": "Narrow the claim or
  aggregate the display. Leaving both is the only wrong answer."
- Size: S (aggregate to total WIP + stale count in the readout; keep
  names on `/portfolio` only if the claim is narrowed to "planning
  surfaces")
- Overlaps the ROADMAP decision item — promote, and reshape: decide the
  readout case first, because the artifact egresses.

**7. Capacity has no forward view** — conflicts and what-ifs only answer
"today"

- Category: `new`
- Why a manager cares: `allocations` carries `starts_on`/`ends_on` and
  absences carry windows, but `allocation_conflicts` filters to windows
  "covering today" and `what_if` projects against today's totals. The
  staffing questions a manager actually holds are forward-shaped: "who
  frees up when Atlas closes in three weeks?", "does accepting this
  request conflict with Dana's allocation that *starts* next month?" A
  request accepted today against today's numbers can be a conflict on
  its real start date, and Skein will only notice when the date arrives.
- Evidence: `backend/app/services/portfolio.py::allocation_conflicts`
  (both window predicates bound to `today`); `what_if` (same
  `today, today` binding). Anti-surveillance-clean by definition:
  person-level data planning the future is the permitted direction.
- Size: M (a per-week projection over the next 4–8 weeks from data
  already in `allocations` + `absences`; deliberately a table, not a
  Gantt, honoring the existing refusal)
- No direct ROADMAP overlap; complements ROADMAP self-serve item 2
  (what-if button on intake rows), which should carry a `starts_on` when
  this lands.

**8. Interrupt ledger** — the manager cannot explain *why* kept-% dipped

- Category: `new` (designed but unshipped; fully derived)
- Why a manager cares: when the commitment line reads 60% kept, the
  manager's next sentence to execs is either "we planned badly" or "we
  absorbed an incident" — and those have opposite remedies. A task
  created after the week line locked and finished in-week is unplanned
  work; the ratio is derivable today with zero new user actions, and it
  belongs in flow metrics and the readout as the team-level receipt
  behind a weak kept-%.
- Evidence: `docs/ROADMAP.md` P4;
  `backend/app/services/portfolio.py::flow_metrics` has no
  planned/unplanned split; `readout.py` reports flow with no interrupt
  context.
- Size: S–M
- Overlaps ROADMAP **P4** — promote as-is (its team-ratio-only shape is
  already anti-surveillance-correct).

Honorable mention, not counted: the **time-zone decision** (ROADMAP
"Decisions needed") disproportionately hits the manager persona — the
Monday brief and Friday close-out are *their* rituals, and a team at
UTC-7 gets Friday close-out at 8 a.m. That is a decision, not a build,
but the cockpit (gap 1) will inherit whatever is decided, so decide it
first.

### The one thing

**The Monday planning cockpit (gap 1), with the spend card (gap 4)
inside it.** Reasoning: it is the highest leverage-to-cost item on the
board — the roadmap itself certifies it as pure composition of shipped
endpoints, so it is mostly frontend work with no new schema, no new
writes, and no anti-surveillance exposure. More importantly, it is the
*frame* the other manager gaps land in: the interrupt ratio, the forward
capacity view, and the spend rollup are all cards this page would host,
and building them before it exists means bolting more cards onto an
already six-card `/portfolio`. The manager's weekly ritual is the moment
Skein either becomes the room where the week is run or stays a reference
the manager checks after running the week elsewhere — and everything
else in this product (rituals, commitment line, intake triage, findings)
gets more valuable if the manager's own operating loop lives inside it.
Ship the cockpit, and the quarter's remaining manager work becomes
incremental card additions instead of new surfaces.

---

## Review 2 — IC persona (UX Researcher agent)

### IC-persona friction and gaps

**1. Every "needs you" item links to a page, never to the row** — the
briefing names the exact thing and then drops you at the top of a
13-section page.

- Category: `surface-existing`
- The moment it bites: My Day says "Decide: question #1 from ava" or
  "task #12 due" with a why-you-see-this reason, the dev clicks, and
  lands at the top of Browse (`/dashboard`, thirteen sections) to hunt
  for the row by eye. Same dead-end for `/ask` citations and activity
  rows. The five-second morning loop becomes a thirty-second scroll,
  several times per morning.
- Evidence: `backend/app/services/briefing.py` — attention `link` values
  are bare `"/dashboard"`, `"/review"`, `"/charter"`; no task detail or
  `?task=` anchor exists anywhere in `frontend/app/`.
- Size: M
- ROADMAP overlap: K4 `?task=` side peek — promote; the roadmap itself
  already names the three consumers waiting on it.

**2. Search and `/ask` are invisible** — the two features that answer
"where did we decide X" have zero UI.

- Category: `surface-existing`
- The moment it bites: mid-task, the dev needs the vendor decision or
  the staging blocker. `GET /api/search` (FTS5, `#42` short-id fast
  path) and `GET /api/ask` (answers with `entity #id` receipts) both
  work — verified live — but no page, nav box, or ⌘K mode calls either.
  Grep of `frontend/` finds no consumer of `/api/search` or `/api/ask`.
  The dev falls back to Slack scrollback, which is exactly the leak
  Skein exists to plug.
- Evidence: `curl "/api/search?q=vendor"` and `"/api/ask?q=..."` both
  answer; no hits for either endpoint in `frontend/`. CLI `skein search`
  exists; `skein ask` (F8) does not.
- Size: M (nav search box); `/ask` could ride the same input with a `?`
  prefix.
- ROADMAP overlap: Self-serve item 1 (global search box) and F8 —
  promote both together; one input, two backends.

**3. The git loop is half-installed: commits close tasks, but nothing
starts the branch** — the dev must hand-type the grammar the webhook
expects.

- Category: `new` (backlogged)
- The moment it bites: to get the forge webhook's auto-start and
  `code ↗` link, the branch must be named `task/42-slug` exactly, from
  memory. To close on commit, the dev types `Closes-Task: #12` by hand
  (post-commit hook only syncs what is already typed). Picking up a
  task — the most frequent IC transition — is the one moment with no
  command: no `skein task start 42`, no prepare-commit-msg trailer
  injection, no `skein pr-body`.
- Evidence: `cli/skein_cli.py` (`install-hooks`/`sync-commit` only);
  forge webhook branch grammar in FEATURES.md "Code forge webhook" row.
- Size: M
- ROADMAP overlap: D3 branch-aware git flow — the highest-leverage
  unshipped IC item on the whole backlog.

**4. Delegation — the IC's biggest labor-saver — has no web affordance
in either direction** — you cannot delegate from the UI, and you cannot
watch the worklog in the UI.

- Category: `surface-existing`
- The moment it bites: the dev wants to hand "summarize competitor
  pricing" to research-agent. The Agents page empty state *advertises*
  delegation ("delegate a task or let the chat agent..."), but the only
  paths are CLI `skein tasks delegate` or asking the chat agent. Then,
  while the agent works, `GET /api/tasks/{id}/worklog` is readable
  pre-verdict — but `worklog` appears nowhere in `frontend/`, so the
  sponsor checks progress by curl or `skein worklog`.
- Evidence: grep of `frontend/` — `delegate` appears only in display
  text (`frontend/app/agents/page.tsx`), never a button; no `worklog`
  consumer at all.
- Size: S (delegate button on a task row) + S (worklog panel — which
  also wants item 1's side peek).
- ROADMAP overlap: Self-serve item 3 (delegate affordance, sized S
  there). The worklog surface is not on the roadmap — new sub-item.

**5. An escalation reaches the IC only if they are already looking** —
immediate-tier notifications are a My Day card, not a signal.

- Category: `rethink`
- The moment it bites: blocker #5 escalated at 16:26 (live in the
  briefing fetch); if the dev is heads-down in the editor all afternoon,
  nothing changes anywhere they can see it — the nav badge deliberately
  counts only Inbox work, Slack tier requires a configured webhook, and
  the in-app row waits on the next My Day visit. "Immediate" is only
  immediate for a keyless deployment if the app is already open.
- Evidence: FEATURES.md (badge counts only Inbox), notification rows
  ride `GET /api/briefing`; no bell, no title-count, no favicon dot in
  `frontend/components/nav.tsx`.
- Size: S (document-title count / nav dot fed from the briefing the nav
  already polls) — deterministic, keyless, own-data-only so
  anti-surveillance-clean.
- ROADMAP overlap: D2 (shell-prompt attention count) is the terminal
  half of the same fix; the "attention budget / ack states" cut does not
  cover this — this is delivery, not dedupe.

**6. The standup value exchange is uneven at the terminal** — the web
writes half your standup for you; the CLI makes you type it from memory.

- Category: `surface-existing`
- The moment it bites: the web My Day prefills "yesterday" from the
  dev's own activity (derived, not asked for — the fairest trade in the
  product). The terminal-native dev running `skein standup` gets three
  bare flags and reconstructs yesterday manually, so the CLI user pays
  the manager-benefiting tax the web user does not.
- Evidence: `frontend/components/standup-card.tsx` (uses
  `standup_suggestion`); `cli/skein_cli.py::cmd_standup` — no `--draft`,
  never reads the suggestion `GET /api/briefing` already carries.
- Size: S
- ROADMAP overlap: "P5's CLI half", listed verbatim — promote; it is a
  few lines of CLI.

**7. Finished work — and its PR link — vanishes on the day it ships** —
Browse hides done tasks, so the merge that just closed your task deletes
its own receipt.

- Category: `rethink`
- The moment it bites: the forge webhook closes task #42 on merge and
  attaches the PR URL; the dev goes to show a teammate and the task is
  gone from Browse (`status !== "done"`), reachable only via Health's
  week plan *if* the task happened to be committed to a week. The most
  satisfying moment in the loop — done, with proof — has no surface,
  while confetti fires for engagements only.
- Evidence: `frontend/app/dashboard/page.tsx` task filter; FEATURES.md
  forge row admits it ("Browse hides done tasks, which is where a
  merge's PR link would otherwise vanish").
- Size: S
- ROADMAP overlap: Self-serve item 7 ("recently shipped" strip or done
  filter) — promote the strip variant: it doubles as the IC-side reward
  loop.

### What would make the IC open Skein unprompted

Today Skein computes "3 things need you" but only tells you after you
have already opened it — the pull has to start where the dev already
lives, so ship D2 (attention count in the shell prompt) plus a
nav/title-count for the browser, and make the click pay off instantly by
landing on the exact row (K4 side peek) instead of the top of Browse.
The habit-forming trade is already built but half-delivered: Skein
writes your standup's "yesterday" for you and answers "what changed /
where is X" with receipts — surface the standup draft in the CLI and put
search/`/ask` in the nav, and the morning open becomes the cheapest way
to start the day rather than a reporting chore. In short: Skein already
knows things the dev wants; it just never says so anywhere the dev is
looking.

---

## Review 3 — Agent layer (Multi-Agent Systems Architect agent)

### Agent-layer gaps

**1. The delegation loop has no motor — nothing ever runs an agent turn
unattended**

- Category: `new` (with A3 as its deterministic core)
- What breaks: Delegate a multi-day task to `research-agent` today and
  the machinery is all there — `claim_delegated_task` →
  `report_progress` → `submit_for_acceptance`, sponsor binding, the
  inbox. But the only two things that ever *execute* an agent are a
  human typing in chat and an external process polling MCP. FEATURES.md
  calls the inbox an "ambient wake-up view" — there is no waker. The
  live instance shows it: `research-agent` holds 1 open task and 1
  pending proposal, `last_seen` 2026-08-07, and nothing will happen
  until a human cranks the handle. "Carry a delegated task end-to-end"
  currently means "a human re-invokes the agent every session and
  re-supplies context."
- Evidence: `services/jobs.py` — the JOBS registry runs 16 deterministic
  jobs and `review_authority`, zero agent turns; `GET /api/agents` live
  output; `services/delegation.py::agent_inbox` (pull-only); FEATURES.md
  "Agent inbox".
- What it looks like built right: a scheduled, budgeted worker sweep —
  per agent with open delegated work, one bounded turn against
  `my_agent_inbox` + the per-engagement context pack (which already
  exists exactly for this: "cheaper tokens, cleaner blast radius"),
  under the same deadline discipline flock members get, with every write
  still passing the gate. Keyless-first holds: the deterministic core is
  A3's rule-based sweep; the agent run is the LLM upgrade, exactly the
  pattern the codebase already uses everywhere.
- Size: L
- ROADMAP overlap: A3 (morning sweep) is the deterministic cousin —
  promote it and build the agent-run layer on top. The execution loop
  itself is genuinely new.

**2. No per-run budget or circuit breaker on agent spend**

- Category: `new`
- What breaks: The moment gap 1 is closed, an unattended agent runs with
  no human watching the stream — and today nothing bounds a run.
  `SKEIN_MONTHLY_BUDGET_USD` "reports overspend and never refuses"
  (FEATURES.md, by design). Flock members and consults carry a 180s
  member deadline; the orchestrator's own turn is bounded only by the
  provider socket's idle-gap timeout (`READ_TIMEOUT_S` — the comment
  itself says "it caps the stall, never the orphan's total life") with
  no total-turn deadline, cycle cap, or token ceiling. Live
  `/api/usage`: 56 calls, all `cost_usd: null` (unpriced), all
  `(unlinked)` — the cost governance that exists is currently measuring
  nothing. A prompt-injected or merely confused agent in a tool loop is
  a cost incident with detection lagging by up to a month.
- Evidence: `team_agent.py` (READ_TIMEOUT_S comment),
  `routes/chat.py::MEMBER_TIMEOUT_S` (members/consults only), live
  `GET /api/usage`, FEATURES.md usage accounting row.
- What's needed: a per-turn cycle/deadline cap on the head agent, and a
  per-delegation or per-agent-per-day spend ceiling checked where spend
  happens (the gate and `_log_usage` already see every call) —
  refuse-and-report, not report-only, for unattended runs. This is the
  fallback-chain discipline: primary → bounded → halt-with-receipt →
  sponsor notified.
- Size: M
- ROADMAP overlap: none (the cut "capability broker" is about tool
  grants, not spend). Genuinely new, and a hard prerequisite for gap 1.

**3. The trust flywheel is data-starved and cannot close under the live
configuration**

- Category: `rethink` + promote a cut
- What breaks: The flywheel design is sound — verdicts → streaks → filed
  authority proposals → human flips the switch. But look at the data it
  turns on. Live `/api/agents/trust`: one row *ever* (planner-agent, 1
  approval, `recent_streak: 0` — the verdict was not key-authenticated,
  so it never counts). Live `/api/agents/status`: `review_gate: false`,
  which means every `review`-level write applies directly and generates
  **no verdict at all** (`_gate.py`: `not config.AGENT_REVIEW` takes the
  direct branch). And in the default trusted-header deployment, no
  verdict is ever `reviewed_strong=1`, so streaks structurally cannot
  accrue. All 11 agent identities show an empty authority matrix.
  Separately: even with data, a human deciding to promote to `notify`
  sees counts per (agent, entity) — never *what* was approved, whether
  the writes were trivial or consequential, or samples of rejected work.
- Evidence: `services/delegation.py::trust_scores`
  (`reviewed_strong = 1 AND reviewed_override = 0`), `tools/_gate.py`,
  live `/api/agents/trust` and `/api/agents/status`.
- Fixes, in order of weight: surface the dependency (the trust card and
  `/agents` should state plainly that with the review gate off or under
  weak identity, no trust accrues — today it reads as "no data" rather
  than "cannot produce data"); and promote the cut **evidence pack** —
  its re-entry trigger is effectively met, because the first real
  promotion decision will have nothing to stand on.
- Size: S for the surfacing, M for the evidence pack
- ROADMAP overlap: "evidence pack" sits in the cut table
  (`docs/reviews/2026-07-24-agent-sol.md`) — argue to promote.

**4. An agent resuming a delegated task cannot read its own worklog**

- Category: `surface-existing`
- What breaks: The worklog is the designed continuity record for
  multi-day work — "the agent's running account, readable by the
  sponsor" — and `report_progress` writes it. But nothing in `ALL_TOOLS`
  reads `task_worklog`, `my_agent_inbox` does not carry it, and the
  chat-session alternative decays: `pin_first` is documented inert
  across turns and post-compaction re-injection is a deliberate cut. So
  on day 3, the agent that wrote "blocked on the API schema, waiting on
  #14" on day 1 cannot see its own note. Day-3 work restarts from the
  task title. Deterministic, keyless, and about 30 lines: a
  `read_worklog` tool (or worklog rows inlined into `my_agent_inbox`),
  plus MCP parity.
- Evidence: `tools/__init__.py::ALL_TOOLS` (no worklog reader),
  `services/delegation.py::list_worklog` (REST/CLI only),
  `team_agent.py::_conversation_manager` (pin inert), ROADMAP cut
  "post-compaction context re-injection".
- Size: S
- ROADMAP overlap: adjacent to D4 (MCP mid-task parity) — reshape D4 to
  include the read side, not just the write side.

**5. Flocks end in N opinions; there is no work-shaped output — promote
A5**

- Category: `surface-existing` (promote)
- What breaks: Flocks and consults are consultative *by construction* —
  stateless members, `force_review` on every write, the delegation trio
  refusing outright. That is the right call for "should we shard the
  database?". But it means the fan-out machinery can never produce a
  deliverable: ask a flock to plan a migration and you get three essays
  plus scattered single-row proposals with no ordering, no
  cross-references, no atomic approval. The synthesis step — the one
  agent that reads all members — is built with `tools=[]` and writes
  nothing. A5 (proposal bundles: `bundle_id`, symbolic `$1.id` refs,
  atomic apply) is exactly the missing output shape: the flock's merge
  proposes a bundle, the sponsor approves it as one unit, and the review
  flywheel gets richer verdicts (a bundle rejection is a stronger signal
  than a note rejection). Its "deferred until the simpler pieces prove
  out" condition is met — the simpler pieces shipped.
- Evidence: `team_agent.py::build_synthesizer` (`tools=[]`),
  `agents/identity.py::force_review`/`refuse_when_consultative`,
  docs/FLOCKS.md non-goals, ROADMAP A5.
- Size: M/L
- ROADMAP overlap: A5 — promote, with the flock synthesis as its first
  named consumer.

**6. Turn-level accounting exists; anomaly detection over it does not**

- Category: `surface-existing`
- What breaks: When an agent turn goes wrong today you have good raw
  material — `usage_log` (tokens, cycles, latency per turn per agent),
  `flock_traces` (per-member status/duration/receipts),
  session-persisted tool transcripts for real providers, optional OTEL.
  What is missing is anything that *reads* it and raises a hand: no
  findings rule fires on a single turn with pathological cycle count, on
  a member/consult failure streak in `flock_traces`, or on an agent
  whose last N proposals all bounced (the rejection data exists; only
  the weekly authority review reads it, and only for demotion
  proposals). A cost spike or a degraded persona is discovered by a
  human browsing raw JSON — ROADMAP already notes `/api/usage` has no
  UI. These are deterministic rules over existing tables, which is
  precisely what the 19-rule findings engine is for.
- Evidence: `services/insights.py` rule set (weekly token spend +
  monthly budget are the only spend rules), `flock_traces` schema in
  docs/FLOCKS.md (written, never read by findings), ROADMAP "A UI
  surface for /api/usage".
- Size: S
- ROADMAP overlap: reshape the "/api/usage UI" item into "usage UI +
  turn-anomaly findings rules" — same tables, one pass.

### Verdict

The governance half of this agent layer is ahead of almost anything
comparable — the authority matrix with family-propagating kill switches,
force-review on consultative turns, sponsor-bound acceptance,
structurally-proven gate coverage, and the one-hop consult cap enforced
by construction rather than by counter are production-grade controls
most multi-agent systems never build. The execution half is behind the
delegation it promises: Skein built the leash, the ledger, and the
review court before building anything that walks — no scheduler entry
ever runs an agent turn, so "agents as teammates" currently means
"agents as very well-audited chat responses." The single
highest-leverage move is the budgeted worker sweep (gap 1 with gap 2 as
its safety precondition): one bounded unattended turn per delegated
agent per day, against the inbox and the engagement context pack that
already exist for exactly this. It converts every shipped control from
decoration on human-cranked chat into live enforcement on autonomous
work — and, because every resulting write is a gated proposal, it is
also the only realistic source of the verdict volume the trust flywheel
is currently starving without.
