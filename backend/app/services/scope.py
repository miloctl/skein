"""Visibility scope: who may read a row.

The read half of the three tiers in docs/VISIBILITY.md. Nothing calls
`visible_filter` yet — the `visibility` and `crew_id` columns land in phase 3,
and this module is what those queries will use so that ~95 read functions do
not each invent the predicate.

There is no chokepoint in this codebase to hang a filter on: 382 hand-written
SELECT statements across 50 service files, and `db.py` is a transport that
never inspects SQL. So the design is a fragment plus an inventory that CI
checks, modeled on `activity.visible_actor_filter` — which is the only
precedent, and which reached three callers on discipline alone.

**`private` never reaches this module.** A private row is excluded
structurally: it is never indexed, never embedded, never in a context pack, a
digest, a readout, a finding, the ICS feed, or an export. That is the
`private_notes` principle applied to a column, and it is what keeps the filter
here to ONE tier instead of three.
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


NOBODY = Viewer("", False)


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
        raise ValueError(f"{table!r} carries no visibility tier — see scope.CLASSIFIED")
    p = f"{alias}." if alias else ""
    if not viewer.name:
        return f"({p}visibility = ?)", [WORKSPACE]
    parts = [f"{p}visibility = ?", f"{p}{author_column} = ?"]
    params: list = [WORKSPACE, viewer.name]
    if viewer.crew_ids:
        marks = ", ".join("?" for _ in viewer.crew_ids)
        parts.append(f"({p}visibility = ? AND {p}crew_id IN ({marks}))")
        params.append(CREW)
        params.extend(viewer.crew_ids)
    return "(" + " OR ".join(parts) + ")", params


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
# Adding `visibility` and `crew_id` to these tables is phase 3.
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
