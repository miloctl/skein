# Live product study — proposals

**Closed.** The P-numbered proposals the study produced, with the evidence
standard each had to meet and the reason each deferral was deferred. Kept for
the *why not*: the rejected and blocked entries are the part that lives
nowhere else.

Every surviving proposal moved to `docs/ROADMAP.md` under "From the live
product study (2026-08-15)". P25 (`void` task state) is item 8 under
"Self-serve UX" and was not duplicated. One statement here is now false:
P11's "restrict proposer-level review statistics to agent identities" shipped
as "exclude human proposers", which keeps system actors so the scheduler's
authority proposals still count — P11's own check, "a human proposer never
appears in `by_proposer`", still holds. P10's last delivery bullet (reviewer
name, time and identity strength on rejected inbox rows) did not ship and is
on the ROADMAP.

## Evidence standard

These proposals use the live study and the persona panels in `2026-08-15-product-study-findings.md`.

The study was one seeded scenario on one day. It supports usability and contract fixes. It does not prove monthly frequency.

## Candidate proposals

### P1 — Project delegation eligibility

**Evidence:** The Agent teammate and reviewer journeys exposed `mcp-agent` and service identities as delegation choices. The service then refused them.

**Delivery:**

- Project the existing machine-identity owner into Mission control.
- Mark each identity as delegatable or not delegatable from the canonical ownership rule.
- Show only delegatable identities in the task panel.
- Keep all identities on Mission control because authority and operations still use them.

**Checks:** Every offered destination can pass the identity reservation step for delegation.

**Size:** Small.

### P2 — Make Mission control actionable

**Evidence:** Dana saw 19 identities, mostly idle. The selected inbox appeared below Authority, Trust, and Team memory.

**Delivery:**

- Add `Has work` and `All agents` controls.
- Define `Has work` as an open delegated task or a pending proposal.
- Put the selected inbox directly after Mission control.
- Mark the selected row and move focus to the inbox heading.
- Show description, due date, milestone, and engagement fields from the existing task record.

**Checks:** A reviewer can open an inbox and reach its heading without manual scrolling. Idle identities remain available under `All agents`.

**Size:** Small.

### P3 — Return human capture to My Day

**Evidence:** My Day tells a person to capture a task. Human quick capture creates an unassigned task, and My Day reads assigned tasks only.

**Delivery:**

- Assign a human-captured task to its creator by default.
- Keep agent capture unassigned so it does not bypass sponsorship and delegation.
- Keep the preview payload and the write payload identical.
- Update the Quick capture field-guide card with the assignment rule.

**Checks:** A human `todo:` capture appears in that person’s My Day list. An agent `todo:` capture stays unassigned.

**Size:** Small.

### P4 — Restore My Day focus

**Evidence:** Historical extension activity pushed the field-guide suggestion below the daily loop. The empty-task text also displayed the search key beside quick capture.

**Delivery:**

- Move the existing field-guide hint before the recent activity card.
- Remove the search shortcut from the quick-capture instruction.
- Name the Capture button directly.

**Checks:** The hint appears before recent activity. `Ctrl+K` is taught only as a search shortcut.

**Size:** Extra small.

### P5 — Preserve audience in `/briefing`

**Evidence:** The web page separates personal work from team queues. The deterministic command labels shared reviews and intake as personal My Day counts.

**Delivery:**

- Read the existing attention audience instead of adding another classification rule.
- Label personal work and team queues separately.
- Use `pending_reviews_total` instead of the list capped at 50.

**Checks:** A user with no personal action can still see team queue counts without false personal urgency.

**Size:** Extra small.

### P6 — Make write receipts accessible and current

**Evidence:** The shared status region mounts only after it already contains text. The study missed transient standup and verdict results.

**Delivery:**

- Keep polite and assertive live regions mounted before a message arrives.
- Keep the visual status pill separate from the announcement nodes.
- Announce standup and verdict success through the shared confirmation path.
- Use confirmation tone for the successful week-open action.
- Ask the navigation badge to refresh after a verdict.

