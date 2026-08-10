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

from .. import config, db
from . import scope


def lineage(entity: str, entity_id: int, viewer: scope.Viewer = scope.NOBODY) -> dict:
    """The provenance chain for one row: how it was made, and what since.

    `entity` is the ledger's word for the row, not a table name — `task`,
    `decision`, `blocker`. The caller has already read the row, so this takes
    no tier filter of its own for the row itself; every JOINED read below is
    filtered or carries no content.
    """
    table = _TABLES.get(entity)
    if not table:
        return {}
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
    proposal = db.query_one(
        "SELECT id, entity, action, proposed_by, requested_by, origin, created_at,"
        " reviewed_by, reviewed_at, reviewed_strong, reviewed_override, review_note"
        " FROM pending_changes WHERE result_id = ? AND entity IN (?, ?)"
        " AND status = 'approved' ORDER BY id DESC LIMIT 1",
        (entity_id, entity, f"{entity}_edit"),
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
        "auth_mode": config.AUTH_MODE,
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
    "engagement": ("update_engagement", "close_engagement"),
}


def _history(entity: str, entity_id: int, viewer: scope.Viewer) -> list[dict]:
    """Changes to this row since it was made, newest first.

    Matched on the ledger's `detail`, which every writer starts with `#<id>`
    (services/work.py, services/blockers.py and the rest all do). The action is
    tested too, so `#12` in a task's detail cannot pull in a blocker's row —
    the two number spaces are independent and a bare id match crossed them.

    NOT viewer-filtered by tier: `activity` carries none (scope.UNSCOPED says
    why). The scoping that matters already happened — the caller could read the
    row, and `detail` is written through `scope.detail`, which keeps a scoped
    row's body out of the ledger entirely.
    """
    actions = _CHANGED.get(entity, ())
    if not actions:
        return []
    marks = ",".join("?" * len(actions))
    return db.query(
        f"SELECT actor, action, detail, created_at FROM activity"  # noqa: S608 — marks are bound
        f" WHERE action IN ({marks})"
        " AND (detail = ? OR detail LIKE ? OR detail LIKE ?)"
        " ORDER BY COALESCE(seq, 0) DESC, id DESC LIMIT 20",
        (*actions, f"#{entity_id}", f"#{entity_id} %", f"#{entity_id}->%"),
    )
