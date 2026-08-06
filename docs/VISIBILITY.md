# Visibility tiers and crews — design

> This file holds the design for a three-tier visibility model
> (`private` / `crew` / `workspace`) and the crew membership it needs.
> Phases 0, 1 and 2 have shipped. Phases 3 to 6 are not built.
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

A new `003_*.sql`. Append-only, DDL plus non-`activity` backfill, no
triggers, and no semicolon inside a comment — `db.py::_statements` splits
statements on `;` with no string or comment awareness.

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

A new `services/scope.py` holds two things:

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
  written reason for each unscoped one.

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
| `search_index` (FTS5) | Private rows are never indexed. Crew rows carry `visibility` and `crew_id` as UNINDEXED columns; `search()` over-fetches and filters. Rebuilding the virtual table drops five shadow tables — plan it as its own step. |
| `search_ids` | `_short_id_hit` (`services/search.py::_short_id_hit`) resolves `task 42` straight to a row with no authorization. It takes the same filter. |
| `embeddings` | `_embed` sends `text[:8000]` to `EMBED_BASE_URL` inside every `index_record`. Private rows are never indexed, so they never reach it. |
| `memories` | `recall()` has three branches with three scopes (`services/memory.py::recall`); the query branch drops the `user` column entirely, so `recall_memories` returns any person's memories into any turn. Fix this regardless of the tier work. |
| `notifications` | `flush_digest_tier` (`services/notifications.py:95`) posts every user's pending messages, name-labelled, into one Slack channel. Workspace tier only. This is a leak today. |
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
| 3 | Columns on the content tables. Write paths accept a tier. Viewer threading through the read functions. Picker and badge in the UI. The `StrongUser` bar. |
| 4 | The sinks: FTS, search, memory, notifications, `activity.detail`. |
| 5 | Jobs and egress locked to workspace. |
| 6 | Per-crew packs, digests, and insights. |

Phase 0 did not depend on the rest and shipped on its own, and so did
phase 1. Phase 3 is the expensive one and does not
reduce: roughly 20 tables, 95 read functions, 110 endpoints, and a
frontend with no shared primitives to reuse.

Phase 6 needs a table rebuild. `context_packs` is `UNIQUE(version)`, and
per-crew packs need `UNIQUE(crew_id, version)` — the 12-step SQLite
rebuild, which is why it sits last.

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

Two remain open and belong to phase 4: `GET /api/memories` and MCP
`search_workspace` both return every person's memories, because
`services/memory.py::recall` drops its `user` filter on the query branch
and every memory body is in the global FTS index.

## What phase 3 has to solve that phase 2 did not

- **The review queue is a mirror.** `pending_changes.payload` holds a copy
  of the proposed row and `GET /api/review` serves it to any
  `CurrentUser`. A rejected proposal is worse than an approved one: its
  summary is republished by `review_stats` and copied into
  `findings.receipt`, which is never pruned.
- **`review.approve_change` is a write path that does not look like one.**
  It splats the payload as kwargs straight into the service, bypassing
  `gated_write`. So `assert_writable` belongs in the SERVICE, not the
  route handler, or a proposal carrying another crew's id applies.
- **Parent rows copy text into child rows.** `collab.post_standup` lifts
  the `blockers` field into `raise_blocker`, a different table with its
  own default tier. The tier picker will sit on the standup form, and
  nothing on that form says one field escapes.

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
