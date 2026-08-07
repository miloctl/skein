"""Visibility scope: who may read a row.

The read half of the three tiers in docs/VISIBILITY.md, and the write
half's one check. `visible_filter` is what every scoped read splices in, so
the predicate lives here rather than in each read function that needs it.

There is no chokepoint in this codebase to hang a filter on: every read is a
hand-written SELECT in a service, and `db.py` is a transport that never
inspects SQL. So the design is a fragment plus an inventory that CI checks,
modeled on `activity.visible_actor_filter` — which is the only precedent, and
which reached three callers on discipline alone.

Counts are deliberately absent from this file. Four of them were wrong within
one branch of being written, and a stale number reads as a measurement.

**What makes `private` private is not this filter.** The filter only ever
matches a private row for its own author. What keeps it private is the set of
places it never reaches: search.index_record refuses to index one,
admin.export leaves it out, every job and egress builder reads
WORKSPACE_ONLY, `detail` keeps its body out of the hash-chained ledger, and
`assert_readable_by` refuses to hand it to anyone. Each of those is a
separate promise, and a forgotten one is a body somewhere permanent.
"""

from .. import db
from . import crews

# The three tiers. `workspace` is the migration default, so a deployment that
# never touches the picker behaves exactly as it did before the column landed.
PRIVATE = "private"
CREW = "crew"
WORKSPACE = "workspace"
TIERS = (PRIVATE, CREW, WORKSPACE)


# Never a real identity: `anonymous` is the pre-name-pick fallback
# (routes/deps.py) that every unnamed caller shares, and `agent` is the
# chat-tool default (agents/identity.py). Either as a viewer would hand one
# unnamed reader another unnamed writer's private rows.
_NOT_A_VIEWER = frozenset({"", "anonymous", "agent"})


class Viewer:
    """Who is asking, and how well the server knows it.

    A bare name is not enough. docs/VISIBILITY.md sets the enforcement bar at
    STRONG identity — an API key or a validated sign-in — because in
    trusted-header mode a name is whatever the caller typed. Carrying that in
    the type is what makes the bar mechanical: with a plain string, the rule
    lives at every call site as `viewer = user if strong else ""`, and the one
    site that forgets hands a rewritten X-User header the private tier,
    silently.

    Built in routes/deps.py and nowhere else. Everything with no human behind
    it — a scheduled job, an agent tool, an MCP call — passes NOBODY and gets
    the workspace tier, which is the rule those surfaces need anyway.
    """

    __slots__ = ("crew_ids", "name")

    def __init__(self, name: str, strong: bool):
        weak = not strong or name in _NOT_A_VIEWER
        self.name = "" if weak else name
        # resolved ONCE per viewer, not once per query. One dashboard load
        # fans out to roughly 27 scoped reads, and db.connect() (db.py) costs
        # two orders of magnitude more than this SELECT — so per-call it grows
        # the process's scarcest budget by half, for an answer that cannot
        # change mid-request.
        self.crew_ids: list[int] = crews.crews_of(self.name) if self.name else []

    @classmethod
    def for_actor(cls, actor: str) -> "Viewer":
        """The write path's viewer, built from the bare actor name.

        A WRITE already trusts this name: assert_editable resolves crews from
        exactly the same string, and routes/deps.py has already resolved it.
        Use this only to REFUSE AN ID — never to return a row. The
        strong-identity bar guards scoped content, and the link probes this
        serves return no column at all.

        Without it those probes are an existence oracle: `SELECT id FROM
        blockers WHERE id = ?` says nothing about the tier, so update_task's
        `waiting_on` accepted a private blocker id and rejected an absent one,
        and ids are sequential integers. Walking 1..n read off which private
        rows exist.
        """
        v = cls("", False)
        v.name = "" if actor in _NOT_A_VIEWER else actor
        v.crew_ids = crews.crews_of(v.name) if v.name else []
        return v

    @classmethod
    def for_crew(cls, crew_id: int) -> "Viewer":
        """An AUDIENCE, not a person: one crew and the workspace tier, with no
        author arm at all. What a document written FOR a crew may contain.

        Built by scope.audience. Nothing else must construct one — a nameless
        viewer skips the strong-identity bar this class exists to carry, and
        it is safe here only because it is strictly narrower than the caller
        who is already allowed to write the document.
        """
        v = cls("", False)
        v.crew_ids = [crew_id]
        return v


NOBODY = Viewer("", False)


