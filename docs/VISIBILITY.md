# Visibility tiers and crews — design

> This file holds the design for a three-tier visibility model
> (`private` / `crew` / `workspace`) and the crew membership it needs.
> Phases 0 to 6 have shipped. Phase 6 covers per-crew context packs only.
> `docs/ROADMAP.md` holds the rest of the backlog.

Three decisions are settled and the rest of this file depends on them:

1. **The open tier means "every roster member of this deployment"**, not
   "reachable from the internet". Internet-facing stakeholder pages stay
   refused (`docs/ROADMAP.md`, `docs/reviews/2026-07-24-panel.md`) — if
   they ever land, they land as a push-generated static artifact on a
   separate host, not as a fourth enum value. The tier is named
   `workspace` so no reader mistakes it for public.
2. **The people-grouping entity is a crew.** `team` is taken five times
   over: a `SYSTEM_ACTORS` entry (`services/activity.py::SYSTEM_ACTORS`), the
   notifications broadcast address with shared-read semantics
   (`services/notifications.py::list_notifications`), a `promises.audience` value, a
   `resolve_teammate` passthrough (`services/users.py::resolve_teammate`), and
   `app_settings['team_theme']`.
3. **A non-workspace row is read only by a `StrongUser`.** An API key or a
   validated OIDC sign-in. This matches `routes/private.py` and degrades
   honestly: in trusted-header mode a person can create a private row but
   reads nothing scoped, including their own. The alternative ships a
   privacy claim that one rewritten `X-User` header defeats.

## The constraints this design answers to

**There is no chokepoint.** 382 hand-written SELECT statements across 50
service files. `db.py` is a transport — it never inspects SQL and has no
notion of a caller. About 95 read functions take no viewer
(`work.list_tasks`, `collab.search_notes`, `portfolio.engagement_health`,
`search.search`). Each one is a signature change that reaches both write
paths. The single precedent is `activity.visible_actor_filter(viewer)`
(`services/activity.py::visible_actor_filter`) — eight lines, three callers.

**The repository already refused the column approach once.**
`services/private_notes.py`'s module docstring: *"Exclusion is structural (no code path
touches the file), not a filter every query must remember."*
`docs/reviews/2026-07-24-panel.md:93-98` ruled that a visibility column
cannot hold the author-private journal, because backup, export, and
in-process MCP are file-level paths that no column check stops. A
`private` column that reuses the word without the guarantee is a
regression under the same name.

**45 of the 76 GET endpoints resolved no caller at all** before phase 0,
and in trusted-header mode with no `SKEIN_API_TOKEN` the perimeter returns
before any check (`app/main.py`). A filter cannot attach to a request that
never names a person. Phase 0 closed the first half of that. The second
half does not close: in trusted-header mode identity stays self-asserted,
which is why the enforcement bar below is `StrongUser`.

**No existing structure maps onto a crew.** `users` carries no role or
group column. An engagement is terminal (`status` reaches `closed`),
fractional (`allocations.percent`), and date-windowed — access built on
it expires the moment work ships. A flock is 2 to 4 AI personas. OIDC
group claims reach `routes/deps.py` and are then discarded by
`current_user` and `strong_user`, feeding one admin boolean
(`routes/deps.py::_is_admin`).

## The design

### Private is structural. Only crew is filtered.

| Tier | Who reads it | Mechanism |
|---|---|---|
| `private` | the author | Kept out of every sink: no FTS row, no embedding, no context pack, no digest, no readout, no finding, no ICS event, no body in `activity.detail`, no export row. |
| `crew` | crew members, and the author | Column-filtered at read time. |
| `workspace` | every roster member | Today's behavior. The migration default. |

This is the move that makes the work finite: only the crew tier is
retrofitted across the 382 queries.

**Be honest about what "structural" buys here.** `private_notes` earned the
word because it is a separate FILE that no other code path opens. A
`visibility` column earns less: `index_record` is called from 24 sites
across 9 services, and `admin.export` is `SELECT *` over 37 tables. Both
tiers fail OPEN when a predicate is forgotten. The difference is
reversibility — a forgotten `visible_filter` is a query you fix, while a
forgotten sink predicate has already written to the FTS index, the
immutable ledger, a `UNIQUE`-keyed findings row, a file on disk, and a
vector at a third party. That is the argument for doing the sinks as
phase 4 rather than alongside phase 3, and for `private` never becoming
the default.

