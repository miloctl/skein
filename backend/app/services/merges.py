"""Merges both accounts agree to.

A merge moves everything the source holds to the target, including data only
the source can read: private-tier rows, solo chats, addressed memories,
private proposals, files, MCP servers, the 1:1 journal, rooms, crews and the
sign-in binding. An administrator cannot run that merge
(users._holds_personal_data refuses it), because the person reading the
result would be one the owner never chose. Here the owner chooses twice:
signed in as the source they ask, and signed in as the target they confirm.
"""

from .. import db
from .notifications import notify
from .users import fold, rename_user, resolve_teammate


def _row(request_id: int) -> dict | None:
    return db.query_one("SELECT * FROM merge_requests WHERE id = ? FOR UPDATE", (request_id,))


def request(target: str, *, actor: str) -> dict:
    """The source (the actor) asks to merge into `target`."""
    person = resolve_teammate(target, actor, "target", allow_team=False)
    if not person or fold(person) == fold(actor):
        raise ValueError("Name the other account, not this one.")
    with db.transaction():
        # the pending check decides the insert (merge_requests_pending)
        db.name_lock(db.LOCK_IDENTITY, fold(actor))
        if db.query_one(
            "SELECT 1 FROM merge_requests WHERE source = ? AND status = 'pending'", (actor,)
        ):
            raise ValueError("A merge request from this account already waits. Cancel it first.")
        rid = db.execute(
            "INSERT INTO merge_requests (source, target, status, created_at)"
            " VALUES (?, ?, 'pending', ?) RETURNING id",
            (actor, person, db.now()),
        )
        db.log_activity(actor, "request_merge", f"#{rid}")
        notify(
            person,
            f"{actor} asks to merge that account into yours. Confirm or decline in Settings.",
            tier="immediate",
            link="/settings",
        )
    return {"id": rid, "source": actor, "target": person, "status": "pending"}


def confirm(request_id: int, *, actor: str) -> dict:
    """The target (the actor) confirms, and the merge runs with consent."""
    seen = db.query_one("SELECT source, target FROM merge_requests WHERE id = ?", (request_id,))
    if not seen:
        raise db.NotFound("merge request not found")
    with db.transaction():
        # identity locks FIRST, in the order rename_user takes them: request()
        # holds the source's before it touches this table, and a row lock
        # taken ahead of them is the other half of a deadlock
        for identity in sorted({fold(seen["source"]), fold(seen["target"])}):
            db.name_lock(db.LOCK_IDENTITY, identity)
        row = _row(request_id)
        # re-read under the locks: a rename or a cancel can land in between
        if not row or row["target"] != actor or row["status"] != "pending":
            raise db.NotFound("merge request not found")
        db.execute(
            "UPDATE merge_requests SET status = 'confirmed', settled_at = ? WHERE id = ?",
            (db.now(), request_id),
        )
        result = rename_user(
            row["source"], row["target"], actor=actor, expected_merge=True, consented=True
        )
    return {"id": request_id, "status": "confirmed", **result}


def settle(request_id: int, *, actor: str) -> dict:
    """The source cancels, or the target declines."""
    with db.transaction():
        row = _row(request_id)
        if not row or actor not in (row["source"], row["target"]) or row["status"] != "pending":
            raise db.NotFound("merge request not found")
        status = "cancelled" if actor == row["source"] else "declined"
        db.execute(
            "UPDATE merge_requests SET status = ?, settled_at = ? WHERE id = ?",
            (status, db.now(), request_id),
        )
        # two literal calls: tests/test_activity_feed.py reads each logged
        # action name off the source
        if status == "declined":
            db.log_activity(actor, "decline_merge", f"#{request_id}")
            notify(
                row["source"],
                f"{actor} declined the merge request.",
                tier="immediate",
                link="/settings",
            )
        else:
            db.log_activity(actor, "cancel_merge", f"#{request_id}")
    return {"id": request_id, "status": status}


def list_for(person: str) -> dict:
    rows = db.query(
        "SELECT id, source, target, created_at FROM merge_requests"
        " WHERE status = 'pending' AND (source = ? OR target = ?) ORDER BY id",
        (person, person),
    )
    return {
        "outgoing": [r for r in rows if r["source"] == person],
        "incoming": [r for r in rows if r["target"] == person],
    }
