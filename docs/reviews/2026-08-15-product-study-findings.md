# Live product study — findings

**Closed.** Observations from four passes of live use, kept for the evidence
behind each proposal and for the problems that were seen and deliberately not
built. Everything actionable left this file: what shipped is in
`docs/FEATURES.md` and the sibling results file, and what did not is in
`docs/ROADMAP.md` under "From the live product study (2026-08-15)".

A finding here describes the product AT THE TIME IT WAS SEEN. Several were
fixed by the same branch, so a problem stated below is not a claim about
current behavior — check the code before acting on one.

## Study record

- Branch: `proposed-features`
- Application: live local Skein instance
- Frontend: `http://localhost:3000`
- Backend: `http://localhost:8000`
- Provider at study start: `ollama`
- Model at study start: `glm-5.2:cloud`
- Study date: 2026-08-15

## Baseline

The application started before this study and both services were healthy. The seeded instance contained tasks, engagements, proposals, events, agents, and extension data.

The first page opened as `dana`. My Day reported that nothing waited on this user. The team queue still contained four proposals and three review notifications.

### What works well

- The top navigation presents the product around daily jobs instead of database entities.
- My Day separates personal needs from team queues. This prevents shared work from looking personally assigned.
- The empty personal state has useful warmth and does not hide the shared queue.
- Search, capture, identity, and the main destinations remain visible without crowding the page.
- The seeded page gives useful evidence for agents, extensions, review gates, events, and field-guide suggestions.

### Problems to examine

- The onboarding card showed `3/4`, but the missing standup action sat above an empty task state. The next action was not visually dominant.
- My Day stated that nothing waited on `dana`, while the navigation badge showed four verdicts. The page explains the difference, but the first impression still conflicts.
- The team queue repeated each extension proposal and its review notification. The related rows increased scanning cost.
- The activity summary contained many low-context extension correlation identifiers. These identifiers are useful for diagnosis but noisy for a daily brief.
- The field-guide suggestion appeared after a long activity list. A new user can miss it before they reach the page end.

## Persona sections

### Manager — mario

#### Month pattern

The manager journey covered weekly planning, engagement health, approvals, requests, insights, reports, and one real verdict.

The study treated the current seeded records as four weeks of accumulated work. Repeated visits represented the Monday, midweek, Friday, and month-end loops.

#### What works well

- The planning page follows the meeting order. Last week appears before new commitments.
- Health calls include receipts. The manager can inspect the reason instead of trusting a color.
- The engagement brief combines health, milestones, work, blockers, lessons, reports, and plan drift.
- The approval card puts the agent summary beside recent worklog evidence.
- The review gate exposed a real contradiction. The proposal claimed six completed pages, while the worklog showed four.
- Reports render as readable documents. They do not expose raw Markdown or server paths.
- Insights protect the anti-surveillance rule. The page measures systems and funnels, not individual output.
- Intake decisions preserve the reason. A requester can read why the team accepted or declined the work.

#### Problems

- The planning page has no level-one heading. The page starts with section navigation and explanatory text.
- The critical ledger finding dominates weekly planning. This is correct during an incident, but it needs a direct incident action path.
- The engagement brief said that the intended outcome was absent. It sent the manager to Chat instead of offering the existing write near the missing field.
- The Browse page grew to more than 600 accessibility-tree nodes in seeded use.
- Browse showed 24 open tasks and a long activity stream without a local limit or collapse control.
- Extension-generated tasks overwhelmed the hand-authored work. The page did not offer a source, engagement, or owner filter.
- Three identical extension proposals appeared as separate full cards. Batch selection exists, but comparison still costs repeated reading.
- My Day repeated each proposal and its review notification as separate team-queue rows.
- The rejection field did not show its 1,000-character server limit.
- The first live attempt looked disabled, but current source disables only an empty reason. Treat this as a harness observation, not a confirmed product defect.
- An over-limit reason has no counter or inline validation before the server rejects it.
- After a successful rejection, the proposal disappeared without a visible completion message in the accessibility snapshot.
- The navigation badge kept the old count until another page load.
- The planning page told the manager to draft on Work → Health. The action exists there, but the cross-page step interrupts the meeting.
- Capacity shows allocation totals, but no allocation control appears near that evidence.

#### Real action

The manager rejected proposal `#2`. The reason stated that the worklog showed four of six pages complete.

The action succeeded after the reason was shortened. The first reason disabled the action with no explanation.

### Developer — marcus

#### Month pattern

The developer journey covered My Day, standups, task status, task detail, inline edits, search, capture, and deterministic chat commands.

The journey included one real standup and one real captured task. The captured task became task `#25`.

#### What works well

- My Day showed the owned blocker before the task list.
- Task actions use short verbs. The developer can start or finish work without opening the Browse page.
- The standup form sits beside assigned work. The workflow does not require another destination.
- Standup visibility supports workspace, crew, and private scopes.
- The successful standup cleared the form and appeared at the top of recent activity.
- Quick capture showed `will file as: task` before the write.
- Quick capture gave a precise receipt: `Captured as task #25`.
- Search found task `#2` from `task 2` and opened the shared task panel.
- The deterministic `/briefing` command returned without a model call.
- Chat saved the command as a thread and made it available in the chat list.

#### Problems

- The `Since yesterday` section again contained a long extension activity stream.
- The section pushed the field-guide suggestion far below the daily work.
- The task panel did not offer edit controls for a human assignee.
- The task panel only offered delegation and provenance for task `#2`.
- The Browse inline editor exposed title, assignee, and due date only.
- The inline editor did not expose priority, description, status, `waiting_on`, commitment week, or visibility.
- My Day can change status, but a developer must use another path for most task corrections.
- Human task progress has no worklog. The worklog section states that notes come from delegated work.
- The `/briefing` response said `Pending reviews: 3` without saying that these reviews belong to the team queue.
- The web My Day page makes that audience distinction. The chat command loses it.
- Search needed an explicit Enter before the result region appeared. Current search uses explicit submission by design, so this is not a confirmed defect.
- Current source assigns `Ctrl+K` to search. Quick capture has no keyboard shortcut.
- The My Day empty-task text incorrectly shows the search shortcut beside a quick-capture instruction.
- The standup button shows a transient `✓ posted` state for 700 milliseconds. This state can disappear before an accessibility snapshot or announcement.
- Current source reloads My Day after a standup. Reproduce the stale onboarding count before changing that flow.

