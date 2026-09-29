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


def parent_of(task_id: int, decision_id: int, blocker_id: int) -> tuple[str, int]:
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
            "The comment has an invisible format character. Remove it, then try again."
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
    as_delegate: bool = False,
) -> dict:
    """Post one comment. The parent keywords match a gate payload, so an
    approval applies the payload with no mapping.

    `as_delegate` is the agent's direct path on its own open delegated task
    (tools/portfolio.py::post_comment), which skips the gate as the
    delegation trio does. It only adds refusals."""
    kind, pid = parent_of(task_id, decision_id, blocker_id)
    body = _check_body(body)
    table = _TABLE[kind]
    if as_delegate:
        from ..agents.identity import refuse_when_consultative

        # the gate is where force_review turns a write into a proposal, and
        # this path never reaches it
        refuse_when_consultative("comment on a delegated task")
        refuse_forbidden(actor, kind)
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
        # re-checked under the lock: a reassignment between the tool's plain
        # read and this insert would let an agent post, unreviewed, on a task
        # it no longer holds
        if as_delegate and (
            kind != "task"
            or parent["delegated_agent"] != actor
            or parent["status"] in ("done", "void")
        ):
            raise ValueError(
                f"{kind} #{pid} is not an open task delegated to you, so you cannot comment"
                " there directly."
            )
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
        woke = _wake(kind, parent, body, actor=actor, origin=origin)
        _tell_the_thread(kind, parent, cid, actor=actor, told=notified, tier=(tier, crew_id))
    return {"id": cid, "parent": kind, "parent_id": pid, "notified": notified, "woke": woke}


# the named parties of each parent, read from rows every reader already sees
_PARTIES = {
    "task": ("assignee", "sponsor"),
    "decision": ("created_by", "decided_by"),
    "blocker": ("owner", "created_by"),
}


def _tell_the_thread(
    kind: str,
    parent: dict,
    cid: int,
    *,
    actor: str,
    told: list[str],
    tier: tuple[str, int | None],
) -> None:
    """A digest notice for the parent's named parties and everyone who wrote
    in the thread before. No subscription table: the rows say who is in it.

    Skipped: the author, anyone the mention scan just told, agents (the inbox
    lists what an agent has not answered), inactive people, anyone who cannot
    read the new comment, and anyone who still holds an unread notice into
    this thread, which already points them here. The parent row is held FOR
    UPDATE by the caller, so two replies cannot both see "no notice yet".
    ponytail: a dismissal that races the unread check can hide one notice;
    a per-(person, parent) watch row is the upgrade."""
    from . import users
    from .notifications import notify

    pid = int(parent["id"])
    link = _LINK[kind](pid, cid)
    same_thread = f"?task={pid}#comment-%" if kind == "task" else link
    writers = db.query(
        f"SELECT DISTINCT created_by FROM comments WHERE {KEY[kind]} = ? AND id <> ?",  # noqa: S608 — closed key map
        (pid, cid),
    )
    people = {str(parent.get(column) or "") for column in _PARTIES[kind]}
    people |= {str(row["created_by"]) for row in writers}
    # folded: an assignee is free text, and "Dana" beside "dana" is one person
    skip = {users.fold(name) for name in (actor, *told)}
    for person in sorted(p for p in people if p and users.fold(p) not in skip):
        # a person on the roster: an agent reads its own inbox, and `team`
        # (every CI blocker's owner) or a system actor is nobody's name
        if not users.is_human(person) or not users.is_active(person):
            continue
        if not scope.can_read(tier[0], tier[1], scope.Viewer.for_actor(person), actor):
            continue
        if db.query_one(
            'SELECT 1 FROM notifications WHERE "user" = ? AND read_at IS NULL AND link LIKE ?',
            (person, same_thread),
        ):
            continue
        notify(
            person,
            lambda source: f"{actor} commented on {kind} #{source['id']}.",
            tier="digest",
            link=link,
            source_entity=kind,
            source_id=pid,
        )