**Checks:** Repeated equal messages announce again. Confirmations do not use an assertive alert. The verdict badge updates immediately.

**Size:** Small.

### P7 — Show the verdict reason limit

**Evidence:** The review reason has a 1,000-character server limit. The input gives no counter or inline guidance.

**Delivery:**

- Set the same maximum on the input.
- Show the remaining character count after the reason reaches 800 characters.
- Keep the Reject or Accept action available for every valid non-empty reason.

**Checks:** The browser cannot submit more than 1,000 characters. The user can see the limit before submission.

**Size:** Extra small.

### P8 — Refresh field-guide progress

**Evidence:** The identity menu held a cached `5/39` value while the guide page returned `5 of 40 tied`.

**Delivery:**

- Fetch field-guide progress each time the identity menu opens.
- Do not retain one person’s count after an identity change.
- Keep the backend registry as the only count source.

**Checks:** The menu and guide agree after a tie, identity change, or backend restart.

**Size:** Extra small.

### P9 — Restore the Planning page heading

**Evidence:** The Planning route has no level-one heading. Next.js route announcements use the title, then the `h1`, then the path.

**Delivery:** Add one descriptive `h1` before the planning sequence.

**Checks:** The page exposes one level-one heading and keeps the existing section order.

**Size:** Extra small.

### P10 — Make trust evidence truthful

**Evidence:** The Trust card can show `1/1 approved` and `last verdict was not an approval`. These statements use different verdict populations.

**Delivery:**

- Do not infer the last verdict from a zero qualified streak.
- Label all settled history separately from verified trust evidence.
- Return an explicit last verified verdict state.
- Show reviewer name, time, and identity strength in rejected-proposal inbox rows.

**Checks:** No row can state that the last verdict was a rejection when no verified verdict exists.

**Size:** Small to medium.

### P11 — Keep review statistics outside human performance scoring

**Evidence:** Review statistics group proposals by proposer. Meeting-note ingestion can create proposals under a human name.

**Delivery:**

- Restrict proposer-level review statistics to agent identities.
- Keep entity-level team aggregates.
- Keep scoped rejection text behind the existing visibility filter.

**Checks:** A human proposer never appears in `by_proposer`. Agent statistics remain available for authority decisions.

**Size:** Extra small.

### P12 — State the Authority identity requirement

**Evidence:** The Authority form looked active without a personal key. Its endpoint refused the write, but the controls did not explain why.

**Delivery:**

- Read the current identity from `/api/whoami`.
- Disable Authority writes without strong administrator identity.
- State the strong-identity and administrator requirements beside the controls.
- Keep backend authorization unchanged.

**Checks:** Weak identity cannot use the form. A strong administrator can use it.

**Size:** Extra small.

### P13 — Link proposal notifications to pending changes

**Evidence:** My Day showed a proposal and its review notification as two rows for one pending change.

**Delivery:**

- Add a typed `pending_change_id` relation to notifications.
- Suppress the notification when the linked proposal is already visible.
- Clear the linked notification when the proposal settles.
- Keep a bounded fallback for notifications created before the migration.

**Checks:** One visible pending proposal produces one My Day row. Unrelated notifications remain visible.

**Size:** Small.

## Proposals to defer

### D1 — Full task editing in the task panel

The workflow cost is credible, but the field set needs product decisions. Visibility changes also need a backend transition policy.

Revisit after the smaller capture, audience, and task-context fixes ship.

### D3 — Settings information architecture

Personal setup competes with operator and administrator sections. A complete restructure touches many permissions and deep links.

Start with section anchors and usage evidence before new routes or tabs.

### D4 — Browse filters and task pagination

The seeded page was dense. The study did not measure task-finding time or missed work.

Measure those outcomes before adding several filter dimensions.

### D5 — Planning action placement

Direct links from evidence to existing controls are valuable. The correct targets and return paths need one focused manager study.

Do not copy write forms into Planning or the engagement brief.

### D6 — Automatic agent execution

The empty runner allowlist is an intentional safety default. It prevents unexpected model spend and unattended writes.

