# Live product study — delivered improvements

**Closed.** What the study actually shipped across the three
`proposed-features` commits, written at delivery. `docs/FEATURES.md` is the
living reference and wins on any disagreement; this file is kept for the
per-improvement reasoning that the feature table compresses away.

## Daily work

### Human task capture returns to My Day

A human `todo:` capture now assigns the task to its creator. The task appears in that person’s My Day list.

Agent capture stays unassigned. This rule keeps human sponsorship and delegation intact.

The Quick capture field-guide card now states the assignment rule.

### `/briefing` keeps the audience split

The deterministic `/briefing` command now separates these counts:

- reviews waiting on the reader,
- pending reviews in the team queue,
- intake waiting in the team queue.

The command reads the existing audience labels. It does not add a second classification rule.

### My Day keeps current guidance before history

The field-guide hint now appears before the recent activity card. The empty-task instruction names the Capture button directly.

The instruction no longer displays `Ctrl+K`. That shortcut stays reserved for search.

## Action receipts and focus

### Persistent live regions

The application shell now mounts empty polite and assertive live regions before any message arrives.

Confirmations use the polite region. Failures use the assertive region and stay visible until dismissal.

Equal consecutive messages replace a child node without replacing the live region.

### Write confirmations

These successful actions now use the shared confirmation path:

- standup post,
- proposal verdict,
- week-open brief.

A proposal verdict also refreshes the Inbox badge immediately.

### Review focus recovery and reason limit

The verdict reason input now shows the server limit of 1,000 characters. It shows the remaining count after the reason reaches 800 characters.

After a verdict removes a card, focus moves to the next proposal. If the queue is empty, focus moves to the Approvals heading.

## Planning and field guide

### Planning page heading

The Planning route now has one `Planning` level-one heading. Loading, error, and loaded states use the same heading.

### Current field-guide progress

The identity menu now fetches field-guide progress each time it opens.

The guide and hint endpoints now use the same tieable-card denominator. Cards with `ties: never` stay outside the completion total on both surfaces.

## Agent operations

### Canonical delegation choices

Mission control now projects a backend-derived `delegatable` value for each agent identity.

The task panel shows only delegatable identities. Service and MCP identities stay visible on Mission control for operations and authority work.

### Mission control work view

Mission control now starts with `Has work`. This view shows identities with an open task or a pending proposal.

`All agents` restores idle, service, and MCP identities for operations and inspection. Authority controls continue to use the complete identity list.

### Actionable agent inbox

The selected inbox now appears directly after Mission control. The selected row is marked, and focus moves to the inbox heading after load.

Delegated task rows now project existing context:

- description,
- priority,
- due date,
- milestone,
- engagement,
- sponsor.

Task names open the existing task panel.

### Authority identity requirement

The Authority form now reads `/api/whoami`. Its fields and write control stay disabled without strong administrator identity.

The form states that administrator access and strong identity are necessary. A deployment sign-in or personal API key can supply strong identity.

Manager controls still change scope only.

### Truthful trust wording

Trust rows now separate settled history from verified evidence.

The backend returns `last_verified_verdict` from the same strong, non-override verdict population as the streak. A zero streak no longer invents a rejection.

### Human proposer privacy

Proposer-level review statistics exclude human proposers. Agent identities and system actors both remain: the scheduler files every authority promotion, and hiding it drops the whole authority entity from the proposer table while the entity table still counts it. Entity-level team aggregates remain unchanged.

## Review queue clarity

### One row for one pending change

Proposal notifications now store a typed `pending_change_id` relation. My Day omits a linked notification when the reader can already see its proposal.

Unrelated notifications remain visible. Proposal resolution clears its linked notification without parsing display text.

Notifications created before migration 002 use the old prefix match only when no typed link exists.

## Deferred work

The release does not include these items:

- full task editing in the task panel,
- a Settings information-architecture rebuild,
- new Browse filters or pagination,
- new Planning write forms,
- automatic agent execution,
- new growth, meeting, PTO, or leadership systems,
- the larger verified-trust and rejection-provenance contract.

These items need more evidence or a separate contract decision.

## Verification

### Automated checks

