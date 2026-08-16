# Feature-round study — roles, horizons, and restrained gamification

**Closed.** This is the record of the second live study of 2026-08-15,
separate from the four-pass `2026-08-15-product-study-*` set. It produced the
three features in commit `6876725` (Guided First Week, Today's Three, Threads
in This Report) and the branch-review fixes in commit `7a244aa`.
`docs/FEATURES.md` is the living reference and wins on any disagreement. This
file is kept for the observations behind each feature, the rejected concepts
with their reasons, and the per-feature review and verification record.

## Method

The study ran the live app in Chrome against the deterministic mock provider
and seeded data. One person walked it from six perspectives: new team member,
individual contributor, manager, sponsor, reviewer, and long-term reader of
accumulated history. Each perspective was read at its own time horizon, from
first days to months.

Four review agents then assessed the product for missing value: a product
manager, a game designer, a whimsy specialist, and a behavioral-nudge
specialist. Three independent reviewers judged the surviving feature ideas.
Each implemented feature then received up to two independent implementation
reviews, and every valid finding was fixed before the next feature started.
Each feature closed with live Chrome validation on desktop, 360px, and 390px.

An idea had to pass these tests before it was built:

- It solves an observed problem in the live app.
- It is not already shipped or in the backlog.
- It works without an LLM.
- It gives value to more than one role or has strong value for a neglected role.
- It has a small, clear implementation path.
- It avoids harmful competition or employee surveillance.
- Its game mechanic supports useful work instead of activity volume.

## Time horizons the product already serves

- Immediate: capture, mentions, questions, chat, review receipts, notifications, and forge or CI transitions.
- Hours: blocker escalation, promise chase, and meeting outcome prompts.
- Days: quiet delegated work, stale work, engagement silence, and aging work findings.
- Weekly: planning, commitments, stale-work nudges, rituals, and authority review.
- Multiweek: six-week pulse, 28-day insights, flow and spend windows, and forecast calibration.
- 90 days: elevated authority review, decision reconfirmation, and retention windows.
- Long term: one-year snapshots and permanent ledger, decision, and cost history.

## Delight and game-like systems that already existed

- Weekly commitments and kept percentage.
- Trust scores and authority progression.
- Six-week pulse seasons.
- Findings converted into work and measured for follow-through.
- Themes, field-guide knots, ritual openers, mock-agent voice, and whimsy pools.

The trust model already uses review streaks and verified verdicts. The study
treated a second score layer as a smell, and every review agent agreed.

## Live study log

The live study used an isolated frontend and a mock-provider backend. The
study did not depend on model output.

### First days — new team member

Observed surfaces:

- The identity picker explains attribution before a user can write.
- My Day shows a four-step first-week checklist and reports `1/4` complete.
- The checklist gives direct actions for capture, standup, and API-key setup.
- My Day also shows team proposals, notices, events, the personal standup form, a feature hint, and the full activity delta.
- The Field guide shows `2 of 41 tied` and explains every feature through knot cards.
- Settings contains identity, API-key, growth, agent, calendar, forge, theme, model, backup, crew, roster, and field-guide controls on one long page.

Observed needs:

- The first-week checklist is clear, but the rest of My Day exposes the full team queue immediately.
- At 390px, CSS places the first-week checklist after every My Day section. A new member can scroll through the full team queue before setup appears.
- A new member must separate onboarding work from team-wide noise without a role-specific path.
- The Field guide has strong game language and progress, but 41 cards form one long page.
- The guide has no section progress, short quest path, or visible next milestone.
- The new member can see season and system metrics before they understand the work model.

### First weeks — individual contributor

Observed surfaces:

- My Day groups assigned needs into Unblock, Promise, Review, Decide, and Notice.
- It combines personal work, team queues, standup, team events, hints, and the activity delta.
- Work → Browse contains the six-week season card, all engagements, blockers, capacity, absences, milestones, tasks, recent work, questions, decisions, standups, lessons, calendar, notes, and activity.
- Work → Health explains each engagement status and shows flow, forecast, promises, capacity, and spend.

Observed needs:

- The product tracks many outcomes but gives little support for choosing a small daily focus from them.
- The Browse page is complete but long. The roadmap already owns filters and pagination, so this study did not duplicate that work.
- The season card reports team metrics, but it gives no collective objective or next useful move.
- Recently shipped work is visible for seven days. A person has no compact personal record across several weeks.

### Weekly rhythm — manager

Observed surfaces:

- Planning follows the meeting order: last week, decisions, this week, future capacity, intake, external promises, stale decisions, and close.
- Health shows current evidence, flow, forecasts, and model use.
- Insights measures only the system and team aggregates. It explicitly avoids individual performance measurement.
- Reports stores weekly, daily, handoff, and readout artifacts.

Observed needs:

- Planning identifies the next decision, but the season layer does not convert system needs into a shared, motivating objective.
- Managers have system trends but no safe way to celebrate team help or progress without ranking people.
- A report that is old enough to matter can be hard to connect to the work and people it helped.

### Sponsor and reviewer

Observed surfaces:

- Inbox shows proposal evidence, provenance, reviewer controls, recent approvals, and trust context.
- Team → Agents shows the bench, mission control, authority, trust, memory, and flock traces.

Observed needs:

- Review has strong evidence but little positive closure after useful work lands.
- Any game mechanic around reviews must not reward volume or fast approval.

### Months — accumulated history

Observed surfaces:

- The product retains activity, decisions, costs, reports, findings, forecast history, field-guide progress, trust history, and season metrics.
- Browse shows a six-week season with standup chain, shipped work, blockers, lessons, and clear time.
- My Day shows a short delta, while Reports and Activity hold the long record.

Observed needs:

- A person has no concise, private-safe story of their completed work, help, learning, and growth across a month or season.
- The team season shows outcomes but not a small set of active quests or completed quest history.
- The product records useful collaboration, but it has no explicit, safe appreciation action tied to real work.

### Runtime findings

- The app loaded with no console errors on My Day, Guide, Planning, Health, Browse, Insights, Inbox, People, Chat, and Agents.
- Reports listed an artifact that its detail route returned as `404 no artifact #6`. The Reports detail stayed on `Loading…` after the failed request. This bug predated the study and was not one of the three features.

## Rejected and deferred concepts

These concepts were examined and not built. The reasons live only here.

- **Personal Trail** — a self-only season reflection from factual receipts. Rejected: current data cannot support truthful personal attribution, and a partial trail claims completeness it does not have.
- **Season Quests** — a governed set of collective objectives on the six-week season. Rejected: Pulse metrics are not consistently season-scoped or absence-safe, and the existing standup chain is not PTO-safe.
- **Assists** — an explicit appreciation action tied to real work. Rejected for this round: Skein has no pairwise recipient privacy model or explicit help records, and inferred recognition data can become a popularity measure.
- **A larger Knot Trail** — section progress and quest paths over the field guide. Rejected: insufficient evidence for a second progression system beside the existing suggestion.

## Review record

### Whimsy review

The whimsy review recommended restraint. It rejected a second reward system
and public person-level progress. It preferred a formation view of the first
three ordered tasks, report-reference links, one deterministic season move,
and a self-only event-based trail without totals or comparisons.

### Game-design review

The game-design review recommended one nested loop: learn, select, act, and
reflect. It preferred a single next-knot suggestion, a three-task daily
formation with no streak, carryover, or manager view, and self-only season
receipts. It found that the existing standup chain is not PTO-safe.

### Product review

The product review found that Skein needs connective experiences more than
new entities or dashboards. It preferred browser-local daily focus, an
onboarding-first guided layout, and a self-only reflection. It replaced
automatic Season Quests with a governed Season Focus and rejected Assists
because Skein has no safe pairwise visibility model.

### Behavioral review

The behavioral review recommended a small self-selected focus of at most
three existing My Day items, with no new route, table, checklist, or
reminder. It rejected Season Quests on absence-safety grounds and inferred
Assists on recognition-data grounds.

### Independent idea review

Three independent reviewers assessed the final set. The UX reviewer approved
all three with scope changes. The minimal-change reviewer approved Guided
First Week and report threads, and approved Today's Three with a one-bit
field-guide mark. The adversarial reviewer approved Guided First Week and
report threads, and approved Today's Three as a controlled browser-local
pilot.

## Implementation record

### Guided First Week