def audience(tier: str, crew_id: int | None, writer: Viewer) -> Viewer:
    """The viewer a document stored at this tier is written FOR.

    An artifact carries ONE tier and its body is prose. Built with the
    writer's own viewer, a workspace handoff generated by a strong user
    contained her private decisions and her other crews' rows, and every
    roster member then read them out of GET /api/artifacts and the file on
    disk. The body has to be narrowed to the document, not to the author.

    The result is the INTERSECTION of what the document's readers may see and
    what the WRITER may see. Both halves matter, and the second one was
    missing: a caller reaches a scoped row through any of three disjuncts, and
    authorship is one of them. Somebody who created a crew engagement and was
    later removed from the crew still passes the read filter on that ONE row —
    handing them Viewer.for_crew put the whole crew's decisions, questions and
    tasks into a document they generated and can read, which is precisely what
    crews.remove_member promises does not happen.

    A PRIVATE document returns the writer unchanged: its one reader is the
    author, so anything the author can read is already something its only
    reader can read.
    """
    if tier == PRIVATE:
        return writer
    # `crew_id in writer.crew_ids`, not just `tier == CREW`: membership is what
    # makes for_crew a narrowing. Without it the writer is an author-only
    # reader, and the two sets intersect at the workspace tier and nowhere else.
    if tier == CREW and crew_id and crew_id in writer.crew_ids:
        return Viewer.for_crew(crew_id)
    return NOBODY


# Actors with no person behind them. `activity.SYSTEM_ACTORS` is the display
# side of the same idea; this one is the authorization side and adds the
# never-a-viewer names, so the two are not merged.
_SYSTEM_ACTORS = frozenset({"system", "scheduler", "forge", "ci", "mcp"}) | _NOT_A_VIEWER


def is_machine(actor: str) -> bool:
    """Is this actor a job, a webhook, or an agent rather than a person?"""
    if actor in _SYSTEM_ACTORS:
        return True
    from .users import is_agent

    return is_agent(actor)


# What a JOB reads. A scheduled job has no viewer — "who is this digest for"
# has no answer — so it reads the workspace tier and nothing else. Spliced as
# a literal rather than through visible_filter because these are hand-written
# SQL strings with their own params, and a fragment with a bound parameter
# would have to be threaded into each one's tuple.
WORKSPACE_ONLY = f"visibility = '{WORKSPACE}'"


def resolve_write(visibility: str, crew_id: int, *, actor: str) -> tuple[str, int | None]:
    """The tier a write lands on, checked once so every service does not each
    invent it. Returns the pair to store.

    `private` means the author and nobody else. What makes that true is not
    this function but the sinks: search.index_record refuses to index one,
    admin.export leaves it out, every job reads WORKSPACE_ONLY, and
    scope.detail keeps its body out of the hash-chained ledger. A private row
    also cannot be handed to anyone (assert_readable_by), because there is
    nobody who could read it.

    It is still weaker than private.db, which no code path opens at all. The
    difference is reversibility: a forgotten filter is a query you fix, a
    forgotten sink has already written the body somewhere permanent.

    A crew tier costs a membership check (crews.assert_writable), and that call
    belongs INSIDE the caller's transaction: bare, it opens its own connection,
    so a person removed from the crew between the check and the insert still
    scopes a row into it.
    """
    visibility = (visibility or WORKSPACE).strip().lower()
    if visibility == WORKSPACE:
        return WORKSPACE, None
    if visibility == PRIVATE:
        return PRIVATE, None
    if visibility != CREW:
        raise ValueError(f"visibility must be one of {', '.join(TIERS)}")
    if not crew_id:
        # what happened, then the fix (CLAUDE.md). Fix-only, the caller who
        # sent visibility=crew with no crew_id gets an instruction and no
        # diagnosis.
        raise ValueError("a crew tier needs a crew. Pick the crew this belongs to.")
    crews.assert_writable(crew_id, actor)
    return CREW, crew_id


def detail(tier: str, ident: str, body: str) -> str:
    """What a scoped write may put in activity.detail.

    The ledger is hash-chained: a migration may not UPDATE a row carrying a
    seq (tests/test_migrations.py), and the off-box anchor log makes
    re-chaining impossible by design. So a body written here is written for
    good — there is no delete, no redaction, and no later tier change that
    takes it back. An identifier is enough to find the row, and the row
    itself carries the tier.
    """
    return ident if tier != WORKSPACE else f"{ident} {body}".rstrip()