- Focused backend suite: 117 tests passed after one test assertion correction.
- Field-guide suite: 22 tests passed.
- Full backend suite: 2,091 tests passed. One existing thread-warning remained in `test_auth_modes.py`.
- Focused frontend suite: 25 tests passed.
- Full frontend suite: 307 tests passed.
- Frontend production build: passed.
- `./scripts/lint.sh`: passed all CI gates.

### Follow-up automated checks

- Notification, briefing, review, and migration checks: 79 tests passed.
- Identity and authentication checks: 49 tests passed with the existing thread warning.
- Authority and Mission control checks: 16 tests passed.
- Full backend suite: 2,092 tests passed with the existing thread warning.
- Full frontend suite: 310 tests passed.
- Frontend production build: passed with an isolated output directory.
- `./scripts/lint.sh`: passed all CI gates after the output directory was removed.
- One code-review agent found two Authority identity-contract errors. Both fixes passed its second review.

### Live browser checks

The final live traversal confirmed these results:

- The application shell exposed empty polite and assertive regions before a message.
- Human task `#26` appeared in Marcus’s My Day list after Quick capture.
- `/briefing` separated personal reviews from the team review and intake queues.
- The Planning page exposed one level-one heading.
- The delegation picker omitted MCP and service identities.
- Agent inbox headings received focus and appeared after Mission control.
- Trust rows said `no verified verdicts` instead of inventing a rejection.
- The verdict input showed the 1,000-character limit without an idle countdown. The remaining count appeared after 800 characters.
- Rejecting proposal `#6` announced success, moved focus, and changed the Inbox badge from 3 to 2.
- Standup `#4` announced `Standup posted.` through the polite region.
- The hint and guide endpoints both returned `7/39` after the final restart.
- The active browser page had no current console warnings or errors.

### Follow-up live checks

- Backend startup recorded `002_link_review_notifications.sql` and added `notifications.pending_change_id`.
- Mission control started with `Has work` and showed three identities.
- `All agents` showed all 19 identities, including idle, service, and MCP identities.
- Weak identity disabled every Authority field and the Set button.
- Both Authority helpers stated the administrator and strong-identity requirements.
- The live delegation knot explained `Has work` and `All agents` after backend startup.
- The browser console had no current warnings or errors on the changed Agents page.
- The ingest flow created proposal `#7` and one batch summary. It does not create a per-proposal notification.
- Proposal `#7` was rejected after the My Day check. The Inbox badge returned from 3 to 2.
- The typed notification creation, suppression, and resolution paths passed automated checks against PostgreSQL.

### Live records created during final verification

The final traversal wrote these records to the seeded local instance:

- task `#26`, `Verify proposed feature flow`,
- chat `/briefing`,
- rejection of proposal `#6`,
- standup `#4`,
- meeting-ingest proposal `#7`, rejected after the follow-up check,
- the aggregate ingest notification for proposal `#7`.

## Delivery boundary

The follow-up adds one nullable notification relation and its index. It adds no model call, dependency, page, table, authority level, or unattended runner behavior.

## Second-pass delivery

### Durable native agent writes

The governed wrapper now finishes each specialized write inside its database transaction. The transaction commits before the terminal tool event reaches Strands.

This change covers claim, progress, acceptance submission, and handoff generation. If a delegate raises, no success event leaves the transaction.

A regression check consumes one terminal event and closes the generator. The fresh database still contains the claimed task state.

### Stale chat recovery

A chat `404` now states that the message was not sent. The page removes the stale browser pointer and unmounts the unavailable thread, so the optimistic message and composer disappear.

The page moves focus to a direct `New chat` action. Skein does not replay the failed message.

The response says that the chat is not available. It does not reveal whether another identity owns the thread.

### Finding conversion keeps context

A converted task now keeps `high`, `medium`, or `low` finding severity as its priority. A `positive` finding uses the valid `medium` task priority.

A human conversion assigns the task to the converter. The response includes `task_id`, and Insights opens the shared task panel after conversion.

The existing `source_finding_id` relation is now the idempotency key. Conversion locks the finding first and commits the work, typed link, event, and disposition in one transaction.

A repeated same-kind conversion returns the linked row. A failed disposition rolls back the complete conversion.

Ledger-integrity findings now require strong identity for conversion, as they already did for other dispositions. A weak header can no longer remove the ledger alert from the digest by converting it.

### My Day refreshes after Quick capture