def _wake(kind: str, parent: dict, body: str, *, actor: str, origin: str) -> str:
    """One agent turn for the task's delegate when a person names it.

    Read from the parent row this transaction holds FOR UPDATE: a
    reassignment between a plain read and the enqueue would wake an agent
    that no longer holds the task. An agent-origin comment never wakes
    anyone (delegation.py's rule: no unattended chain), a done or void task
    has no turn to give, and an edit never wakes (edit_comment). The parser
    is names_in, never scan's result: scan cannot reach an agent on a crew
    task, because agents hold no crews."""
    agent = str(parent.get("delegated_agent") or "") if kind == "task" else ""
    if (
        not agent
        or origin not in ("human", "agent_verified")
        or parent["status"] in ("done", "void")
        or agent not in mentions.names_in(body, actor)[1]
    ):
        return ""
    from . import agent_wakeups

    agent_wakeups.enqueue(agent, int(parent["id"]), requested_by=actor)
    return agent


def list_comments(
    viewer: scope.Viewer = scope.NOBODY,
    *,
    task_id: int = 0,
    decision_id: int = 0,
    blocker_id: int = 0,
    me: str = "",
    actor: str = "",
    limit: int = 200,
) -> list[dict]:
    """The thread, oldest first, for a reader who may see the parent.

    `me` sets can_edit and can_delete. It is the caller's own name, which a
    trusted-header viewer does not carry (scope.Viewer drops a weak name),
    and it decides the two flags only: the edit and the delete check the
    actor again.

    `actor` is the delegation door, for the reason delegation.list_worklog
    gives: an agent holds no crews, so the tier filter alone would refuse
    the thread on a crew task it is answering. It opens for the task's own
    delegate only, per task, and a private task cannot carry a delegate."""
    kind, pid = parent_of(task_id, decision_id, blocker_id)
    table = _TABLE[kind]
    limit = max(1, min(int(limit or 200), 200))
    party = None
    if actor and kind == "task":
        row = db.query_one(
            "SELECT delegated_agent, visibility, crew_id FROM tasks WHERE id = ?", (pid,)
        )
        party = row if row is not None and row["delegated_agent"] == actor else None
    # the parent through its own filter first, so an unreadable parent
    # answers exactly like an absent one
    pfrag, pp = scope.visible_filter(viewer, table)
    if not party and not db.query_one(
        f"SELECT id FROM {table} WHERE id = ? AND {pfrag}",  # noqa: S608 — closed table map, bound marks
        (pid, *pp),
    ):
        raise scope.missing(table, pid)
    frag, params = delegate_door(party) if party else scope.visible_filter(viewer, "comments")
    # the newest page, oldest first: the wake prompt sends the agent here for
    # the NEW comment, and a page that kept the oldest would drop exactly that one
    rows = db.query(
        "SELECT * FROM (SELECT id, created_by, origin, body, created_at, edited_at,"  # noqa: S608 — closed key map, bound marks
        f" deleted_at, deleted_by, visibility FROM comments WHERE {KEY[kind]} = ? AND {frag}"
        " ORDER BY id DESC LIMIT ?) newest ORDER BY id",
        (pid, *params, limit),
    )
    human = bool(me) and not scope.is_machine(me)
    for row in rows:
        live = not row["deleted_at"]
        mine = live and row["created_by"] == me
        row["can_edit"] = mine
        row["can_delete"] = mine or (live and human and row["origin"] != "human")
    return rows


def delegate_door(task: dict, alias: str = "") -> tuple[str, list]:
    """What a task's delegate reads of the rows under it, whatever its own
    crews: the rows at the task's CURRENT tier or wider, never a narrower
    one. A child keeps the tier it was written at when its task is shared
    wider (sharing.py only widens a row), so an unbounded door would hand a
    private or crew-era comment to anyone who drove the agent, and to the
    model in an unattended turn."""
    column = f"{alias}." if alias else ""
    if task["visibility"] == scope.CREW:
        return (
            f"({column}visibility = ? OR ({column}visibility = ? AND {column}crew_id = ?))",
            [scope.WORKSPACE, scope.CREW, task["crew_id"]],
        )
    return f"{column}visibility = ?", [scope.WORKSPACE]


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
        person = not scope.is_machine(actor)
        if row["created_by"] != actor and not (person and row["origin"] != "human"):
            raise PermissionError("Only the author can delete this comment.")
        if row["deleted_at"]:
            return {"id": comment_id, "parent": kind, "parent_id": pid, "deleted": False}
        db.execute(
            "UPDATE comments SET body = '', deleted_at = ?, deleted_by = ? WHERE id = ?",
            (db.now(), actor, comment_id),
        )
        # an approved agent comment keeps its words in the proposal it came
        # from, which the approved list shows: the text goes there too
        db.execute(
            'UPDATE pending_changes SET payload = (payload::jsonb || \'{"body": ""}\')::text'
            " WHERE entity = 'comment' AND result_id = ?",
            (comment_id,),
        )
        db.log_activity(actor, "delete_comment", _detail(kind, pid, comment_id))
    return {"id": comment_id, "parent": kind, "parent_id": pid, "deleted": True}


