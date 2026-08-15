# Live product study — method

**Closed.** This is the method record for the four-pass study that produced
the three `proposed-features` commits (`9a1502a`, `11ebd16`, `7819100`) and
the pre-merge fixes on top of them. The findings, proposals and delivery
record are the three sibling `2026-08-15-product-study-*` files. Every
surviving proposal moved to `docs/ROADMAP.md` under "From the live product
study (2026-08-15)". Read this file for HOW the study was run — it is the
repeatable part.

## Goal

Study Skein as a live product, not as source code alone. Use the product through one simulated work month.

Then select and build improvements that have clear evidence from the study.

## Working rules

- Work on the `proposed-features` branch.
- Use the running application at `http://localhost:3000`.
- Record observations in `findings.md` during each journey.
- Keep the deterministic, keyless path complete.
- Reuse the service layer and existing UI patterns.
- Add a field-guide knot for each new user-facing feature.
- Do not build speculative systems without a daily-use problem.
- Run the complete lint gate before completion.

## Personas

1. **Manager** — plans the week, checks health, handles requests, reviews proposals, and prepares reports.
2. **Developer** — starts work, records progress, handles blockers, searches context, and closes tasks.
3. **Team member** — posts standups, captures work, checks personal priorities, and prepares for meetings.
4. **Reviewer and administrator** — manages approvals, agents, settings, backups, identity, and policy controls.
5. **Agent teammate** — reads its inbox, claims delegated work, reports progress, and submits work for acceptance.

## Month simulation

The study uses representative workdays. Each day includes real navigation and a real read or write when safe.

### Week 1 — Start and orient

- Day 1: sign in, use onboarding, read My Day, and inspect the field guide.
- Day 2: capture tasks, questions, decisions, blockers, promises, and requests.
- Day 3: plan work, inspect engagement health, and use the task panel.
- Day 4: use chat, personas, flocks, search, and ask.
- Day 5: post a standup, inspect notifications, and close the week.

### Week 2 — Execute and coordinate

- Day 6: start tasks and record work.
- Day 7: create and resolve a blocker.
- Day 8: delegate work and inspect the agent inbox.
- Day 9: submit agent work and review the proposal.
- Day 10: inspect capacity, intake, and stakeholder threads.

### Week 3 — Adjust and govern

- Day 11: change the weekly plan and inspect carryover.
- Day 12: record a decision and review its half-life.
- Day 13: ingest meeting notes and review the proposals.
- Day 14: inspect findings, trends, activity, and provenance.
- Day 15: inspect authority, trust, crews, themes, and deployment settings.

### Week 4 — Close and learn

- Day 16: inspect engagement drift and close-out data.
- Day 17: generate and read reports and handoffs.
- Day 18: inspect promises, questions, lessons, and time away.
- Day 19: repeat the main daily loop as each persona.
- Day 20: perform month-end review and rank product gaps.

## Review method

Each persona receives a review panel of no more than four agents:

- one product or domain reviewer,
- one usability or accessibility reviewer,
- one engineering or workflow reviewer,
- the Whimsy Injector.

The final findings and proposals receive one more panel of no more than four agents.

## Selection rules

Build an item only when it meets all these conditions:

1. The live study shows a repeated or high-cost problem.
2. The improvement has a clear reader and a clear place in the daily flow.
3. The repository does not already contain the same solution.
4. The change fits the project constraints and can be verified.
5. The value is higher than the maintenance cost.

## Delivery stages

- [x] Create the branch.
- [x] Read the feature reference and roadmap.
- [x] Start the application and open the live My Day page.
- [x] Complete the persona journeys and month simulation.
- [x] Review each persona's findings with agents.
- [x] Write `proposals.md`.
- [x] Review the full findings and proposals with agents.
- [x] Select and implement the strongest improvements.
- [x] Add focused tests and update the Quick capture field-guide knot.
- [x] Traverse the changed product again.
- [x] Run backend tests, frontend tests, build, and `./scripts/lint.sh`.
- [x] Write `new_features.md` and update product documentation.

## Follow-up delivery

- [x] State the strong administrator requirement beside Authority controls.
- [x] Add `Has work` and `All agents` to Mission control.
- [x] Link proposal notifications to pending changes.
- [x] Add focused backend and frontend checks.
- [x] Update the field-guide knot and product documentation.
- [x] Run the full backend and frontend checks.
- [x] Run the frontend production build and CI lint gate.
- [x] Restart the application and apply migration 002.
- [x] Traverse the changed flows in the live application.
- [x] Record the final follow-up evidence.

## Completion evidence

Completion requires four working files: the method, the findings, the
proposals, and the delivered result. The study wrote them at the repository
root under the names used throughout this file (`PLAN.md`, `findings.md`,
`proposals.md`, `new_features.md`). They are archived here as
`2026-08-15-product-study-*` — a repeat drafts at the root and archives the
same way when the work closes.

Completion also requires passing checks, live browser evidence, and an accurate list of skipped proposals.