#### Real actions

- Posted standup `#3` for `marcus`.
- Captured task `#25`: `Review monthly usability study findings`.
- Ran `/briefing` in a new saved chat.

### Team member — ava

#### Month pattern

The team-member journey covered My Day, meeting preparation, the field guide, Settings, growth interests, and the private 1:1 boundary.

The journey included one real profile write. Ava saved `usability research, observability` as growth interests.

#### What works well

- My Day clearly stated that no personal action waited.
- The event row offered a direct `what is open?` preparation control.
- The event control returned an honest empty state instead of hiding the feature.
- The field guide explains each feature with a purpose, a method, and a deep link.
- The knot names give the guide a distinct product voice.
- Settings explains why a personal key is necessary before it asks for one.
- The 1:1 page refused private data with a direct path to Settings.
- Growth interests stated that they are display-only and not an automatic score.
- Saving growth interests produced a visible `Saved.` status.
- Settings explains the direct-write risk when the agent review gate is off.

#### Problems

- Ava leads the Onboarding revamp engagement, but My Day showed no lead responsibility or engagement summary.
- Ava has upcoming PTO, but My Day did not show it near the daily plan.
- The field guide showed all 40 cards in one long page with no tied or untied filter.
- The identity menu returned `Field guide 5/39` while the guide page returned `5 of 40 tied`.
- The navigation cached its first answer. The guide also calculated its denominator after it removed the `ties: never` field from projected cards. Either defect can make the two surfaces disagree.
- Current source assigns `Ctrl+K` to search and the Search & `/ask` card follows that contract.
- The incorrect shortcut appears in My Day, where a quick-capture instruction displays the search key.
- Settings mixed personal setup, operator setup, and administrator controls in one long page.
- A user without a key still scanned disabled team model, long-chat, deployment, backup, crew, and roster sections.
- The first-week setup count did not explain which completed action produced each tied guide card.
- My Day again repeated the same three proposals and three notifications.
- The field-guide suggestion remained below the long activity section.

#### Real action

Ava saved growth interests. The page gave immediate and accessible confirmation.

### Reviewer and administrator — dana

#### Month pattern

The reviewer journey covered the bench, mission control, agent inboxes, authority, trust, activity, charter, and manager controls.

The journey also checked whether the earlier rejection reached the agent. The research-agent inbox contained the full reviewer note.

#### What works well

- The Agents page states the active provider, model, review-gate state, and long-chat behavior.
- The page states the consequence of a disabled review gate before it shows authority controls.
- The bench cards explain each specialist's role and voice.
- Mission control shows open tasks, pending proposals, and last-seen data without a hidden hover state.
- The research-agent inbox showed the delegated task and the exact rejection note.
- Trust data changed after the real rejection. The research-agent row showed `0/1 approved`.
- The authority section explains which levels still matter when the review gate is off.
- The Activity page states its privacy boundary. It does not show other humans' rows.
- Activity rows use complete sentences and expose raw rows only through an explicit toggle.
- The Charter page puts review dates on working agreements.

#### Problems

- Mission control showed 19 agent identities. Most rows said only that the agent was idle.
- There was no `active only`, `has work`, or `hide idle` control.
- The selected research-agent inbox opened more than one viewport below the selected row.
- The page did not scroll the inbox into view after the explicit inbox action.
- A click appeared to do nothing until the reviewer scrolled past authority, trust, and memory.
- The authority form looked active without a personal key in the browser.
- The page did not state beside the Set button that this write needs strong identity.
- The Activity page was dominated by one extension agent and opaque correlation identifiers.
- Activity offered pagination but no actor, action, or salience filter.
- The bench and mission-control lists repeated many of the same agent names.
- The page mixed discovery, operations, policy, trust, memory, inbox detail, and flock history in one long surface.
- The trust row said `1/1 approved` and `last verdict was not an approval` for planner-agent. That combination needs a clearer time basis.

#### Evidence from the real verdict

The rejection traveled to the correct agent inbox with the correct task, sponsor, and reviewer note.

### Agent teammate — built-in `agent`

#### Month pattern

The agent journey used the real chat agent and its `my_agent_inbox` tool. Marcus delegated study task `#25` to the built-in agent.

The agent read its inbox through the live model path. The study made no domain write after the agent asked for permission to claim work. The chat transcript and usage records still recorded the turn.

#### What works well

- Delegation produced a precise sponsor receipt.
- The task panel changed immediately to show the agent and sponsor.
- The agent inbox returned the exact task, status, priority, and sponsor.
- The agent distinguished delegated work from questions, rejected proposals, and notifications.
- The agent stated that the task was unstarted.
- The agent asked for permission before it claimed or changed the task. This was observed model behavior, not an enforced product gate.
- The request caused no task or proposal state change. The chat transcript and usage records still changed.
- The tool call appeared in the transcript as `my_agent_inbox`.

#### Problems

- The task delegation menu listed `mcp-agent` as a valid destination.
- Delegation to `mcp-agent` failed with `'mcp-agent' is already owned by another machine identity`.
- A destination that the service will always refuse must not appear as a selectable agent.
- The menu also listed service identities such as `atlas-events` and `atlas-sync`. These identities can have the same ownership conflict.
- The task panel gave no explanation of agent identity ownership before the failed action.
- The unattended runner is off through an explicit allowlist. The Agents page states this configuration and its remedy.
- The agent inbox is also available through the deterministic Agents REST surface. The selected web inbox opened far below its trigger.
- The inbox projection omits description, due date, milestone, and engagement fields even when the task contains them.
- Task `#25` also had no engagement, description, due date, or acceptance criteria in its source record.
- A linked task needs enough projected context for the agent to request the existing engagement context pack.

#### Real actions

- Delegated task `#25` to the built-in `agent` with Marcus as sponsor.
- Asked the agent to read its inbox without changing data.
- The agent found task `#25` and asked for permission before work.

## Cross-persona month summary

### Repeated strengths

- The service receipts are specific and useful.
- The product separates personal work from team queues in the web My Day page.
- Provenance, review, privacy, and identity rules are visible in the interface.
- The deterministic core is useful without a model.
- Deep links converge on the task panel and engagement brief.
- Empty states usually state what the user can do next.

### Repeated costs

