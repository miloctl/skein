# Board, the team's open work by status

Confirmed 2026-09-28 with the repository owner.

- **Outcome:** A person sees the team's open work as four columns named by
  the task statuses. They move a task between columns by drag, by a Move
  button and a column, or by keyboard alone. The status rules that already
  exist decide every move: a blocker sets Blocked, the sponsor's verdict
  closes a delegation, and resolving the last blocker releases the task.
- **User:** The strike team at standup and during the Monday plan. Agents do
  not use the board. Their writes show on it.
- **Why now:** Work → Browse is a list, and the engagement brief stops at 50
  open tasks and says so (`TASK_CAP`, `app/engagement/[id]/page.tsx:93`). No
  surface shows flow across `todo → in_progress → blocked → done`. The owner
  approved the board with four other features on 2026-09-28
  (`docs/ROADMAP.md:34`).
- **Success:** With `SKEIN_MODEL_PROVIDER=mock`, a person opens an
  engagement's board from its brief. They drag a To do card to In progress.
  With the keyboard only, they move a card to Blocked, write what blocks it,
  and a blocker row exists with that `task_id`. They resolve it from the
  card, and the service returns the card to In progress. A second person
  with a stale board gets a 409 and the current state, never a silent
  revert. A delegated card offers no move. A task the reader cannot read
  never appears, not even in a count. axe is clean in the phone and dark
  walks.
- **Constraint:** Keyless: every slice works with the mock provider. No new
  table and no migration. No drag library: native HTML5 drag and drop plus a
  Move disclosure. Writes use the existing `PATCH /api/tasks/{id}`,
  `POST /api/blockers` and `POST /api/blockers/{id}/resolve`. No polling and
  no push channel.
- **Out of scope:** Swimlanes, manual card order, WIP limits, user-defined
  columns, live push updates, a "this week" lens, hotkeys, a CLI or MCP
  board, and card editing on the board (the peek edits). Each is a cut row
  in `docs/ROADMAP.md` with the trigger that brings it back (slice 4).

The 2026-08-21 posture note freezes new portfolio surfaces, and the owner's
2026-09-28 approval reopens this one. The board is a view over rows that
already exist: it adds no agent tool, no gated entity, no activity verb and
no setting. Branch order: the calendar (0.6.10) and the trust-loop hardening
(0.6.11) are merged, and `feature/blocker-form` merges before this branch.
That branch replaces the peek's bare `blocked` option with a "What blocks
it?" form, and the board reuses the form (D7). Tasks that routines generate
are ordinary `create_task` rows and land in To do with no board change.

## What exists before this work (2026-09-28)