`private.db` does not move. It is the strongest confidentiality the
product has, and the journal stays in it.

### Schema

Three migrations: `003_crews.sql` (the crews tables), `004_visibility_tier.sql`
(the columns on all 16 content tables), and `005_crew_context_packs.sql`
(per-crew packs). Append-only, DDL plus non-`activity` backfill, no triggers,
and no semicolon **and no apostrophe** inside a comment — `db.py::_statements`
splits on `;` with no string or comment awareness, and sqlite3 reads a lone
apostrophe as an unterminated string literal. Migration 005 got this wrong in
the comment that forbids it and bricked `init_db` until it was fixed.

```sql
CREATE TABLE crews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    summary TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    origin TEXT NOT NULL DEFAULT 'human',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
-- the engagements precedent (001_baseline.sql:538): the service pre-checks
-- and inserts in two steps, so without a NOCASE unique index two concurrent
-- creates of `Alpha` and `alpha` both land
CREATE UNIQUE INDEX ux_crews_name_nocase ON crews (name COLLATE NOCASE);

CREATE TABLE crew_members (
    crew_id INTEGER NOT NULL REFERENCES crews(id) ON DELETE CASCADE,
    person TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('member', 'steward')),
    origin TEXT NOT NULL DEFAULT 'human',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (crew_id, person)
);

-- per content table
ALTER TABLE tasks ADD COLUMN visibility TEXT NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace'));
ALTER TABLE tasks ADD COLUMN crew_id INTEGER REFERENCES crews(id);
```

`crew_members.person` must join `users._ATTRIBUTION`
(`services/users.py::_ATTRIBUTION`). If it does not, a rename orphans every
membership row. That map carries a parity test which catches the
omission.

`crews.name` must take `refuse_reserved_name` (`services/users.py::refuse_reserved_name`)
so no crew is named `team`, `system`, `scheduler`, or `forge`.

### The mechanism that keeps it alive

A new `services/scope.py` holds the read filter, the write check, and the
inventory:

- `visible_filter(viewer, table, alias="")` returning a SQL fragment and
  its parameters, modeled on `activity.visible_actor_filter`. Positional
  `?` marks, because `db.query` takes a tuple. A viewer in NO crew is the
  common case and SQLite has no `IN ()`, so the crew disjunct is dropped
  rather than emitted empty.
- `Viewer(name, strong)`, built in `routes/deps.py` and nowhere else, plus
  `NOBODY` for every surface with no human behind it. The strength lives
  in the TYPE because the enforcement bar is otherwise a rule at ~95 call
  sites, and the one that forgets hands a rewritten header the private
  tier. The viewer resolves its crews once, not once per query: a
  dashboard fans out to about 27 scoped reads and `db.connect()` costs
  280 microseconds against a 2 microsecond SELECT.
- The filter takes the TABLE, never a column. Four tables carry both their
  real author column and a `created_by` holding the agent slug, so a
  column-taking signature let `notes` be filtered on `created_by` — which
  compiles, runs, and hides a private note from the person who wrote it.
- `CLASSIFIED`, a map naming every table as scoped or unscoped, with a
  written reason for each unscoped one — plus `NOUN`, what a reader calls one
  row of each (`table[:-1]` renders "memorie", and "intake_request" is an
  identifier, not a word).
- `resolve_write(visibility, crew_id, actor)` — the tier a write lands on,
  checked once so fourteen services do not each invent it. The membership
  check inside it belongs in the caller's own transaction.
- `assert_readable_by(tier, crew_id, person, label, author)` — refuse handing
  scoped work to somebody who cannot read it. `author` is the third disjunct
  and leaving it out refused the ordinary case: capture hardcodes
  `owner=actor`, so every private capture that classified as a blocker was
  refused, and every private standup with blockers text rolled back whole.
- `assert_editable(table, row, actor)` — every mutation finds its row by a
  caller-supplied id, so `UPDATE notes SET ... WHERE id = ?` matched a private
  note whoever asked. Any reader can edit. A machine actor can work a CREW row
  (it IS the mechanism — the forge webhook, `approve_change` applying as
  `proposed_by`, the delegation trio) but never a private one.
- `missing(table, row_id)` — ONE "no such row" sentence for both the absent row
  and the row the caller cannot read. Any wording only the scoped case
  produces answers "does #12 exist", and ids are sequential integers.
- `detail(tier, ident, body)` — what a scoped write can put in
  `activity.detail`. The chain is append-only, so a body written there is
  written for good.
