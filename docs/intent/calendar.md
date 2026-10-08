# Calendar, written by people and agents

Drafted 2026-09-28 from the repository owner's request: a calendar that
people and agents both write to, with notes, tasks and decisions linked to
the meetings they came from.

- **Outcome:** One page shows everything with a date. A meeting keeps a
  record of what came out of it, and anyone who can read that meeting can
  find those items later.
- **User:** The strike team. Agents write through the same services, under
  the review gate.
- **Why now:** Most of the parts are already built and have no page to show
  them. `events` has visibility tiers, an agenda, an engagement link and an
  outcome status, and three agent tools use it. People see it only as a flat
  list in Work → Browse. Meeting-notes ingestion turns notes into tasks,
  decisions and questions, but nothing records which meeting they came from.
- **Success:** A person opens Work → Calendar and sees this month's meetings,
  due dates and time away on the team clock. They move a meeting without
  deleting it. They paste the notes of a meeting, and every approved item
  links back to it. The meeting panel lists those items. A person who cannot
  read an item never sees its title, its id or a count that includes it.
- **Constraint:** Keyless first: every slice works with
  `SKEIN_MODEL_PROVIDER=mock`. No calendar or date library (`package.json`
  has none, and `dayjs` is present only as a mermaid dependency). No runtime
  integration with Outlook, Exchange or Teams. The ICS feed stays the only
  export.
- **Out of scope:** Recurring events, a week view with hour rows, and
  two-way sync, among others. Each is a row in the cut table of
  `docs/ROADMAP.md`, with the trigger that brings it back.

The 2026-08-21 posture note in `docs/ROADMAP.md` freezes new *portfolio*
surfaces: engagements, health, forecasts, intake and readouts. The calendar
is team coordination, not portfolio. Slices 2 and 3 also serve the agent
loop that the posture funds: an agent reschedules a meeting, and files what
came out of it with the link, through the review gate.

## What existed before this work (2026-09-28)

| Part | Where | Note |
|---|---|---|
| `events` table | `001_baseline.sql` | `starts_at`/`ends_at` are naive UTC `YYYY-MM-DDTHH:MM`, or a date alone for all day. It has `visibility`, `crew_id`, `agenda`, `engagement_id` and `outcome_status`. It has no `updated_at`. |
| Services | `services/schedule.py` | `schedule_event`, `list_events` (a start date only, LIMIT 50), `get_event`, `cancel_event` (hard DELETE), `record_outcome`, `meetings_awaiting_outcome`, `team_day_events`, `ics_feed`. |
| Agent tools | `tools/schedule.py` | `schedule_event` (gated `event` create), `list_events`, `cancel_event` (`event_cancel`, ALWAYS_REVIEW). |
| REST | `routes/api.py` | `GET/POST /api/events`, `DELETE /api/events/{id}`, `POST /api/events/{id}/outcome`, `GET /api/events/{id}/stakeholders`. There is no GET by id and no PATCH. |
| UI | `app/dashboard/page.tsx` | "Calendar" register in Browse → People: a list plus an add form with a title and a start only. My Day shows today's workspace events and the outcome ask. |
| Feed | `GET /api/calendar.ics` | Workspace events, plus milestone and promise due dates. |
| Pre-meeting brief | `stakeholders.brief_for_event` | Open threads with the attendees. It is rendered only on My Day (`StakeholderBrief` in `app/page.tsx`). |

## Decisions

Each decision names the alternative it replaced, so the next reader does not
make it a second time.

### D1. A link is one `event_id` column per table, not a link table

Every link has two ends with different tiers. With a column, the child's own
`visible_filter` governs every read, and `ON DELETE SET NULL` handles cancel,
erasure and `my_data.delete_private` with no service code. A generic
`links(from_kind, from_id, to_kind, to_id)` table needs a two-ended scope
check in every query that reads it. It also needs an entry in the backup,
erasure, retention, search and gate registries. Nothing references
`events(id)` today, so the migration starts clean.

### D2. The linked kinds are what the capture grammar produces

