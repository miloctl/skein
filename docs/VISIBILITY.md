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
`services/private_notes.py` keeps the author-private journal in a separate
schema. Portable export, search, MCP, and agents do not query it. The local
database recovery dump is the explicit exception. A visibility column cannot
hold this journal because every ordinary query would need to remember the
filter. A
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
word because it is a separate schema that ordinary platform paths never query. A
`visibility` column earns less: `index_record` is called from many sites,
and `admin.export` maintains an explicit portable table set with per-table
filters. Both tiers fail OPEN when a predicate is forgotten. The difference is
reversibility — a forgotten `visible_filter` is a query you fix, while a
forgotten sink predicate has already written to the FTS index, the
immutable ledger, a `UNIQUE`-keyed findings row, a file on disk, and a
vector at a third party. That is the argument for doing the sinks as
phase 4 rather than alongside phase 3, and for `private` never becoming
the column's default. Personal records (standups, captures, your own time
away) now start at "only you" for a strong identity, by the request's
default rather than the column's (see "Personal records start at only
you" below).

The `private` schema does not move. It is the strongest confidentiality the
product has, and the journal stays in it.

### Schema

Three migrations: `003_crews.sql` (the crews tables), `004_visibility_tier.sql`
(the columns on all 16 content tables), and `005_crew_context_packs.sql`
(per-crew packs). Append-only, DDL plus non-`activity` backfill, no triggers,
and **no semicolon inside a comment** — `db.py::_statements` splits on `;`
with no comment awareness, so the tail half becomes a statement and `init_db`
fails on a fresh database. An apostrophe is fine: `--` runs to end of line and
the engine opens no string literal there. Both migration headers say this. A
version of this paragraph that also forbade the apostrophe was wrong, and
teaching a rule the runner does not have costs the next author a real
debugging session.

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
  common case and `IN ()` is not legal SQL, so the crew disjunct is dropped
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
- `relationship_contains(parent_tier, parent_crew_id, child_tier,
  child_crew_id)` — require every task reader to be able to read each linked
  engagement, milestone, task, blocker, or promise. A workspace task cannot
  publish a crew or private relationship ID. A crew task can use workspace or
  same-crew links. A private task can use a link that its author can read. The
  write path checks every link and a milestone's engagement parent in one
  transaction. Read paths redact inaccessible relationship IDs for legacy
  rows.
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

Then a test walks the catalog and fails on any table that is in
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
| `embeddings` | `_embed` sends `text[:8000]` to `EMBED_BASE_URL` for workspace rows only, and no memory addressed to a person (`search._embeddable`, used by `index_record` and the `embed-reconcile` repair). Crew rows stay in the local FTS index and never reach it. Every search query also goes there, and the search box says so when embeddings are on (`semantic_search` on `/api/health`). |
| `memories` | Closed in phase 4. `recall()` applies BOTH the `user` filter and the tier on every branch (`services/memory.py::recall`) — the query branch used to apply neither, so one person's search answered out of another person's memories, and `memory_prompt` injects the result into a system prompt. |
| `notifications` | Every team-wide `notify("team", ...)` that quotes a scoped row's text is gated on the workspace tier (the blocker funeral, the stale-decision sweep, ship-it, the unlinked-milestone warning), and a per-person notify checks the recipient can read the row. |
| `admin.export` | Private rows are excluded structurally. Crew rows stay. Tables that can copy private text without a visibility column are excluded. Each new table takes an explicit `admin.TABLES` or `admin.EXCLUDED` classification. Artifact metadata stays, but absolute storage paths do not. |
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
| 5 **(shipped, before 3c)** | Jobs and egress read `WORKSPACE_ONLY`: digest, readout, context pack, the findings rules, and the team-wide block of My Day. The handoff is the exception — it takes a viewer and narrows to the artifact's own tier through `scope.audience`, because it is generated on demand by a person rather than by a job. Moved AHEAD of the picker — a crew task would otherwise have gone straight into the daily digest, which is the same control-that-does-not-hold problem `private` was sequenced around. |
| 6 **(shipped, packs only)** | Per-crew context packs: `005_crew_context_packs.sql`, `build_pack(crew_id)` appending a crew section to the shared body, per-crew version counters, `GET /api/context-pack?crew=`. Per-crew digests and insights are deliberately NOT built. A digest is one morning page for one team — N of them is a different product decision, not a parameter, and the crew pack already answers "what is my crew working on" on demand. A findings row is the most dangerous sink in the app: it quotes another table's text into a row with no identity column and a UNIQUE (rule_id, subject, week) key, and it is never pruned. Per crew, that needs the tier ON the finding, not a second run. Build either when somebody asks for it, not before. |