| Part | Where | Note |
|---|---|---|
| Statuses | `services/work.py:11`, CHECK in `004_task_void.sql:9` | `todo`, `in_progress`, `blocked`, `done`, `void`. The `open_only` filter already drops `done` and `void`. |
| Task reads | `work._task_rows` (`work.py:1482`), `list_tasks_joined` (`:1459`) | `_task_rows` joins milestone, engagement, meeting (the calendar's `event_id`) and waiting-on, each under its own `visible_filter`. It filters by `status`, `open_only`, `milestone_id` and `assignee`, and orders by priority or `completed_at`. `list_tasks_joined` passes only `status` and `order`. |
| Policy scan | `routes/api.py::_task_collection` (`:282`) | Scans past `skein.rest.get.tasks` denials to 500 rows, then `redact_task_relationships` nulls hidden milestone, engagement, meeting and waiting-on links. |
| Browse read | `GET /api/tasks/browse` (`routes/api.py:348`) | Open and done slices from one snapshot, projected to `_TASK_BROWSE_FIELDS` (`:334`). The calendar did not change it, and it carries no `event_id`. |
| Blockers on a task | `work.blocking` (`work.py:1330`) | One task per query, open blockers only, under the blocker's `visible_filter`. The peek filters them through policy too (`filter_task_projection`, `work.py:1303`). |
| Status rules | `services/blockers.py` | `raise_blocker` refuses a blocker wider than its task (`:124`) and sets `blocked` unless the task is done or void (`:164`). `resolve_blocker` returns the task to `in_progress` when the last blocker resolves (`:294-304`) but does not return that fact. `_update_task_locked` refuses `done` and `void` on a delegated task. |
| Task PATCH | `routes/api.py:3301` | Holds the task row first (`policy_context.hold_resource`, `FOR UPDATE` at `policy_context.py:135`), calls `ratelimit.check("write")`. `TaskPatch` ignores unknown keys. |
| Stale-state precedent | `services/crews.py:315` | `expected_role` raises `db.Conflict` (`db.py:120`), which `main.py:936` maps to 409. |
| Blocker form | `feature/blocker-form` (merges first) | `components/raise-blocker-form.tsx`: title, owner, impact, `POST /api/blockers` with `task_id` and the task's tier. Before it, no page posted `/api/blockers`: capture (`capture.py:276`) and standup (`collab.py:577`) set no `task_id`. |
| Stale-work marker | `slas.STALE_WIP_DAYS` = 7 | Cutoff `db.local_midnight_utc(db.today() - 7 days)` in `intervention.py:490` and `portfolio.py:178`. Worklog notes bump `tasks.updated_at` (`delegation.py:294`, 0.6.11), so a delegate that reports progress does not read as stalled. |
| Engagement brief | `services/engagement_brief.py:99-100` | Membership: `t.engagement_id = ?` or a milestone of the engagement. Open work capped at 50 with a "50+" title (`app/engagement/[id]/page.tsx:403`). |
| UI parts | `frontend/` | `PeekLink` and `openTaskPeek` (`task-peek.tsx:124-132`), `skein-peek-close` (`:245`), `MenuPanel` private to `chat-sidebar.tsx:27-62`, the drag idiom (`chat-sidebar.tsx:373-374`, `:662-664`), `reportStatus` (`lib/status.ts`), `skein-attention-change` and its cross-tab bridge (`lib/attention.ts`, `nav.tsx:146`), a 15-second GET cache (`lib/api.ts:97`). |
| Navigation | `lib/navigation.ts` | Work: Plan the week, Calendar, Health, Browse, Notes, Insights, Reports. `ROUTE_TITLES` and `activeNavigation` derive from the array. |

## Decisions

Each decision names the alternative it replaced, so the next reader does not
make it a second time.

### D1. The board is its own route, `/board`

One Work child after Browse: `{ href: "/board", label: "Board" }`. The
title and the active state follow from the array. A Browse register was
refused: `app/dashboard/page.tsx` is 2,227 lines and loads 12 collections
plus the pulse on mount (`COLLECTIONS`, `:545`), and a board inside it pays
that on every open.

### D2. Columns are the statuses, and order is priority

Four columns: To do, In progress, Blocked, Done. `void` is not a column. The
peek keeps its confirmed void control (`task-peek.tsx:714`). Open cards use
the Browse order, priority then id. Done orders by `completed_at DESC`.
There is no rank column. Fizzy sorts by field and has no rank
(`~/external/fizzy/app/models/card.rb:22-24`). Kaneo keeps a rank and
rewrites every card in both columns on each drop, with no rollback
(`~/external/kaneo/apps/web/src/components/kanban-board/index.tsx`). Skein
already has priority, and a rank is a migration plus a write per drop.

### D3. Rows pass the Browse policy action

The row decision uses `skein.rest.get.tasks`, the action Browse and the peek
use. A workplace rule that hides a task from Browse must hide it here, and a
new row action skips every deployed rule. The route's generic gate
action is new, `skein.rest.get.tasks.board`, derived by the stated rule
(`extensions/fastapi.py::_route_policy_action`, `docs/EXTENSIONS.md:225`).
No EXTENSIONS edit is needed.

### D4. A PATCH move is compare-and-set on the status

`TaskPatch` and `update_task` gain `expected_status`. After `current` is read
and `assert_editable` passes, a mismatch raises `db.Conflict`: "Task #12
changed after you loaded the board. It is now done. Move it again from the
current board." The message names the current status, which the caller
can read, and never echoes the sent value. A value outside `TASK_STATUSES`
is a `ValueError` (400), so a typo is not an endless 409. The compare needs
no new lock: `patch_task` already holds the row first in its transaction.
The comment at the compare must name that hold, because
`_update_task_locked` reads `current` without `FOR UPDATE`, and any other
caller that passes the argument compares an unlocked read. The agent tool
(`tools/work.py:174`) does not take it.

### D5. One move rule for every path, and no optimistic move

`move(card, target)` is called by the drop and by the Move panel, and by
nothing else:

| Card state | Target | Action |
|---|---|---|
| delegated | any | None. The card is not draggable and has no Move button. The peek explains why. |
| has visible open blockers | any other column | The panel lists each blocker with Resolve, not the columns (D6). |
| done | Blocked | None. Status line: "Reopen task #12 first. Then raise a blocker." `raise_blocker` never flips a done task (`blockers.py:164`). |
| other | Blocked | The blocker form in the card (D7). No PATCH: the service sets `blocked`. |
| other | To do, In progress, Done | `PATCH {status: target, expected_status: card.status}` |

Only the changed field travels (the peek's rule, `task-peek.tsx:782`). The
card shows "Moving…" with `aria-busy` until the write returns, then the
board reloads. Fizzy and kaneo move first and never roll back, so a failed
drop leaves the screen wrong. Skein's inline actions re-fetch
(`dashboard/page.tsx:726`).

### D6. Leaving Blocked is a board rule, not a service rule (owner)

The board requires the reader to resolve the visible blockers before a card
leaves. The panel says: "Resolve its blockers to move it. When the last one
is resolved, the task moves to In progress." Resolve calls
`POST /api/blockers/{id}/resolve` with `{resolution: "resolved from the
board"}`, the kind of text the register sends (`dashboard/page.tsx:1304`).
Other doors can still PATCH a blocked task out with a blocker open: an API
client, the `update_task` agent tool and the forge webhook. A refusal in
`_update_task_locked` for every door was deferred. Trigger: a task reaches
Done while a blocker raised before it finished is still open. A blocker
raised on a task that is already done does not count, because a stale board
files one on purpose (residual risks below). `resolve_blocker` holds the task
before it writes the blocker (the order the escalation sweep takes), so two
people who resolve the last two blockers at once release the task.

### D7. Blocked reuses the peek's blocker form (owner)

`feature/blocker-form` builds `components/raise-blocker-form.tsx` for the
peek and merges before this branch. The board renders the same component in
the card. It relies on the form to take the task's id, title, `visibility`
and `crew_id`, to post `{title, owner, impact, task_id, visibility,
crew_id}`, to focus its title field on mount, and to report success and
cancel to its caller. The card's own tier is sent because `raise_blocker`
refuses a blocker wider than its task. If the merged component lacks a prop
the board needs, slice 4 adds it to that component, never to a copy.

### D8. Raising a blocker holds its task, after the meeting (shipped)

Shipped with the task panel's blocker form (branch `feature/blocker-form`,
commit d579f6bf): `raise_blocker` reads the task `FOR UPDATE`, with the
meeting probe (`check_event_link`) above it, because the calendar's order is
meeting first, then the item (`schedule.link_item`). Before the fix, a
completion that committed between the unlocked read and the flip turned a
done task back to `blocked` and cleared `completed_at`. The board relies on
it and adds nothing here.

### D9. No swimlanes, and no view of another person (owner)

"Only my tasks" is the personal view. The route takes `mine`, never an
arbitrary assignee, so the board cannot show one other person's work.
Nothing aggregates per person: no lane totals and no per-person done
counts. A lane that carries Done puts per-person throughput side by side on
a team screen, which is the leaderboard the future-versus-past rule forbids
(`docs/INSIGHTS.md:19`). Capacity and `/planning` answer "who carries what".

### D10. The board reloads on events, never on a timer (owner)

It reloads on mount, on `skein-attention-change` (capture and review
verdicts fire it, and the nav relays other tabs), on `skein-peek-close` (an
edit in the peek), on `visibilitychange` to visible, and after each board
write. Each read passes `cache: "no-store"`: `api()` serves a GET from a
15-second cache, and a focus reload inside that window shows the board from
before another person's move. A per-request generation guard drops an
older response (the `dashboard/page.tsx:734-771` pattern). Two people who
move one card meet the row lock plus `expected_status`: the second gets 409
and the board reloads. Skein has no push channel for the web UI, and
adding one is out of scope.

### D11. "Not moved" is the 7-day stale-work marker

A card in In progress shows "Not moved for 9 days" only when `updated_at`
is older than the `STALE_WIP_DAYS` cutoff, the same one Health and the
intervention queue use, so the board marks exactly the tasks those surfaces
name to the same readers. Insights' `AGING_WIP_DAYS` (14) was refused: it
is a team-aggregate finding (`insights.py:445`), and two thresholds for one
card state disagree on screen. The marker is about the task: `text-ink-3`,
no danger color.

### D12. Bounds: 500 open tasks, 7 days of Done

Each slice returns at most `TASK_LIST_LIMIT` (500) rows after policy. If
`open.length >= limit`, a line above the columns reads "This board shows the
first 500 open tasks, highest priority first. Open one engagement to see the
rest." At the cap, an empty open column shows no empty-state claim, because
rows past the cap can belong to it. Done holds 7 days
(`work.BOARD_DONE_DAYS`, matching Browse's `SHIPPED_WINDOW_DAYS`,
`dashboard/page.tsx:628`). The server sends both numbers, so the client
does not copy them. Tier rule question 4: no admin has a standing reason to
change either between deploys, so both stay module constants.

### D13. Engagement membership copies the brief's rule

The engagement filter is the rule at `engagement_brief.py:99-100`, so the
board and the brief agree on what belongs to an engagement. That includes
a task that belongs through a milestone the reader cannot open. Fix both or
neither: this plan leaves both as they are.

### D14. The board is never a new audience

- Every row passes `scope.visible_filter` in `_task_rows`, then
  `ProjectionPolicy.permits` under `skein.rest.get.tasks`.
- Blocker badges pass the blocker's own `visible_filter` and the same
  policy callback, the peek's rule for nested blockers.
- The scope header reads the engagement or milestone under its own filter
  and policy. A hidden or denied parent returns 404 through
  `scope.missing`, never an empty board, which confirms the id exists.
- Column counts count only rows the reader received.
- `_task_collection` redacts links, so `waiting_on_*` to a hidden target
  arrives as null.
- `delegated_agent` and `committed_week` join the projection. The peek
  already shows both to the same readers.
- A new blocker takes the card's tier (D7).

No table is added, so there is no `scope.UNSCOPED` row, no `erasure.py` or
`my_data.py` entry, and none of the seven gated-entity registries changes.

## Slices

Each slice is one commit, or a short series. Each passes
`./scripts/lint.sh` and the full backend and frontend suites. Each new test
must fail against the code before its slice, and its fixtures must come
from a running instance (CLAUDE.md conventions).

### Slice 1 — The board read [S]

- `_task_rows` gains `engagement_id` (the D13 clause) and `completed_since`
  (`AND t.completed_at >= ?`). `list_tasks_joined` and `_task_collection`
  pass `engagement_id`, `milestone_id`, `assignee` and `completed_since`
  through. `GET /api/tasks/browse` does not change.
- In `services/work.py`:

  ```python
  BOARD_DONE_DAYS = 7

  def board_scope(viewer, *, engagement_id=0, milestone_id=0,
                  resource_filter=None) -> dict | None
      # None for the whole workspace, else {"kind", "id", "title"} for ONE
      # parent under visible_filter, then resource_filter.
      # Hidden or denied -> scope.missing (404). Both ids -> ValueError (400).

  def blocking_by_task(task_ids: list[int], viewer) -> dict[int, list[dict]]
      # blocking() with "b.task_id = ANY(?)". blocking(task_id) becomes
      # blocking_by_task([task_id], viewer).get(task_id, []).

  def board_cards(rows, viewer, resource_filter) -> list[dict]
      # blockers: policy_context.filter_resource_rows("blocker", ...) over
      #   blocking_by_task, reduced to [{id, title}].
      # quiet_days: whole days since updated_at, set only when status is
      #   in_progress and updated_at < the D11 cutoff, else None.
  ```

- `GET /api/tasks/board?engagement_id=&milestone_id=&mine=`, declared
  beside `/tasks/browse` and before `/tasks/{task_id}` (the reason is in the
  `get_task` docstring, `routes/api.py:383-386`):

  ```python
  _TASK_BOARD_FIELDS = (*_TASK_BROWSE_FIELDS, "committed_week",
                        "delegated_agent", "waiting_on_type", "waiting_on_id")

  with db.read_transaction():
      policy = ProjectionPolicy(..., "skein.rest.get.tasks", "rest", viewer)
      scope_row = work.board_scope(viewer, ..., resource_filter=policy.permits)
      filters = {engagement_id, milestone_id, "assignee": user if mine else ""}
      open_rows = _task_collection(policy, viewer, status="open", order="priority", **filters)
      done_rows = _task_collection(policy, viewer, status="done", order="completed",
                                   completed_since=now - BOARD_DONE_DAYS, **filters)
  fieldguide.mark(user, "board")   # after a successful read only
  return {"scope": scope_row, "limit": work.TASK_LIST_LIMIT,
          "done_days": work.BOARD_DONE_DAYS, "today": db.today(),
          "open": [...board_cards projected...], "done": [...]}
  ```

  `today` is the team's day, so "overdue" on a card agrees with Planning,
  which answers the same field.

  The mark follows the `task_peek` precedent (`routes/api.py:423`), so no
  `POST /api/field-guide/board` route is needed. The card lands in slice 3,
  and until then `mark` ignores the id.
- `backend/tests/test_task_board.py` (the route answers 404 or 422 today):
  - The projection keys equal `_TASK_BOARD_FIELDS` plus `quiet_days` and
    `blockers`, and `event_id` is absent.
  - Engagement membership through `engagement_id` and through a milestone.
  - A hidden engagement or milestone returns 404 with the absent-row text.
  - Done window edge pair: completed 6 days 23 hours ago is in, 7 days 1
    hour ago is out.
  - `quiet_days` edge pair: `in_progress` just past the cutoff is marked,
    just inside is null, and a 30-day-old `todo` is null.
  - Another person's private blocker on a workspace task is absent from
    `blockers`.
  - A policy deny on `skein.rest.get.tasks` removes the row (the pattern of
    `test_task_browse_projection.py::test_browse_projection_runs_after_visibility_and_workplace_policy`).
  - `mine=true` returns only the caller's tasks.

### Slice 2 — Compare-and-set and the blocker write paths [XS]

- `expected_status: str = Field("", max_length=20)` on `TaskPatch`, and a
  keyword-only `expected_status` on `update_task` and
  `_update_task_locked` (D4).
- `resolve_blocker` returns `"task_unblocked": bool(task_unblocked)`. The
  value exists (`blockers.py:294`). The board uses it to say whether the
  card moved without a second read.
- Tests:
  - A mismatched `expected_status` returns 409, and the row and the ledger
    stay unchanged. A match returns 200. An unknown status returns 400.
    Against today's code the mismatch returns 200, because `TaskPatch`
    ignores the key.
  - The agent tool signature (`tools/work.py::update_task`) has no
    `expected_status`.
  - Of two blockers on one task, resolving the first returns
    `task_unblocked: false` and the last returns `true`.

### Slice 3 — The read-only board page [M]

- `frontend/app/board/page.tsx`: the fetch, the columns, the scope header,
  the cap line (D12), the D10 reload listeners and an "Only my tasks"
  checkbox, which sets `mine`. A scoped board shows "Engagement: Atlas · Show
  all work". The page reads `?engagement=` and `?milestone=` with
  `useSearchParams` in its own Suspense boundary, as `app/notes/page.tsx`
  does for `?note=`. `window.location` was the draft's choice and was
  refused: "Show all work" is a same-route link, and a read made at mount
  keeps the old scope after that soft navigation. The primitive values keep
  the peek's pushed `?task=` from causing a reload.
- Layout: four `<section aria-labelledby>` columns, each an `<h2>` with a
  count ("In progress (7)") over a `<ul>` of cards, in
  `grid grid-cols-1 md:grid-cols-4 gap-3`. Below 768px the columns stack
  and the page scrolls down. There is no horizontal scroll region, so the
  keyboard-reachable-scroll probe in `e2e/responsive.spec.ts` has nothing to
  catch. Fizzy's horizontal snap scroll at phone width
  (`~/external/fizzy/app/assets/stylesheets/card-columns.css:77`) and
  kaneo's `overflow-x-auto` both need a focusable scroll region. At 1024px
  each column is about 190px beside the 240px sidebar, so titles clamp at
  two lines, and the peek has the full title.
- The card, every item a fact about the task, never about the person:
  - `#12` and the title as a `PeekLink`, which opens `?task=12` over the
    board. Back closes it.
  - The meta line: `@assignee` or "unassigned", priority, "due 2026-10-03"
    with "overdue" when past, and the `committed_week` chip ("2026-W40").
  - Badges: "Blocked by #4 Vendor key", one per visible blocker, with "+N"
    past two. "Waiting on task #9". "Delegated to scout".
    "Not moved for 9 days" (D11).
  - Tokens only: `bg-card`, `border-line`, `text-weld` for blocked,
    `text-ok` for done. They are AA-checked in every pack
    (`app/globals.css:12`, the `lib/theme.ts` gate in `scripts/lint.sh`).
    No new token.
- Empty columns: "Nothing waiting." and "Nothing is blocked." can be warm,
  because nothing is asked of the reader. Done says "Nothing finished in the
  last 7 days." and stays plain, because it carries a number. D12 governs a
  board at its cap.
- Entry links on the engagement brief: "Open the board"
  (`/board?engagement=<id>`) as the first line of the Open work card. The
  extension-api `Card` takes a string title with no action slot, and that
  package is frozen. Each milestone row (`app/engagement/[id]/page.tsx:369`)
  gets a "board" link to `/board?milestone=<id>`.
- The nav entry (D1).
- Field-guide card, in the loops set:

  ```yaml
  - id: board
    feature: Board
    knot: Ossel Hitch
    set: loops
    ties: mark
    pitch: Every strand hung from one line, in the column its status names.
    how: "Open Work → Board. Select a task to open it in the task panel. To see one engagement, open the engagement and select Open the board."
    link: /board
    since: <the ship date>
  ```

  Add `"board": None` to `fieldguide.PREDICATES`. Raise both card counts in
  `test_fieldguide.py` (`:33` and `:201`, 69 and 68 at 0.6.11) by one from
  what main holds at merge.
- Registration: `/board` in `responsive.spec.ts` `PAGES` and in `CLAIMS`
  (the empty-state sentences), and a Board row in
  `__tests__/loading-states.test.tsx`, the calendar's precedent. A page that
  claims "Nothing is blocked." before its answer arrives is the false claim
  those walks catch.
- `__tests__/board-columns.test.tsx`. The server pins the void rule
  instead (`tests/test_task_void.py` checks the board omits a void task):
  the server never sends a void row in `open`, and a client fixture no code
  path emits pins nothing (CLAUDE.md). The delegated-card check is in
  `board-move.test.tsx`, beside the Move button it tests.
  - The cap line appears only at `open.length >= limit`, and a capped empty
    column makes no claim.
  - `skein-peek-close` and `visibilitychange` each trigger a read with
    `cache: "no-store"`.
- Docs in this commit: the FEATURES rows listed under slice 4 that describe
  the read.

### Slice 4 — Moves [M]

Needs `feature/blocker-form` merged.

- `components/menu-panel.tsx`: `MenuPanel` moved out of
  `chat-sidebar.tsx:27-62` unchanged, and chat-sidebar imports it. The
  alternative is a second copy.
- `components/board-card.tsx`: the card, its Move button
  (`id="board-move-12"`, screen-reader text "Move task #12: <title>"), and
  the Move panel with the D5 rules.
- Keyboard and single pointer (WCAG 2.1.1, 2.5.7):
  - Move opens `MenuPanel`, plain buttons and not `role="menu"`, the choice
    recorded at `chat-sidebar.tsx:28`. Escape and Tab-out close it.
  - The panel lists the other columns: "To do", "In progress",
    "Blocked…", "Done". "Blocked…" swaps the panel for the blocker form
    (D7), which focuses its title field.
  - After a move, focus goes to `board-move-<id>` in the card's new column,
    by its stable id after the reload (the `refocusEdit` idiom,
    `dashboard/page.tsx:781`).
  - The status region reports "Task #12 moved to In progress." as a
    confirmation, or the error as a failure. A 409 reports its message and
    the board reloads. A 429 from the `write` cap (30 in 60 seconds per
    person, `app/ratelimit.py:89`) reports through `actionError`.
- Drag and drop, for a fine pointer only:
  - The `<li>` is `draggable` only when `matchMedia("(pointer: fine)")`
    matches. On touch, a long-press drag fights scrolling, and the Move
    button is the touch path.
  - `onDragStart` sets `dataTransfer.setData("text/plain", String(id))`
    (`chat-sidebar.tsx:664`). A column accepts `dragover` only for a card on
    this board that is not already in it. `onDrop` ignores stray payloads
    (`chat-sidebar.tsx:374`).
  - The drop target shows `outline-2 outline-dashed outline-thread`, not a
    `ring`. Tailwind's ring is a box-shadow, and forced-colors mode drops
    box-shadows (the escape block at `app/globals.css:933`).
  - Nothing animates, so `prefers-reduced-motion` needs no rule. A
    transition added later must be `motion-safe:`.
- `__tests__/board-move.test.tsx`:
  - Move to In progress sends exactly `{status, expected_status}`.
  - "Blocked…" POSTs `/api/blockers` with the card's tier and sends no
    PATCH.
  - A card with visible blockers lists Resolve and no columns.
  - Done to Blocked is refused with the status line.
  - A 409 reports and reloads.
  - A stray `dataTransfer` id is ignored.
- Playwright: `locator.dragTo` moves a seeded card from To do to In
  progress, then the same move by keyboard only, with axe clean.
- Extend the `board` card's `how:`: "To move a task, drag it to a column, or
  select Move and then a column. To move a task to Blocked, write what
  blocks it. This raises a blocker. To move a blocked task out, resolve its
  blockers. A task delegated to an agent has no Move control. Only its
  sponsor's verdict closes it." (The sponsor can also close it from the task
  panel, so "in Inbox" was dropped.)