`services/capture.py::PATTERNS` is Skein's existing definition of what comes
out of a meeting. The linked tables are `notes`, `tasks`, `decisions`,
`questions`, `blockers`, `promises` (both directions) and
`intake_requests`. Milestones are excluded: they already appear on the
calendar through their own due dates. Lessons are excluded until a retro
workflow asks for them.

### D3. Two directions, two mechanisms

- **Came out of the meeting:** the `event_id` column. An item comes from one
  meeting.
- **Brought to the meeting:** the existing `agenda` text, parsed by
  `services/refs.py::readable_refs`. One question can be on several agendas.
  The parser already checks scope and policy before it resolves a title, and
  it already runs on text that people write (documents, acceptance
  criteria). No join table.

### D4. Tier containment: an item is never wider than its meeting

`scope.assert_relationship_contains(event tier, item tier)`, the same rule
tasks apply to engagements and milestones (docs/VISIBILITY.md,
`relationship_contains`). A workspace task that points at a private meeting
publishes that meeting's sequential id. The writer must also be able to read
the event: the probe raises `ValueError(scope.missing_text("events", id))`
for both an absent event and a hidden one, so the error is not an oracle.

Events cannot narrow after a link exists: `update_event` does not take
`visibility` (D8), `events` is not in `sharing.SHAREABLE`, and an event
links only to an engagement at least as wide as itself (every policy reader
drops an event whose engagement it cannot read, which narrows it in
effect). Containment at write time therefore holds for the life of the
link. The one exception is recorded under "Residual risks".

### D5. Ingested items take the meeting's tier

Ingest payloads carry no `visibility` today, so every approved record is
created at the workspace tier. With an event chosen, `ingest_notes` copies
the event's `visibility` and `crew_id` into each payload. Without this, D4
refuses every approval for a crew or private meeting, and the proposal
returns to pending with no way forward. The copy also gives the narrowest
correct audience: the people who were in the room.

### D6. The browser never converts a time zone

The frontend does not know the team zone, and My Day's comment explains why
it must not guess (`app/page.tsx`, the week label). The range endpoint
returns `today` (from `db.today()`) and `starts_local`/`ends_local` (from
`schedule.with_local`) for every event. The grid places items by those
strings. Date arithmetic for the grid uses `Date.UTC` on `YYYY-MM-DD` keys
only, so the browser's zone never enters.

### D7. An all-day end date is exclusive

This matches RFC 5545. The ICS feed already writes `DTEND;VALUE=DATE` from
the stored value, and calendar clients read it as exclusive. No test or
playbook uses an all-day end today, so the grid adopts the feed's meaning,
and slice 1 pins it with a test. A one-day all-day event is `ends_at` empty
or the next day. The add form offers "all day" plus a last-day field and
stores last day + 1.

### D8. Editing is `event_edit`, and visibility is not editable