- My Day keeps the first-week setup before personal work in DOM and visual order.
- Needs You and Your Work stay visible during guided mode.
- Team Queues, Team Today, and Since Yesterday stay behind one accessible disclosure until capture and standup are complete.
- The initial onboarding request cannot flash team context or restore dismissed guidance after a late response.
- Dismissal restores the normal layout and moves focus to the main landmark.
- The field guide includes the Double Bowline card. Capture and standup tie it without a false rollout announcement for existing users.
- Two implementation reviews found asynchronous state, test-isolation, semantics, reduced-motion, and focus problems. All valid findings were corrected.
- Live Chrome validation passed on desktop, 360px, and 390px with a clean console.

### Today's Three

- My Day lets a person select up to three current assigned tasks as a compact daily focus.
- The complete server-ranked task list stays visible and keeps its original order.
- One browser payload per resolved user stores only `team_date` and `task_ids`. A new team date overwrites the payload, and invalid ids are removed.
- A stale tab cannot overwrite a newer team date from another tab.
- A feature-specific rate-capped route ties only the Trinity Knot field-guide card.
- The code review found three valid cross-feature defects: stale-tab date reversal, local-name onboarding keys, and silent suppression of future Guided First Week unlocks. All three were corrected and pinned by tests.
- Live Chrome validation passed on desktop, 360px, and 390px, including persistence after reload and the field-guide tie.

### Threads in This Report

- Report reads parse explicit typed references from the unchanged markdown source.
- The response contains only current targets that the viewer can read and the destination policy permits.
- Deleted, private, absent, unsupported, and bare references stay as report text without an active control.
- Tasks use the shared side peek. Decisions use charter anchors. Proposals use exact `?id=` review links.
- Two implementation reviews found one shared barrier: inaccessible references still received active controls. The read boundary now filters these targets in one batch per entity.
- Live Chrome validation passed on desktop, 360px, and 390px. The Lighthouse snapshot scored 100 for accessibility, best practices, SEO, and agentic browsing.

### Integrated verification

- The full CI lint script, the full backend suite, the full frontend suite, and an isolated production build all passed at ship time.
- Integrated testing found four additional defects, fixed in the same commit: a missing visibility scope on the historical first-week standup read, lost contrast on dispositioned findings, unwrapped long activity text, and dashboard activity overflow in the wider Phosphor font.
- The Reports smoke walk stayed blocked by the pre-existing `artifact #6` row, whose stored path sits outside the Playwright data root. The new thread flow was validated against a current readable report instead.

## Pre-merge branch review (commit `7a244aa`)

Three independent agents reviewed the branch against main by subsystem:
backend, My Day frontend, and the report-thread slice. Each agent had to
verify a finding against the code before reporting it. Seven findings
survived and all were fixed:

1. **Unquoted titles minted wrong-row thread chips.** The ritual, digest, handoff, and readout generators interpolated user titles without the quoted frame `refs.py` documents, so a task called "chase decision #4 approval" linked decision #4 from a report that never referenced it. The fix is `wording.quoted()`, now used by every artifact generator and receipt producer.
2. **An apostrophe inside a quoted title corrupted the parse frame across lines.** The `_QUOTED` pattern now refuses to match across a newline, and `quoted()` converts interior apostrophes to typographic ones.
3. **The backend fixtures avoided both failing shapes.** A new test builds its body with the real `week_open` generator and a title that embeds both a reference and an apostrophe.
4. **Established-but-incomplete users saw the guided layout flash on every My Day load.** The core-steps verdict is now cached per resolved user and read before the serial onboarding request resolves.
5. **`aria-pressed` combined with a state-flipping accessible name** announced "Remove … pressed" as if the task were removed. The name now carries the state alone.
6. **The row-level half of the thread policy filter had no denying test.** A mutant that deleted it passed the suite. A new test denies one task row while permitting the route.
7. **The `row_policy=False` contract was unrecorded.** The destination map now names the routes it mirrors and the change that must flip each flag.

The review also produced smaller fixes: batched policy-context lookups for
questions and decisions, a recorded constraint on `read_artifact` defaults, a
removed fixture that no code path emits, an honest identity-loading line in
Settings, a corrected field-guide sentence, and a dead thread chip that no
longer advertises a hover.