def assert_readable_by(
    tier: str, crew_id: int | None, person: str, *, label: str, author: str = ""
) -> None:
    """Refuse handing scoped work to somebody who cannot read it.

    A crew task assigned to a non-member is a row its own assignee never sees:
    the filter's disjuncts are the workspace tier, authorship, and crew
    membership, and an assignee is none of those. Caught at the write, this is
    a sentence the writer can act on. Left to the read, it is a task that
    silently does not exist for the person meant to do it.

    `author` is the third disjunct, and leaving it out refused the ordinary
    case. capture.py hardcodes `owner=actor` on a blocker and post_standup
    passes `owner=author`, so without it EVERY private capture that classified
    as a blocker was refused, and every private standup with blockers text
    rolled back whole — naming a remedy ("leave the owner empty") that neither
    caller can take.
    """
    if person and person == author:
        return
    if tier == PRIVATE and person:
        # names no name: the value came from the caller, and an error never
        # echoes a rejected value back (CLAUDE.md)
        raise ValueError(
            # The remedy has to be one EVERY caller can take. "leave the
            # {label} empty" is not: delegate_task refuses an empty sponsor
            # two checks earlier, and add_absence requires the person. Both
            # remedies below are always available, and both name the picker's
            # own labels rather than the tier names (docs/LEXICON.md).
            f'"only you" means one reader, so this takes no {label}.'
            f" Pick a crew, or make it visible to everyone on the roster."
        )
    if tier != CREW or not person:
        return
    if crew_id not in crews.crews_of(person):
        raise ValueError(
            f"that {label} is not in the crew, so they cannot read this."
            f" Add them to the crew, or pick a different {label}."
        )


def can_read(tier: str, crew_id: int | None, viewer: Viewer, author: str = "") -> bool:
    """Whether `viewer` may read a row at this tier — the Viewer half of
    visible_filter, for the places that hold rows in Python rather than SQL.

    The three disjuncts are the SAME three visible_filter emits, in the same
    order: the workspace tier, authorship, then crew membership. Dropping the
    author arm here would hide a person's own private row from them, which is
    the bug assert_readable_by already had once.

    review.list_changes needs it: `pending_changes` rows are not the scoped
    rows, they only quote them, so there is no column to filter on. Anything
    that CAN filter in SQL must use visible_filter instead — this evaluates
    one row at a time, and a list of them is N round trips.
    """
    if tier == WORKSPACE:
        return True
    if not viewer.name:
        return False
    if author and viewer.name == author:
        return True
    if tier == PRIVATE:
        return False
    return crew_id in viewer.crew_ids


def missing(table: str, row_id: int) -> db.NotFound:
    """The one "no such row" sentence, for BOTH the absent row and the row the
    caller may not read.

    They have to be the same string. "you cannot edit #12" — or any wording
    that only the scoped case produces — answers "does #12 exist", and ids are
    sequential integers, so a caller walks 1..n and reads off which ones are
    scoped. That is the fact a private row must not carry. A caller who cannot
    read the row cannot tell the two apart, and absent is the honest answer.

    Every service guarded by assert_editable raises THIS for its own existence
    check too. tests/test_visibility_authz.py compares the two byte for byte.
    """
    return db.NotFound(missing_text(table, row_id))


def missing_text(table: str, row_id: int) -> str:
    """The sentence, without the exception type.

    A row named in a request BODY (a task's milestone_id, a waiting_on
    target) is a 400: the addressed row exists and the caller sent a value the
    server refuses, so a 404 would claim the wrong thing is absent. It still
    has to read identically to the addressed case — the two must not be
    distinguishable by wording OR by status. Raise
    `ValueError(scope.missing_text(...))` there and `scope.missing(...)` for
    the row in the path.
    """
    return f"no {NOUN[table]} #{row_id}"


