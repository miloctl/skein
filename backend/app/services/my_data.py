"""A person's own data: what Skein holds that only they can read, the delete
of one of their private records, and an export of it.

Strong identity only (routes/api.py): in trusted-header mode a name is
whatever the caller typed, and these answers are about one named person.

Private records only. A shared record is the team's: others may have built
on it, the same reason a share never narrows (services/sharing.py), and its
readers change over time. A crew row read after its author left the crew
reached them through an export that asked only who wrote it.

The list and the export pass every row through the workplace's projection
policy (projection_policy.ProjectionPolicy), the same per-row gate the other
composite reads apply. A workplace that denies a project's rows on REST
reads denies them here too."""

import json
from pathlib import Path

from .. import db
from . import artifact_files, erasure, private_notes, scope, uploads, work
from .projection_policy import ProjectionPolicy

# One line that names a record in the list. Memories keep their own surface
# (memory.forget), which also cleans the search index, and attached files
# theirs (uploads.delete_upload, the Attached files card); tests/test_my_data.py
# checks that LABELS and OWN_SURFACE together cover scope.CLASSIFIED.
LABELS = {
    "absences": "kind || ' ' || starts_on || ' to ' || ends_on",
    # every private artifact but an upload: a handoff made for a private
    # engagement, for one
    "artifacts": "kind || ': ' || title",
    "blockers": "title",
    "decisions": "title",
    "engagements": "name",
    "events": "title",
    "intake_requests": "title",
    "lessons": "lesson",
    "milestones": "title",
    "notes": "topic",
    "promises": "promise",
    "questions": "question",
    "standups": "today",
    "task_worklog": "note",
    "tasks": "title",
}
OWN_SURFACE = frozenset({"memories"})

# what the workplace policy calls a row of each table (policy_context._TABLES).
# Standups, time away and worklog entries carry no project, so no project
# rule can reach them.
_POLICY_ENTITY = {
    "artifacts": "artifact",
    "blockers": "blocker",
    "decisions": "decision",
    "engagements": "engagement",
    "events": "event",
    "intake_requests": "intake",
    "lessons": "lesson",
    "memories": "memory",
    "milestones": "milestone",
    "notes": "note",
    "promises": "promise",
    "questions": "question",
    "tasks": "task",
}

# Rows that point at a record: deleting it would leave them pointing at
# nothing, or take them along (allocations cascade, and one names another
# person's time). The delete is refused while any exists.
_ENGAGEMENT_CHILDREN = (
    "tasks",
    "milestones",
    "memories",
    "chat_threads",
    "allocations",
    "artifacts",
    "events",
    "lessons",
    "promises",
)
_LINKED: dict[str, tuple[tuple[str, str], ...]] = {
    "engagements": tuple((child, "engagement_id = ?") for child in _ENGAGEMENT_CHILDREN),
    "milestones": (("tasks", "milestone_id = ?"),),
    "decisions": (("decisions", "superseded_by = ?"),),
    "tasks": (
        ("blockers", "task_id = ?"),
        ("tasks", "waiting_on_type = 'task' AND waiting_on_id = ?"),
    ),
    "blockers": (("tasks", "waiting_on_type = 'blocker' AND waiting_on_id = ?"),),
    "promises": (("tasks", "waiting_on_type = 'promise' AND waiting_on_id = ?"),),
    "questions": (("tasks", "waiting_on_type = 'question' AND waiting_on_id = ?"),),
}


def summary(person: str) -> dict:
    counts = erasure.holdings(person)
    files = uploads.list_uploads(person)["files"]
    # the holdings count every private artifact; attached files have their
    # own card, so the rest is counted apart
    counts["uploads"] = len(files)
    counts["artifacts"] = max(0, counts["artifacts"] - len(files))
    return {"counts": counts, "file_bytes": uploads.used_bytes(person)}


def _kind(table: str) -> str:
    if table not in LABELS:
        raise db.NotFound("Your data has no list for this kind of record. Select a kind it lists.")
    return scope.CLASSIFIED[table]


def _private(table: str) -> str:
    """The WHERE clause for one person's private rows of `table`."""
    clause = f'visibility = ? AND "{scope.CLASSIFIED[table]}" = ?'
    return clause + " AND kind <> 'upload'" if table == "artifacts" else clause