A successful Quick capture dispatches the existing data-change signal. An open My Day page reloads its briefing and onboarding state.

A failed capture does not dispatch the signal. The dialog keeps the draft and the error.

### Mobile search stays visible

Below the `sm` breakpoint, the result panel uses 16-pixel viewport gutters. Desktop search keeps the existing input-relative position.

The responsive browser check now opens results at 360 pixels and measures both horizontal edges.

### One term for incoming promises

The `awaiting:` chip now reads `awaiting`. The parser still accepts `waiting for:` as an input alias, and the stored direction remains `received`.

The Quick capture, promise, and finding-conversion field-guide cards now explain the shipped behavior.

## Second-pass verification

### Automated checks

- Focused backend policy and conversion suite: 23 tests passed.
- Broader conversion, provenance, route-bound, and visibility suite: 52 tests passed.
- Full backend suite: 2,097 tests passed. The existing intentional thread warning remained in `test_auth_modes.py`.
- Focused frontend regression suite: 35 tests passed before the final stale-thread state check. The stale-thread files then passed 18 tests.
- Full frontend suite: 316 tests passed.
- Mobile search browser check: passed at 360 pixels.
- Frontend TypeScript check: passed.
- Frontend production build: passed.
- `./scripts/lint.sh`: passed all CI gates.
- One code-review agent found three defects: a ledger-finding identity bypass, non-atomic conversion, and an enabled stale composer. All three fixes passed focused checks and a second review with no remaining defect.

### Live checks

- Agent task `#29` changed to `in_progress` after the receipt. A fresh task read confirmed the state.
- The agent added a worklog note. A fresh task panel showed the note.
- Acceptance submission created proposal `#9`. Marcus approved it, and a fresh task read showed task `#29` as done.
- A thread owned by another identity returned `404`. The page removed the optimistic message and composer, showed `Message not sent. This chat is not available.`, and moved focus to `New chat`.
- Quick capture created task `#30`. The open My Day list showed it without a page reload. The task was then marked done.
- At 360 pixels, the search panel measured `left: 16`, `right: 344`, and `width: 328`.
- Converting the medium promise finding created task `#31`, assigned it to `mario`, and opened the task panel at `/insights?task=31`.
- The Quick capture chip exposed the accessible name `awaiting`.

### Live records created during verification

- agent task `#29`, completed through proposal `#9`,
- task `#30`, `Verify second-pass instant My Day refresh`, completed after the check,
- task `#31`, converted from the promise finding and completed after the check.

## Second-pass deferred work

The release does not include legacy notification repair or direct engagement-outcome editing.

Legacy notification repair needs a typed batch identity and an idempotent repair contract. The outcome action needs one canonical write surface and return path.

Capacity controls, unblock counts, guide restructuring, model comparison, PTO, lead summaries, growth expansion, trust expansion, and runner expansion remain deferred.

## Second-pass delivery boundary

This delivery adds no dependency, migration, page, table, authority level, provider branch, model call, or unattended runner behavior.

## Third-pass delivery

### Reach every report

Reports now loads a stable page of older artifacts with `before=<artifact id>`.

The page appends older reports without changing the selected report or its `?id=` URL. Both the cursor route and compatible bare-array route scan past workplace-policy denials.

The report reader keeps its existing scope, path-containment, file-type, size, and UTF-8 checks.

### Show an idempotent ritual result

Week-open and week-close repeats now return the artifact that already exists.

Work → Health shows this literal result:

> This ritual already ran this week. Skein did not send duplicate notifications.

The receipt links the existing report. It does not force a rerun.

The weekly claim and report now commit in one serialized transaction. A concurrent repeat cannot see a claim before the artifact exists. Forced reruns also serialize the artifact check and write.

### Keep active tasks reachable

The task service accepts `open` and `done` state projections and applies each state before the 500-row limit.

The route scans past rows that workplace policy denies before it returns the visible limit.

Browse reads one backend projection with two independently bounded slices:

- open tasks in priority order,
- completed tasks in completion order.

Both slices share one repeatable-read snapshot. A concurrent completion cannot place one task in both sections or in neither section.

The default CLI task list requests open work. `skein tasks --all` uses the same Browse projection and removes duplicate IDs.

### Use one My Day projection

`skein my-day` now renders the backend `attention` list. It no longer rebuilds the daily queue from raw briefing sections.

