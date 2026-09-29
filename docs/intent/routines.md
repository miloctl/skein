# Routines, standing delegations and recurring tasks

Confirmed 2026-09-28 with the repository owner.

- **Outcome:** A person writes recurring work once, with a weekly schedule. Each time it
  is due, Skein creates a fresh task. If the person picked an agent, Skein also delegates
  the task to that agent with the person as sponsor, and the existing wake queue starts
  one bounded turn.
- **User:** Strike-team members with standing work: a lead's Monday decision sweep, a
  triage owner's Thursday intake pre-scoring, an on-call checklist every other Wednesday.
  Agents receive the delegations. They never create routines.
- **Why now:** Skein has no recurring work, the calendar cut recurring events, and every
  agent turn starts with a person typing a delegation. The owner approved routines on
  2026-09-28 with four other features from the external-repo discovery.
- **Success:** The lead writes the Monday 07:00 `quartermaster` routine once. The next
  Monday at 07:00 a delegated task exists with a pending wake. If that task is still open
  the Monday after, Skein skips that time and says why. After three skipped times the
  routine pauses itself and tells its owner once. The season readout shows the weekly
  acceptances on their own line. A person who cannot read the routine never sees its
  title, its id or its tasks.
- **Constraint:** Keyless. The routine, the tick and every guard are SQL plus stdlib
  `zoneinfo`. With `SKEIN_MODEL_PROVIDER=mock`, Skein still creates and delegates the
  tasks, and the wake records its normal mock refusal. No new dependency, no cron string
  in the UI, no new `origin` value, and no new setting.
- **Out of scope:** Monthly schedules, assignee rotation, an agent write path, recipes and
  playbook routines, among others. Each becomes a row in the `docs/ROADMAP.md` cut table
  (slice 4) with the trigger that brings it back. Routines never create `events` rows.

The 2026-08-21 posture note in `docs/ROADMAP.md` freezes new portfolio surfaces and funds
the agent loop. A routine is agent-loop work: each agent routine produces one delegation
and one sponsor verdict a week. That is also its risk to the loop's honesty. The exit
trigger reads trust rows, promotions and delegations, and weekly "nothing due" approvals
make a season read healthy by repetition alone. D11 counts routine work apart.

## What exists before this work (2026-09-28)

| Part | Where | Note |
|---|---|---|
| Delegation | `services/delegation.py::delegate_task` | Queues one wake when `origin in ("human", "agent_verified")`. Refuses a private task and an inactive or undelegatable agent. With `mint_authorized=False` it refuses an agent name that does not exist. Logs `delegate_task` with detail `#<task> -> <agent> (sponsor: <name>)`. |
| Wake queue | `services/agent_wakeups.py` | One row per agent, so two delegations to one agent coalesce into one turn. `WAKE_TOOLS` is the turn's tool set. `SKEIN_AGENT_WAKES_PER_DAY` caps turns workspace-wide. Outcome codes in `reason` (`nothing_filed`, `wake_cap` and others) show in the task peek. `review.reject_change(send_back=True)` returns a rejected acceptance to the same agent on the same task. |
| Jobs | `services/jobs.py` | `JOBS` drives cron and startup catch-up. `fire_key` names one firing per window. `run_job` fences the job, and `retry_safe` lets a failed firing run again. |
| Trust loop | `delegation.trust_scores`, `delegation.review_authority` (job `authority-review`), `review.season_readout` | Every settled proposal counts alike. `TRUST_STREAK = 5` strong approvals suggest a promotion, and `DEMOTION_STREAK = 3` strong rejections propose a demotion. `task_completion` is in `NO_AUTHORITY`, so it never holds a level. |
| Calendar | `schedule.calendar_range`, `/calendar` | The task layer draws open tasks by `due_date`. "Only my tasks" filters on `assignee`. The query selects no link column. |
| Task link redaction | `work.get_task`, `work._task_rows`, `work.redact_task_relationships` | The `event_id` pattern: a `visible_event_id` join plus `visible_ids`. The admin export and Your data run tasks through `redact_task_relationships`. |

## Decisions

Each decision names the alternative it replaced, so the next reader does not make it a
second time.

### D1. A routine is a template row, and a firing is an ordinary task

The task carries `routine_id`, like `tasks.source_finding_id`. Firing state lives on the
routine (`next_at`, `skipped_in_row`, `last_outcome`). History lives in `activity` and in
`tasks WHERE routine_id = ?`. A `routine_runs` table was rejected: a second record needs
its own scope, erasure and retention entries. `tasks.routine_id` has no foreign key (D11).

### D2. A firing is a human delegation by standing consent

The task has `origin='human'` and `created_by` = the owner. `delegate_task(...,
actor=owner, sponsor=owner, origin="human")` queues the wake unchanged. The owner can read
their own private task (`scope.CLASSIFIED["tasks"] = "created_by"`). The review gate and
the authority matrix do not change. The finer provenance is `routine_id` plus one quiet
ledger row by `scheduler`: `fire_routine` "routine #7 -> task #412". A new origin
`routine` was rejected: it needs a branch in `delegate_task`, in trust scores and in every
`origin` reader.