- `WORKSPACE_ONLY` — what a JOB reads. Spliced as a literal because these are
  hand-written SQL strings with their own parameter tuples.

Then a test walks `sqlite_master` and fails on any table that is in
neither set. The repository does this three times already —
`admin.TABLES`/`admin.EXCLUDED`, `users._ATTRIBUTION`, and
`tests/test_gate_coverage.py::UNGATED_WRITERS`. Each exists because an
enumeration that CI does not check goes stale, and `_ATTRIBUTION` proved
the point during phase 1: its parity test checked only that declared
columns exist, so a new person column could be left out of it silently.
`tests/test_users.py::test_no_person_column_is_left_out_of_the_rename_map`
is the reverse direction that was missing.

`CLASSIFIED` maps a table to its AUTHOR COLUMN, not to a boolean. There
is no single author column across the schema — `notes` and `standups`
carry `("created_by", "author")`, `tasks` carries four, `blockers`
carries `("created_by", "owner")` — so a filter that emits one column
name for twenty tables cannot work.

### Refusals are 404

`app/main.py`'s NotFound handler already decided it: *"an owner-scoped miss is a 404 too,
because any other status confirms the row exists."* Raise `db.NotFound`
and the correct status arrives with no new handler. A 403 belongs only
where the surface is refused rather than a row — which is what
`_require_strong` (`routes/deps.py`) already does.

### Derived artifacts read the workspace tier only

The nightly jobs run as `scheduler` and have no viewer, so "who is this
digest for" has no answer. They read `workspace` and nothing else. This
covers `digest`, `readout`, `context-pack`, `findings`, `handoff`,
`week-open`, and `week-close`.

The ICS feed is the same case for a different reason: one shared token is
one audience, so a feed cannot be scoped. Workspace tier only. Per-person
feed tokens are the fix if the feed ever needs to carry more.

`findings` matters most here. `services/insights.py::run_findings` writes rule
messages and JSON receipts that carry task titles, blocker titles, promise
text, and question text into a table with no identity column and a
`UNIQUE (rule_id, subject, week)` key. Rules that read a scoped row would
republish its content permanently. Rules read workspace only.

### Activity keeps no visibility column

`ALTER TABLE activity ADD COLUMN` is legal — it matches no pattern in
`tests/test_migrations.py::REWRITES_ACTIVITY` and rewrites no row. It is also useless:
the column can never be backfilled, it cannot enter `activity_hash`
without invalidating every existing chain, and `activity` carries no
`entity_id` to join a row back to its subject.

The feed is already scoped by actor, which is a different and working
axis. The real exposure is `activity.detail`, which carries content —
`services/memory.py::forget` writes 200 characters of a deleted memory body
into the immutable ledger. **A write to a non-workspace row logs an
identifier, never a body.** Pin it with a test.

### The sinks, one by one

| Sink | Rule |
|---|---|
| `search_index` (FTS5) | Private rows are never indexed at all — `index_record` looks the tier up itself rather than trusting 20 call sites. The FTS table gains NO tier column (it cannot get one cheaply): `search()` over-fetches 4x, then `visible_hits` checks each hit's SOURCE row by primary key. |
| `search_ids` | `_short_id_hit` (`services/search.py::_short_id_hit`) resolves `task 42` straight to a row with no authorization. It takes the same filter. |
| `embeddings` | `_embed` sends `text[:8000]` to `EMBED_BASE_URL` inside every `index_record`. Private rows are never indexed, so they never reach it. |
| `memories` | Closed in phase 4. `recall()` applies BOTH the `user` filter and the tier on every branch (`services/memory.py::recall`) — the query branch used to apply neither, so one person's search answered out of another person's memories, and `memory_prompt` injects the result into a system prompt. |
| `notifications` | Every team-wide `notify("team", ...)` that quotes a scoped row's text is gated on the workspace tier (the blocker funeral, the stale-decision sweep, ship-it, the unlinked-milestone warning), and a per-person notify checks the recipient can read the row. `flush_digest_tier` posts COUNTS, never messages: it batches into ONE Slack channel, `notifications` carries no tier to filter on, and a count carries nothing whatever a future caller writes. The post is a nudge — the bodies are one click away in the app. |
| `admin.export` | Private rows are excluded structurally. Crew rows stay — a full dump is what the surface is for — but every new table takes its `admin.TABLES` classification. |
| `data/artifacts/` | A file on disk carries no column. Anything a job writes is workspace-tier by the rule above. |