Archiving is not a file move. `docs/reviews/README.md` sets the order: drain
every surviving proposal to `docs/ROADMAP.md` FIRST, then write the status
line, then add the README row. A transcript that still holds live work is a
backlog nobody reads.

## Completed checks

- Initial backend release: 2,091 tests passed.
- Follow-up backend release: 2,092 tests passed with the existing thread warning.
- Initial frontend release: 307 tests passed.
- Follow-up frontend release: 310 tests passed.
- Frontend production build: passed.
- CI lint gate: passed.
- Live browser traversal: passed for the changed Mission control, Authority, migration, and field-guide flows.
- The typed notification lifecycle passed focused and full automated checks.
- Deferred proposals: recorded in `proposals.md` and `new_features.md`.

## Second pass — updated product

### Goal

Repeat the live month study after the first release. Start with the changed daily, review, and agent-governance flows.

Then find residual problems that survive the first fixes. Select work only when the live product gives clear evidence.

### Updated month focus

- Week 1: repeat My Day, capture, standup, search, field-guide, and `/briefing` loops.
- Week 2: repeat task work, delegation, agent inbox, Mission control, and review loops.
- Week 3: repeat planning, intake, ingest, Authority, trust, activity, and Settings loops.
- Week 4: repeat reports, handoffs, close-out, and each persona's main daily loop.

### Delivery stages

- [x] Confirm the branch and running application.
- [x] Read the current feature reference and roadmap.
- [x] Complete the updated persona journeys and month simulation.
- [x] Review each persona's findings with no more than four agents.
- [x] Update `proposals.md` from the second-pass evidence.
- [x] Review all findings and proposals with no more than four agents.
- [x] Select and implement the strongest remaining improvements.
- [x] Add focused checks and update the field guide where necessary.
- [x] Run backend tests, frontend tests, build, and `./scripts/lint.sh`.
- [x] Traverse every changed flow again.
- [x] Update `new_features.md` and product documentation.

### Selected second-pass work

1. Make native agent tool writes durable.
2. Recover from stale chat threads without false sent messages.
3. Preserve finding severity, ownership, and navigation on conversion.
4. Refresh My Day after Quick capture.
5. Keep mobile search results inside the viewport.
6. Use one incoming-promise term.

The aggregate notification repair and direct engagement-outcome action remain separate contract work.

### Second-pass completion evidence

- Focused backend feature suite: 61 tests passed.
- Full backend suite: 2,097 tests passed with the existing intentional thread warning.
- Full frontend suite: 316 tests passed.
- Mobile search browser check: passed at 360 pixels.
- Frontend production build: passed.
- `./scripts/lint.sh`: passed all CI gates.
- Live agent flow: task `#29` claim, progress, proposal `#9`, sponsor approval, and final done state all matched fresh reads.
- Live chat recovery: the failed message and composer disappeared, the error stayed visible, and focus moved to `New chat`.
- Live Quick capture: task `#30` appeared in My Day without reload.
- Live search: the panel measured 16-pixel gutters at 360 pixels.
- Live finding conversion: task `#31` opened from Insights with source, priority, and assignment intact.
- Live terminology: the chip and prefix both used `awaiting`.

## Third pass — three-to-six-month use

### Objective

Study the updated product as a system that a team uses every workday for up to six months. Test accumulation, aging, turnover, recurring rituals, governance, recovery, and trust.

Start from commit `11ebd16` on `proposed-features`. Use the live local application and the existing seeded history.

### Long-horizon focus

- Month 1: adoption, capture, daily planning, chat, search, and field-guide habits.
- Month 2: work aging, repeated reviews, notifications, promises, and meeting outcomes.
- Month 3: engagement close-out, handoffs, playbook reuse, and ownership changes.
- Month 4: agent delegation, rejection recovery, trust evidence, and authority renewal.
- Month 5: findings noise, season history, reports, retention, and operational maintenance.
- Month 6: stale state, recovery after absence, accumulated records, and repeated workflow costs.

### Personas

- Manager and engagement lead.
- Developer and daily task owner.
- Normal team member and occasional contributor.
- Reviewer and administrator.
- AI agent teammate.

### Delivery stages

- [x] Confirm the branch and running application.
- [x] Read the current feature reference and roadmap.
- [x] Traverse the updated product across the long-horizon scenarios.
- [x] Record evidence and the representative six-month cycle in `findings.md`.
- [x] Review each persona section with no more than four agents.
- [x] Update `proposals.md` from the third-pass evidence.
- [x] Review all findings and proposals with no more than four agents.
- [x] Select and implement the strongest remaining improvements.
- [x] Add focused checks and update the field guide where necessary.
- [x] Run focused checks, full suites, the production build, and `./scripts/lint.sh`.
- [x] Review the implementation and correct verified defects.
- [x] Traverse every changed flow again.
- [x] Update `new_features.md` and product documentation.

### Selected third-pass work

1. Enforce expired elevated authority as required human review.
2. Add cursor access to older reports.
3. Filter open and completed tasks before collection limits.
4. Align My Day and Approvals on one cursor-reachable pending queue.
5. Show skipped ritual results and link the existing report.
6. Remove resolved corrections from the active agent inbox without changing verdict history.
7. Render the shared My Day attention projection in the CLI.