### D3. The routine row is the once-per-occurrence claim, and identity locks come first

A firing holds `SELECT ... FOR UPDATE` on the row, re-checks `next_at <= now`, and
advances `next_at` in the transaction that creates the task. A second process waits,
reads the advanced value, and does nothing. A per-occurrence `db.claim_job` was rejected:
that receipt commits apart from the task and can disagree with it. The tick itself still
goes through `jobs.run_job`.

Lock order: the owner's and agent's identity locks (folded, sorted), the routine row, the
crew row (`crews.assert_writable` inside `create_task`), then `delegate_task`'s locks
(`ensure_agent_identity` re-takes the agent lock at no cost). `users.rename_user` and
`users.set_active` take identity locks before rows too, so with the row first a firing
deadlocks against a rename updating `routines.agent`. `create_routine` and
`resume_routine` take the owner lock first, which also guards the `MAX_ACTIVE_PER_OWNER`
count (CLAUDE.md, "A read takes no lock").

### D4. A missed time fires once, late, however old

Several missed times collapse into one firing, dated with the latest. If more than one
collapsed, the owner gets one notice with the count. A collapsed firing that meets an
open previous task is one skip, not several. Resume and a schedule edit compute `next_at`
from now, so a pause never catches up. A restart at 07:20 fires the 07:00 routine at boot
(`catch_up=True`). A backup restored three days late fires each active routine once. This
replaced a 24-hour window after which Skein recorded `missed` and created nothing. Hermes
makes the same choice: past its grace window a dispatch is `catch_up`, "accumulated misses
skipped, executed once now" (`~/external/hermes-agent/cron/jobs.py` 941-947).

A firing is `late` when it runs more than `ON_TIME` (10 minutes, two tick periods) after
its time. The `agent-run` job has `catch_up=False` because a restart must not buy a turn
nobody scheduled. A routine's owner scheduled its turn, so the catch-up is correct here.

### D5. One open occurrence at a time, and three skipped times pause the routine

If the newest task with this `routine_id` is not `done` or `void`, the firing creates
nothing, records `previous_open`, and increments `skipped_in_row`. At `AUTO_PAUSE_AFTER =
3` the routine pauses with `nobody_finished` and notifies the owner once. A firing that
creates a task resets the count. For an agent routine, finished means the sponsor
accepted, so the rule also covers a rejection, a send-back in progress and a turn that
filed nothing. It is a local guard, and the cut "Attention budget" row stays cut. Revisit
the constant when a real routine pauses and its owner disagrees.

### D6. The schedule is weekly

Days, a time, every 1 to 4 weeks, from a start date. "Every other Wednesday" is
`weekdays='3', every_weeks=2`, with parity from the ISO week of `starts_on`. APScheduler's
`CronTrigger` cannot express week parity, so a cron string adds nothing. Hermes also keeps
raw cron away from users (`cron/blueprint_catalog.py` line 5). Monthly waits for its
trigger.

### D7. The tier is fixed at creation, and tasks inherit it