### Frontend

There is no shared `Button`, `Badge`, `Select`, or `Modal` in
`frontend/components/`. A visibility picker is a new component; the
closest template is the authority-level select at
`app/agents/page.tsx`'s authority select, which already maps a raw enum to human
labels. Two enums already ship the same shape on the wire and render
today: `promises.audience` (`app/portfolio/page.tsx`) and
`Onboarding.steps[].scope` (`app/page.tsx:53`).

**Crew scope lives in the URL.** `lib/api.ts`'s GET cache caches GET responses for
15 seconds keyed on the path alone. That is correct today only because
every identity writer dispatches `storage` and clears it. An active-crew
switcher held in client state would return one crew's rows for another
crew's view, silently, for 15 seconds.

## Phases

| Phase | Work |
|---|---|
| 0 **(shipped)** | Give the 45 open GET endpoints a `CurrentUser`. Claim `thread_id` on `POST /api/chat`. Remove the free `user` parameter from MCP `get_my_day`. Make the nav sign-out clear rendered state. |
| 1 **(shipped)** | `crews` + `crew_members`, the service, and the Settings card. No visibility yet. |
| 2 **(shipped)** | `services/scope.py`, the classification inventory, and the parity tests. No behavior change. |
| 3 **(shipped)** | Columns on all 16 content tables. Every write path accepts a tier, and nine REST bodies expose one (milestone, task, decision, standup, note, event, blocker, capture, engagement). Children inherit (blockers from a standup, task_worklog from a task, the ship-it note and experiment lesson from an engagement, an engagement from an accepted intake request). Viewer threaded through the reads. Picker and badge in the UI. The `StrongUser` bar. |
| 4 **(shipped)** | The sinks: FTS (search.index_record looks the tier up itself rather than trusting 20 call sites), admin export, and `activity.detail` via scope.detail. `private` became writable here. |
| 5 **(shipped, before 3c)** | Jobs and egress read `WORKSPACE_ONLY`: digest, readout, handoff, context pack, the findings rules, and the team-wide block of My Day. Moved AHEAD of the picker — a crew task would otherwise have gone straight into the daily digest, which is the same control-that-does-not-hold problem `private` was sequenced around. |
| 6 **(shipped, packs only)** | Per-crew context packs: `005_crew_context_packs.sql`, `build_pack(crew_id)` appending a crew section to the shared body, per-crew version counters, `GET /api/context-pack?crew=`. Per-crew digests and insights are deliberately NOT built. A digest is one morning page for one team — N of them is a different product decision, not a parameter, and the crew pack already answers "what is my crew working on" on demand. A findings row is the most dangerous sink in the app: it quotes another table's text into a row with no identity column and a UNIQUE (rule_id, subject, week) key, and it is never pruned. Per crew, that needs the tier ON the finding, not a second run. Build either when somebody asks for it, not before. |

### Where the picker actually went

The plan named four create forms. Tasks and notes have no create form in this
UI — both are made through quick capture — so the picker went into the ⌘K
palette, which routes to seven entities, plus the standup card. That covers
the four the plan named and three more, from one control.

### What still lands at workspace, always

All sixteen tables can now carry a non-workspace tier. `lessons` and
`artifacts` are the two nobody sets by hand: a lesson inherits from the
experiment whose conclusion drafted it, and a handoff artifact inherits from
its engagement — which is what stops `list_artifacts` handing out a path to a
file of another crew's work.

Six of the sixteen have no create form in this UI at all (milestones, events,
absences, memories, engagements, lessons). Their REST bodies take a tier where
one makes sense; quick capture covers the other seven entities.

A comment claiming "this table carries no settable tier" was written four
times in this codebase and was false at every site by the time it shipped.
That class of rot is why `tests/test_visibility_authz.py` walks the AST for
every read of a scoped table and fails on one with no viewer, no
`WORKSPACE_ONLY`, and no written reason.

The hazardous sites, counted before any of them were touched: 8 reads with
GROUP BY (the fragment cannot go in HAVING), 6 with a LEFT JOIN where it must
go in the ON clause, 4 whose WHERE already has a top-level OR, the 8-way
UNION in `insights.automation_ratio`, 6 nested `milestones` subqueries, and 3
builders that can emit no WHERE at all.

`retention.prune` takes a written carve-out rather than a filter. Its
orphan-reaping `NOT IN` subqueries decide what to DELETE, so a filter there
does not hide rows — it deletes live ones.

