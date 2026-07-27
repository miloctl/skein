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
| Engagement | ✅ incl. rename | close w/ conclusion | n/a | rename propagates labels |
| Allocation | delete+recreate | ✅ DELETE | n/a | capacity window-aware |
| Memory | recreate | ✅ forget (UI+API) | n/a | deindexed |
| Event | recreate | ✅ DELETE (REST+tool) | n/a | deindexed |
| Chat thread | rename/move | ✅ | folders | sessions removed too |
| Question | assign/answer | answered state | n/a | overwrite guarded |
| Decision | reconfirm | supersede chain (UI) | n/a | never hard-deleted by design |
| Blocker | resolve | resolved state | waiting_on | edit of title: gap (small) |
| Commitment | status | kept/missed | n/a | promise-text edit: gap (small) |
| Note | — | — | n/a | **gap**: no edit/delete anywhere |
| Standup | — | — | n/a | immutable by design (a diary, not a doc) |
| Intake request | terminal dispositions | declined/deferred | n/a | title edit pre-triage: gap (small) |
| User | rename/merge/deactivate | deactivate | n/a | merge backfills theme+interests |

## Remaining gaps (next batch, all small)

1. Notes: PATCH + DELETE (with deindex) + inline edit on the KB card — the
   only entity with no correction path at all.
2. Blocker title/detail edit; commitment promise edit; intake title edit
   while still `submitted`.
3. prompt()/confirm() stragglers (Settings rename/deactivate, chat-sidebar
   delete confirms) → inline panels, matching rule 1.
4. Raw SQL in two routes (`/activity`, one other) → move behind services.