Task `void`, unread-notification history, direct outcome editing, and My Day overflow disclosure remain separate work.

### Implementation review corrections

The implementation review found and corrected these defects before final delivery:

- a concurrent authority approval could overwrite a new `forbidden` kill switch,
- a concurrent ritual repeat could see its weekly claim before the report existed,
- workplace-policy denials could still consume task and settled-review limits,
- Browse combined task slices from two snapshots,
- a missing review deep link could scan the complete queue,
- review selection and seen requests could exceed the 200-row endpoint limit,
- settled review defaults changed compatibility limits,
- legacy authority dates used the database session zone,
- trust changed the meaning of `current_level` for existing clients,
- replacement-agent completion did not clear the original correction,
- the CLI hid review overflow and exposed internal group names,
- policy-safe review counts repeated target-row reads on each navigation poll.

Focused regressions now cover each correction.

### Third-pass completion evidence

- Focused backend regressions: 103 tests passed across the final focused runs.
- Full backend suite, serial: 2,123 tests passed with the existing intentional thread warning.
- Full frontend suite: 323 tests passed.
- Frontend production build: passed with 23 generated routes.
- `./scripts/lint.sh`: passed every CI gate.
- Live Reports and ritual reuse: passed.
- Live Approvals FIFO, existing-ID focus, and missing-ID focus: passed.
- Live Browse task separation: passed.
- Live Agent correction cleanup and authority wording: passed.
- CLI My Day, task slices, and review cursor: passed.
- One parallel backend run hit an unrelated workflow deadline timing failure. That test passed in a direct serial rerun. The complete serial suite then passed.

## Fourth pass — pre-merge review of the branch

### Objective

Review the three commits on `proposed-features` against `main` before merge.
The first three passes built the work. This pass answers one question: does
this branch belong in `main`, and what must change first.

The reviewer does not read the notes from the earlier passes. A review that
starts from the author's own record inherits the author's blind spots. Start
from the diff and the code it touches.

### Method

The diff was 85 files and about 3,800 inserted lines. One reviewer cannot hold
that carefully, so the work splits by LENS, not by directory. Three reviewers
run at the same time, each with the whole diff and one question:

1. **Backend correctness** — locking discipline and lock ordering, error
   classification (4xx against 500 against 429/503), the service-layer write
   rule, provenance, migration rules, pagination edges, and whether each new
   test fails against the unfixed code.
2. **Frontend correctness** — state and effect bugs, contract match against the
   backend routes the same branch adds, accessibility on rewritten pages, and
   test quality. This reviewer runs the frontend suite and the production build.
3. **Comments and user-visible wording** — every added comment checked AGAINST
   the call chain it describes, plus ASD-STE100 compliance on functional copy.

Three is the number because there are three questions, not because three is a
good number. It agrees with the panel rule in "Review method" above: no more
than four agents.

### Standing rules for this pass

- **A reviewer reports; the lead verifies.** No finding reaches the user as
  fact until the lead reads the code and confirms it. Two findings in this pass
  did not survive that step.
- **Lens overlap is signal.** Two independent reviewers reached the same ritual
  `RuntimeError` from different directions. That raised its rank.
- **Every behavior fix carries a test that FAILS without it.** Stash the fix,
  run the test, see it fail, restore. A test that passes both ways pins nothing.
  All three fixes in this pass were proved this way.
- **Audit your own comments last.** The comment rules apply to the reviewer's
  fixes too. A final read of the fix diff found a cross-reference to a function
  that does not exist (`review.py::_apply`) and three comments that narrated the
  edit instead of stating the constraint.
- **Gate before delivery**: full backend suite, full frontend suite,
  `./scripts/lint.sh`, and the production build.

### Fourth-pass results

Three verified defects, fixed:

1. A false comment on `GET /api/review`: it claimed the pre-cursor response
   windows were preserved, while pending had changed from the newest 200
   descending to the oldest 50 ascending.
2. Approvals rebuilt the queue from the first page after every verdict, so a
   reviewer working past page one lost every fetched page and their place.
3. `rituals._existing_week_artifact` raised on a claim whose report was gone,
   turning a benign repeat into a 500 for the rest of the claimed week.

Nine smaller corrections: one shared administrator refusal, system actors
restored to proposer statistics, a named cost ceiling on the exact pending
count, a resurrected ROADMAP line for the part that did not ship, a dead
dictionary filter, a stale cross-reference, and three frontend defects
(`aria-controls` at an unmounted id, a blanking inbox panel, an empty grid row).

### The defect the review method found and the reviewers did not

`test_a_concurrent_repeat_waits_for_the_ritual_report` claims a FIXED week
(2035-W01) and does not reset the database. It passes once per database and
fails on every later run. It was already failing locally before this pass
began. CI on a fresh database stays green and hides it.

The lesson generalizes: a suite run once proves less than the same suite run
twice. Any test that writes a claim, a lock key, or a job row under a constant
key needs `fresh_db`.
