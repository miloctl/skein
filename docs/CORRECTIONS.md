# The correction contract

Skein's audit (2026-07-27) found the app created records far better than it
corrected them. This doc is the standing contract every entity must meet —
new features are reviewed against it.

## The five rules

1. **Editable.** Every user-visible field is PATCHable through the service
   layer, exposed via REST *and* agent tools, and re-indexed on change. The
   UI offers the edit at the point of display (inline `edit…`, the EditRow
   idiom on Browse) — never a browser prompt().
2. **Deletable or terminally-stateable.** Agent-side, hard deletes are
   ALWAYS proposals (gate ALWAYS_REVIEW) even with review mode off —
   deletes-as-proposals is the contract for destructive agent verbs.
   Ephemeral records (chats, events,
   memories, allocations) hard-delete — and every hard delete calls
   `search.deindex_record` so search/`/ask` never cite ghosts. Records that
   carry history (decisions, engagements, blockers, questions) get terminal
   states instead: supersede, close-with-conclusion, resolve, answer.
3. **Relinkable.** Foreign keys are corrections too: `engagement_id` /
   `milestone_id` are PATCHable (−1 unlinks), with existence checks, and a
   mislink warns loudly at create time instead of storing NULL silently.
4. **Provenance on corrections.** Edits log to activity; overwrites of
   meaning (growth interests, renames) log old→new; destructive overwrites
   of someone else's content ERROR instead (answers); merges backfill
   person-level fields rather than dropping them; approvals apply as the
   proposer, never the reviewer.
5. **Bounded.** Three rules. One is enforced mechanically today. The other
   two are review obligations, and this entry says which is which — a rule
   that claims an enforcement it does not have is worse than a soft rule,
   because it stops people looking.
   - **A PATCH never loosens a create cap.** ENFORCED.
     `tests/test_patch_cap_parity.py` discovers every `*Patch`/`*In` pair by
     convention and fails on any field capped looser than its create model,
     so a new pair is covered the day it is written.
   - **Writable is length-capped.** REVIEW OBLIGATION. Every string field of
     every request model carries `max_length`, and every create refuses a
     record whose capped fields are all empty. The parity test above compares
     PATCH against create; it cannot see a field left uncapped on BOTH sides.
   - **Writable is rate-capped, listable is LIMITed.** REVIEW OBLIGATION.
     A mutating route calls `ratelimit.check(<surface>, user)`; a list service
     takes a `limit` and puts `LIMIT ?` in its SQL on every branch. Neither is
     swept today — see the ROADMAP entry for the census that would make them
     enforced.

   To exempt an endpoint deliberately, write `# unbounded: <reason>` above it
   and add a row below.

### Bounded exemptions

| Endpoint | Check waived | Reason | Declared |
|---|---|---|---|
| _(none)_ | | | |

## Status matrix (post-audit batches)

| Entity | Edit | Delete/terminal | Relink | Notes |
|---|---|---|---|---|
| Task | ✅ UI+API+tool | done state | ✅ (−1 unlinks) | |
| Milestone | ✅ UI+API+tool | done state | ✅ | create warns on unmatched project |
| Engagement | ✅ incl. rename (API+tool+UI status) | close w/ conclusion | n/a | rename propagates labels, transactional |
| Allocation | delete+recreate | ✅ DELETE | n/a | capacity window-aware |
| Memory | recreate | ✅ forget (UI+API+tool) | n/a | deindexed; agent forget is review-gated |
| Event | recreate | ✅ DELETE (REST+tool) | n/a | deindexed |
| Chat thread | rename/move | ✅ | folders | sessions removed too |
| Question | assign/answer | answered state | n/a | overwrite guarded |
| Decision | reconfirm | supersede chain (UI) | n/a | never hard-deleted by design |
| Blocker | ✅ wording (API+tool) | resolved state | waiting_on | resolved refuses edits; no UI yet |
| Commitment | ✅ promise (API+tool) | kept/missed (API+tool+UI) | n/a | old→new logged; settled refuses; edit has no UI yet |
| Note | ✅ UI+API+tool | ✅ DELETE (UI+API+tool) | n/a | deindexed; KB-card inline edit + two-step delete |
| Standup | — | — | n/a | immutable by design (a diary, not a doc) |
| Intake request | ✅ title/detail (API+tool) | declined/deferred | n/a | submitted/scored only; no UI yet |
| User | rename/merge/deactivate | deactivate | n/a | merge backfills theme+interests |
| Absence | delete+recreate | ✅ DELETE (REST; UI two-step) | n/a | 180d window cap, person/note capped, rate-capped |
| Worklog | append-only | frozen once task is done | n/a | delegate+sponsor only, 2000-char cap, origin recorded |

## Remaining gaps (next batch)

1. UI affordances for the new wording edits — blocker (My Day), commitment
   (Portfolio), intake title (Intake) — and a cancel on the dashboard
   Calendar card (event delete is REST+tool only).

Closed 2026-07-27 (sponsor-bound verdicts): task-acceptance verdicts belong
to the task's sponsor, looked up at verdict time. Anyone else may still
approve or reject, but only with a reason on the record — the verdict is
marked `reviewed_override` (migration 030), logged "(accepted for X)", and
excluded from trust streaks, so promotions and demotions are backed only by
the people who actually sponsored the work. The Inbox shows the sponsor on
acceptance rows and turns Approve into "Accept for X…" with an inline
reason field for everyone else. Orphaned proposals (reassignment cleared
the delegation) require a reason from ANY judge and never feed streaks;
overrides neither count toward nor interrupt a streak in either direction
(pinned by test — a buddy's override approval can't shield a demotion, at
the cost that promotion streaks read only sponsor verdicts).

Closed 2026-07-27 (A1/A2 hardening): the delegation loop holds under
adversarial use — agents cannot self-complete a delegated task
(`update_task` refuses `done` from agent identities; the sponsor's verdict
is the only close), worklogs accept only the delegate/sponsor, completion
submissions dedupe against pending proposals, and the `forbidden` kill
switch reaches the whole trio. Authority verdicts are human-only AND
strong-identity-only end to end (weak `X-User` can no longer approve a
filed promotion), stale authority proposals refuse to apply when the level
changed underneath them (`expected_current` pin — `forbidden` is never
silently lifted), and streak proposals are filed only for agent-kind
proposers on gate-consulted entities. Manual ritual runs consume the weekly
claim so the scheduler can't double-brief.

Closed 2026-07-27 (later batch): **tool parity** — agents now correct under
the same review gate humans use: `edit_note`/`delete_note`, `edit_blocker`,
`edit_commitment` + `mark_commitment`, `edit_intake_request`,
`update_engagement`, and review-gated `forget_memory` (new registry entities
note_edit/note_delete/blocker_edit/commitment_edit/intake_edit/memory_forget,
each its own authority knob). Also closed: old→new rename logging
(blocker/intake/note), the last raw route SELECT, delete rate caps,
strong-verdict trust gating, EditRow clear sentinels, textarea note editing,
transactional rename propagation.

Closed by the 2026-07-27 batch: notes CRUD (last zero-path entity),
blocker/commitment/intake wording edits at the service+REST layers with
history guards, prompt()/confirm() fully gone (two-step confirms + inline
inputs), raw SQL out of `/tasks`, `/activity`, `/usage`.