The CLI uses the same public group names as the web page. It keeps `Needs you` and `Team queues` separate.

If the review preview is incomplete, the CLI states how many proposals remain in Inbox → Approvals.

### Keep only active agent corrections

A rejected task-completion proposal leaves the active agent inbox after a later completion for the same task is approved.

The resolving completion can come from the original agent or a replacement agent. A generic task update does not clear the correction.

The rejected and approved verdicts remain in immutable review history. Trust continues to count both.

### Use one pending-review queue

Pending proposals now use one readable oldest-first queue across:

- Approvals,
- My Day,
- the navigation badge,
- the CLI.

The queue scans past scope and workplace-policy denials before it applies a page limit.

Approvals supports `after=<proposal id>` and `limit=1..200`. `More proposals` appends a page without clearing selected rows.

Proposal links use `/review?id=<proposal id>`. The page first probes the named ID. If it is pending, the page loads through its page and moves focus to its card. If it is not pending or is not readable, the page loads one normal page and moves focus to the Approvals heading.

Each loaded page marks at most 50 rows seen. Batch selection stops at 200 rows and states that endpoint limit.

Approved and rejected history keeps its compatible default windows. It now scans past denied rows before it applies those windows.

The queue batches target-tier reads by table. My Day and the badge obtain the first page and exact total from one scan.

### Enforce authority expiration

Authority rows now expose these separate values:

- configured level,
- effective level,
- review date,
- expiration state.

After an `autonomous` or `notify` review date passes, the effective level becomes `review`. Agent writes then wait for a human even when `SKEIN_AGENT_REVIEW=0`.

The configured grant stays unchanged for audit and compatible clients. Re-granting the elevated level sets a new 90-day review date.

Legacy elevated rows calculate their fallback review date from the team-local day.

Authority changes now lock identity first and the authority pair second. The stale proposal check and upsert are one serialized operation. A concurrent proposal cannot overwrite a new `forbidden` kill switch.

Direct authority writes and authority-proposal approvals both require a strong administrator identity.

A sponsor's delegated task remains task-specific authority for claim and progress. `forbidden` still stops the complete delegated loop.

### Field guide and documentation

The field guide now contains 42 cards. New cards cover report history and authority renewal.

Existing cards now explain:

- FIFO review pagination,
- ritual reuse and report links,
- open and recently shipped task separation,
- CLI My Day parity,
- completed agent correction work.

`docs/FEATURES.md`, `docs/INSIGHTS.md`, and `docs/ROADMAP.md` now match the shipped contracts.

## Third-pass verification

### Automated checks

- Focused backend regressions: 103 tests passed across the final focused runs.
- Full backend suite, serial: 2,123 tests passed with the existing intentional thread warning.
- Full frontend suite: 323 tests passed.
- Frontend production build: passed with 23 generated routes.
- `./scripts/lint.sh`: passed all CI gates.

The implementation review found concurrency, policy-limit, compatibility, bounded-request, and CLI-contract defects. Each confirmed defect was corrected and received a focused regression.

One parallel backend run had one unrelated workflow-timeout failure. The same timing check passed immediately in a serial rerun. The final backend suite uses serial execution.

### Live browser and CLI checks

- Reports rendered all four readable live artifacts and correctly omitted `Older reports` because no older page existed.
- Friday close-out showed the skipped result and linked artifact `#4`.
- Approvals showed proposal `#4` before proposal `#5`. The Inbox badge showed two.
- `/review?id=5` moved focus to proposal `#5`.
- `/review?id=999` moved focus to the Approvals heading without scanning the complete queue.
- Browse showed task `#25` in Tasks and task `#32` only in Recently shipped.
- The Agents page stated that expired elevated grants still wait when the review gate is off.
- The built-in agent inbox showed no rejected proposals after proposal `#11` completed task `#32`.
- `skein my-day` used public group labels and kept personal and team queues separate.
- `skein tasks` returned open work. `skein tasks --all` added completed work.
- `skein review --limit 1` and `--after 4 --limit 1` returned proposals `#4` and `#5` in order.

### Delivery boundary

This delivery adds no dependency, migration, page, table, task status, authority level, provider branch, model call, archive state, or unattended runner behavior.

It adds two read-only field-guide cards and one read-only task projection route. All writes continue through the existing service layer.