def open_delegate(task_id: int) -> str:
    """The agent an open task is delegated to, or "". The tool's plain read
    that picks the direct path: add_comment re-checks it under the lock."""
    row = db.query_one("SELECT delegated_agent, status FROM tasks WHERE id = ?", (task_id,))
    if not row or row["status"] in ("done", "void"):
        return ""
    return str(row["delegated_agent"] or "")


def refuse_forbidden(agent: str, kind: str) -> None:
    """`forbidden` on a thread's parent entity stops every comment path, and
    on `comment` stops this agent commenting at all: the kill switch an
    operator reaches for must not leave a side door open."""
    from .delegation import authority_level

    for entity in (kind, "comment"):
        if authority_level(agent, entity) == "forbidden":
            # delegation._check_not_forbidden's words, for one condition
            raise ValueError(f"'{agent}' is forbidden on {entity}s — ask a human to lift it")


def unanswered_for(agent: str, task_ids: list[int], limit: int = 20) -> list[dict]:
    """Others' comments on the agent's open delegated tasks that are newer
    than the agent's own last comment or worklog note on that task. The
    watermark is the agent's own writes, never a read record: answering, or
    reporting progress, clears it. An edit counts as new, so a steering
    comment changed after the agent acted is listed again.

    A write in the same second as the comment counts as its answer:
    db.now() has seconds, and listing it again invites a second reply.

    For the agent's own inbox only (delegation.agent_inbox with no viewer):
    the rows are the delegated tasks' own threads, which the delegate reads
    whatever the tier (list_comments' door)."""
    if not task_ids:
        return []
    marks = ",".join("?" * len(task_ids))
    # the delegate's door (delegate_door), row by row: a comment narrower
    # than its task stays out. A deleted answer is no answer, so the question
    # it answered comes back.
    rows = db.query(
        "SELECT c.id, c.task_id, c.created_by, c.body, c.created_at, c.edited_at"  # noqa: S608 — marks only
        " FROM comments c JOIN tasks t ON t.id = c.task_id"
        f" WHERE c.task_id IN ({marks}) AND c.created_by <> ? AND c.deleted_at IS NULL"
        " AND (c.visibility = 'workspace'"
        "  OR (c.visibility = 'crew' AND t.visibility = 'crew' AND c.crew_id = t.crew_id))"
        " AND COALESCE(c.edited_at, c.created_at) > COALESCE(GREATEST("
        "  (SELECT MAX(o.created_at) FROM comments o"
        "   WHERE o.task_id = c.task_id AND o.created_by = ? AND o.deleted_at IS NULL),"
        "  (SELECT MAX(w.created_at) FROM task_worklog w"
        "   WHERE w.task_id = c.task_id AND w.author = ?)), '')"
        " ORDER BY c.id LIMIT ?",
        (*task_ids, agent, agent, agent, max(1, min(int(limit), 20))),
    )
    for row in rows:
        row["body"] = row["body"][:1000]
    return rows


def parent_context(kind: str, pid: int, viewer: scope.Viewer) -> dict[str, str]:
    """The policy attributes of a thread's parent, for a viewer who can read
    it. REST and the gate both resolve the parent here, so a workplace rule
    on a task's project governs the task's thread."""
    from . import policy_context

    attributes = policy_context.existing_scoped(kind, pid, viewer)
    if not attributes or str(attributes.get("relationship_conflict") or "").lower() == "true":
        raise scope.missing(_TABLE[kind], pid)
    return attributes