- Long mixed pages become noisy after extension or agent activity grows.
- The same extension work dominates My Day, Browse, Activity, and Agents.
- Several controls disable or fail without a nearby reason.
- Audience wording is not consistent between web My Day and chat `/briefing`.
- Cached discovery counts and one quick-capture instruction drifted from the current behavior.
- The selected Agents inbox can open outside the current viewport.
- Task editing is split across My Day, Browse, the task panel, chat, and API paths.
- The delegation picker does not project machine-identity eligibility.
- The trust row can mix all verdicts with a qualified streak and state a false last-verdict result.

### Scenario conclusion

Skein has strong coordination primitives and unusually honest system boundaries. The study found three problem classes.

1. Extension-heavy data can hide the next human action.
2. Several workflows separate evidence from the action that resolves it.
3. Some interfaces do not project identity, trust, or audience rules from the service contract.

This was one seeded scenario on one day. Repeated visits do not establish monthly frequency or long-term behavior.

## Persona review panels

Each panel used no more than four reviewers. Every panel included the Whimsy Injector.

### Manager panel

Reviewers: Product Manager, UX Researcher, Workflow Optimizer, and Whimsy Injector.

- Preserve the approval card because it keeps the claim beside the worklog evidence.
- Fix verdict validation, completion feedback, and badge refresh before adding new approval features.
- Put existing actions one click from planning, outcome, incident, and capacity evidence.
- Treat Browse node count as a diagnostic. Measure time to find work and complete an action.
- Do not build a separate month-end dashboard from this study.
- Keep manager surfaces calm. Use warmth only after an empty or finished state.

### Developer panel

Reviewers: Developer Advocate, UX Researcher, Workflow Optimizer, and Whimsy Injector.

- The canonical task panel has rich read detail but no ordinary task-edit action.
- `/briefing` loses the web audience split and can create false personal urgency.
- A human quick-captured task starts unassigned, so the My Day instruction does not return the task to My Day.
- The search submit model is intentional. The study did not show abandonment.
- The delegated worklog has a specific acceptance purpose. Do not expand it without user evidence.
- Use stable receipts for writes. Do not add rotating jokes to operational states.

### Team-member panel

Reviewers: Product Manager, UX Researcher, and Whimsy Injector.

- Correct field-guide trust defects before adding more discovery features.
- Put the existing field-guide suggestion before historical activity.
- Treat engagement-lead and time-away cues as hypotheses. Show them only when they change a current plan.
- Keep personal Settings work before operator and administrator sections.
- Preserve the direct private-data, key, and growth-interest boundaries.
- The knot names carry enough voice. Do not add points, ranks, streaks, or large celebrations.

### Reviewer and administrator panel

Reviewers: Agentic Identity and Trust Architect, Security Engineer, UX Architect, and Whimsy Injector.

- The delegation menu and service enforcement disagree about machine-identity ownership.
- The trust row combines different verdict populations and can state a false last-verdict result.
- The selected inbox belongs directly after Mission control, with focus and a selected state.
- The authority endpoint fails closed. The active-looking form still needs a clear identity prerequisite.
- Review statistics must not expose person-keyed human proposal totals.
- Keep security, trust, authority, privacy, and verdict text literal.

### Agent-teammate panel

Reviewers: AI Engineer, Workflow Architect, and Whimsy Injector.

- The built-in agent receives enough data to triage work and asks before writes.
- The delegation menu advertises destinations that the service will always reject.
- The runner allowlist is a safe deployment choice, not a missing automation feature.
- The inbox needs task context fields that already exist in the task record.
- The study did not exercise claim, progress, submission, verdict, or rework branches.
- Keep identity, ownership, sponsor, permission, and verdict text direct.

## Follow-up resolution

Three findings moved into the follow-up delivery.

- Mission control now starts with `Has work`. `All agents` restores the complete operational list.
- Authority controls now state the strong administrator requirement and stay disabled for weak identity.
- Proposal notifications now carry a typed pending-change relation. My Day omits a linked notification when its proposal is visible.

The Authority review found one important contract detail. The strict `admin` flag does not include the trusted-header scarcity fallback that `AdminUser` accepts.

The final contract adds `can_administer` to `/api/whoami`. This value uses the same authorization rule as the Authority endpoint.

The live database applied migration 002. The live Agents page confirmed both Mission control views and the weak-identity Authority state.

The live ingest flow creates one batch summary instead of a per-proposal notification. The typed notification lifecycle therefore uses PostgreSQL service checks for its final evidence.

## Second pass — updated product

### Study record

- Baseline commit: `9a1502a`
- Branch: `proposed-features`
- Application: live local Skein instance
- Frontend: `http://localhost:3000`
- Backend: `http://localhost:8000`
- Provider: `ollama`
- Model: `glm-5.2:cloud`
- Study date: 2026-08-15

### Updated baseline

My Day opened as `mario`. The page showed three personal needs and two pending team proposals.

The first release improved the page order, audience labels, write receipts, and field-guide progress. The second pass starts from those corrected surfaces.

#### What works well

- The field-guide suggestion remains before historical activity.
- The Inbox badge and pending proposal count agree.
- The review queue still separates team proposals from personal needs.
- Human task capture and verdict focus behavior remain in place.
- The running app loaded the new Mission control and Authority contracts.

#### Problems to examine

- The notification `mario ingested meeting notes: 1 proposal awaiting review` remains after proposal `#7` was rejected.
- The stale batch summary states that one proposal awaits review when no proposal from that paste remains pending.
- Proposals `#4` and `#5` still appear beside pre-migration `Review needed` notifications. The migration cannot infer old links.
- Historical extension activity still dominates the lower part of My Day.

### Manager — mario

#### Updated month pattern

The manager repeated My Day, weekly planning, engagement health, engagement detail, and deterministic chat briefing loops.

#### What works well

- Planning keeps one `Planning` level-one heading and follows the meeting order.
- `/briefing` now labels zero personal reviews separately from two pending team reviews.
- The Inbox badge also showed two pending verdicts.
- Health uses an honest empty state when nobody exceeds capacity.
- The engagement brief keeps health, outcome, work, lessons, and reports in one place.
- Starting a fresh chat made `/briefing` save and return without a model call.

#### Problems

