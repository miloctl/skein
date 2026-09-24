"""A person's own data: what Skein holds about them, the delete of one of
their private records, and an export of their own records.

Strong identity only (routes/api.py): in trusted-header mode a name is
whatever the caller typed, and these answers are about one named person.

Shared records have no delete here. Others may have built on them, the same
reason a share never narrows (services/sharing.py)."""

import json

from .. import db
from . import erasure, private_notes, scope, uploads

# One line that names a record in the list. Memories and attached files keep
# their own surfaces (memory.forget, uploads.delete_upload), which also clean
# the search index and the file bytes; tests/test_my_data.py checks that
# these two maps together cover scope.CLASSIFIED.
LABELS = {
    "absences": "kind || ' ' || starts_on || ' to ' || ends_on",
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
OWN_SURFACE = frozenset({"memories", "artifacts"})

# rows that point at a record and keep no meaning without it (NO ACTION
# foreign keys, core_migrations): deleting the record under them is refused
_LINKED = {
    "engagements": ("tasks", "milestones", "memories", "chat_threads"),
    "milestones": ("tasks",),
}


def summary(person: str) -> dict:
    return {
        "counts": erasure.holdings(person),
        "file_bytes": uploads.used_bytes(person),
    }


def _kind(table: str) -> str:
    if table not in LABELS:
        raise db.NotFound("No such kind of record.")
    return scope.CLASSIFIED[table]


def list_private(table: str, person: str) -> list[dict]:
    author = _kind(table)
    return db.query(
        f"SELECT id, left({LABELS[table]}, 120) AS label, created_at FROM {table}"  # noqa: S608 — table and label from LABELS
        f' WHERE visibility = ? AND "{author}" = ? ORDER BY id DESC LIMIT 200',
        (scope.PRIVATE, person),
    )


def delete_private(table: str, row_id: int, *, actor: str) -> dict:
    """Delete one of the actor's own private records. Anything else reads as
    absent (scope.missing): a different refusal would say which ids are
    somebody's private rows."""
    author = _kind(table)
    with db.transaction():
        row = db.query_one(
            f'SELECT visibility, "{author}" AS author FROM {table} WHERE id = ? FOR UPDATE',  # noqa: S608 — table and column from LABELS and scope.CLASSIFIED
            (row_id,),
        )
        if not row or row["visibility"] != scope.PRIVATE or row["author"] != actor:
            raise scope.missing(table, row_id)
        for linked in _LINKED.get(table, ()):
            column = "milestone_id" if table == "milestones" else "engagement_id"
            if db.query_one(
                f"SELECT 1 FROM {linked} WHERE {column} = ? LIMIT 1",  # noqa: S608 — names from _LINKED
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
        # the id and the kind, never the text: the ledger cannot be rewritten
        db.log_activity(actor, "delete_private_record", f"#{row_id} {scope.NOUN[table]}")
    return {"id": row_id, "deleted": True}


def export(person: str) -> dict:
    """The person's own records at every tier, their solo chats, memories
    addressed to them, the 1:1 notes they wrote, and their files by name.
    File contents stay out: each file downloads on its own."""
    records = {}
    for table, author in scope.CLASSIFIED.items():
        if table in OWN_SURFACE:
            continue
        records[table] = db.query(
            f'SELECT * FROM {table} WHERE "{author}" = ? ORDER BY id',  # noqa: S608 — table and column from scope.CLASSIFIED
            (person,),
        )
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
    body = {
        "person": person,
        "exported_at": db.now(),
        "records": records,
        "memories": db.query(
            'SELECT id, topic, content, created_at FROM memories WHERE "user" = ? ORDER BY id',
            (person,),
        ),
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