Deployment operators can enable named agents through `SKEIN_AGENT_RUNNER`.

### D7 — New growth, meeting, PTO, or leadership systems

The live study showed clear current boundaries. It did not show demand for larger systems.

Keep growth interests display-only. Keep meeting preparation deterministic. Show time away only when it changes a current plan.

## Final review result

The final panel used a Product Manager, an Accessibility Auditor, an Agentic Identity and Trust Architect, and the Whimsy Injector.

The Product Manager selected P3, P5, and P4 as one daily-loop release. The accessibility review selected P4, P6, P7, and P9.

The identity and trust review selected P1, the inbox parts of P2, the narrow P10 correction, and P11.

The Whimsy Injector required literal text for identity, security, trust, limits, numbers, and verdicts.

### Selected for delivery

1. **P3** — Return human capture to My Day.
2. **P5** — Preserve audience in `/briefing`.
3. **P4** — Restore My Day focus and correct the capture instruction.
4. **P6** — Keep live regions mounted. Add write receipts, verdict focus recovery, and immediate badge refresh.
5. **P7** — Show and associate the 1,000-character verdict limit.
6. **P9** — Add the Planning `h1`.
7. **P1** — Project the canonical delegation eligibility into the task panel.
8. **P2, first part** — Move and focus the selected inbox. Project existing task context.
9. **P10, narrow** — Stop inferring a last verdict from a zero verified streak. Defer the larger trust evidence redesign.
10. **P11** — Remove human proposers from proposer-level review statistics.
11. **P8** — Refresh field-guide progress each time the identity menu opens.

### Selected for follow-up delivery

12. **P2, remaining part** — Add `Has work` and `All agents` controls.
13. **P12** — State the strong administrator identity requirement beside Authority controls.
14. **P13** — Link proposal notifications to pending changes and show one My Day row.

### Deferred after final review

- The full verified-trust and rejection-provenance design in **P10** needs one contract decision.
- D1 and D3 through D7 remain deferred for the reasons above.

### Delivery boundary

These releases reuse existing services, task fields, audience labels, and status infrastructure.

The follow-up adds one nullable notification relation and its index. It adds no model call, dependency, page, data table, authority level, or automated runner behavior.

## Second-pass proposals

The second pass starts from commit `9a1502a`. It uses new live evidence from the updated product.

### P14 — Make native agent writes durable

**Evidence:** The built-in agent displayed successful claim, progress, and acceptance-submission receipts for task `#29`. Fresh task, worklog, review, Mission control, and inbox reads showed no durable write. A claim-only request repeated the false success.

**Delivery:**

- Fix the shared native tool transaction boundary once.
- Commit each successful mutation and its activity evidence before the success event leaves the backend.
- Return a structured failure when the mutation rolls back or produces no durable state.
- Keep task state, worklog, proposal, provenance, and hash-chained activity evidence atomic.
- Do not add a frontend shadow store or sponsor bypass.

**Checks:** Claim, progress, submission, and sponsor acceptance agree across fresh service and REST reads. Rollback, concurrency, idempotency, provenance, and unchanged task `#25` also pass.

**Size:** Medium.

### P15 — Recover from a stale chat thread

**Evidence:** Two identities sent a message from a stale local thread. The backend returned `404`, but the optimistic user message stayed visible without a readable recovery. Selecting `+ New chat` fixed the flow.

**Delivery:**

- Handle the missing-chat response in the shared send path.
- Mark the optimistic message as failed.
- Clear the invalid thread selection.
- Offer a direct `New chat` recovery action.
- Do not retry the message automatically.

**Checks:** Specialist and ordinary messages use the same recovery. The failed message is not presented as sent, and a new chat can send once.

**Size:** Small.

### P16 — Refresh My Day after Quick capture

**Evidence:** Quick capture created assigned task `#27` and closed. The current My Day list stayed unchanged until a manual reload. Task completion already refreshes the list immediately.

**Delivery:**

