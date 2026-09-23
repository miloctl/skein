"""Share with the team: the one-way widening of a row its author wrote at
"only you" or for one crew.

Never narrower. Once the roster has read a row, its digest lines, search
hits, embeddings and exports may already carry it, and a narrower tier cannot
recall those copies. Narrowing stays a separate question for that reason.

Child rows keep their own tier: a blocker forked from a private standup stays
private until its author shares it too (collab.post_standup)."""

from collections.abc import Callable

from .. import db
from . import scope
from .search import index_record

# table -> (search entity, the row's index text). Private rows are never
# indexed (search._is_private), so a shared row must be indexed here, in the
# format its own service writes: collab.py (questions, decisions, standups,
# notes), work.py (tasks), blockers.py, promises.py, intake.py. A format that
# drifts from its service's leaves a shared row findable by other words.
SHAREABLE: dict[str, tuple[str, Callable[[dict], tuple[str, str]]]] = {
    "standups": (
        "standup",
        lambda r: (f"{r['author']}'s standup", _join(r, "yesterday", "today", "blockers")),
    ),
    "notes": ("note", lambda r: (r["topic"] or "", r["content"] or "")),
    "tasks": ("task", lambda r: (r["title"] or "", _join(r, "description", "assignee"))),
    "questions": (
        "question",
        lambda r: ((r["question"] or "")[:120], _join(r, "question", "answer")),
    ),
    "decisions": ("decision", lambda r: (r["title"] or "", _join(r, "decision", "context"))),
    "blockers": ("blocker", lambda r: (r["title"] or "", _join(r, "detail", "owner"))),
    "promises": ("promise", lambda r: ((r["promise"] or "")[:120], _join(r, "promise", "to_whom"))),
    "intake_requests": (
        "intake",
        lambda r: (r["title"] or "", _join(r, "detail", "requester", "project_class")),
    ),
}


def _join(row: dict, *columns: str) -> str:
    return " ".join(str(row[column] or "") for column in columns)


def share_with_team(table: str, row_id: int, *, actor: str) -> dict:
    """Make one row the actor wrote visible to everyone on the roster."""
    if table not in SHAREABLE:
        raise ValueError("This kind of record cannot be shared from here.")
    author = scope.CLASSIFIED[table]
    with db.transaction():
        # FOR UPDATE: an edit landing between the check and the write would
        # reindex the row at its old tier (search._is_private)
        row = db.query_one(
            f"SELECT * FROM {table} WHERE id = ? FOR UPDATE",  # noqa: S608 — table from SHAREABLE
            (row_id,),
        )
        # scope.missing for a row somebody else wrote: a private one is
        # unreadable to the caller, and the refusal must not say it exists
        if not row or row[author] != actor:
            raise scope.missing(table, row_id)
        if row["visibility"] == scope.WORKSPACE:
            raise ValueError("Everyone on the roster already sees this.")
        db.execute(
            f"UPDATE {table} SET visibility = ?, crew_id = NULL WHERE id = ?",  # noqa: S608 — table from SHAREABLE
            (scope.WORKSPACE, row_id),
        )
        entity, text = SHAREABLE[table]
        index_record(entity, row_id, *text(row))
        db.log_activity(actor, "share_with_team", f"#{row_id} {scope.NOUN[table]}")
    return {"id": row_id, "visibility": scope.WORKSPACE}