### Where the picker actually went

Tasks and notes have no create form of their own in this UI — both are made
through quick capture — so the picker went into the ⌘K palette, which routes
to seven entities, plus the standup card. Two controls, eight entities.

### What still lands at workspace, always

All sixteen tables can now carry a non-workspace tier. `lessons` and
`artifacts` are the two nobody sets by hand: a lesson inherits from the
experiment whose conclusion drafted it, and a handoff artifact inherits from
its engagement — which is what stops `list_artifacts` handing out a path to a
file of another crew's work.

Four of the sixteen have no create form in this UI at all (milestones,
events, memories, lessons). Two more do have one but offer no picker on it:
the Time away card (`/settings`) and the engagement field in the chat sidebar
both POST without a tier, so they file at `workspace`. Their REST bodies
accept one — every create body whose service takes a tier exposes it, pinned
by `test_a_create_body_exposes_the_tier_its_service_accepts` — so adding a
picker there is UI work, not a model change.

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
`CREATE UNIQUE INDEX ... (COALESCE(crew_id, 0), version)` changes the key
without touching a row. `COALESCE`, not a bare `crew_id`: a unique index
treats every NULL as distinct, so two team packs could share version 1.

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

**An addressed memory reaches only its addressee, on every surface.** A
memory's `user` is who it is addressed to, and it is a read boundary, not only
a recall filter: the memory list, recall, forget, `get_memory`, and search
(REST search, `/ask`, the short-id lookup, the chat `/search` command, and the
agent and MCP `search_workspace` tools) all serve it to that person alone,
whatever its tier. `search.visible_hits` takes the reader by name and reads no
addressed memory when it names nobody. The reason is consent: `/remember` and
the remote MCP tool address a memory to the speaker, and the agent tool to the
person who drove the turn (with no person, to whoever the model names), all
at the workspace tier with no tier to pick, so a teammate reading it through
search was sharing that nobody chose. Over stdio the MCP tool addresses the
agent itself, and a memory addressed to an agent is no one's private data.
The rule covers the copies too: the review gate files every memory create
addressed to a person as a proposal private to them (tools/_gate.py), a
private proposal sends no team notice, the activity row carries the id only,
and the portable export leaves memories addressed to people out. Approver
groups that a workplace policy names govern team memories only: under such a
policy, a memory addressed to a person is still that person's private
proposal, and that person approves or rejects it
(`review._check_policy_approver`). A fact meant for the team is a memory
addressed to nobody, and it reaches the team only by a deliberate share: "share
with the team" on the memory list, or `/remember team: <fact>` in chat. Both
file a team-visible proposal (`memory.propose_team_memory`), and a teammate
other than the author approves it (`review._check_team_memory_approver`). An
approved share moves the memory, so the author's own copy is deleted. An
engagement memory follows the same approval rule.

**An agent's proposal is its requester's first.** A proposal that an agent
files during a person's turn carries that turn: their words, their own crew
and private rows, the files they attached. When the person has a strong
identity, the proposal is private to them and sends no team notice, and
their approval is what shares the result. `review.requester_judges` is the
one rule, and the gate, the stock and extension tools, and remote MCP tools
all call it. The team reviews instead when the run is unattended, when the
identity is weak (a weak viewer reads no private row), when
`SKEIN_REVIEW_SEPARATION=1` is on, or when policy names approvers. A
proposal from a shared chat follows the same rule for the member who sent
the message. A requester with a key or a sign-in can always withdraw (reject)
their own request, whatever its tier. If a relink or a policy change later
names approvers for a private proposal, its owner is told to withdraw it and
ask again, because approvers cannot read a private proposal. A call to a personal
MCP server is judged by its owner or by a reviewer the policy names, never by
any other teammate, because it runs on the owner's credential and returns
their data. The proposal record stays private after the verdict. The row it made has its
own tier.

The agent changes only rows its requester can read: the gate refuses a
write to a row a strong requester cannot open, because the apply runs as
the agent, which `assert_editable` lets work a crew row. For the same
reason a private review holds only while its owner can read the target row
(`review._private_review_tier`); otherwise nobody may judge it. A personal
row, meaning a memory addressed to a person, a forget of one, or a create
that declares the private tier (a standup or time away), is judged by its
person alone (`review.personal_owner`), under separated duties and approver
groups too, because nobody else can read it. The team "Review needed" notice
goes out only when the proposal's row is at the workspace tier.