- Reuse the existing client refresh path after a successful capture.
- Refresh My Day only when the current route contains that list.
- Keep the entered text if the write fails.
- Do not add polling, WebSockets, or a new state store.

**Checks:** A human task appears in My Day after capture without a reload. A failed capture keeps the modal and text available.

**Size:** Extra small.

### P17 — Keep mobile search inside the viewport

**Evidence:** At 360 pixels, the 320-pixel result panel started 107 pixels left of the viewport. Every result began mid-word.

**Delivery:**

- Anchor the existing result panel to responsive viewport insets.
- Constrain its width to the available viewport.
- Keep keyboard and pointer behavior unchanged.
- Do not build a new mobile navigation system.

**Checks:** Search input, results, and controls remain visible at 360 pixels without two-dimensional scrolling.

**Size:** Extra small.

### P18 — Preserve finding severity and ownership on conversion

**Evidence:** Dana converted a `high` ledger-integrity finding. Task `#28` became an unassigned `medium` task, and the conversion state gave no direct task link.

**Delivery:**

- Map finding severity to the matching task priority.
- Assign a human conversion to the person who converted it.
- Return the new task identifier from the conversion.
- Replace the terminal conversion marker with an `Open task` link.
- Keep the source-finding relation.

**Checks:** A high finding creates an assigned high task. The reader can open the task from the converted finding.

**Size:** Small.

### P19 — Reconcile review notifications

**Evidence:** Proposals `#4` and `#5` still appeared beside pre-migration notification rows. A meeting-ingest summary also claimed one pending proposal after its only proposal was rejected.

**Delivery:**

- Repair old standard proposal notifications with the typed relation.
- Give aggregate ingest notifications a typed batch relation.
- Recompute the aggregate pending count when a proposal settles.
- Clear the aggregate notification when the count reaches zero.
- Do not add another UI-only text filter.

**Checks:** One pending proposal produces one active row. Old standard rows reconcile once. A settled batch cannot state a false pending count.

**Size:** Medium.

### P20 — Use one incoming-promise term

**Evidence:** Quick capture labels one chip `promise (to us)` and tells the reader to use `awaiting:`.

**Delivery:**

- Select one literal incoming-promise term.
- Use it in the chip, prefix guidance, preview, accessible name, and field-guide text.
- Keep the old prefix as a compatible parser alias if the visible prefix changes.

**Checks:** One concept has one visible name. Existing captured text still parses.

**Size:** Extra small.

### P21 — Put outcome recording beside the missing outcome

**Evidence:** Two engagement pages tell a manager to use Chat when the intended outcome is absent.

**Delivery:** Link the missing state to the existing outcome action or a prefilled Chat action. Do not create a second outcome workflow.

**Checks:** The manager reaches the existing write path from the missing state and returns to the engagement.

**Size:** Small.

### Second-pass deferrals before final review

- Keep capacity controls deferred. The live state had no conflict.
- Keep unblock counts deferred. No current task released another task.
- Keep guide restructuring deferred. Length alone did not show abandonment.
- Keep model comparison deferred. The deployment had no model menu.
- Keep new PTO, lead-summary, growth, meeting, trust, and runner systems deferred.

## Second-pass final review

The final panel used a Product Manager, Backend Architect, Accessibility Auditor, and Whimsy Injector.

- All reviewers ranked P14 first. A write receipt cannot be trusted until the write commits.
- The Product Manager selected P15, P18, and P16 beside P14 as one write-truth release.
- The Accessibility Auditor also ranked P17 as a serious reflow defect.
- The Backend Architect placed contract-discovery gates on P19 and P21.
- The Whimsy Injector required literal wording for every write, failure, incident, review, count, and permission state.

### Selected for second-pass delivery

1. **P14** — Make native agent writes durable.
2. **P15** — Recover from a stale chat thread.
3. **P18** — Preserve finding severity and ownership on conversion.
4. **P16** — Refresh My Day after Quick capture.
5. **P17** — Keep mobile search inside the viewport.
6. **P20** — Use one incoming-promise term.

P17 stays in this delivery because the defect blocks result text at phone width and already has a precise roadmap contract.

