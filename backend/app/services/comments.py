"""Comment threads on a task, a decision or a blocker (docs/intent/task-threads.md).

One flat thread per parent row. A comment takes its parent's tier when it is
written and keeps it, so a reader of a comment can always read its parent,
and a thread never shows a count that includes a row the reader cannot see.
The ledger carries ids only: it can never be rewritten, and an author's
delete must remove the text.
"""

from .. import db
from . import mentions, scope, wording

BODY_LEN = 4000
_TABLE = {"task": "tasks", "decision": "decisions", "blocker": "blockers"}
KEY = {"task": "task_id", "decision": "decision_id", "blocker": "blocker_id"}
# where a notice about the thread sends its reader (frontend/lib/entity-ref.ts
# HREF): the task panel opens over any page, and a decision or a blocker row
# is where its thread mounts
_LINK = {
    "task": lambda pid, cid: f"?task={pid}#comment-{cid}",
    "decision": lambda pid, cid: f"/charter#charter-entry-{pid}",
    "blocker": lambda pid, cid: f"/dashboard#blocker-{pid}",
}


def _parent(task_id: int, decision_id: int, blocker_id: int) -> tuple[str, int]:
    named = [
        (kind, int(value))
        for kind, value in (("task", task_id), ("decision", decision_id), ("blocker", blocker_id))
        if value
    ]
    if len(named) != 1:
        raise ValueError("A comment needs exactly one parent: a task, a decision or a blocker.")
    return named[0]


def _check_body(body: str) -> str:
    body = (body or "").strip()
    if not body:
        raise ValueError("A comment needs text. Write the comment, then post it.")
    if len(body) > BODY_LEN:
        raise ValueError(f"The comment is longer than {BODY_LEN} characters. Write a shorter one.")
    # agents read threads as context: the same refusal review._refuse_invisible
    # gives, because a bidi override makes the text say what the reader did not see
    if wording.INVISIBLE.search(body):
        raise ValueError(
            "The comment has an invisible format character. Remove it, then post the comment again."
        )
    return body


def _where(row: dict) -> tuple[str, int]:
    for kind, key in KEY.items():
        if row[key]:
            return kind, int(row[key])
    raise ValueError(f"comment #{row['id']} has no parent")


def _detail(kind: str, pid: int, cid: int) -> str:
    # ids at every tier, never scope.detail: it adds the body at the
    # workspace tier, and the hash-chained ledger cannot drop it on delete
    return f"{kind} #{pid} comment #{cid}"


def add_comment(
    body: str,
    *,
    task_id: int = 0,
    decision_id: int = 0,
    blocker_id: int = 0,
    actor: str,
    origin: str = "human",
) -> dict:
    """Post one comment. The parent keywords match a gate payload, so an
    approval applies the payload with no mapping."""
    kind, pid = _parent(task_id, decision_id, blocker_id)
    body = _check_body(body)
    table = _TABLE[kind]
    with db.transaction():
        # FIRST, and FOR UPDATE: the parent's tier decides the comment's, and
        # a tier change between this read and the insert would file a comment
        # wider than its thread (CLAUDE.md, "A read takes no lock")
        parent = db.query_one(
            f"SELECT * FROM {table} WHERE id = ? FOR UPDATE",  # noqa: S608 — closed table map
            (pid,),
        )
        if not parent:
            raise scope.missing(table, pid)
        scope.assert_editable(table, parent, actor)
        tier, crew_id = scope.inherit(parent)
        # No updated_at on the parent: a comment is talk, not work. A task
        # that looks fresh because somebody asked "still alive?" stops the
        # stale-work findings from firing on work that has stalled.
        cid = db.execute(
            "INSERT INTO comments (task_id, decision_id, blocker_id, created_by, origin, body,"
            " created_at, visibility, crew_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (
                pid if kind == "task" else None,
                pid if kind == "decision" else None,
                pid if kind == "blocker" else None,
                actor,
                origin,
                body,
                db.now(),
                tier,
                crew_id,
            ),
        )
        db.log_activity(actor, "post_comment", _detail(kind, pid, cid))
        notified = mentions.scan(
            "comment", cid, body, actor=actor, link=_LINK[kind](pid, cid), parent=(kind, pid)
        )
    return {"id": cid, "parent": kind, "parent_id": pid, "notified": notified, "woke": ""}


