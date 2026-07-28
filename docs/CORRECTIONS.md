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
| Blocker | ✅ wording (API only) | resolved state | waiting_on | resolved refuses edits; no UI/tool yet |
| Commitment | ✅ promise (API only) | kept/missed | n/a | old→new logged; settled refuses; no UI/tool yet |
| Note | ✅ UI+API | ✅ DELETE (UI+API) | n/a | deindexed; KB-card inline edit + two-step delete; no tool |
| Standup | — | — | n/a | immutable by design (a diary, not a doc) |
| Intake request | ✅ title/detail (API only) | declined/deferred | n/a | submitted/scored only; no UI/tool yet |
| User | rename/merge/deactivate | deactivate | n/a | merge backfills theme+interests |

## Remaining gaps (next batch)

1. **Tool parity** (rule 1 says REST *and* tools): agents can create every
   record but correct almost none — no `update_note`/`delete_note`,
   `edit_blocker`, `edit_commitment` (nor commitment status), `edit_request`
   (intake), `forget` (memory), or `update_engagement` tool. Only
   task/milestone update and `cancel_event` exist on the agent write path.
2. UI affordances for the new wording edits — blocker (My Day), commitment
   (Portfolio), intake title (Intake) — and a cancel on the dashboard
   Calendar card (event delete is REST+tool only).
3. Rule 4 old→new on renames: `edit_commitment` logs it for promise text,
   but blocker title, intake title, and note topic edits log field names /
   old topic only.
4. Housekeeping: one raw `SELECT` left in a route (agent-inbox kind check in
   `api.py`); `update_note` takes an `origin` param it never uses.

Closed by the 2026-07-27 batch: notes CRUD (last zero-path entity),
blocker/commitment/intake wording edits at the service+REST layers with
history guards, prompt()/confirm() fully gone (two-step confirms + inline
inputs), raw SQL out of `/tasks`, `/activity`, `/usage`.