P20 stays because it is an extra-small, already-decided lexicon repair beside the capture refresh.

### Deferred after second-pass final review

- **P19** needs one typed batch identity and an idempotent legacy-repair contract. Keep it as a separate data-cleanup release.
- **P21** needs a decision about the canonical outcome write surface and return path.
- Capacity, unblock counts, guide restructuring, model comparison, PTO, lead summaries, growth, trust expansion, and runner expansion remain deferred.

### Delivery boundary

The selected work adds no dependency, page, table, authority level, provider branch, model call, or runner behavior.

### Second-pass delivery result

All six selected proposals passed focused checks, full suites, the production build, the CI lint gate, and live traversal.

A code review found three defects: a ledger-finding identity bypass, non-atomic conversion, and an enabled stale composer. The fixes passed a second review with no remaining defect.

P19 and P21 remain deferred. Their data and workflow contracts are not part of this delivery.

P14 changes the shared native tool transaction boundary. The other selected work reuses existing routes, services, events, task fields, and responsive layout patterns.

## Second-pass delivery result

### Delivered

- **P14:** The specialized native-tool wrapper now commits before it yields the terminal event. Fresh reads confirmed claim, progress, acceptance proposal, and sponsor approval.
- **P15:** Chat `404` recovery removes the optimistic message, clears the stale thread pointer, and offers `New chat` without replay.
- **P16:** A successful Quick capture refreshes an open My Day list through the existing event seam.
- **P17:** Mobile search results use viewport gutters below `sm`. The 360-pixel browser check passed.
- **P18:** Task conversion keeps finding severity, assigns the converter, returns `task_id`, and opens the shared task panel.
- **P20:** The incoming-promise chip now uses `awaiting`. The parser and stored direction keep their compatibility contracts.

### Not delivered

- **P19:** Legacy and aggregate review-notification repair remains separate contract work.
- **P21:** Direct engagement-outcome editing remains deferred until the canonical write surface is selected.
- All other second-pass deferrals remain unchanged.

### Final boundary

The delivered work adds no dependency, migration, page, table, model call, provider branch, authority level, or runner behavior.

## Third-pass proposals

The third pass studies daily use for three to six months. It separates verified bounds from projected arrival dates.

### P22 — Make every stored report reachable

**Evidence:** Reports returns only the newest 50 artifacts. Scheduled output creates nine reports each week, so the visible window fills in about five and a half weeks.

**Delivery:**

- Add a cursor to the existing report query and route.
- Add `Older reports` on the Reports page.
- Keep the newest page as the default.
- Keep the selected report open while an older page loads.
- Correct the page claim that every report is present in the current list.

**Checks:** More than 50 reports remain reachable in stable newest-first order. Scope filters apply on every page.

**Size:** Small.

### P23 — Show the result of an idempotent ritual

**Evidence:** The scheduler had already run Friday close-out. The manual control returned `skipped`, but the page showed no result or report link.

**Delivery:**

- Return the existing artifact identifier when a weekly ritual already ran.
- Show a literal `already ran this week` receipt.
- Link the report that already exists.
- Do not force a rerun.
- Remove the stale comment that claims browser runs always force a report.

**Checks:** A first run creates one artifact. A second run returns the same artifact and a visible skipped receipt.

**Size:** Extra small.

### P24 — Filter task state before collection limits

**Evidence:** The task service returns at most 500 rows before Browse and the CLI remove completed tasks.

**Delivery:**

- Add optional task-state filters to the existing route and service.
- Apply each filter before `LIMIT 500`.
- Fetch open work separately for Browse and `skein tasks`.
- Fetch recent completed work by `completed_at DESC` for Recently shipped.
- Keep the current unfiltered route behavior for compatible callers.

**Checks:** Five hundred older completed urgent tasks cannot hide a later low-priority open task or a recent completed task.

**Size:** Small.

### P25 — Give invalid tasks a truthful terminal state

**Evidence:** A mistaken or validation task must remain active or become done. Done places it in Recently shipped and flow metrics.