- The Acme engagement still sends the manager to Chat to record its missing outcome.
- A stale unsaved chat ID returned `404 no chat` for both a specialist request and `/briefing`.
- The failed chat showed the user message but no readable recovery in the accessibility snapshot.
- Selecting `+ New chat` recovered the workflow. The product did not state that fix.
- The stale ingest summary on My Day still claimed that proposal `#7` awaited review after rejection.
- Capacity was not a live problem in this dataset. The honest empty state was sufficient.

#### Real action

The manager started a fresh chat and ran `/briefing`. The command saved as a chat and preserved the personal-team split.

### Cross-persona mobile navigation check

At a 360-pixel viewport, the nav search results opened at `left: -107.4px` with a 320-pixel width.

The screenshot began each result mid-word. The left part of every result was outside the viewport, although the accessibility tree contained the complete text.

This confirms the open mobile-search roadmap defect. Ordinary horizontal-overflow checks do not detect left-side clipping.

### Developer — marcus

#### Updated month pattern

The developer repeated My Day, task detail, Quick capture, task completion, search, and deterministic chat loops.

#### What works well

- My Day led with the owned blocker and active task.
- The Quick capture preview said `will file as: task` before the write.
- The capture API created task `#27` under Marcus.
- After a manual reload, task `#27` appeared in My Day as a medium-priority todo.
- Marking task `#27` done removed it from the list immediately.
- The task panel still exposes the canonical detail without changing pages.

#### Problems

- Quick capture closed after the successful write, but My Day did not show task `#27` until a manual reload.
- Waiting two minutes did not expose the capture receipt in the accessibility snapshot.
- The `promise (to us)` chip still teaches a different term from its `awaiting:` prefix.
- The mobile search result panel clips its left 107 pixels at a 360-pixel viewport.
- My Day does not show unblock counts, but none of Marcus's current tasks released another task. This dataset gives no prioritization evidence.

#### Real action

Marcus captured task `#27`, `Second-pass daily workflow check`. He then marked it done from My Day.

### Team member — ava

#### Updated month pattern

The team member repeated My Day, field-guide, personal setup, calendar-feed, and agent-connection checks.

#### What works well

- My Day said that nothing waited on Ava. Her time away starts in a later week, so its absence from the daily plan was correct.
- The field-guide menu and the guide page both showed `6/39`.
- The delegation card now explains `Has work` and `All agents` in the place where a teammate learns delegation.
- Settings explains the personal-key boundary before private surfaces and the CLI.
- The growth-interest text states that interests are display-only and are never scored or matched automatically.
- The agent-connection section states that the review gate is off and names the control that stops an agent.
- The calendar-feed section warns that a hosted calendar copies titles outside the team network.

#### Problems and limits of the evidence

- Ava leads Onboarding revamp, but she has no assigned work or current personal obligation. The study still has no evidence for a new lead-summary feature.
- The field guide remains long. The study has no new evidence that readers abandon it.
- The deployment has no model menu. This dataset cannot support a model-price comparison feature.
- Quick capture still labels an incoming promise as `promise (to us)` while the prefix is `awaiting:`.

#### Real action

Ava opened the updated delegation card and checked her personal setup, growth interests, calendar feed, and agent-connection instructions.

### Reviewer and administrator — dana

#### Updated month pattern

The reviewer repeated Mission control, Authority, trust, Approvals, Activity, private 1:1 access, findings, close-out, and handoff loops.

#### What works well

- Mission control started with three identities under `Has work`.
- `All agents` restored all 19 operational identities, including idle service and MCP identities.
- The selected agent inbox moved focus to its heading and showed task priority, sponsor, status, and rejected-work notes.
- Every Authority field stayed disabled for weak identity.
- The Authority helper named administrator access and strong identity. It also named deployment sign-in and a personal API key as valid sources.
- Trust rows said `no verified verdicts` instead of turning an unverified rejection into a fact.
- The review queue kept the 1,000-character limit beside the rejection reason and did not show an idle countdown.
- Activity rows expanded to show the sequence, timestamp, actor, action, and detail.
- The 1:1 page refused private data without a personal key and linked to Settings.
- Friday close-out created a report and linked to it from the action receipt.
- The engagement handoff appeared in the engagement and Reports without a page reload.
- Insights withheld a blocker trend verdict because the sample had fewer than eight records.

#### Problems

- My Day showed proposals `#4` and `#5` beside old `Review needed` notifications for the same proposals. These rows predate the typed relation.
- The aggregate meeting-ingest notification still said that one proposal awaited review after proposal `#7` was rejected.
- Converting the ledger finding to a task created unassigned task `#28`. My Day still said that Dana had no assigned task, and the conversion gave no clear path to the new task.
- The source finding is `high`, but converted task `#28` has `medium` priority. The conversion lowered the severity of a ledger-integrity incident.
- The live data reported that the activity ledger and its anchor logs disagree at entry `58`. The product showed a direct incident instruction, and Dana converted it to a task.

#### Real actions

Dana generated the 2026-08-15 Friday close-out and handoff artifact `#5`. Dana also converted the ledger finding to task `#28`.

### Updated month coverage

#### Week 1 — orient and capture

- Day 1: each human persona opened My Day and checked the personal and team audiences.
- Day 2: Marcus used Quick capture. Ava checked the capture types and setup instructions.
- Day 3: Mario checked Planning, Health, and engagement detail.
- Day 4: Mario repeated `/briefing` and recovered by starting a new chat after a stale-thread error.
- Day 5: the study checked standup, event preparation, notifications, and the field guide.

#### Week 2 — execute and coordinate

- Day 6: Marcus opened active work and completed the study task that he captured.
- Day 7: each daily view kept the owned blocker above ordinary work.
- Day 8: Dana checked `Has work`, `All agents`, and the delegated agent inboxes.
- Day 9: Dana inspected the pending extension proposals and the rejection reason flow.
- Day 10: Mario checked the capacity, intake, and stakeholder surfaces.

#### Week 3 — adjust and govern

- Day 11: Planning and the commitment line were checked again.
- Day 12: engagement drift, decisions, and outcome gaps were checked.
- Day 13: the study checked the stale aggregate notification from meeting ingest.
- Day 14: Dana checked Insights and expanded Activity provenance.
- Day 15: Dana checked Authority, trust, private-data access, Settings, and deployment controls.

#### Week 4 — close and learn

