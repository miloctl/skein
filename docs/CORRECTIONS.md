# The correction contract

Skein's audit (2026-07-27) found the app created records far better than it
corrected them. This doc is the standing contract every entity must meet —
new features are reviewed against it.

## The five rules

1. **Editable.** Every user-visible field is PATCHable through the service
   layer, exposed via REST *and* agent tools, and re-indexed on change. The
   UI offers the edit at the point of display (inline `edit…`, the EditRow
   idiom on Browse) — never a browser prompt().
2. **Deletable or terminally-stateable.** Ephemeral records (chats, events,
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
5. **Bounded.** Anything listable is LIMITed; anything writable is
   rate-capped and length-capped where flood-prone.

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

## Remaining gaps (next batch)

1. UI affordances for the new wording edits — blocker (My Day), commitment
   (Portfolio), intake title (Intake) — and a cancel on the dashboard
   Calendar card (event delete is REST+tool only).

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