**Delivery:**

- Add `void` as a terminal task state in a new migration.
- Require a non-empty reason for the transition.
- Keep the row, search entry, provenance, and activity history.
- Exclude void tasks from active work, due-soon work, Recently shipped, throughput, cycle time, and weekly completion ratios.
- Treat void tasks as terminal for dependencies, delegation, blockers, and forge events.

**Checks:** A void task stays directly readable and searchable. It does not appear as active or shipped work and cannot reopen through an external transition.

**Size:** Medium.

### P26 — Use one My Day projection in web and CLI

**Evidence:** The web renders the shared `attention` list. The CLI rebuilds rows from raw briefing sections and restores linked proposal-notification duplicates.

**Delivery:**

- Make `skein my-day` render the existing `attention` projection.
- Keep the existing personal and team audience labels.
- Do not copy notification suppression into the CLI.

**Checks:** One linked proposal appears once. An unrelated notification remains visible.

**Size:** Extra small.

### P27 — Remove resolved rework from the active agent inbox

**Evidence:** Proposal `#10` remained in the rejected inbox after approved proposal `#11` completed the same task.

**Delivery:**

- Exclude a rejected task-completion proposal when a later approved task-completion proposal exists for the same task.
- Apply the condition before the ten-row limit.
- Keep the rejected verdict and reviewer note unchanged.
- Keep both verdicts in trust history.
- Do not add an archive or acknowledgment state.

**Checks:** The rejected row remains in review history and trust counts. It leaves the active inbox after the replacement is approved.

**Size:** Extra small.

### P28 — Make every unread notification reachable

**Evidence:** My Day reads 20 unread rows and renders at most five groups. Older unread rows remain stored without a count or route.

**Delivery:**

- Return the complete unread count beside the My Day preview.
- Add `View all unread` to a cursor-paginated notification view.
- Reuse the canonical unread predicate and linked-notification suppression.
- Preserve keyboard focus when an older page loads.
- Keep My Day concise.

**Checks:** More than 20 unread rows remain reachable. The shown count equals the reachable unread set.

**Size:** Medium.

### P29 — Use one reachable pending-review queue

**Evidence:** Approvals returns the newest 200 pending proposals. My Day reads the oldest 50, and one ingest can create 500.

**Delivery:**

- Select one canonical pending predicate and ordering.
- Make the My Day preview a subset of Approvals.
- Add cursor navigation to Approvals.
- Keep the badge total over the complete readable queue.
- Keep proposal scope checks on every page.

**Checks:** After 500 proposals, every My Day review opens in Approvals. Every readable proposal remains reachable.

**Size:** Medium.

### P30 — Enforce the authority half-life at the policy gate

**Evidence:** Elevated authority receives a 90-day review date. The shared policy gate ignores that date, while the feature reference calls it an authority half-life.

**Delivery:**

- Preserve the configured authority level for audit.
- Expose configured level, effective level, and review date from one resolver.
- After the review date passes, force `PolicyEffect.REVIEW` for `autonomous` and `notify`.
- Force review independently of `SKEIN_AGENT_REVIEW`.
- Keep `forbidden` absolute.
- Restore elevated effective authority only after a strong administrator reconfirms it.

**Checks:** Cover the review date, the next day, both review-gate settings, reconfirmation, and a real tool write.

**Size:** Medium.

### P31 — Put outcome recording beside the missing outcome

**Evidence:** An engagement with no intended outcome directs the manager to Chat. The page provides no direct action or prefilled route.

**Delivery:**

- Link the empty state to the existing outcome write path.
- Prefill the engagement and intended-outcome field.
- Return to the engagement after the write.
- Do not create a second outcome system.

**Checks:** A manager reaches the existing write path from the empty state and returns to the updated engagement.

**Size:** Small.

### P32 — Disclose the My Day task window

**Evidence:** My Day returns at most 200 active tasks and gives no overflow count. Equal-ranked rows have no final task-ID key.

**Delivery:**