- Day 16: the study checked engagement drift and the missing intended outcome.
- Day 17: Dana generated and read a Friday close-out and an engagement handoff.
- Day 18: the close-out showed the open external promise and team question.
- Day 19: each human persona repeated its main daily loop.
- Day 20: the study ranked residual problems from live evidence instead of roadmap breadth.

### Agent teammate — built-in `agent`

#### Updated month pattern

The agent journey covered inbox inspection, sponsorship, delegation, claim, progress, submission, and sponsor-review discovery.

#### What works well

- Mission control exposed task `#25` with its sponsor and priority.
- The sponsor created task `#29`, then delegated it to `agent` from the shared task panel.
- Delegation gave a direct receipt: `Task #29 delegated to agent. You are the sponsor.`
- The built-in chat showed each requested tool call and described the expected claim, worklog, and acceptance sequence.
- Agent-owned REST access stays unavailable. The product keeps agent writes on the tool path.

#### Critical problem

- The chat displayed successful write receipts for `claim_delegated_task`, `report_progress`, and `submit_for_acceptance`.
- The chat stated that task `#29` became `in_progress`, gained a worklog note, and created proposal `#8`.
- A fresh task read still showed task `#29` as `todo` with no worklog.
- A fresh review read did not contain proposal `#8`.
- Mission control showed two open tasks and zero pending proposals for `agent`.
- The study then asked the agent to call only `claim_delegated_task(29)`. The chat again displayed a successful write receipt.
- A fresh task read still showed `todo` and no worklog.
- The agent tool path therefore reports durable success for writes that do not persist. A sponsor cannot accept work that the review API cannot read.

#### Other observations

- The first send used a stale chat ID and returned `404`. Selecting `+ New chat` was necessary before the agent run could start.
- The study did not modify pre-existing task `#25`. It created task `#29` for this live flow, then delegated it to the built-in agent.
- Task `#29` remains delegated and `todo` because the claimed agent writes did not persist.

#### Real actions

Marcus created and delegated task `#29`. The built-in agent attempted claim, progress, and acceptance submission through its native tools.

## Second-pass persona review panels

### Manager panel

Reviewers: Product Manager, Workflow Architect, and Whimsy Injector.

- Fix stale-chat recovery first. The shared send path must mark the optimistic message as failed, clear the stale thread, and offer `New chat`.
- Clear or update the aggregate meeting notification when its proposals leave the pending state.
- Fix mobile search positioning without a new navigation system.
- Keep the engagement-outcome problem limited to a direct existing action. Do not design a new outcome system.
- Keep My Day audience separation, Planning, `/briefing`, capacity empty states, close-out, and handoff unchanged.
- Do not build allocation controls or unblock counts from this dataset.
- Preserve warmth in empty and finished states. Keep chat errors, counts, mobile defects, and outcome instructions literal.

### Developer panel

Reviewers: Frontend Developer, Workflow Architect, and Whimsy Injector.

- Refresh or invalidate My Day after a successful Quick capture. Do not add polling, WebSockets, a new state store, or a new dependency.
- Preserve the current immediate removal after task completion.
- Keep the mobile search panel inside a 360-pixel viewport and add a responsive regression check.
- Use one literal term for an incoming promise across the chip, prefix, accessible label, and related text.
- Keep blocker-first ordering, capture preview, assignment, search, and the task panel unchanged.
- Do not build unblock counts until a real fixture proves the full blocked-to-released transition.
- Keep task, count, and error text literal. Warmth belongs only in completed or empty states.

### Team-member panel

Reviewers: UX Researcher, Accessibility Auditor, and Whimsy Injector.

- Keep the honest My Day empty state, consistent `6/39` progress, delegation guidance, and direct Settings boundaries.
- Standardize the incoming-promise term across the chip, prefix, help text, and accessible name.
- Treat mobile search clipping as a WCAG reflow problem. Keep content available without two-dimensional scrolling.
- Do not add a model menu for a deployment that has no configured choices.
- Do not redesign the guide from length alone. The study has no abandonment, comprehension, or completion evidence.
- Do not invent work from a future PTO date or an engagement-lead label without a current obligation.
- Preserve warmth in an honest empty state. Keep privacy, keys, permissions, counts, and instructions direct.

### Reviewer and administrator panel

Reviewers: Agentic Identity and Trust Architect, Security Engineer, UX Architect, and Whimsy Injector.

- Preserve finding severity when the finding becomes a task. The high ledger finding must not become a medium task.
- Give the conversion a clear owner or assignment rule, and return a direct link to the new task.
- Reconcile pre-migration proposal notifications at the shared data source. Do not add another UI-only hide rule.
- Recompute or clear a batch-ingest notification when its linked proposals leave the pending state.
- Keep weak-identity Authority gating, verified-trust wording, private-data isolation, review limits, Activity detail, incident wording, close-out, and handoff unchanged.
- Keep ledger, authority, trust, privacy, notification counts, and incident text literal.
- Do not add a new administration or incident-response system for these repairs.

### Agent-teammate panel

Reviewers: Backend Architect, Workflow Architect, Agentic Identity and Trust Architect, and Whimsy Injector.

- Treat the false agent-write receipts as the highest-priority defect. Identity and authority do not prove that a mutation committed.
- Inspect the common native tool adapter and transaction boundary first. Claim-only requests fail, so this is not a submission-sequence defect.
- Emit success only after commit and a fresh authoritative read of the resulting state.
- Keep the task mutation, worklog, proposal, provenance, and hash-chained activity record atomic.
- If a write rolls back or is not durable, return a structured failure and no success receipt.
- Add the full claim, progress, submission, sponsor-acceptance, rollback, concurrency, idempotency, and provenance checks.
- Do not add a frontend shadow store, receipt parser, reconciliation system, replay, or sponsor bypass.
- Fix stale-chat recovery separately. Keep every write, review, trust, and provenance message literal.
- Preserve voice only in Mission control framing, honest inbox empty states, and verified completion.

## Second-pass resolution

### Agent teammate

The shared native-tool transaction fix removed the false success condition.

- The live agent claimed task `#29` after the fix.
- The chat showed a write receipt.
- A fresh task panel showed `in_progress`.
- The live agent reported progress.
- A fresh task panel showed the new worklog note.
- The agent submitted the task for acceptance.
- A fresh Approvals read showed proposal `#9` with the worklog evidence and sponsor.
- Marcus approved proposal `#9`.
- A fresh task read showed task `#29` as `done` with the acceptance note.