This follows the precedent of `note_edit`, `blocker_edit`, `promise_edit`
and `intake_edit`. A new family key means existing `event` grants
(autonomous or notify) do not silently cover rescheduling: new edit grants
start at review. Visibility stays out, because `policy_context.py` states
that core update services never accept it ("an ignored extra JSON field
cannot replace stored classification"). A future share-an-event feature
goes in `sharing.py`, which only widens.

### D9. Lock order: the event first

"A read takes no lock" (CLAUDE.md) applies here: the event's tier decides
whether the link write is allowed. Each path takes
`policy_context.hold_resource("event", id)` FIRST in its transaction, then
`SELECT ... FOR UPDATE` on the item. The approval path takes `("event_id",
"event")` FIRST in the parent-lock loop in `review.py`, before the
engagement: `update_event` holds the event and then, relinking it, its new
engagement, and the create services probe the event before their insert
checks the engagement's foreign key. With the engagement first, a reschedule
and an approval deadlock (`test_rescheduling_and_approving_do_not_deadlock`).
Create services run the event probe with `FOR KEY SHARE` right after they
resolve the row's tier, which the containment check needs: a concurrent
cancel waits instead of turning the insert into a foreign-key 500. No path
takes the event after the item or after the engagement.

### D10. Time away follows the rule of the reader it derives from

The calendar shows a person's own windows at any date. It shows other
people's windows only when `ends_on >= today`. That is the anti-surveillance
rule (docs/INSIGHTS.md: person-level data only for planning the future), and
it matches what `GET /api/absences` exposes today. Another person's window
is included when the viewer's tier filter admits it, or when
`absences.TEAM_SEES_DATES` admits it. In the second case the kind is masked
to `away` and the note is dropped unless the row is workspace, which is
exactly what `portfolio.capacity_ahead` shows every signed-in user. The
calendar is not a new audience for any window.

## Slices

Each slice is one commit on its own branch, or a short series of commits.
Each slice passes `./scripts/lint.sh` and the full backend and frontend
suites. Each deletes its own bullet from the ROADMAP section in the same
commit. Each new test must fail against the code before the change, and its
fixtures must come from a running instance (CLAUDE.md conventions).

### Slice 1 - The calendar page (read path)

Backend:

- `schedule.calendar_range(start, end, viewer, *, resource_filter)` in
  `services/schedule.py`.
  - Both dates go through `db.validate_date`. `end >= start` and a span of
    at most 42 days (a six-week grid) are required, or it raises
    `ValueError`, which maps to 400.
  - **Events overlap, not start-in-window.** Every event reader today
    compares `starts_at` only, so a meeting that starts before the window
    disappears. Timed rows (`length(starts_at) > 10`): `starts_at < win_end
    AND (ends_at > win_start OR (ends_at IS NULL AND starts_at >=
    win_start))`, with the window from `db.local_event_window(start)[0]` and
    `db.local_event_window(end)[1]`. Date-only rows use the same shape
    against `start` and `end` as dates (D7). This needs a `ponytail:`
    comment: the query scans every event before the window end, and a span
    cap or a range index is the fix if events reach six figures.
  - Tasks: `status NOT IN ('done', 'void') AND due_date BETWEEN ? AND ?`.
    The query selects no link column, so no link id needs redacting.
  - Milestones: `status != 'done'`.
  - Promises: `status = 'open'`, with the direction word the ICS feed uses.
  - Time away: D10.
  - Every query takes `scope.visible_filter(viewer, table)` and `LIMIT n+1`.
    The response lists each truncated kind in `truncated`, so the page can
    say that more items exist instead of dropping them without notice.
  - Returns `{start, end, today, events, tasks, milestones, promises,
    time_away, truncated}`.
- `GET /api/calendar?start=&end=`: a `ProjectionPolicy(...,
  "skein.rest.get.calendar", ...)` with `filter_rows` per kind
  (`policy_context.resource_contexts` covers all five). The handler contains
  `ProjectionPolicy(`, which satisfies
  `test_every_bare_route_literal_is_accounted_for`.
- `GET /api/events/{event_id}`: `schedule.get_event(id, viewer)` plus
  `with_local` plus a single-row policy decision, or 404. The panel and deep
  links need it, because the event can be outside the loaded month.

Frontend:

- `frontend/app/calendar/page.tsx`: month grid at `sm` and wider, and a day
  list below `sm`. A seven-column grid is unreadable at 360px, and
  `responsive.spec.ts` walks that width. Previous, Today and Next controls.
  Kind toggles, with "Only my tasks" on by default. Date helpers go in
  `frontend/lib/calendar.ts`, with their own vitest.
- Event panel: `?event=N`, read on the calendar page the way Notes reads
  `?note=`. The task peek is task-specific, and making it generic is a
  refactor this feature does not need. The panel copies the task peek's
  dialog rules: `role="dialog"`, `aria-modal`, focus to Close, Escape, and
  focus returned to the opener. It shows the team-clock time, attendees,
  description, agenda and the pre-meeting brief (My Day keeps the outcome
  buttons). Move
  `StakeholderBrief` out of `app/page.tsx` into `components/` so that My Day
  and the panel share it. Delete keeps the dashboard's existing confirmation
  text.
- Add event: select a day to prefill the date. Fields: title, start, end or
  all day (D7), description, attendees, agenda, and the tier control the
  other create forms use.
- Navigation: `{ href: "/calendar", label: "Calendar" }` under Work, after
  "Plan the week". `nav-search.tsx` `ENTITY_PAGE.event` becomes
  `/calendar?event=${id}`, with a special case like the one for notes.
- Retire the Browse → Calendar register (open question 1). Remove it from
  `BROWSE_GROUPS`, `COLLECTIONS` and the section, and update
  `browse-registers.spec.ts`, `dashboard-register-controls.test.tsx` and
  `hash-deep-links.test.tsx`.
- `whimsy.ts`: a `calendar` pool in `EMPTY` and in each `PACK_EMPTY` pack.
  An unknown key falls back to `allclear` ("Nothing needs you…"), and that
  is a false claim on a calendar (`all-clear-claims.test.ts`).

Registration and docs: `responsive.spec.ts` `PAGES` and `CLAIMS`, and
`loading-states.test.tsx`. Field-guide card `calendar` (`ties: mark`,
`POST /api/field-guide/calendar`, `link: /calendar`, `since:` the ship
date). Add `"calendar": None` to `PREDICATES`, and edit both card counts in
`test_fieldguide.py`. Rewrite the FEATURES.md Calendar row and add the
navigation line.

Tests that pin the risky parts, in `backend/tests/test_calendar.py`:

- A range longer than 42 days, and a reversed range, return 400.
- A timed meeting that starts the day before the window and ends inside it
  is returned.
- A 23:30 meeting on the team clock lands on its team day with `SKEIN_TZ`
  west of UTC.
- An all-day end is exclusive (D7).
- Another person's private event is absent. A crew event is present only
  for members.
- Another person's past time away is absent. Their dates-shared private
  window shows as `away` with no note.
- `truncated` names a kind at its limit.
- A denying policy rule drops rows (the `test_policy_axis` pattern).

### Slice 2 - Edit and reschedule

- No policy fix comes first. `policy_context._target_engagement` reads a
  stored `engagement_id` only for tasks and milestones, but the gate and the
  review both call `for_change` with an `actor`, and that path uses
  `_target_engagement_scoped`, which reads it for every engagement-linked
  entity. The unscoped resolver serves only playbooks (`for_route`). A test
  pins that a project rule judges an event edit.
- Move `_canon` out of `schedule_event` to module level, together with the
  end-after-start check. That check then runs on the MERGED final start and
  end.
- `schedule.update_event(event_id, title="", starts_at="", ends_at="",
  description="", attendees="", agenda="", engagement_id=0, *, actor,
  origin)`:
  - Wraps everything in one `db.transaction()` and reads the row with
    `SELECT ... FOR UPDATE`.
  - Raises `scope.missing` if the row is absent, so an approved proposal
    whose target is gone auto-rejects.
  - Calls `assert_editable(verb="update")`.
  - Empty string means unchanged. `"-"` clears `ends_at`, `description`,
    `attendees` and `agenda`. For `engagement_id`, 0 means unchanged, a
    negative value unlinks, and a new link reuses the guard in
    `schedule_event`.
  - Refreshes the search entry (`index_record`) and writes one
    `log_activity("update_event", scope.detail(tier, "#id", <field
    names>))`, with field names only, never the text.
  - Leaves `outcome_status` alone.
- `PATCH /api/events/{event_id}` with an `EventPatch` model: `extra="forbid"`
  and caps no larger than `EventIn` (`test_patch_cap_parity.py`). It calls
  `ratelimit.check("write", user)`. `NotFound` maps to 404 and `ValueError`
  to 400.
- Agent tool `update_event` in `tools/schedule.py`, gated as `event_edit`
  `update` (D8). The registries it needs, each guarded by a different test:
  - `review._registry`, `_DIFF_TABLES` and `_TARGET_TABLE`
  - `lexicon.CAPABILITY`
  - `_gate._FAMILY`
  - `policy_context._TABLES`. Without it the gate skips the
    requester-readability and private-row check;
    `test_a_project_rule_judges_an_event_edit` fails.
  - `notifications._SOURCE_ALIASES`
  - `activity.VERBS`
  - `ALL_TOOLS` and `CORE_WRITE_TOOLS`
  - `test_gate_coverage.py` `ARGS`, with one real field, and
    `expected_writers`. Without the `ARGS` entry the tool degrades to an
    error path, or passes silently and is wrapped as a read.
  - The tool counts in README.md and FEATURES.md (62 → 63)
  - The edit-tool prose in the Chief-of-Staff prompt
  - `SHARED_CHAT_TOOLS`, beside `schedule_event`
- Review diff: `review.change_diff` shows the stored UTC `starts_at` beside a
  team-clock proposal. For events, render the current values through
  `db.local_wall`, so both columns are on the same clock.
- Frontend: an Edit form in the event panel. Update the `how:` text of the
  `calendar` card. No new card.
- The ICS feed does not change. The UID is stable, and subscribed clients
  replace the event on the next refresh. `SEQUENCE` is left out until a
  client that ignores the update is seen.

### Slice 3 - Items that came out of a meeting

- Migration `044_event_links.sql`, following the style of `026` and `028`.
  For each of the seven tables:
  `ADD COLUMN event_id bigint REFERENCES events(id) ON DELETE SET NULL`,
  plus `CREATE INDEX idx_<table>_event_link ON <table>(event_id) WHERE
  event_id IS NOT NULL`. Without the indexes, every event delete scans
  seven tables.
- `schedule.check_event_link(event_id, *, actor, tier, crew_id, label)`:
  the probe (D4, D9) and containment, raising `ValueError`.
- The seven create services get `event_id: int = 0` and call the check.
  They must: approval passes the payload into the service as keyword
  arguments with no filter, so a key the service does not accept raises
  `TypeError` at approval, and the proposal returns to pending.
  - `collab.save_note`, `collab.record_decision`, `collab.ask_question`
  - `work.create_task`
  - `blockers.raise_blocker`
  - `promises.add_promise`
  - `intake.submit_request`
- The matching agent tools get `event_id: int = 0`: `save_note`,
  `record_decision`, `ask_question`, `create_task`, `raise_blocker`,
  `add_promise` and `submit_intake_request`. Under the review gate an agent
  has no item id until approval, so a separate link call cannot follow a
  create. The link must travel in the create payload.
- Linking after the fact:
  - Services: `schedule.link_item(event_id, kind, item_id, *, actor)` and
    `schedule.unlink_item(event_id, kind, item_id, *, actor)`, over the
    constant `schedule.LINKED` map from kind to table and title column. Lock
    order follows D9.
    `assert_editable` runs on the item. Each writes one activity row
    (`link_event` / `unlink_event`) through `scope.detail` on the item's
    tier.
  - Routes: `POST /api/events/{id}/links` and `DELETE
    /api/events/{id}/links/{kind}/{item_id}`.
  - No agent tool links after the fact. Its proposal would carry the
    item's id, and the review queue shows a proposal to everyone who can
    read its target, the meeting. A private item's id would reach them.
    Agents link at creation through the create tools' `event_id`.
  - A link does not bump `updated_at` and emits no domain event. The 1.0
    event catalog (`public/events.py`) is frozen, and no typed view carries
    `event_id`. `update_task` does not accept `event_id`, so the string
    never reaches `skein.task.updated.changes`.
- `schedule.event_items(event_id, viewer, resource_filter)` follows the
  pattern of `engagement_brief.brief`: the parent through `visible_filter`
  or `scope.missing`; then each table through its own `visible_filter`,
  `policy_context.filter_resource_rows` and `LIMIT`. Each row is its id and
  title, with no link column. Served as `GET /api/events/{id}/items` and
  shown in the panel as "From this meeting".
- Redaction: the survey found three places.
  - `work.redact_task_relationships`: null `event_id` when the reader cannot
    read the event.
  - The admin export `_make_export`: null `event_id` for events the export
    leaves out, next to the existing `engagement_id` and `task_id`
    handling. Extend `test_export_redacts_relationships_to_omitted_rows`.
  - `sharing._PARENTS`: `event_id` for each shareable linked kind. A share
    is refused while the linked meeting is narrower.
- Ingest:
  - `IngestIn` gets `event_id: int = 0` and `extra="forbid"`. Today an
    unknown field is ignored without notice.
  - `ingest_notes(..., event_id=0)` checks that the event is readable before
    it proposes anything (400 otherwise), then stamps `event_id`,
    `visibility` and `crew_id` into every payload (D5).
  - `/ingest?event=N` names the meeting and has a control to clear it. My
    Day's meeting ask (`briefing.py` builds its `/ingest` link) points
    there.
- Outcome hint:
  - `meetings_awaiting_outcome` rows gain `linked`, a count of items the
    VIEWER can read. It is one `UNION ALL` query, grouped by event, with
    each table's own filter. A count that includes a hidden row reveals
    that the row exists.
  - My Day's reason adds "1 item came out of it" or "N items came out of
    it", with the plural computed. The string carries a number, so it
    is never warm.
  - The reader still records the outcome. FEATURES.md says Skein never
    infers it.
- Field-guide card `meeting_links` (`ties: predicate`): true when the person
  created at least one row that has an `event_id`. Edit both card counts.
- `backend/tests/test_event_links.py`:
  - The containment matrix: a workspace item on a private meeting is
    refused; a crew item on another crew's meeting is refused; a private
    item on any readable meeting is allowed.
  - A hidden event id gives the same 400 text as an absent one.
  - Cancel keeps the items and nulls the link.
  - Ingest stamps the tier, and approval then succeeds for a crew meeting.
  - An approval after the meeting is deleted stays pending with a reason.
  - `event_items` and `linked` both leave out another person's private item.
  - The export nulls a hidden `event_id`.
  - A share is refused while the linked meeting is narrower.

### Slice 4 - Agenda references, and events as a reference target

- `services/refs.py`: add `"event"` to `TARGETS` and `("events", "title")`
  to `_TITLE_SOURCE`. Add a `quoted: bool = True` keyword argument to `refs`
  and `readable_refs`. `_QUOTED` blanks single-quoted spans because the
  generated receipts quote titles, but in human text an apostrophe does the
  same: "Mira's blocker #4 … don't" loses the reference. The agenda passes
  `quoted=False`.
- `frontend/lib/entity-ref.ts`: `event: (id) => \`/calendar?event=${id}\``.
  It is a page, so a normal link works. The task peek rule does not apply.
- Check `intervention.py`: a finding receipt key that ends in `_id` becomes
  a reference when its stem is in `TARGETS`. Once `event` is a target, a
  rule that stores `event_id` renders it as a link. That is the intended
  effect, but confirm which rules store it.
- `GET /api/events/{id}` adds `agenda_refs`, from `readable_refs(agenda,
  viewer, resource_filter=policy.permits, quoted=False)`. `handoff.py`
  warns that a caller without a resource filter skips every workplace rule.
  The panel renders the agenda through `splitReceipt` and `ReceiptLine`,
  never as markup.
- Update the `how:` text of the `calendar` card.
- Tests:
  - An agenda "Discuss question #3 and Mira's blocker #4" links both. This
    fails against today's `_QUOTED`.
  - Another person's private question is neither linked nor titled.
  - A denying policy rule drops the reference.

## Residual risks accepted

- **An author can keep a link to a meeting they can no longer read.** For
  example, they leave the crew that owns the meeting. A private item is read
  by its author alone, and the author clause of `visible_filter` keeps a crew
  item readable by its author after they leave the crew too. In both cases
  the id is one they once read. Tasks are redacted anyway. Redacting the
  other six kinds means a pass on every list endpoint that returns them. If
  that is wanted, it is a later item.
- **The overlap query has no lower bound on `starts_at`** (the `ponytail:`
  comment in slice 1).
- **Linking changes no `updated_at`.** A client that syncs on `updated_at`
  does not see a new link. No such client exists for these kinds today.

## Open questions for the owner

Each question has a recommended default. The slices proceed on the default
unless the owner chooses otherwise.

1. **Retire Browse → Calendar?** Recommended: yes. Two event forms that
   differ are the next reader's bug. Time away stays in Browse.
2. **Tasks on the calendar: only mine by default?** Recommended: yes, with
   a toggle. Every visible task with a due date is noise on a team month.
3. **Ingested items take the meeting's tier (D5)?** Recommended: yes. The
   alternative, workspace as today, fails D4 for every crew or private
   meeting.
4. **A week view in the first version?** Recommended: no (see the table
   above).