def assert_editable(table: str, row: dict, actor: str, *, verb: str = "") -> None:
    """Refuse a mutation of a row the actor could not read.

    `visible_filter` covers the read half. It does nothing for a write, and
    every mutation in this codebase finds its row by a caller-supplied id:
    `UPDATE notes SET ... WHERE id = ?` matches a private note whoever asks.
    Ids are small integers, so this is not obscurity — it is enumeration.

    Editing is not a separate permission here. Any reader of a row may change
    it (this is a coordination harness, not a document store), so the check is
    exactly `visible_filter`'s disjuncts evaluated in Python. Keeping the two
    in one file is the point: a fourth disjunct added there and forgotten here
    hands a reader a row they cannot edit, which reads as a bug, not a breach.

    Delete matters more than update. collab.delete_note writes 300 characters
    of the note into activity.detail so a deletion stays reviewable, and the
    ledger is hash-chained — a private body that lands there is there for good.

    Takes the plain actor name, not a Viewer. The write path already trusts it
    (resolve_write -> crews.assert_writable), so a stronger bar here would
    refuse the very write that created the row.
    """
    tier = row["visibility"]
    if tier == WORKSPACE:
        return
    if tier == CREW and is_machine(actor):
        # A crew is a set of PEOPLE, and this check answers "may this person
        # read it". A machine actor is the mechanism, not a reader: the forge
        # webhook moves a task on a push, review.approve_change applies with
        # actor=proposed_by (the agent slug, never the approving human), and
        # the delegation trio runs as the agent. Refusing them turned every
        # agent proposal against a crew row into a permanent auto-reject that
        # told the reviewer the row had vanished, while it sat on their screen.
        #
        # PRIVATE deliberately falls through to the checks below. Nothing ever
        # hands an agent private work — assert_readable_by refuses a private
        # assignee, owner and sponsor outright — so a machine reaching one is
        # already wrong.
        return
    author_column = CLASSIFIED.get(table)
    if author_column is None:
        # KeyError, not ValueError — see visible_filter below for why
        raise KeyError(f"{table!r} carries no visibility tier — see scope.CLASSIFIED")
    # `actor not in _NOT_A_VIEWER`, the same bar Viewer applies to a reader.
    # Without it every tool call authors rows as the literal "agent", so one
    # agent's private note matched another agent's delete on `author == actor`
    # and the two shared an identity the product never gave them.
    if actor not in _NOT_A_VIEWER and row[author_column] == actor:
        return
    if tier == CREW and actor not in _NOT_A_VIEWER and row["crew_id"] in crews.crews_of(actor):
        return
    raise missing(table, row["id"])


def inherit(row: dict | None) -> tuple[str, int | None]:
    """The tier a CHILD row takes from its parent.

    Called by delegation.report_progress and delegation.accept_completion,
    and by nothing else. Every OTHER parent-to-child crossing threads the pair
    by hand — collab.post_standup into a blocker, intake._disposition into an
    engagement, engagements._ship_it into a note, _experiment_lesson into a
    lesson, handoff.generate_handoff into an artifact. So a new crossing is a
    place to REMEMBER the tier, not a call to make: this helper is not the
    chokepoint, and looking for one finds nothing to hang a check on.

    No membership re-check: the parent already passed one, and re-checking
    would refuse a legitimate write by an agent or a job that is in no crew.
    """
    if not row:
        return WORKSPACE, None
    return row["visibility"], row["crew_id"]


def visible_filter(viewer: Viewer, table: str, alias: str = "") -> tuple[str, list]:
    """A SQL fragment limiting rows to what `viewer` may read, plus its params.

    HOW TO SPLICE IT. The fragment is an AND term, always parenthesized:

        WHERE status = ? AND {frag}

    Three placements are wrong, and each fails silently rather than loudly:

    - Beside a caller's own top-level OR (`WHERE a = ? AND {frag} OR b`). The
      caller's OR defeats any fragment. Parenthesize the whole WHERE.
    - After GROUP BY, in HAVING. A bare column in a grouped query takes an
      arbitrary row from the group, so the whole group passes on one workspace
      row. Filter BEFORE aggregating.
    - In the WHERE of a LEFT JOIN, against the nullable side. That drops every
      base row with no match and turns the join INNER. Put it in the ON clause
      there.

    Takes the TABLE, never an author column. There is no single author column
    in this schema, and four tables carry both their real one and a
    `created_by` that holds the agent slug on the tool path — so a
    column-taking signature let `notes` be filtered on `created_by`, which
    compiles, runs, and hides a private note from the person who wrote it.
    CLASSIFIED is the only place that mapping lives.

    Positional `?` marks, because `db.query` takes a tuple.

    A viewer in NO crew is the common case, and SQLite has no `IN ()` — the
    crew disjunct is DROPPED rather than emitted empty, which is a syntax
    error rather than a filter that matches nothing.
    """
    author_column = CLASSIFIED.get(table)
    if author_column is None:
        # KeyError, not ValueError: app/main.py maps ValueError to 400, and
        # `table` is a literal at every call site — a miss here is our bug, so
        # it has to stay a 500 (CLAUDE.md, "Input errors are 4xx")
        raise KeyError(f"{table!r} carries no visibility tier — see scope.CLASSIFIED")
    p = f"{alias}." if alias else ""
    parts = [f"{p}visibility = ?"]
    params: list = [WORKSPACE]
    # the author disjunct only when there IS an author. A nameless viewer with
    # crews is Viewer.for_crew — an AUDIENCE rather than a person (see
    # scope.audience) — and giving it the author arm would put the writer's
    # own private rows into a document written for somebody else.
    if viewer.name:
        parts.append(f"{p}{author_column} = ?")
        params.append(viewer.name)
    if viewer.crew_ids:
        marks = ", ".join("?" for _ in viewer.crew_ids)
        parts.append(f"({p}visibility = ? AND {p}crew_id IN ({marks}))")
        params.append(CREW)
        params.extend(viewer.crew_ids)
    return "(" + " OR ".join(parts) + ")", params