The earlier false proposal `#8` does not exist. Proposal `#9` is the durable post-fix record.

### Manager

Stale-thread recovery now stays on the chat page and states the result.

The live check switched from Marcus to Mario while the tab retained Marcus's thread ID. The backend returned `404` without revealing the owner.

The page showed `Message not sent. This chat is not available.` It removed the optimistic message and the stale session pointer.

The page also unmounted the unavailable thread and composer. It moved focus to `New chat`, which opened a clean thread without replay.

### Developer

Quick capture created task `#30` while My Day remained open. The task appeared in `Your work` without a page reload.

The onboarding count also changed from `1/4` to `2/4` from the same refresh. Task `#30` was marked done after the check.

### Mobile check

At 360 pixels, the live search panel measured:

- left edge: 16 pixels,
- right edge: 344 pixels,
- width: 328 pixels.

The result text stayed complete inside the viewport. The browser regression check passed.

### Reviewer and administrator

Converting the remaining medium promise finding created task `#31`.

The task panel opened immediately at `/insights?task=31`. It showed `medium` priority, `@mario` assignment, and the source-finding link.

The automated high-severity check confirmed that a high finding creates an assigned high-priority task. The positive-severity check confirmed the valid medium fallback.

Conversion now locks the finding and commits the task, typed relation, activity event, and disposition in one transaction. Repeated conversion returns the linked task. A failed disposition rolls back all conversion writes.

Ledger-integrity findings require strong identity for conversion. A weak trusted header cannot remove the ledger alert from the digest through conversion.

### Team member

The Quick capture chip now reads `awaiting`. Its accessible name and visible label match the `awaiting:` prefix.

The `waiting for:` parser alias remains available. The stored direction remains `received`.

## Second-pass completion state

All six selected repairs passed focused checks, full suites, the production build, the CI lint gate, and live traversal.

The final suites passed 2,097 backend tests and 316 frontend tests. A second code review found no remaining defect.

The remaining stale aggregate and pre-migration notifications are still visible. They remain deferred because they need typed repair contracts rather than another display-text filter.

## Third pass — three-to-six-month use

### Study record

- Baseline commit: `11ebd16`
- Branch: `proposed-features`
- Application: live local Skein instance
- Frontend: `http://localhost:3000`
- Backend: `http://localhost:8000`
- Provider: `ollama`
- Model: `glm-5.2:cloud`
- Study date: 2026-08-15

### Method

The study compressed repeated daily and weekly use into representative cycles. Live actions exercised the current product, while fixed schedules and list bounds supplied the long-horizon projections.

The horizon covered these recurring costs:

- daily capture, search, My Day, chat, and notifications,
- weekly planning, standups, reviews, findings, and rituals,
- monthly spend, flow, intake, and report review,
- quarterly decision and authority review,
- engagement close-out, handoff, lessons, and ownership changes,
- record accumulation, stale state, absence, and rework.

### Long-horizon baseline

The current instance contains 32 tasks, 11 proposals, 16 team or personal notifications, five reports, and several saved chats.

The live data already includes test, extension, study, rejected-review, agent-rework, and scheduled-job history. This mix exposed accumulation faults before six calendar months passed.

### Manager and engagement lead — mario

#### What works well

- Planning keeps the meeting order and names its twelve-row intervention limit.
- The six-week capacity table includes ordinary load, not only conflicts.
- Engagement briefs keep health, open work, lessons, drift, and reports together.
- Health states why each engagement is green.
- Reports render readable markdown and preserve author and type.
- Week-open and week-close jobs remain idempotent.

#### Long-horizon problems

- Reports says that it contains every report, but the list returns only the newest 50.
- The daily digest and two weekly rituals produce nine scheduled reports each week.
- Scheduled output alone fills the visible report list in about five and a half weeks.
- At three months, approximately 117 scheduled reports exist. At six months, approximately 234 exist.
- Handoffs and readouts shorten the visible period further.
- The report files and rows remain stored, but the page has no path to older reports.

The live Friday close-out had already run through the scheduler. Selecting `Run Friday close-out` returned:

```json
{"week":"2026-W33-close","skipped":"already ran this week"}
```

The page showed no receipt, explanation, or report link. This silent no-op repeats every week when a manager selects the control after the scheduled run.

The Acme engagement still says to ask Chat when its intended outcome is absent. The engagement page gives no direct action or prefilled route.

All nine model calls remain under `(unlinked)`. The cost view is honest, but engagement-level accounting stays empty unless people maintain chat links.

#### Real actions

The manager repeated Planning, Health, Reports, both engagement briefs, the Friday close-out control, and My Day notification review.

### Developer and daily task owner — marcus

#### What works well

- My Day keeps the owned blocker above ordinary work.
- Quick capture adds the new task to the current daily list.
- Search found task `#26` from its title and returned related workspace rows.
- The CLI returned the same personal count as the web header.
- The task panel kept delegation, provenance, blockers, and the worklog in one place.
- Agent claim, progress, resubmission, and acceptance stayed durable.

#### Long-horizon problems

Browse requests at most 500 tasks before the frontend removes completed rows. Old completed or high-priority rows can consume the response before active work reaches the browser.

At five new tasks each workday, 500 rows arrive in 20 workweeks. That is inside the requested six-month horizon.

The current Browse page already shows 28 open tasks in one unfiltered task list. Its task section occupies about 900 vertical pixels.

Task `#26`, `Verify proposed feature flow`, is a study record that was never real delivery work. The available choices can start or complete it.

Completing a mistaken or validation task places it in Recently shipped and flow metrics. Leaving it open pollutes active work. Skein has no `void` task state.

The CLI My Day output showed `Review needed` notifications for proposals `#4` and `#5` beside the same pending proposals.

The web My Day correctly suppresses those linked notification rows. The CLI reads the raw notification list and restores the duplicate work.

#### Real actions

Marcus used browser capture, nav search, the task panel, the CLI `attention`, `search`, `my-day`, and agent `inbox` commands.

Marcus created task `#32`, delegated it to `agent`, rejected proposal `#10`, then approved corrected proposal `#11`.

### Normal team member — ava

#### What works well