**Time away counts for the team only as far as its person chose.** A window
has three settings: only the person away (no team effect at all), the team
sees the dates (private, `dates_shared`), and the team sees the details
(workspace). Capacity, planning, the weekly draft and its summary, and
staffing what-ifs all read `absences.TEAM_SEES_DATES`, and a window below
the workspace tier shows "away", never its kind or note. The weekly draft and
what-ifs count PTO only, so comparing them with capacity tells PTO from
on-call or focus for a dates-only window. A private window
about somebody else is refused, so a teammate's window defaults to the
workspace tier. An agent files the requester's own window through
`requester`, which the review sets from the proposal, never from the payload.

**Growth interests are their person's until shared.** `users.growth_shared`
starts false (migration 037 sets it false for every existing row). The roster
(`users.public_users`), staffing what-ifs and the portable export show another
person's interests only after that person shares them. The share is one way,
and a merge carries the flag with the text it backfills.

**Personal records start at "only you".** For a strong identity, a standup,
a capture (the REST default of `/api/standups` and `/api/capture`), and your
own time away start private, and the standup card, the capture palette and
the time-away form remember the last choice per person
(`frontend/lib/audience.ts`). A weak identity (a trusted-header name with no
key) reads no private row, so for it they start at the workspace tier
(`routes/api.py::_personal_default`), or the record would be hidden from its
own author. The CLI
sends the roster tier only for `--team`. Shared work (tasks, milestones,
decisions, blockers made through their own forms) keeps the workspace
default. An agent files a private standup or time away only where the row
names a person (`author`, `person`). In a table keyed on `created_by`, a
private row an agent makes is readable by no human, so those agent tools
keep the workspace tier. An agent's private standup forks no blocker, for
the same reason. The author widens a row later with "share with the team"
(`services/sharing.py`): it becomes workspace, is indexed for search (a
void task stays out), and its child rows keep their own tier. A share is
refused while a linked parent is narrower, because every reader would still
hide the row. A capture that assigns a question to a teammate goes to the
roster when no tier is sent, because that question cannot be private.
Nothing here narrows a row.

**A merge that carries private data takes both accounts.** An administrator's
merge is refused while the source holds data only it can read
(`users._holds_personal_data`): the reader of the result would be a person
the owner never chose. Signed in as the source, the person asks to merge into
the target; signed in as the target, they confirm (`services/merges.py`).
Only that merge runs `rename_user(consented=True)`, which moves everything,
the 1:1 journal, notifications and the OIDC binding included. Keys and
browser sessions are still revoked. A request keeps the names it was made
with, and a rename cancels a pending one that names the renamed account.
A request is one strong call, which a stolen key can make, so the source
account gets a notice, a request lapses after 7 days, and a deactivation or
a key revocation of either account (or revoke-all) cancels it. An
administrator's merge ends the source's 1:1 pairings instead of carrying
the subject's consent to another account.

**A room agent reads from its join point.** Each call sends what a room
agent reads to the model provider, and the earlier messages were written for
the people in the room. So a new agent starts at the system message that
records its addition (`chat_members.history_from`). The steward can share the
earlier messages when adding it, and that system message says which, so every
member sees the choice. Agents added before this rule keep the whole history.
An agent added again without the history also loses its model session,
which would replay its earlier prompts.

**A deleted record leaves the model sessions too.** A model session replays
everything its agent read on every later turn.
- An attached text file is stored in a session as a pointer, and each restore
  of its owner's own turn reads the file's current text
  (`session_store._with_attached_text`). Another reader gets the pointer or
  the name, never the text. The model's answers and a written summary can
  still quote the file, so deleting it also clears every session of the
  owner's chats that points at it (`uploads.delete_upload`); the chats stay.
  An image description is stored as the file name, like the image bytes.
  Sessions written before the pointer keep the text until their chat is
  deleted or goes idle.
- Deleting a room message also deletes the agent answers to it and clears the
  model session of every agent in the room. Each agent re-reads the room from
  its join point on its next call, its own earlier answers included, where
  the message is a placeholder. The delete waits while any agent in the room
  is answering.
- A chat with no activity for 90 days loses its model sessions
  (`retention.IDLE_SESSION_DAYS`). The chat stays, and its agent starts
  fresh. For an idle chat, this bounds how long a record deleted elsewhere
  stays in a teammate's session, where their agent read it while they were
  allowed to. A chat in use keeps it until the chat is deleted.

