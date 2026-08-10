"""How one row came to exist, read from the row itself.

Every write already records `origin` and `created_by`, an approved proposal
already records the row it produced (`pending_changes.result_id`), converted
work already records the finding that asked for it (`source_finding_id`), and
the activity ledger already records every change since. Four facts, four
tables, and no surface that put them together — so a reader looking at a task
could see it was made by an agent and could not see who approved it, what was
proposed, or what has happened to it since.

The chain matters where the judgement happens. "An agent wrote this" is a
label; "an agent proposed this, Mira approved it on Tuesday, and it has been
edited twice since" is a reason to trust it or to look again.

Read-only composition over rows that already exist. No table, no write path.
"""

from .. import db
from . import scope


def _proposal_kinds(table: str) -> tuple[str, ...]:
    """Every proposal entity that applies to this table.

    Read from `review._TARGET_TABLE`, which is the registry's own authority for
    "what does this proposal target" and is CI-checked against the registry —
    so an entity added there shows up here on the day it ships rather than the
    day somebody remembers.
    """
    from .review import _TARGET_TABLE

    return tuple(k for k, v in _TARGET_TABLE.items() if v == table) or ("",)


def lineage(entity: str, entity_id: int, viewer: scope.Viewer = scope.NOBODY) -> dict:
    """The provenance chain for one row: how it was made, and what since.

    `entity` is the ledger's word for the row, not a table name — `task`,
    `decision`, `blocker`. The row itself is read THROUGH the viewer's filter,
    so an unreadable row raises `scope.missing` exactly as an absent one does —
    the route takes a caller-supplied id over a dense integer space, and any
    other pairing answers "does #12 exist" for every id somebody cares to walk.
    Every joined read below is filtered or carries no content.
    """
    table = _TABLES.get(entity)
    if not table:
        # a 404, never `{}` at 200: an empty object has no `history` key, and
        # the renderer reads `.length` off it and unmounts the panel — the
        # same failure `review.list_changes` records for its evidence block
        raise db.NotFound(f"no provenance for '{entity}'")
    frag, vp = scope.visible_filter(viewer, table)
    row = db.query_one(
        f"SELECT origin, created_by, created_at FROM {table}"  # noqa: S608 — table from the _TABLES map, visible_filter emits only bound marks
        f" WHERE id = ? AND {frag}",
        (entity_id, *vp),
    )
    if not row:
        raise scope.missing(table, entity_id)

    # The proposal that produced this row, if one did. `result_id` is stamped
    # by approve_change at apply time, so this is the authoritative link — a
    # match on entity + entity_id would also catch REJECTED proposals against
    # the same row, which never became anything.
    #
    # The entity set is DERIVED from review's own target map, never guessed by
    # appending `_edit`: only three `_edit` entities exist, and the real update
    # entities are named differently — `task_completion` and `delegation` both
    # target `tasks`, `question_assign` targets `questions`. Guessing missed a
    # sponsor's own acceptance verdict on the one entity the UI ships.
    kinds = _proposal_kinds(table)
    marks = ",".join("?" * len(kinds))
    proposal = db.query_one(
        "SELECT id, entity, action, proposed_by, requested_by, origin, created_at,"  # noqa: S608 — marks are bound
        " reviewed_by, reviewed_at, reviewed_strong, reviewed_override, review_note"
        f" FROM pending_changes WHERE result_id = ? AND entity IN ({marks})"
        " AND status = 'approved' ORDER BY id DESC LIMIT 1",
        (entity_id, *kinds),
    )

    return {
        "origin": row["origin"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "proposal": proposal,
        # Whether the verdict was made with strong identity. In the default
        # trusted-header mode a name is whatever the caller typed, so a verdict
        # there is a record of a click and not of a person — the same
        # distinction the trust score refuses to count (services/delegation.py).
        "verdict_is_weak": bool(proposal and not proposal["reviewed_strong"]),
        "history": _history(entity, entity_id, viewer),
    }


# The ledger's word for a row, mapped to the table that holds it. Only entities
# whose rows a reader opens: this answers "how did THIS come to exist", and a
# row nobody can open needs no answer.
_TABLES = {
    "task": "tasks",
    "milestone": "milestones",
    "decision": "decisions",
    "blocker": "blockers",
    "question": "questions",
    "promise": "promises",
    "note": "notes",
    "lesson": "lessons",
    "engagement": "engagements",
}

# What has HAPPENED to a row, by the action word the ledger records. Creation
# is already reported by `origin`/`created_by` above, so listing it again would
# say the same thing twice.
_CHANGED = {
    "task": ("update_task", "delegate_task", "complete_task", "claim_task"),
    "milestone": ("update_milestone",),
    "decision": ("supersede_decision", "reconfirm_decision", "stale_decision"),
    "blocker": ("resolve_blocker", "edit_blocker", "escalate_blocker"),
    "question": ("answer_question", "assign_question"),
    "promise": ("update_promise", "edit_promise"),
    "note": ("update_note", "delete_note"),
    "lesson": (),
    # no `close_engagement`: no writer emits that action word
    "engagement": ("update_engagement",),
}


def _history(entity: str, entity_id: int, viewer: scope.Viewer) -> list[dict]:
    """Changes to this row since it was made, newest first.

    Matched on the ledger's `detail`, which every writer starts with `#<id>`
    (services/work.py, services/blockers.py and the rest all do). The action is
    tested too, so `#12` in a task's detail cannot pull in a blocker's row —
    the two number spaces are independent and a bare id match crossed them.

    The ACTOR is withheld for anybody `activity.visible_actor_filter` would
    hide — every human but the reader. Agent identities and the four system
    actors stay NAMED, because that filter admits them: "scout claimed this"
    is the provenance an agent's work exists to carry.

    In trusted-header mode `Viewer.name` is "" (a self-asserted name carries no
    strength), so NO human is admitted and the reader's own actions read as
    "somebody" too. That is the honest answer there: the server cannot prove
    the row is theirs, and a mode that guesses would name a colleague's row on
    a header anybody can type.
    Without that, this is a fourth reader of `activity` beside `feed`, the raw
    endpoint and My Day's digest, and the only one that would answer "who
    touched what, when" about a colleague. Task ids are a dense integer space,
    so iterating this endpoint would rebuild the timeline `feed` refuses to
    serve. The change itself is what the reader needs — "edited twice since" —
    and that survives without the name.

    `detail` is not selected at all. Nothing renders it, and it is the one
    column in this table that can carry a person's name from a writer that
    does not route through `scope.detail` (delegate_task and assign_question
    both interpolate one).

    No tier filter: `activity` carries none (scope.UNSCOPED says why). The
    scoping that matters already happened — the caller could read the row.
    """
    from .activity import visible_actor_filter

    actions = _CHANGED.get(entity, ())
    if not actions:
        return []
    marks = ",".join("?" * len(actions))
    rows = db.query(
        f"SELECT actor, action, created_at FROM activity"  # noqa: S608 — marks are bound
        f" WHERE action IN ({marks})"
        " AND (detail = ? OR detail LIKE ? OR detail LIKE ?)"
        " ORDER BY COALESCE(seq, 0) DESC, id DESC LIMIT 20",
        (*actions, f"#{entity_id}", f"#{entity_id} %", f"#{entity_id}->%"),
    )
    # `visible_actor_filter` returns `actor IN (?, …)` over a static list, and
    # every actor here already came from `activity` — so the names it would
    # admit ARE its bound parameters. Querying for them would be a round trip
    # for an answer already in hand.
    _, allowed = visible_actor_filter(viewer.name)
    shown = set(allowed)
    return [{**r, "actor": r["actor"] if r["actor"] in shown else ""} for r in rows]
