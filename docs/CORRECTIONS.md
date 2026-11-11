# The correction contract

Skein's audit (2026-07-27) found the app created records far better than it
corrected them. This doc is the standing contract every entity must meet -
new features are reviewed against it.

## The five rules

1. **Editable.** Every user-visible field is PATCHable through the service
   layer, exposed via REST *and* agent tools, and re-indexed on change. The
   UI offers the edit at the point of display (inline `edit…`, the EditRow
   idiom on Browse) - never a browser prompt().
2. **Deletable or terminally-stateable.** Agent-side, hard deletes are
   ALWAYS proposals (gate ALWAYS_REVIEW) even with review mode off -
   deletes-as-proposals is the contract for destructive agent verbs.
   Ephemeral records (chats, events,
   memories, allocations) hard-delete - and every hard delete calls
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
5. **Bounded.** Four bounds. Two are enforced mechanically today. The other
   two are review obligations, and this entry says which is which - a rule
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
   - **Writable is rate-capped.** ENFORCED.
     `tests/test_bounded_routes.py` walks the route table and fails any
     mutating route that neither calls `ratelimit.check(<surface>, user)` in
     its own handler nor carries an EXEMPT row with a reason. It fails a stale
     exemption too - one for a route since capped, or since deleted.
   - **Listable is LIMITed.** REVIEW OBLIGATION. A list service puts `LIMIT ?`
     in its SQL on every branch. The four reads the 2026-08-03 census named
     were capped 2026-08-09, and `chat_threads.list_folders` beside them.
     Nothing sweeps for the next one.

   The rate-cap exemptions live in `tests/test_bounded_routes.py::EXEMPT`,
   not in a comment above the route: a marker the suite does not read is a
   marker that can go stale silently, and that list is checked in both
   directions on every run.

   The two review obligations carry no exemption path. Neither is
   endpoint-shaped - a listable exemption is a service function and a
   length exemption is a model field - so a waiver is a comment at the site,
   stating what is unbounded and why, and it is found by reading rather than
   by a table nobody updates.

## Status matrix (post-audit batches)

| Entity | Edit | Delete/terminal | Relink | Notes |
|---|---|---|---|---|
| Task | ✅ UI+API+tool | done state · void state (never-should-have-existed; leaves lists, metrics and search; restorable) | ✅ (−1 unlinks) | |
| Milestone | ✅ UI+API+tool | done state | ✅ | create warns on unmatched project |
| Engagement | ✅ incl. rename (API+tool+UI status) | close w/ conclusion | n/a | rename propagates labels, transactional |
| Allocation | delete+recreate | ✅ DELETE | n/a | capacity window-aware |
| Memory | recreate | ✅ forget (UI+API+tool) | n/a | deindexed; agent forget is review-gated |
| Event | ✅ UI+API+tool (reschedule; no tier change) | ✅ DELETE (UI two-step, REST, tool) | ✅ engagement (−1 unlinks); items link to it | deindexed; linked rows keep their record, the link is nulled |
| Chat thread | rename/move | ✅ | folders | sessions removed too |
| Question | assign/answer | answered state | n/a | overwrite guarded |
| Decision | reconfirm | supersede chain (UI) | n/a | never hard-deleted by design |
| Blocker | ✅ wording (API+tool) | resolved state | waiting_on | resolved refuses edits; no UI yet |
| Commitment | ✅ promise (API+tool) | kept/missed (API+tool+UI) | n/a | old→new logged; settled refuses; edit has no UI yet |
| Note | ✅ UI+API+tool | ✅ DELETE (UI+API+tool) | n/a | deindexed; KB-card inline edit + two-step delete |
| Standup | - | - | n/a | immutable by design (a diary, not a doc) |
| Intake request | ✅ title/detail (API+tool) | declined/deferred | n/a | submitted/scored only; no UI yet |
| User | rename/merge/deactivate | deactivate | n/a | merge backfills theme+interests |
| Absence | delete+recreate | ✅ DELETE (REST; UI two-step) | n/a | 180d window cap, person/note capped, rate-capped |
| Worklog | append-only | frozen once task is done | n/a | delegate+sponsor only, 2000-char cap, origin recorded |