- My Day remains quiet when no decision, blocker, promise, or assigned question needs Ava.
- The meeting-outcome notice stays visible to an attendee without inflating the personal attention count.
- Time away appears in the weeks that overlap Ava's PTO.
- Crew visibility appears in the standup picker.
- Settings keeps personal setup before team administration.
- The field guide, growth-interest boundary, calendar warning, and agent-connection warning remain direct.

#### Long-horizon problems

Unread notifications do not age out. The retention job removes only read notifications older than 90 days.

My Day reads 20 unread notification rows and renders at most five coalesced notification groups. Older unread rows stay stored but leave the daily surface.

The current one-day dataset already shows stale team notification text about proposal `#7`. It also shows repeated proposal notification classes.

A person who misses a week can return to a partial unread window without a count or path to the older notices.

The normal-user journey still gives no evidence for a model menu, a new growth system, a guide redesign, or a PTO summary.

#### Real actions

Ava repeated My Day, the meeting-outcome notice, future PTO, crew visibility, Settings, field-guide progress, and the team notification queue.

### Reviewer and administrator — dana

#### What works well

- Approvals shows the task evidence and complete worklog before a verdict.
- Rejection reasons return to the agent inbox.
- Resubmission keeps both progress notes and creates a new proposal.
- Trust history changed from one of one to two of three after the live rework cycle.
- Weak identity keeps Authority, private notes, backups, and tuning disabled.
- Activity has cursor pagination and keeps personal, agent, and system rows separate.
- Finding conversion for ledger findings is atomic, idempotent, and linked. It requires strong identity.

#### Long-horizon problems

Approvals returns the newest 200 pending proposals. My Day reads the oldest 50 pending proposals, and the badge counts the complete queue.

Meeting ingestion accepts up to 500 proposal lines in one paste. One allowed paste can create proposals that My Day lists but Approvals does not show.

The Insights feed shows four weeks and at most 50 findings. Disposition rows remain stored. If the condition persists in a later week, the service can create another finding.

Elevated agent authority receives a 90-day review date. After that date passes, the service creates a finding but does not reduce the effective authority level.

A decision review date marks the decision stale. An authority review date creates a finding but does not reduce effective authority.

This authority behavior is deliberate in the current service. The three-to-six-month study makes the contract important enough for an explicit decision.

The current activity-ledger incident remains visible as converted. Its pre-fix task `#28` still carries the old medium and unassigned state.

#### Real actions

The reviewer inspected Approvals, Insights, Activity, Authority, trust, Settings, private-surface refusal, and the full agent rework history.

### AI agent teammate — built-in `agent`

#### What works well

- The agent claimed task `#32` and wrote progress before submitting.
- Proposal `#10` included the partial evidence and sponsor.
- Rejection returned a precise correction to the agent inbox.
- The agent added final evidence and submitted proposal `#11`.
- Marcus approved proposal `#11`, and a fresh task read showed task `#32` as done.
- The durable-write correction from the second pass remained effective.

#### Long-horizon problem

After proposal `#11` completed task `#32`, the agent inbox still showed rejected proposal `#10` as work to learn from.

The task was done and the requested correction was present in the approved replacement. The rejected row had no remaining action.

The agent inbox has no acknowledgment or archive action. It returns only ten rejected proposals, so old unresolved notes and completed rework share one fixed window.

This creates a permanent nag for each reject, correct, resubmit, and approve loop.

#### Live rework evidence

1. Marcus created and delegated task `#32`.
2. The agent claimed it and wrote partial progress.
3. The agent submitted proposal `#10`.
4. Marcus rejected it with the missing-work instruction.
5. The agent wrote the final progress note.
6. The agent submitted proposal `#11`.
7. Marcus approved proposal `#11`.
8. Task `#32` became done.
9. Agent inbox still returned rejected proposal `#10`.

### Representative six-month cycle

#### Month 1 — adoption and routine

- People establish My Day, capture, search, standup, chat, and weekly-plan habits.
- The report list already approaches its five-and-a-half-week visible limit.
- Linked notification suppression works on the web but not in the CLI.

#### Month 2 — review and notification load

- Weekly rituals, digests, findings, and review notices accumulate.
- Unread notifications begin leaving the 20-row daily payload while remaining stored.
- Rejected agent proposals remain in the fixed ten-row inbox window.

#### Month 3 — close-out and handoff

- Reports from the first month are no longer reachable from Reports.
- Engagement close-out and handoffs add more artifacts to the same fixed list.
- Missing intended outcomes still route managers through an instruction instead of an action.

#### Month 4 — governance renewal

- Decisions and elevated authority grants reach their 90-day review dates.
- Decisions become stale. Elevated agent authority stays effective and produces a finding.
- The team must understand that these two review dates have different enforcement behavior.

#### Month 5 — task and history pressure

- A five-task-per-day team reaches the 500-task Browse response limit.
- Completed tasks can consume the 500 returned rows because the service applies the status filter after the cap.
- Mistakes and validation tasks must remain open or count as shipped.

#### Month 6 — recovery after absence

- Reports, chats, findings, notifications, decisions, requests, and agent notes all have different hidden-history bounds.
- Search recovers many entity rows, but it does not recover older reports or unread-notification state.
- The main cost is not raw storage. The cost is stored history that its owning surface can no longer reach.

### Third-pass evidence summary

The strongest long-horizon evidence is:

1. Reports loses navigation to stored reports after about five and a half weeks.
2. Weekly ritual controls silently succeed with no result after the scheduler claims the week.
3. Task history can crowd active work before six months.
4. Mistaken tasks have no truthful terminal state.
5. The CLI restores proposal-notification duplicates that the web suppresses.
6. Corrected agent rejections remain forever after the replacement is approved.
7. Unread notifications can outlive the daily window.
8. Review and authority bounds need explicit long-horizon contracts.

### Third-pass persona-panel review

Each persona panel used no more than four reviewers. Each panel included the Whimsy Injector.

#### Manager and engagement lead

The panel ranked report reachability first. Stored reports need cursor navigation from the Reports page instead of a larger fixed limit.

The panel also selected a visible skipped-ritual receipt. The receipt must link to the report that already exists.

The missing intended outcome needs a direct route to the existing write path. The panel did not support a second outcome system.

The panel treated the six-month report count as a projection. The fixed 50-row limit and the nine-report weekly schedule are verified facts.

The Whimsy review put access and truthful state before product voice. One warm closer is safe only after the literal close-out result and link.