Visibility is not editable (the calendar's D8). An agent routine is never private:
`delegate_task` refuses a private task, and a CHECK refuses the routine first.

### D8. Only the owner edits, resumes or deletes, and any reader pauses

A firing writes under the owner's name, so an edit by someone else puts that name on
words the owner did not write. This departs on purpose from `scope.assert_editable`
("Any reader of a row may change it"). A pause writes nothing under anyone's name, and
resume undoes it. Once the owner is deactivated, any reader can also delete. Create,
edit, resume and delete take `StrongUser`: a routine writes under a name for months, and a
typed trusted-header name must not create standing work for someone else. List and pause
take `CurrentUser`.

### D9. A new routine starts active

The form states the schedule in words above Save, and saving is the consent. OpenClaw
found that a disabled job is invisible to every guard and never explains itself
(`~/external/openclaw/docs/automation/cron-jobs/how-it-works.md` 79-90).

### D10. No agent write path, and `WAKE_TOOLS` stay as they are

An agent that schedules its own wakes is the loop that `docs/AGENT-WAKEUPS.md` "Scope"
and `delegate_task` ("Agent-origin writes must not create an unattended fan-out chain")
refuse. Agent routines draft in the worklog, raise blockers and submit. The sponsor files
a decision supersede or an intake score by hand, because neither is a wake tool. Widening
waits for a routine that shows which tool is missing.

### D11. Routine acceptances are counted apart, and they build no streak

**What counts.** A settled proposal is a routine verdict when its entity is
`task_completion` and its task carries `routine_id`. That is the one verdict a firing
produces by construction, and the one a routine repeats every week. A blocker raised in a
routine turn, or an edit proposed later on a routine-made task, is judged on its own
content and stays in the hand counts. One fragment in `delegation.py`, read by
`review.py` too, so the surfaces cannot drift:

```python
# A routine repeats one piece of work: counted as hand work, its weekly
# acceptances read as many judgments (docs/intent/routines.md D11).
# review.season_readout splits on this same join.
ROUTINE_JOIN = "LEFT JOIN tasks rt ON p.entity = 'task_completion' AND rt.id = p.entity_id"
```

**No foreign key.** With `ON DELETE SET NULL`, deleting a routine moves a season of its
acceptances back into the hand counts. Without one, the id stays on its tasks. A
delegated task is never deleted: only `my_data.delete_private` and erasure delete task
rows, both touch private rows only, and `delegate_task` refuses a private task.

**`delegation.trust_scores`.** The aggregate joins `ROUTINE_JOIN`. The existing
`proposed`, `approved` and `rejected` count hand verdicts only (`FILTER (WHERE
rt.routine_id IS NULL)`), so `approval_rate` does too. Two fields are added,
`routine_approved` and `routine_rejected`. The only consumer is `/agents` (slice 4).
`trust_blocked` does not change: routine verdicts are real verdicts, and "none used strong
identity" stays true of them.

**The streaks.** Routine verdicts count toward neither streak. The `recent` query in
`trust_scores`, the source of `recent_streak`, `rejection_streak` and
`last_verified_verdict`, adds `ROUTINE_JOIN` and `AND rt.routine_id IS NULL`.
`review_authority` and the Approvals card (`review._trust_by_pair`) read those fields.

- A streak says a person judged N separate pieces of work in a row. Five weekly
  acceptances of one sweep are one piece of work judged five times.
- No filed proposal changes today: `task_completion` is in `NO_AUTHORITY`, so
  `promotion_blocked` refuses it and it never holds the level a demotion needs. The shown
  streak changes, and the rule holds if `NO_AUTHORITY` ever changes.
- A routine rejection loses no brake: the task stays open, the next time skips, three
  skips pause the routine (D5), and `routine_rejected` and the readout still show it.

**`review.season_readout`** (slice 4). The existing `verdicts`, `proposals`,
`delegations` and `by_agent` counts become hand-only. A new block carries the rest:
`"routine": {"verdicts": {"settled", "approved", "rejected"}, "proposals": n,
"delegations": {"started", "accepted"}}`, and each `by_agent` row gains `routine`.
Verdicts and proposals split on `ROUTINE_JOIN`, `accepted` on `tasks.routine_id`.
`started` counts `delegate_task` ledger rows, which have no task column, so it joins on the
id the detail starts with:

```sql
SELECT COUNT(*) FILTER (WHERE t.routine_id IS NULL) AS hand,
       COUNT(*) FILTER (WHERE t.routine_id IS NOT NULL) AS routine
FROM activity a
LEFT JOIN tasks t ON t.id = substring(a.detail FROM '^#([0-9]+) ')::bigint
WHERE a.action = 'delegate_task' AND a.created_at >= ?
```

Ledger rows never change, and hand plus routine equals today's total. The
`delegate_task` log line in `delegation.py` gains a comment that names this reader.
`review_stats` stays whole: it measures the review queue's flow, and a routine proposal
costs a reviewer the same click.

**The UI** (slice 4). The season card's verdict and delegation lines become hand-only, and
the red zero (settled verdicts, none strong) reads the hand counts. When a routine count
is above zero, one line follows: "From routines, counted separately: 12 acceptances (11
approved · 1 rejected) · 12 delegations started · 11 accepted." A `by_agent` line appends
"· 12 from routines". A trust row appends "· routines, counted separately: 11 approved, 1
rejected", and with hand `proposed` 0 it opens "no verdicts outside routines" instead of
"0/0 approved". Each string carries a number, so none is warm, and each computes plurals.

### D12. Plan the week holds routines

Plan the week is where the team sets up the week, and routines are the work it brings
back. A Work → Routines page adds a navigation entry. Team → Agents fits agent routines only.

### D13. Routines make tasks, never events

The cut row "Recurring calendar events (RRULE)" stays, with its trigger (a team creates
the same weekly meeting by hand more than once a month). A routine does not replace it: a
meeting has attendees, a span and an ICS entry, and a routine makes a task. The calendar
already draws each open routine-made task on its due date. A routine with no due days
makes tasks it does not draw, and an agent routine's task (assigned to the agent) shows
only with "Only my tasks" cleared. `calendar_range` selects no link column, so
`routine_id` needs no redaction there. Future firings are not drawn (a cut row).

## Slices

Each slice is one commit, or a short series, and passes `./scripts/lint.sh` and the full
suites. Each new test fails against the code before the change, with fixtures from a
running instance (CLAUDE.md conventions): every fixture below comes from `create_routine`
and `fire_due`, never from an UPDATE of `routine_id`. That is why `fire_due` lands in
slice 1, where the owner placed the trust split: it needs a code path that emits the column.