# What a scoped row is called when the viewer may not read its name. One
# string for all four capacity surfaces, so the same condition reads the same
# way (docs/LEXICON.md). It carries a number wherever it renders
# ("other work (60%)"), so it stays plain.
OTHER_WORK = "other work"


def visible_name(viewer: Viewer, table: str, column: str, alias: str = "") -> tuple[str, list]:
    """`column` when the viewer may read the row, OTHER_WORK when they cannot.

    The capacity surfaces have to aggregate over EVERY tier and still name
    only some of them. A person allocated 60% to a private engagement is 60%
    committed to everybody who plans against them, so filtering the row out
    makes the total lie — the same argument absences.away_today makes for a
    private PTO day. What must not travel is the engagement's name, which
    GROUP_CONCAT(e.name) put on /api/capacity, /api/portfolio/conflicts,
    /api/allocations, /api/usage, the exec readout artifact on disk, and the
    team_capacity agent tool.

    The CASE sits in the SELECT list, so its params come BEFORE the WHERE
    clause's. A caller that appends them instead binds the tier against a date
    and silently returns nothing.
    """
    frag, params = visible_filter(viewer, table, alias)
    return f"CASE WHEN {frag} THEN {column} ELSE ? END", [*params, OTHER_WORK]


# ---------------------------------------------------------------------------
# The inventory. Every table is here or in UNSCOPED, and
# tests/test_scope.py walks sqlite_master and fails on anything in neither.
#
# An inventory is only as good as the direction CI checks. Both other
# enumerate-everything structures here (services/admin.py::TABLES and
# services/users.py::_ATTRIBUTION) carry a test in each direction for that
# reason: a table missing from the map, and a map entry naming a table that
# is gone. tests/test_scope.py holds both for this one.
# ---------------------------------------------------------------------------

# table -> the column that decides authorship for the tier filter.
# Every table here carries `visibility` and `crew_id` (migration 004).
CLASSIFIED: dict[str, str] = {
    # `person`, not `created_by`: an absence is filed FOR someone, often by
    # someone else (absences.py resolves any teammate). The deliberate cost is
    # that the filer cannot read a scoped absence they wrote for a colleague.
    # Whose calendar it is outranks who typed it.
    "absences": "person",
    "artifacts": "created_by",
    "blockers": "created_by",
    "decisions": "created_by",
    "engagements": "created_by",
    "events": "created_by",
    "intake_requests": "created_by",
    "lessons": "created_by",
    # `user`, not `created_by`: tools/memory.py writes created_by =
    # agent_identity(), so every memory an agent saves is authored by "agent".
    # On created_by, a private memory would be readable by no human at all
    # while still being injected into a system prompt.
    "memories": "user",
    "milestones": "created_by",
    # `author`, not `created_by`: collab.save_note binds created_by to
    # `actor or author`, and the actor is the agent slug on the tool path.
    # Same hazard as memories above. standups already gets this right.
    "notes": "author",
    "promises": "created_by",
    "questions": "created_by",
    "standups": "author",
    "task_worklog": "author",
    "tasks": "created_by",
}

# table -> what a reader calls one row of it. Not `table[:-1]`, which renders
# "memorie" and "intake_request" — an identifier, underscore included, in a
# sentence a person reads. Parity with CLASSIFIED is pinned by
# tests/test_scope.py::test_every_classified_table_has_a_reader_facing_noun —
# a CLASSIFIED table with no entry here raises KeyError inside scope.missing,
# which turns every not-found path for that table into a 500.
NOUN: dict[str, str] = {
    "absences": "absence",
    "artifacts": "artifact",
    "blockers": "blocker",
    "decisions": "decision",
    "engagements": "engagement",
    "events": "event",
    "intake_requests": "request",
    "lessons": "lesson",
    "memories": "memory",
    "milestones": "milestone",
    "notes": "note",
    "promises": "promise",
    "questions": "question",
    "standups": "standup",
    "task_worklog": "worklog entry",
    "tasks": "task",
}