- Docs in the ship commits of slices 3 and 4:
  - `docs/FEATURES.md`: a new **Board** row (the route, the columns, the
    move rules in short, the 500 and 7-day bounds, the reload triggers, and
    why there are no lanes). The "Milestones & tasks" row (`:84`) gains the
    board read and `expected_status`. The Navigation paragraph (`:53-55`)
    lists Board `/board` under Work. The "Task side peek" row (`:92`) adds
    Board to the landing places. The "Field guide" row (`:115`) gets the new
    card count.
  - `docs/ROADMAP.md`: delete the "Board view" row (`:720-723`) and remove
    `board-view` from the list of open designs (`:708`). Add cut rows, each
    with its trigger, under "Cut, with re-entry triggers" (`:545`):
    swimlanes (a planning question that capacity and `/planning` cannot
    answer, and then open columns only with no counts), polling (a 409 on a
    shared standup board is reported), the service-wide Blocked rule (a
    task reaches Done with an earlier blocker still open), hotkeys (asked for after a season of
    use), a "this week" lens, manual rank, WIP limits, a CLI or MCP board
    (someone asks for a terminal board), and a comment count per card
    (threads merged and a person asks). "Browse task pagination" (`:539`)
    stays: the board does not paginate.
  - `CHANGELOG.md` `## Unreleased`: one entry per slice.