### Slice 1 — Schema, service core, one firing, and the trust counts

Migration `0NN_routines.sql`, the next free number when the branch merges (044 is the
calendar's). After the first production deploy it keeps its name for good. No semicolon
inside a comment (`docs/VISIBILITY.md`, "Schema"). Text and bigint, as `001_baseline.sql`.

```sql
CREATE TABLE routines (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title text NOT NULL, description text NOT NULL DEFAULT '',
    priority text NOT NULL DEFAULT 'medium' CHECK (priority IN ('low', 'medium', 'high', 'urgent')),
    assignee text NOT NULL DEFAULT '', agent text NOT NULL DEFAULT '',
    acceptance_criteria text NOT NULL DEFAULT '',
    weekdays text NOT NULL CHECK (weekdays ~ '^[1-7](,[1-7]){0,6}$'),
    at_time text NOT NULL CHECK (at_time ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'),
    every_weeks bigint NOT NULL DEFAULT 1 CHECK (every_weeks BETWEEN 1 AND 4),
    starts_on text NOT NULL, due_days bigint CHECK (due_days BETWEEN 0 AND 27),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused')),
    paused_reason text NOT NULL DEFAULT '' CHECK (paused_reason IN ('', 'by_person',
        'nobody_finished', 'owner_inactive', 'owner_left_crew', 'agent_unavailable',
        'fire_refused')),
    paused_by text NOT NULL DEFAULT '', next_at text,
    skipped_in_row bigint NOT NULL DEFAULT 0,
    last_outcome text NOT NULL DEFAULT ''
        CHECK (last_outcome IN ('', 'fired', 'late', 'previous_open')),
    last_outcome_at text, origin text NOT NULL DEFAULT 'human',
    created_by text NOT NULL, created_at text NOT NULL, updated_at text NOT NULL,
    visibility text NOT NULL DEFAULT 'workspace'
        CHECK (visibility IN ('private', 'crew', 'workspace')),
    crew_id bigint REFERENCES crews(id),
    CHECK (agent = '' OR assignee = ''),
    CHECK (agent = '' OR visibility <> 'private'),
    CHECK ((status = 'active') = (next_at IS NOT NULL))
);
CREATE INDEX idx_routines_due ON routines (next_at) WHERE status = 'active';
-- no foreign key: a deleted routine's tasks keep the id, so their
-- acceptances stay counted apart (docs/intent/routines.md D11)
ALTER TABLE tasks ADD COLUMN routine_id bigint;
CREATE INDEX idx_tasks_routine ON tasks (routine_id, id DESC) WHERE routine_id IS NOT NULL;
```

Every reader of the routine sees `paused_reason` and `last_outcome`, so their CHECKs make
the `agent_wakeups._SAFE_OUTCOMES` rule structural: no exception text.

Service `backend/app/services/routines.py`:

```python
AUTO_PAUSE_AFTER = 3
ON_TIME = timedelta(minutes=10)   # two tick periods, D4
MAX_ACTIVE_PER_OWNER = 25
DUE_BATCH = 200                   # slice 2

def next_occurrence(r: dict, after: datetime) -> datetime   # UTC, strictly after `after`
def create_routine(fields: dict, *, actor: str) -> dict
def update_routine(routine_id: int, fields: dict, *, actor: str) -> dict   # owner only
def pause_routine(routine_id: int, *, actor: str) -> dict                  # any reader
def resume_routine(routine_id: int, *, actor: str) -> dict                 # owner only
def delete_routine(routine_id: int, *, actor: str) -> dict                 # owner, or any reader once the owner is inactive
def list_routines(viewer: scope.Viewer) -> list[dict]
def fire_due(routine_id: int, now: datetime) -> str                        # one routine, one transaction
def pause_for_identity(name: str, kind: str) -> int                        # from users.set_active
```

The constants are not settings: they fail tier question 4 of CLAUDE.md, because no admin
has a standing reason to change them between deploys.

- `create_routine` runs the checks `create_task` runs (`work.validate_task_fields`, and
  `scope.assert_readable_by` for the assignee), so a firing does not fail on a value the
  form accepted. The title cap is `work.TITLE_LEN - 13`, so " (YYYY-MM-DD)" fits. An agent
  must exist, be active and pass `users.is_delegatable_agent_identity`. Minting stays on
  `POST /api/tasks/{id}/delegate`. The ledger row is `scope.detail(tier, "#7", title)`.
- `next_occurrence` walks at most `7 * every_weeks + 7` local days from `max(local date
  of after, starts_on)`. It keeps a day when the ISO weekday is in `weekdays` and `((day -
  monday_of(starts_on)).days // 7) % every_weeks == 0`, and returns the first
  `datetime.combine(day, at_time, tzinfo=config.TZ)` later than `after`, in UTC. In a DST
  gap, fold 0 moves 02:30 to 03:30. In an overlap it takes the first 01:30.
- `fire_due(id, now)` is one transaction:
  1. Read `created_by` and `agent` unlocked, take `db.name_lock(db.LOCK_IDENTITY,
     fold(n))` for each, sorted (D3), then `SELECT ... FOR UPDATE` the row. Return if it
     is gone or paused, if `next_at > now`, or if a rename changed a name meanwhile.
  2. Walk from `next_at`: each occurrence at or before `now` is missed, the last is
     `latest`, and the first after `now` is the new `next_at`.
  3. At the crew tier, if `crews.assert_writable(crew_id, owner)` raises, pause with
     `owner_left_crew`. No guard checks an inactive owner or agent: `set_active` pauses
     those routines under the same identity lock, so a firing that waited reads a paused row.
  4. If the newest task for this routine is open: `previous_open`, one skip, and a pause
     at the limit (D5). A task that closes or reopens during this read only decides
     whether one firing skips, and both results are safe.
  5. Otherwise, in `db.savepoint()`: `work.create_task(title + " (<latest local date>)",
     ..., due_date=<latest + due_days>, actor=owner, origin="human", visibility, crew_id)`,
     `UPDATE tasks SET routine_id`, and with an agent `delegation.delegate_task(tid,
     agent, owner, acceptance_criteria=..., actor=owner, origin="human")`, which queues
     the wake. A `ValueError` or `db.NotFound` rolls back the savepoint and pauses with
     `fire_refused`, or every tick repeats the refusal.
  6. Save `next_at`, `last_outcome` (`fired`, or `late` past `ON_TIME`) and
     `skipped_in_row = 0`. Log `fire_routine` or `skip_routine` by `scheduler`, ids and
     codes only ("routine #7 -> task #412, late, 3 times in 1"). If more than one time
     collapsed, notify the owner.
- `users.set_active(name, False)` calls `pause_for_identity` in its transaction, after its
  `LOCK_IDENTITY`. A human's routines pause with `owner_inactive` and notify nobody.
  Routines that delegate to a deactivated agent pause with `agent_unavailable` and notify
  their owners. Reactivation resumes nothing. Resume repeats the step 3 check, resets
  `skipped_in_row`, and computes `next_at` from now.

Notifications go to the owner only (`digest` tier, plain text, link
`/planning#planning-routines`). The owner can always read their own routine, so
`policy_context._TABLES` needs no entry. Each asks for action, so each follows STE. All
start "Skein paused routine #7 '{title}'" except the second and third:

- Auto-pause: "... Skein skipped the last 3 times because task #412 is still open. Finish
  or close task #412, then resume the routine."
- Collapsed times: "Routine #7 '{title}' missed 3 times because Skein was not running.
  Skein created one task for the latest time, 2026-10-19 at 07:00: task #415."
- Paused by someone else: "{actor} paused your routine #7 '{title}'."
- `agent_unavailable`: "... because agent {agent} is deactivated. Pick another agent, then
  resume the routine." `fire_refused`: "... because it could not create or delegate the
  task. Check the agent and the crew, then resume the routine." `owner_left_crew`: "...
  because you are no longer in its crew. Delete the routine, or ask the crew to add you."

`delegate_task` already tells the sponsor about each firing, so the tick adds no "routine
fired" notice. A `previous_open` skip tells nobody, and no message names who did not
finish (the anti-surveillance rule).

**Redaction of `routine_id`.** `SELECT t.*` carries the column into every task read once
it exists, so this cannot wait for the routes. It copies `event_id`: a
`visible_routine_id` join in `work.get_task` and `work._task_rows`, the null in
`_redact_hidden_task_links`, and `visible_ids("routines", ...)` in
`redact_task_relationships`, which the admin export and Your data already call. The
reason is `sharing.share_with_team`, which can widen a task past its routine. Unlike a
meeting in `sharing._PARENTS`, a narrower routine does not refuse the share: the task is
the unit people share, and the routine is only its template.

**Trust counts:** `ROUTINE_JOIN` and the `trust_scores` changes of D11.

| Registry | Change |
|---|---|
| `scope.CLASSIFIED`, `scope.NOUN` (`tests/test_scope.py`) | `"routines": "created_by"`, `"routine"` |
| `erasure._ORDER` (`tests/test_erasure.py`) | `"routines"`. Private routines are erased after `GRACE_DAYS`. Shared ones stay paused, with the name, as the module docstring requires. |
| `my_data.LABELS` (`tests/test_my_data.py`) | `"routines": "title"`. No `_POLICY_ENTITY`: no project, like a standup. |
| `users._ATTRIBUTION` | `("created_by", "assignee", "agent", "paused_by")` |
| `admin.TABLES` | `"routines"` |
| `retention.KEPT` (`tests/test_retention.py`) | `"routines"` under `_USER_DELETED`: removal is a person's decision, never an age prune |
| `activity.VERBS` | `create_routine`, `update_routine`, `pause_routine` and `resume_routine` normal. `delete_routine` loud, as `delete_note`. `fire_routine` and `skip_routine` quiet. |
| `tests/test_visibility_authz.py` | `_mutations` for update, pause, resume and delete by id. `_UNFILTERED_READS` for `routines.py::fire_due` and `routines.py::pause_for_identity` under "jobs that carry their own rule": they act as each owner, like `blockers.py::_sweep_escalations_locked`. |
| Not touched | `_gate._FAMILY`, `review._registry` and `_TARGET_TABLE`, `lexicon`, `policy_context._TABLES`, `tests/test_gate_coverage.py`: no agent write path and no tool. Routines are not indexed for search. Their tasks are. |

Tests that fail first, in `backend/tests/test_routines.py`:

- Deactivating the owner pauses with `owner_inactive`. Deactivating the agent pauses with
  `agent_unavailable` and notifies the owner once.
- `next_occurrence`: weekly, and every 2 weeks from `starts_on` (the week before the
  anchor gives nothing). With `SKEIN_TZ=America/New_York`, 02:30 on 2026-03-08 gives 07:30
  UTC (03:30 EDT), and 01:30 on 2026-11-01 gives 05:30 UTC, the first 01:30.
- DST and restarts: `fire_due` at 05:30 UTC on 2026-11-01 creates the task, and at 06:30
  UTC (the second 01:30) creates nothing. A time a year old fires once, late, dated with
  that time. Three missed weekly times give one task dated with the latest, one notice
  naming 3 times, and a future `next_at`. A routine paused for three weeks, then resumed,
  creates nothing and gets the next future time.
- Two threads call `fire_due` on one due routine, and exactly one task results.
- An agent routine gives a task with `sponsor` = the owner, `origin` = human, the date in
  its title and `routine_id`, plus a pending wake with `requested_by` = the owner.
- An open previous task gives `previous_open`, and the third skip pauses and notifies
  once. An owner who left the crew gives `owner_left_crew`. A refused delegation gives
  `fire_refused` and no task.
- A non-owner update raises `PermissionError`, a non-reader gets the absent text byte for
  byte, and an agent routine at the private tier is refused.
- A crew routine's task, shared to the workspace, reads `routine_id: null` for a
  non-member (as `test_event_links.py::test_a_task_hides_a_meeting_its_reader_left`).

In `backend/tests/test_routine_counts.py`: a routine acceptance and a hand acceptance by
one agent give `trust_scores` `proposed` 1 and `routine_approved` 1 (today `proposed` is
2). Five strong routine approvals give `recent_streak` 0. Deleting the routine keeps its
acceptance in `routine_approved`.

### Slice 2 — The tick

- `routines.tick(now=None)` selects due ids (`status = 'active' AND next_at <= now ORDER
  BY next_at LIMIT DUE_BATCH`) and calls `fire_due` for each. A failure is logged and
  counted, and the others continue (the `erasure.erase_due` shape). It returns counts only
  (`fired`, `late`, `skipped`, `paused`, and `status: "partial"` after a failure):
  `jobs._outcome_detail` copies the result into `job_outcomes`, which has no tier.
- `JOBS` gains `JobSpec("routines", _routines, {"trigger": "cron", "minute": "*/5"}, 1 /
  12, True, retry_safe=True)`. Cron, so a 07:00 routine fires at 07:00 on the wall clock.
  Retry-safe, because each firing commits its task, wake and `next_at` together.
  `catch_up=True` is D4's restart case. `SKEIN_SCHEDULER=0` (the restore boot) fires
  nothing. `retention.prune` bounds the 288 `job_outcomes` rows a day. The operator pause
  switch (`agent_automation`) holds the wakes pending, and the tasks still appear.
- No APScheduler job per routine: an in-memory schedule runs once per process and is
  rebuilt at each boot, against `docs/intent/work-durability.md` (no scheduled job lost or
  run twice).
- `docs/VISIBILITY.md`, "Derived artifacts read the workspace tier only", names the tick
  as the exception: it acts as each owner, writes rows at their source tier, and writes
  only counts to `job_outcomes`.

Tests that fail first: the tick fires every due routine, and one routine whose firing
raises leaves the others fired and the outcome `partial`. A startup catch-up
(`jobs.run_job` on the spec) with `next_at` 20 minutes past creates one task with
`last_outcome = 'late'`. After a tick, `job_outcomes.detail` holds no routine title.

### Slice 3 — REST

| Route | Caller | Cap | Notes |
|---|---|---|---|
| `GET /api/routines` | `CurrentUser` | none | `{"zone": config.TZ_NAME, "routines": [...]}` under `scope.visible_filter`. Rows add `next_local` and `last_local` (`db.local_wall`), `open_task_id` and `can_edit`. The browser never converts a zone (calendar D6). A task is never narrower than its routine, so any reader can open `open_task_id`. |
| `POST /api/routines` | `StrongUser` | `write` | 201. Calls `decide(..., "skein.rest.post.routines", "routine", ...)` like `skein.rest.post.tasks`, so a workplace policy can refuse a standing delegation when it is created. |
| `PATCH /api/routines/{id}` | `StrongUser` | `write` | Owner only. No `visibility` or `crew_id`. A schedule edit recomputes `next_at` from now. |
| `POST /api/routines/{id}/pause` | `CurrentUser` | `write` | Any reader. Notifies the owner when someone else pauses it. |
| `POST /api/routines/{id}/resume` | `StrongUser` | `write` | Owner only. |
| `DELETE /api/routines/{id}` | `StrongUser` | `delete` | Owner, or any reader once the owner is inactive. Tasks stay and keep `routine_id`. |

A hidden id and an absent id both get `scope.missing("routines", id)`: a 404 with
identical text. A reader who is not the owner gets `PermissionError` (403): "Only
{owner} can change routine #{id}. Pause it, or ask {owner}." A bad field is a 400 and
never echoes the value. `RoutinePatch` caps match `RoutineIn`
(`tests/test_patch_cap_parity.py`). `GET /api/tasks/{id}` carries `routine_id` from
slice 1.

No agent tool (D10), MCP tool or CLI command. The firing is not an agent write, so no gate
applies. The delegated agent's own writes pass the gate, the matrix, the wake caps and
sponsor acceptance, as after a hand delegation.

Tests that fail first: `test_bounded_routes.py` and `test_route_identity.py` until the
caps and callers are in, and `test_policy_axis.py::test_every_bare_route_literal_is_accounted_for`
until `routines` joins `no_project_literals` (no route reads a project row). A
trusted-header caller cannot create a routine. A non-owner PATCH gets the sentence above,
a hidden id the absent text. A denying policy refuses `POST /api/routines`.

### Slice 4 — UI, the season readout split, the knot, docs. The feature ships here

`frontend/components/routines-card.tsx` mounts in `frontend/app/planning/page.tsx` as
`SupportingSection id="planning-routines" title="Routines"`, after the "This week"
agenda section and before "The weeks ahead".

- **List:** the title, the schedule in words from one helper ("Every 2 weeks on Wednesday
  at 10:00"), "Next: Mon 5 Oct, 07:00" from `next_local` (the zone once, in the section
  intro), `→ quartermaster` or `@assignee`, the open task as a `?task=ID` peek link, the
  last outcome ("Skipped Mon 5 Oct: task #412 is still open."), the paused line ("Paused
  because {owner} is deactivated."), and Pause/Resume, Edit and Delete as `can_edit` allows.
- **Form, native controls:** title, description, days as a `<fieldset>` of seven
  checkboxes, `<input type="time">` ("Skein creates the task within 5 minutes of this
  time."), a repeat `<select>` (every 1 to 4 weeks), starts on `<input type="date">`,
  assignee via `components/person-input.tsx`, an agent `<select>` from the `delegatable`
  rows of `GET /api/agents` (as the task peek's Delegate control), acceptance criteria once
  an agent is picked, due in days (`min=0 max=27`), and visibility, where a picked agent
  disables Private and says why. A line above Save states the schedule in words, from the
  list's helper. Save announces through `lib/status.ts::reportStatus(..., "confirmation")`
  and moves focus to the new row. Delete asks first and returns focus to the heading.
- **Empty state** (idle, so warmth is permitted): "No routines yet. Write the work once,
  and the week brings it back."
- **Task peek:** beside the `source_finding` line in `components/task-peek.tsx`: "Repeats
  from routine #7. Change it in Plan the week.", linked to `/planning#planning-routines`.
  The peek is not a second editor.
- **Season readout and trust card:** the D11 split in `review.season_readout`, whose
  docstring then says the exit trigger reads hand work, and the D11 lines in
  `frontend/app/agents/page.tsx` (the `SeasonReadout` and `Trust` types gain the fields).

Field-guide card in `backend/fieldguide/knots.yaml`: id `routine`, feature Routines, knot
**Daisy Chain** (no card uses it, and Clove Hitch is the calendar's), set `loops`, link
`/planning#planning-routines` (which also puts it in the page help of `/planning`),
`since:` the ship date. Predicate `"routine": lambda u: _act(u, "create_routine")`.

- pitch: "One loop, tied again and again down the line. Write the work once, and each
  week ties the next loop."
- how: "Open Work → Plan the week → Routines. Write the task, pick the days and the time,
  and pick an agent if one does the work. Skein creates a new task each time. If the last
  task is still open, Skein skips that time."

Tests that fail first:

- `test_routine_counts.py`: one hand delegation and one routine firing, each accepted,
  give 1 and 1 for settled verdicts, delegations started and accepted. Today the hand
  counts are 2 and `routine` is absent. It also fails if the `delegate_task` detail changes.
- `test_fieldguide.py`: 69 → 70 cards, and a tieable total of 68 → 69.
- Vitest `routines-card.test.tsx`: the checkboxes give `weekdays: "1,4"`, an agent disables
  Private, a `nobody_finished` row renders its sentence and task link, the helper says
  "Every 2 weeks on Wednesday at 10:00", and the peek line needs `routine_id`.
- Vitest `agents-season-readout.test.tsx`: the routine line renders only above zero, the
  red zero reads the hand counts, and a trust row renders its routine clause.
- `seed.py` adds the on-call checklist, paused, so a demo instance creates no tasks. The
  Playwright smoke (axe) and responsive specs already visit `/planning`.

Docs in the ship commit:

- `docs/FEATURES.md`: a **Routines** row after **Delegation** (`services/routines.py` ·
  job `routines` · `/api/routines` · Plan the week). The Scheduler row gains "every 5 min
  routines", **Delegation** "a firing is a delegation by standing consent", **Task side
  peek** the routine line, **Offboarding erase** the routine pause. **Trust scores** and
  **Season readout** say that routine acceptances are counted apart and build no streak.
- `docs/AGENT-WAKEUPS.md` "Scope": a routine firing is a human delegation (`requested_by`
  = the owner). The non-goals stay true: a routine never re-wakes unfinished work.
- `CHANGELOG.md` `## Unreleased`: one entry per slice.
- `docs/ROADMAP.md`: delete the **Routines** bullet under "From the external-repo
  discovery", and `routines` from its list of designs. Add these cut rows:

| Cut | Trigger |
|---|---|
| Monthly routines | A person asks for a month-end routine. Add `every_months`, and the tick does not change. |
| Assignee rotation | Someone edits a rota routine by hand more than twice a month. |
| "Run now" | People delegate a copy by hand to test a routine. |
| An agent write path for routines (a routine proposal, chat "repeat this") | A person asks the Chief of Staff for recurring work and it cannot help. It needs a new gated entity in all seven registries. |
| A read tool, MCP tool or CLI command for routines | A second surface asks what repeats. |
| Take over a departed owner's routine | A paused shared routine is recreated by hand after an offboarding. |
| Wider `WAKE_TOOLS` for routine turns (`supersede_decision`, intake scoring) | Sponsors copy sweep drafts into records by hand for a month. |
| Future routine firings on the calendar | Someone asks the calendar when a routine runs next. |
| Routine recipes: `backend/routines/*.yaml`, a `SKEIN_ROUTINES_DIR` overlay (tier question 1), a validator in `scripts/lint.sh`, "Start from a recipe" filling the form with a copy | A second person writes an agent routine by hand. |
| Playbook routines: `routines:` beside `rituals:` in `backend/playbooks/*.yaml`, a `routines.engagement_id`, created paused with a reason (hermes `cron/job_definition.py` 48-62), paused when the engagement closes, each firing through the policy engine | Two engagements of one class repeat the same weekly task. |

## Residual risks accepted

- **Weekly approval noise.** A sweep that finds nothing still leaves an open task, which
  the agent submits as "nothing due": one sponsor click a week, kept out of the hand counts.
- **Policy at fire time.** `decide` runs at creation only. First-version routines have no
  project, so project-type policies (`deny_regulated_delegation` in
  `tests/test_extension_policy.py`) have nothing to bind. Playbook routines must send each
  firing through the policy engine before routines gain an engagement.
- **A long outage fires every active routine at the next boot,** one task and at most one
  wake each, bounded by `MAX_ACTIVE_PER_OWNER`, the one-open rule, per-agent coalescing
  and `SKEIN_AGENT_WAKES_PER_DAY`. The owner chose this over a silent skip.
- **Routine turns share the daily wake cap** with hand delegations. A morning of routines
  can spend it, and the refusal shows as `wake_cap` in the task peek.
- **A `SKEIN_TZ` change** leaves each stored `next_at` in the old zone until it fires once.
- **An `@name` in a routine** pings on every firing (`mentions.scan` in `create_task`).
- **The `started` split reads the ledger detail.** The rows never change, and the slice 4
  test fails if the format of new rows changes.

## Open questions for the owner

The slices proceed on each recommended default unless the owner chooses otherwise.

1. **Routine verdicts build no streak, in either direction?** Recommended: yes (D11). The
   alternative counts routine rejections toward demotion. That changes nothing today
   (`task_completion` holds no level), and D5 already pauses a routine that keeps failing.
2. **Only acceptances are routine verdicts?** Recommended: yes (D11). The alternative also
   counts every proposal filed in a routine's wake turn. That needs a wake id on each
   proposal, and it moves genuine blocker verdicts out of the hand counts.