- Add task ID as the final stable order key.
- Return the complete active-task total.
- Show the hidden count with a Browse route in the web and the `skein tasks` command in the CLI.
- Keep My Day capped and prioritized.

**Checks:** A new ordinary capture remains visible after 200 equal-ranked tasks. Both clients state the hidden count.

**Size:** Small.

### Third-pass deferrals before final review

- Keep Insights pagination deferred until use shows harm from older findings leaving the feed.
- Keep a global history center deferred. Each owning surface must preserve its own records.
- Keep automatic engagement-cost attribution deferred. Nine unlinked calls show incomplete maintenance, not a safe attribution rule.
- Keep a generic rejected-proposal archive deferred. P27 removes the verified lifecycle fault without new state.
- Keep model-menu, growth, field-guide, PTO, and lead-summary expansion deferred. The study found no new demand.

## Third-pass final review

The final panel used a Product Manager, Backend Architect, Accessibility Auditor, and Whimsy Injector.

The Product Manager selected P30, P22, P24, P29, P23, P27, and P26 as one operational-truth release.

The Accessibility Auditor ranked P29 first. The audit also requested P28 and P32 before accessibility sign-off for high-volume use.

The final selection defers P28 and P32 because the study did not observe either overflow. P24 ships first because it corrects the nearer task-limit fault.

The authority reviews selected one contract. The configured grant remains for audit, but its elevated effect expires to required human review.

The Whimsy Injector required literal text for authority, review, counts, notifications, failures, tasks, and verdicts.

### Selected for third-pass delivery

1. **P30** — Enforce the authority half-life at the policy gate.
2. **P22** — Make every stored report reachable.
3. **P24** — Filter task state before collection limits.
4. **P29** — Use one reachable pending-review queue.
5. **P23** — Show the result of an idempotent ritual.
6. **P27** — Remove resolved rework from the active agent inbox.
7. **P26** — Use one My Day projection in web and CLI.

### Deferred after third-pass final review

- **P25** needs a focused task-lifecycle release. Every terminal-state consumer must change together.
- **P28** advances when a live user exceeds 20 unread rows or absence testing proves a missed action.
- **P31** remains blocked until the canonical outcome write surface and return path are named.
- **P32** advances when a reader approaches 200 active tasks or one task is hidden from My Day.
- Insights and private-history cursor work remains separate reachability work.
- All other third-pass deferrals remain unchanged.

### Third-pass delivery boundary

This release adds no dependency, model call, provider branch, table, task status, archive state, or automatic attribution rule.

It preserves immutable review history and configured authority records. It changes read projections, cursor access, and effective policy behavior.

### Third-pass delivery result

All seven selected proposals are implemented.

- **P22:** Reports has a stable older-page cursor. Both list endpoints scan past workplace-policy denials.
- **P23:** A repeated ritual shows the skipped result and links the existing report. Concurrent repeats cannot observe an incomplete claim.
- **P24:** Task state and workplace policy apply before visible limits. Browse and `skein tasks --all` use one snapshot of independently bounded open and completed slices.
- **P26:** Web and CLI render the same attention projection. The CLI uses public group names and states review overflow.
- **P27:** Approved completion removes earlier same-task rejection notes from active correction work, including replacement-agent completion. Verdict history stays unchanged.
- **P29:** Approvals, My Day, the badge, and CLI use one oldest-first readable queue. Deep links are bounded, seen writes stay within endpoint limits, and settled history remains compatible.
- **P30:** Elevated authority expires to required review. The configured grant stays in audit data, and a concurrent approval cannot overwrite `forbidden`.

The implementation review found defects in concurrency, policy filtering, compatibility, bounds, and CLI wording. Each confirmed defect received a focused regression and a root-cause correction.

### Third-pass work not delivered

- **P25:** The truthful `void` task state remains a separate lifecycle release.
- **P28:** Unread-notification history remains separate cursor work.
- **P31:** Direct outcome editing still needs one canonical write surface.
- **P32:** My Day task-window disclosure remains deferred until live use approaches the limit.
- Insights and private-history cursors remain separate reachability work.

No deferred item was partially implemented.