#### Developer and daily task owner

The engineering reviewers confirmed that completed tasks can consume the 500-row response before the browser removes them.

The smallest robust correction is to filter open and completed work in the service before each limit. Browse can fetch recent completed work separately.

The reviewers confirmed that mistaken tasks need a `void` terminal state. Void work must stay searchable but must not enter active work, shipped work, or flow metrics.

The CLI must render the shared attention projection. It must not rebuild review and notification rows from the raw briefing sections.

Search remains useful beyond 500 tasks. Exact identifiers and distinctive titles use indexed paths, but search does not replace a complete work list.

The Whimsy review required literal task, count, CLI, validation, and shipped-state text.

#### Normal team member

The usability and accessibility reviewers ranked older unread access as the main returning-user problem.

The study proves a fixed unread window. It does not prove that a person already missed an action.

My Day must stay concise. It needs an honest unread total and a route to all unread notifications with cursor navigation.

Focused checks must cover keyboard use, focus preservation, live result counts, zoom, forced colors, reduced motion, and screen-reader output.

The Whimsy review supported warmth only after the active unread set is truly empty. It rejected a warm all-clear while older unread rows remain hidden.

#### Reviewer and administrator

The reviewers confirmed that My Day and Approvals use different slices of the same pending queue.

One 500-line ingest can create proposals that appear in neither slice. A complete repair needs one canonical ordering and cursor access.

The authority panel found a contract conflict. Product documentation calls the 90-day date an authority half-life, but the policy gate ignores it.

The identity review ranked this as a fail-open governance problem. It recommended forced review after expiration, independent of `SKEIN_AGENT_REVIEW`.

The manager review warned against unexpected automatic revocation. It recommended explicit overdue state and `Renew` or `Reduce` actions first.

The final synthesis must select one authority contract. The current text and current enforcement cannot both remain.

The Insights limit is also real. The study does not yet prove user harm from an older finding leaving the feed.

The Whimsy review required literal wording for authority, identity, trust, review, incidents, limits, and stale state.

#### AI agent teammate

The backend and workflow reviews confirmed that an approved replacement does not remove the earlier rejection from the active agent inbox.

The smallest correction is a read-time anti-join. A later approved task-completion proposal makes the earlier rejection non-actionable.

The rejected verdict must remain immutable. Trust must continue to count both the rejected proposal and the approved replacement.

The fix must run before the ten-row inbox limit. Otherwise, resolved rework can still hide an older unresolved correction.

The Whimsy review required one meaning for the inbox. It must contain active correction work, not resolved verdict history.

Warm text is safe only for a true empty or completed state. Review reasons, receipts, counts, failures, trust, and permissions must stay literal.

## Third-pass resolution

### Reports and rituals

The Reports page now uses a stable older-report cursor. Loading an older page keeps the selected report and its URL unchanged.

The live database contained four readable reports after the final restart. The page rendered all four and correctly omitted `Older reports` because no older page existed.

The Friday close-out control returned this visible result:

> This ritual already ran this week. Skein did not send duplicate notifications.

The link opened the existing report at `/artifacts?id=4`.

A concurrency review found that the weekly claim committed before its report. The claim, report, notifications, and activity now run in one serialized transaction. A concurrent repeat waits until the report exists.

### Tasks and long-lived collections

Task state now enters SQL before the 500-row limit. Workplace-policy denials also run before the visible limit through bounded scans.

Browse reads open and completed task slices from one backend snapshot. The final live page showed task `#25` only in Tasks and task `#32` only in Recently shipped.

The CLI uses the same task collection. `skein tasks` returned open work only. `skein tasks --all` added completed work without duplicate IDs.

### One pending-review queue

Approvals now starts with the oldest readable proposal. The live queue showed proposal `#4` before proposal `#5`, and the navigation badge showed the same total of two.

`/review?id=5` loaded and focused proposal `#5`. `/review?id=999` loaded one normal page and focused the Approvals heading. It did not scan the complete queue.

Each deep-link page marks its own IDs seen. Batch selection stops at 200 rows and states the limit. Settled history scans past scope and workplace-policy denials before its compatible response limit.

The review summary now batches target-tier reads by table. My Day and the navigation count obtain the first page and exact total from one queue scan.

### Authority renewal

An expired `autonomous` or `notify` grant now has an effective level of `review`. This enforcement does not depend on `SKEIN_AGENT_REVIEW`.

The configured level remains available for audit and compatible API clients. The new `effective_level`, `review_by`, and `review_expired` fields state the active rule.

A concurrent authority proposal can no longer overwrite a new `forbidden` kill switch. Identity and authority locks serialize the stale check and write in the same order as identity rename.

Legacy grants calculate their 90-day fallback from the team-local date, not the PostgreSQL session date.

The live database had no configured authority row after restart. The Agents page still confirmed the gate-off rule: current grants write directly, but expired elevated grants wait.

### Agent correction loop

The live `agent` inbox now shows task `#25` and no rejected proposals. Proposal `#10` remains in review and trust history, but approved proposal `#11` removed it from active correction work.

A later approved completion for the same task resolves the correction even when a replacement agent submitted it.

### Shared My Day projection

The CLI now renders the backend attention projection with the public group names:

- Decide,
- Unblock,
- Promise,
- Review,
- Notice.

The final CLI run kept personal and team sections separate. It showed proposal `#4` and proposal `#5` once each.

If more review rows exist than the preview contains, the CLI states how many remain in Inbox → Approvals.

### Implementation review result

The code-review panel requested changes. Every verified finding was corrected before final verification.

The dedicated security-review agent did not return a report because its provider stopped the run. The general code review still traced policy, authority, scope, and administrator paths and found no critical security defect.

The final browser checks confirmed these changed flows:

- report selection and ritual reuse,
- FIFO Approvals and deep-link focus,
- bounded missing-review recovery,
- open and recently shipped task separation,
- gate-off authority wording,
- resolved agent correction removal,
- CLI My Day, task, and review pagination behavior.

The final serial backend suite passed 2,123 tests with the existing intentional thread warning. The final frontend suite passed 323 tests. The production build and the complete CI lint gate passed.

The pre-merge review recorded in `2026-08-15-product-study-method.md` then added three regressions and corrected one non-idempotent test. The counts at close are 2,125 backend and 324 frontend.