Phase 0 did not depend on the rest and shipped on its own, and so did
phase 1. Phase 3 is the expensive one and does not
reduce: roughly 20 tables, 95 read functions, 110 endpoints, and a
frontend with no shared primitives to reuse.

Phase 6 did NOT need the 12-step rebuild this file predicted. `UNIQUE(version)`
was a standalone INDEX, not a table constraint, so `DROP INDEX` plus
`CREATE UNIQUE INDEX ... (IFNULL(crew_id, 0), version)` changes the key without
touching a row. `IFNULL`, not a bare `crew_id`: SQLite treats every NULL as
distinct in a unique index, so two team packs could share version 1.

## Two cross-user reads phase 0 closed

Both were independent of this feature, which is why they went first.

1. **Chat sessions carried no owner.** `routes/chat.py` handed the raw
   `thread_id` to `session_store.session_manager`, which keys on
   `session_id` alone with no ownership check.
   `chat_threads.log_message` refused to write the transcript into
   another owner's thread and returned silently, so the sidebar showed
   nothing — but the model had already answered out of the other person's
   history, and the stream carried it. The default thread id was the
   literal string `default`, so any client that omitted one joined a
   session shared across every user.

   Closed by `chat_threads.claim_thread`, a per-person default id, and a
   persona-session separator that sits outside the thread-id charset so a
   caller cannot type one.
2. **MCP `get_my_day` took any name.** It passed a model-controlled
   `user` argument to `briefing.my_day`, which returns that person's
   assigned questions, owned blockers, tasks, and unread notification
   bodies. The parameter is gone.

A third of the same shape was found by the phase-2 review and closed:
the chat tool `my_agent_inbox(agent="")` took a model-controlled name and
answered with that person's assigned questions, rejected proposals
including reviewer notes, and unread notification bodies.

Both were closed in phase 4. `GET /api/memories` passes the caller as `user=`
and a viewer; `recall` applies both on every branch. MCP `search_workspace`
passes NOBODY, so it reads the workspace tier only, and a private row was
never indexed to begin with.

A third, found in the phase 3-6 review and closed with them:
`GET /api/private/brief/{person}` took a free path parameter with no manager
relation behind it, and its six queries were unfiltered — so every strong
identity could read every other person's PRIVATE standup and promise rows in
full. It now filters on the READER, never on the subject.

## What phase 3 solved

- **The review queue is a mirror.** `pending_changes.payload` holds a copy
  of the proposed row and `GET /api/review` serves it to any
  `CurrentUser`. A rejected proposal is worse than an approved one: its
  summary is republished by `review_stats` and copied into
  `findings.receipt`, which is never pruned.
- **`review.approve_change` is a write path that does not look like one.**
  It splats the payload as kwargs straight into the service, bypassing
  `gated_write`. So `assert_writable` belongs in the SERVICE, not the
  route handler, or a proposal carrying another crew's id applies.
- **Parent rows copy text into child rows.** DONE. `scope.inherit` and
  explicit `visibility=`/`crew_id=` passing cover all of them:
  `collab.post_standup` into `raise_blocker`, `delegation.report_progress`
  into `task_worklog`, `collab.supersede_decision` into its successor,
  `intake._disposition` into `create_engagement`, and
  `engagements._ship_it` and `_experiment_lesson` into a note and a lesson.
  The last two were workspace children of a crew engagement until the
  phase 3-6 review found them.

## What this design does not do

- It does not make Skein multi-tenant. One deployment stays one roster.
- It does not move `private.db`. The journal keeps its file-level
  isolation.
- It does not scope the activity ledger by content.
- It does not make a crew an authorization boundary for administration.
  `AdminUser` stays deployment-wide. `crew_members.role = 'steward'`
  governs crew membership only.
- It does not give a row more than one crew. A row carries a single
  `crew_id`, and the cost is real: work that concerns two crews falls
  back to `workspace`. A join table would make `visible_filter` need the
  table name as well as the alias, and turn one indexed integer
  comparison into a correlated subquery in each of 382 hand-written
  SELECTs. Revisit only when a cross-crew row is the common case, not the
  exception.
- It does not let an agent manage crews. There is no `@tool` wrapper and
  no `services/review.py::_registry` entry, deliberately:
  `approve_change` has no per-entity authorization hook, so a registered
  membership entity would route every change around the steward check and
  let any reviewer approve a change to any crew.
