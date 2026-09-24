"""Merges both accounts agree to.

A merge moves everything the source holds to the target, including data only
the source can read: private-tier rows, solo chats, addressed memories,
private proposals, files, MCP servers, the 1:1 journal, rooms, crews and the
sign-in binding. An administrator cannot run that merge
(users._holds_personal_data refuses it), because the person reading the
result would be one the owner never chose. Here the owner chooses twice:
signed in as the source they ask, and signed in as the target they confirm.
"""

from datetime import UTC, datetime, timedelta

from .. import db
from .notifications import notify
from .users import fold, rename_user, resolve_teammate

# A request is one strong call, which a stolen key can make. It lapses after
# this many days, and deactivation or key revocation cancels it (cancel_for),
# so a request filed with a leaked credential cannot be confirmed later.
EXPIRY_DAYS = 7


def _expired_before() -> str:
    # the db.now() format, so the text comparison orders by time
    return (datetime.now(UTC) - timedelta(days=EXPIRY_DAYS)).isoformat(timespec="seconds")


def cancel_for(name: str = "") -> int:
    """Cancel every pending request naming `name`, or every pending request
    when `name` is empty (revoke_all_keys: every credential is suspect)."""
    where, params = ("(source = ? OR target = ?)", (name, name)) if name else ("TRUE", ())
    return db.execute_rowcount(
        f"UPDATE merge_requests SET status = 'cancelled', settled_at = ?"  # noqa: S608 — fixed fragments
        f" WHERE status = 'pending' AND {where}",
        (db.now(), *params),
    )


def _row(request_id: int) -> dict | None:
    return db.query_one("SELECT * FROM merge_requests WHERE id = ? FOR UPDATE", (request_id,))


def request(target: str, *, actor: str) -> dict:
    """The source (the actor) asks to merge into `target`."""
    named = resolve_teammate(target, actor, "target", allow_team=False)
    if not named or fold(named) == fold(actor):
        raise ValueError("Name the other account, not this one.")
    with db.transaction():
        # both identities, sorted, before anything is read: the pending check
        # decides the insert (merge_requests_pending), and a rename of the
        # target landing between its lookup and the insert left a request
        # naming a released name
        for identity in sorted({fold(actor), fold(named)}):
            db.name_lock(db.LOCK_IDENTITY, identity)
        person = resolve_teammate(target, actor, "target", allow_team=False)
        if not person or fold(person) != fold(named):
            raise ValueError("Name the other account, not this one.")
        # a lapsed request would block a new one (merge_requests_pending)
        db.execute(
            "UPDATE merge_requests SET status = 'cancelled', settled_at = ?"
            " WHERE source = ? AND status = 'pending' AND created_at < ?",
            (db.now(), actor, _expired_before()),
        )
        if db.query_one(
            "SELECT 1 FROM merge_requests WHERE source = ? AND status = 'pending'", (actor,)
        ):
            raise ValueError("A merge request from this account already waits. Cancel it first.")
        # refused here, not at confirm: there the request stayed pending and
        # blocked every later one (users.rename_user refuses the same pair)
        if db.query_one(
            "SELECT 1 FROM oidc_identities source JOIN users su ON su.id = source.user_id"
            " JOIN oidc_identities target ON target.issuer = source.issuer"
            " JOIN users tu ON tu.id = target.user_id WHERE su.name = ? AND tu.name = ?",
            (actor, person),
        ):
            raise ValueError(
                "These accounts sign in as different people at the same identity provider."
                " They cannot be merged."
            )
        rid = db.execute(
            "INSERT INTO merge_requests (source, target, status, created_at)"
            " VALUES (?, ?, 'pending', ?) RETURNING id",
            (actor, person, db.now()),
        )
        db.log_activity(actor, "request_merge", f"#{rid}")
        # the account's own person hears of it: a stolen key can file this
        notify(
            actor,
            f"A request to merge this account into {person} was filed from this account."
            " If you did not file it, cancel it in Settings and revoke your keys.",
            tier="immediate",
            link="/settings",
        )
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
        if row["created_at"] < _expired_before():
            raise ValueError(
                f"This merge request is older than {EXPIRY_DAYS} days."
                " Ask again from the other account."
            )
        active = db.query(
            "SELECT name FROM users WHERE name IN (?, ?) AND active = 1",
            (row["source"], row["target"]),
        )
        if len(active) != 2:
            raise ValueError("A deactivated account cannot be merged by request.")
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
        " WHERE status = 'pending' AND (source = ? OR target = ?) AND created_at >= ?"
        " ORDER BY id",
        (person, person, _expired_before()),
    )
    return {
        "outgoing": [r for r in rows if r["source"] == person],
        "incoming": [r for r in rows if r["target"] == person],
    }