def _permitted(table: str, rows: list[dict], policy: ProjectionPolicy) -> list[dict]:
    entity = _POLICY_ENTITY.get(table)
    rows = policy.filter_rows(entity, rows) if entity else rows
    if table == "tasks":
        # a link to a milestone, an engagement or a waited-on record the
        # person can no longer read names only an id, and still names it
        rows = work.redact_task_relationships(rows, policy.viewer, policy.permits)
    return rows


def list_private(table: str, person: str, policy: ProjectionPolicy) -> list[dict]:
    _kind(table)
    rows = db.query(
        f"SELECT id, left({LABELS[table]}, 120) AS label, created_at FROM {table}"  # noqa: S608 — table and label from LABELS
        f" WHERE {_private(table)} ORDER BY id DESC LIMIT 200",
        (scope.PRIVATE, person),
    )
    entity = _POLICY_ENTITY.get(table)
    return policy.filter_rows(entity, rows) if entity else rows


def delete_private(table: str, row_id: int, *, actor: str) -> dict:
    """Delete one of the actor's own private records. Anything else reads as
    absent (scope.missing): a different refusal would say which ids are
    somebody's private rows."""
    author = _kind(table)
    with db.transaction():
        row = db.query_one(
            f"SELECT * FROM {table} WHERE id = ? FOR UPDATE",  # noqa: S608 — table from LABELS
            (row_id,),
        )
        if (
            not row
            or row["visibility"] != scope.PRIVATE
            or row[author] != actor
            or (table == "artifacts" and row["kind"] == "upload")
        ):
            raise scope.missing(table, row_id)
        for linked, where in _LINKED.get(table, ()):
            if db.query_one(
                f"SELECT 1 FROM {linked} WHERE {where} LIMIT 1",  # noqa: S608 — names from _LINKED
                (row_id,),
            ):
                raise ValueError(
                    f"Other records are linked to this {scope.NOUN[table]}."
                    " Delete or move them first."
                )
        if table == "tasks":
            # a worklog entry is about its task and has no meaning without it
            db.execute("DELETE FROM task_worklog WHERE task_id = ?", (row_id,))
        db.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))  # noqa: S608 — table from LABELS
        if table == "artifacts" and row["path"]:
            artifact_files.delete_after_commit(Path(row["path"]))
        # the id and the kind, never the text: the ledger cannot be rewritten
        db.log_activity(actor, "delete_private_record", f"#{row_id} {scope.NOUN[table]}")
    return {"id": row_id, "deleted": True}


def export(person: str, policy: ProjectionPolicy) -> dict:
    """The person's private records, solo chats, memories addressed to them,
    the 1:1 notes they wrote, and their files by name. File contents stay
    out: each file downloads on its own."""
    records = {}
    for table in scope.CLASSIFIED:
        if table in OWN_SURFACE:
            continue
        rows = db.query(
            f"SELECT * FROM {table} WHERE {_private(table)} ORDER BY id",  # noqa: S608 — table from scope.CLASSIFIED
            (scope.PRIVATE, person),
        )
        records[table] = _permitted(table, rows, policy)
    for row in records["artifacts"]:
        # a server path maps the data volume; the title is what a person reads
        row.pop("path", None)
    chats = []
    for thread in db.query(
        "SELECT id, title, created_at FROM chat_threads WHERE owner = ? AND kind = 'solo'"
        " ORDER BY created_at",
        (person,),
    ):
        messages = db.query(
            "SELECT role, content, created_at FROM chat_messages WHERE thread_id = ? ORDER BY id",
            (thread["id"],),
        )
        chats.append({**thread, "messages": messages})
    user = db.query_one("SELECT growth_interests FROM users WHERE name = ?", (person,))
    memories = db.query(
        'SELECT id, topic, content, created_at FROM memories WHERE "user" = ? ORDER BY id',
        (person,),
    )
    body = {
        "person": person,
        "exported_at": db.now(),
        "records": records,
        "memories": _permitted("memories", memories, policy),
        "chats": chats,
        "journal_notes": private_notes.export_author(person),
        "files": [
            {key: f[key] for key in ("id", "title", "size", "created_at")}
            for f in uploads.list_uploads(person)["files"]
        ],
        "growth_interests": user["growth_interests"] if user else "",
    }
    # the ledger records that an export happened, never what it held
    db.log_activity(person, "export_my_data", f"{sum(map(len, records.values()))} record(s)")
    return json.loads(json.dumps(body, default=str))