**Copies have horizons.** The daily prune (`services/retention.py`) removes
copies of records that outlived their reason:
- 180 days: daily digests, old context-pack versions (never a pack's newest),
  and the text of a settled proposal and of its remote tool call. The
  proposal keeps who proposed, who judged, the verdict and the keys that decide
  who may see it. An approved first use of a personal MCP tool keeps its
  server, tool and version, so the approval holds.
- 30 days: the model session of an unattended agent run.
- 365 days: the requester's name on a cost row. The cost stays.
- 90 days of no activity: a chat's model sessions (above).

The local backups and the off-site mirror both keep 14 dumps
(`admin.BACKUP_KEEP`). A record deleted today can live in older dumps for 14
more days, and that is the longest any deletion takes.

**A departed person's private data leaves 30 days after deactivation.**
Deactivation stays reversible. After `erasure.GRACE_DAYS` the daily
`erase-departed` job deletes what only that person could read: private-tier
rows, solo chats and their sessions, chat folders, shared chats where they are
the only person left with no invitation pending, attached files, memories
addressed to them, proposals reviewed privately for them, notifications, and
the 1:1 notes they wrote (`services/erasure.py`). A crew review stays: its crew
reads and judges it. The job runs on the date the roster shows and again every
day after, so what reaches the account later goes too. Crew and team records
keep the name, because the ledger names the person forever and cannot be
rewritten. No button erases early, so a wrong deactivation always has 30 days
to be undone. With the 14-day backup horizon, the data is gone from every copy
44 days after deactivation.

**You can see and delete what is yours alone.** Settings → Your data counts
what only you can read and deletes one of your private records at a time
(`services/my_data.py`). It never deletes a shared record, because others can
rely on it. Your download holds your private records, your solo chats,
memories addressed to you and the 1:1 notes you wrote, and it names your files
without their contents. The list and the download pass the workplace's
projection policy per row, like every other read, and a task's links to
records you can no longer read are redacted. All of it needs a strong
identity.

**Who left is an administrator's list.** `/api/users?all=1` adds deactivated
teammates for an administrator only (`deps.is_administrator`), because the
Settings roster reverses a deactivation there. Everyone else gets the active
roster.

**Trusted-header mode says what it cannot keep private.** A name there is
whatever a caller types, so a person's own records are only as private as the
network. The mode keeps working, and every personal surface (solo chats,
attached files, memories, My Day's notices, your own activity) shows a weak
caller "Anyone who can reach this server can pick your name and read this.
Sign in with a key for privacy." (`components/weak-identity-notice.tsx`). A
team that needs privacy runs `api-key` or `oidc`.

**Administrator actions.** The activity log shows an actor's rows to that
actor alone, so an action an administrator takes on someone else also sends a
notice. A rename, a merge into an account, and a deactivation or reactivation
notify the person whose account changed. An export of the workspace data and
the revocation of every API key notify the team. A machine actor (a script,
the scheduler) sends no notice.

**Edits and deletes.** The activity log can never be edited or pruned, so a
row that carries text keeps it past every later delete. An edit or delete of
a note, blocker, promise, intake request, absence or growth interest records
the id and the changed field names only. A deleted note used to keep its
first 300 characters for recovery, and an edit its old and new wording;
recovery is now the backups' job, and backups expire. Rows written before
this rule keep their text: the chain cannot be rewritten. The row that
records a workspace record's creation still carries its title or topic
(`scope.detail`), because the team could already read it.

The tier check quotes the author column. Unquoted, memories' `user` is
CURRENT_USER: the check compared the database role name, so an addressee never
read their own private memory and a person named like the role read all of
them.

A third, found in the phase 3-6 review and closed with them:
`GET /api/private/brief/{person}` took a free path parameter with no manager
relation behind it, and its six queries were unfiltered — so every strong
identity could read every other person's PRIVATE standup and promise rows in
full. It now filters on the READER, never on the subject. The gathering
itself is a profile, so a lead also needs a pairing the subject accepted
(`services/pairings.py`), and the subject sees each lead and when they last
opened the brief. The author's 1:1 journal needs no pairing. Deactivation
ends a person's pairings, and a lead the subject declined waits 7 days to
ask again.

## What phase 3 solved

- **The review queue was a mirror.** DONE. `pending_changes` carries no tier
  of its own, so `review._readable` resolves each proposal's TARGET row and
  drops the ones the reader cannot open — creates included, reading the tier
  off the payload. All eight readers call it: `GET /api/review`, `my_day`,
  `agent_inbox`, `review_stats`, the handoff, the week-close ritual and the
  two insights rules that write into `findings.receipt`.
- **`review.approve_change` is a write path that does not look like one.**
  DONE. It splats the payload as kwargs straight into the service, and it
  applies as the proposal's `proposed_by` — an agent slug that
  `scope.is_machine` lets work a crew row — so nothing downstream refuses it.
  `_assert_judgeable` gates both verdicts on the target's tier, in the same
  sentence an absent proposal gets.
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
- It does not move the private schema. The local database recovery unit
  includes it. The configured public platform mirror does not.
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