def list_comments(
    viewer: scope.Viewer = scope.NOBODY,
    *,
    task_id: int = 0,
    decision_id: int = 0,
    blocker_id: int = 0,
    me: str = "",
    limit: int = 200,
) -> list[dict]:
    """The thread, oldest first, for a reader who may see the parent.

    `me` sets can_edit and can_delete. It is the caller's own name, which a
    trusted-header viewer does not carry (scope.Viewer drops a weak name),
    and it decides the two flags only: the edit and the delete check the
    actor again."""
    kind, pid = _parent(task_id, decision_id, blocker_id)
    table = _TABLE[kind]
    limit = max(1, min(int(limit or 200), 200))
    # the parent through its own filter first, so an unreadable parent
    # answers exactly like an absent one
    pfrag, pp = scope.visible_filter(viewer, table)
    if not db.query_one(
        f"SELECT id FROM {table} WHERE id = ? AND {pfrag}",  # noqa: S608 — closed table map, bound marks
        (pid, *pp),
    ):
        raise scope.missing(table, pid)
    frag, params = scope.visible_filter(viewer, "comments")
    rows = db.query(
        "SELECT id, created_by, origin, body, created_at, edited_at, deleted_at, deleted_by"  # noqa: S608 — closed key map, bound marks
        f" FROM comments WHERE {KEY[kind]} = ? AND {frag} ORDER BY id LIMIT ?",
        (pid, *params, limit),
    )
    human = bool(me) and not scope.is_machine(me)
    for row in rows:
        live = not row["deleted_at"]
        mine = live and row["created_by"] == me
        row["can_edit"] = mine
        row["can_delete"] = mine or (live and human and row["origin"] != "human")
    return rows


def get_comment(comment_id: int, viewer: scope.Viewer) -> dict:
    """One readable comment with its parent, for the edit and delete routes."""
    frag, params = scope.visible_filter(viewer, "comments")
    row = db.query_one(
        f"SELECT * FROM comments WHERE id = ? AND {frag}",  # noqa: S608 — bound marks
        (comment_id, *params),
    )
    if not row:
        raise scope.missing("comments", comment_id)
    kind, pid = _where(row)
    return {**row, "parent": kind, "parent_id": pid}


def _held(comment_id: int, actor: str) -> dict:
    # FOR UPDATE first: the author, the tombstone and the text decide the write
    row = db.query_one("SELECT * FROM comments WHERE id = ? FOR UPDATE", (comment_id,))
    if not row:
        raise scope.missing("comments", comment_id)
    scope.assert_editable("comments", row, actor)
    return row


def edit_comment(comment_id: int, body: str, *, actor: str) -> dict:
    """The author changes their own comment. It is marked edited, and no
    earlier text is kept. A newly named person is told once, and nobody is
    told twice (mention_log keys on the comment)."""
    body = _check_body(body)
    with db.transaction():
        row = _held(comment_id, actor)
        if row["deleted_at"]:
            raise ValueError("This comment is deleted. Post a new comment.")
        if row["created_by"] != actor:
            raise PermissionError("Only the author can edit this comment.")
        edited = db.now()
        db.execute(
            "UPDATE comments SET body = ?, edited_at = ? WHERE id = ?", (body, edited, comment_id)
        )
        kind, pid = _where(row)
        db.log_activity(actor, "edit_comment", _detail(kind, pid, comment_id))
        notified = mentions.scan(
            "comment",
            comment_id,
            body,
            actor=actor,
            link=_LINK[kind](pid, comment_id),
            parent=(kind, pid),
        )
    return {
        "id": comment_id,
        "parent": kind,
        "parent_id": pid,
        "notified": notified,
        "edited_at": edited,
    }


def delete_comment(comment_id: int, *, actor: str) -> dict:
    """A tombstone: the text goes, the row stays, and it names the deleter. A
    hard delete would leave replies that answer nothing.

    The author may delete, and so may any person who can read an agent's
    comment: the agent is the mechanism, not a speaker with a stake, and a
    delegate's reply posts with no review, so this is how a person removes a
    wrong one."""
    with db.transaction():
        row = _held(comment_id, actor)
        kind, pid = _where(row)
        if row["deleted_at"]:
            return {"id": comment_id, "parent": kind, "parent_id": pid, "deleted": False}
        person = not scope.is_machine(actor)
        if row["created_by"] != actor and not (person and row["origin"] != "human"):
            raise PermissionError("Only the author can delete this comment.")
        db.execute(
            "UPDATE comments SET body = '', deleted_at = ?, deleted_by = ? WHERE id = ?",
            (db.now(), actor, comment_id),
        )
        db.log_activity(actor, "delete_comment", _detail(kind, pid, comment_id))
    return {"id": comment_id, "parent": kind, "parent_id": pid, "deleted": True}


def parent_context(kind: str, pid: int, viewer: scope.Viewer) -> dict[str, str]:
    """The policy attributes of a thread's parent, for a viewer who can read
    it. REST and the gate both resolve the parent here, so a workplace rule
    on a task's project governs the task's thread."""
    from . import policy_context

    attributes = policy_context.existing_scoped(kind, pid, viewer)
    if not attributes or str(attributes.get("relationship_conflict") or "").lower() == "true":
        raise scope.missing(_TABLE[kind], pid)
    return attributes