# table -> why a visibility tier does not belong on it. A reason, not a shrug:
# an absence with no comment reads as an oversight to the next reader.
UNSCOPED: dict[str, str] = {
    # --- already scoped, by a stronger mechanism than a column ---
    "chat_folders": "owner-scoped by primary key (services/chat_threads.py)",
    "chat_messages": "reached only through chat_threads, which is owner-scoped",
    "chat_threads": "owner-scoped, and POST /api/chat claims the id before use",
    "notifications": "addressed per person already (user IN (?, 'team'))",
    "sessions": "the model's own conversation, keyed by a thread id its owner claimed",
    "session_agents": "cascades off sessions",
    "session_messages": "cascades off sessions",
    "session_multi_agents": "cascades off sessions",
    "feature_unlocks": "self-visible only — the anti-surveillance rule already outranks provenance here",
    # --- the ledger ---
    "activity": (
        "the chain covers actor and detail in every row's digest, so a"
        " visibility column could never be backfilled and could never enter"
        " the hash. The feed is scoped by ACTOR instead"
        " (activity.visible_actor_filter), which is a different axis and"
        " already works. The rule a scoped write must keep is that detail"
        " carries an identifier, never a body."
    ),
    # --- derived: the tier lives on the source row, not the copy ---
    "embeddings": "derived from search_index — a private row is never indexed, so never embedded",
    "search_ids": "derived from search_index",
    "search_index": "private rows are never indexed at all, and crew rows carry the tier for filtering",
    "search_index_config": "FTS5 shadow table, rebuilt with search_index",
    "search_index_content": "FTS5 shadow table, rebuilt with search_index",
    "search_index_data": "FTS5 shadow table, rebuilt with search_index",
    "search_index_docsize": "FTS5 shadow table, rebuilt with search_index",
    "search_index_idx": "FTS5 shadow table, rebuilt with search_index",
    "forecast_snapshots": (
        "written by a job, and jobs read the workspace tier only — the same"
        " rule that covers job_outcomes below, not a property of milestones"
        " that anything here enforces"
    ),
    "findings": (
        "rule output, and the rules read the workspace tier only. A finding"
        " persists other tables' TEXT into a row with no identity column and a"
        " UNIQUE (rule_id, subject, week) key, so a scoped row that tripped a"
        " rule would be republished permanently."
    ),
    "finding_dispositions": "points at findings above",
    "context_packs": "assembled from the workspace tier only, and versioned globally",
    # --- infrastructure: no user-authored content ---
    "agent_authority": "the agent write matrix, not content",
    "api_keys": "credentials, owner-scoped, never listed to anyone else",
    "app_settings": "deployment configuration",
    "crew_members": "the membership the filter READS. Scoping it would be circular.",
    "crews": "the crews the filter reads. Who is in which crew is not itself scoped.",
    "allocations": "staffing math — capacity is a team-wide number by design",
    "job_outcomes": (
        "scheduler telemetry. NOT content-free — run_job stringifies the whole"
        " job result into detail, and publish_digest returns its markdown — but"
        " what lands there is what a job read, and a job reads the workspace"
        " tier only. That rule is the classification here."
    ),
    "job_runs": "scheduler claim rows, no user-authored content",
    "mention_log": "a dedupe key, not content",
    "pending_changes": (
        "the review queue. A proposal against a scoped row is the open"
        " question of phase 3: the payload holds a copy of the row, and the"
        " reviewer may not be able to read the original."
    ),
    "schema_version": "migration bookkeeping",
    "tool_usage": "adoption counters, no content",
    "usage_log": "token spend, no content",
    "users": "the roster. Hiding a teammate's existence is not a tier, it is a different product.",
    "feedback": "pulse votes are stored without an author on purpose (services/feedback.py)",
    "flock_traces": "slugs, timings and token counts, never message text",
}


def unclassified() -> set[str]:
    """Tables in neither map. The test calls this; so can a person."""
    live = {
        r["name"]
        for r in db.query(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    return live - set(CLASSIFIED) - set(UNSCOPED)