## Residual risks accepted

- **A hidden blocker does not hold a card.** If a blocked task's only open
  blocker is private to someone else, the card shows in Blocked with no
  badge and moves by a plain PATCH, and that blocker stays open. A badge
  reveals the blocker to a reader who cannot read it.
- **Other doors leave Blocked freely** (D6). An API client, the agent tool
  or the forge webhook can move a task out of Blocked with a blocker open.
- **A stale board can raise a blocker on a finished task.** If someone
  finished the task after the board loaded, `raise_blocker` keeps it done and
  files the blocker beside it. The reload shows the card in Done with its
  "Blocked by" badge, and the reader can resolve it there.
- **A shared screen shows another person's move late.** With no poll, the
  board changes on the next event, focus or own write (D10).
- **The workspace board stops at 500 open tasks** (D12). The engagement
  board is the working view.
- **Engagement membership includes tasks reached through a milestone the
  reader cannot open** (D13), as the brief does.
- **A fast Monday triage can reach the `write` cap.** The 429 carries
  Retry-After. There is no board bucket, because the peek and Browse spend
  `write` on the same edits.

## Open questions for the owner

None. The owner settled every decision of the draft on 2026-09-28 (D1,
D6, D7, D9 to D13, and the residual risks above). The knot is Ossel Hitch,
because Clove Hitch belongs to the calendar. The name is flavor, and the
owner can rename it.
