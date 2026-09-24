"""What only one person can read: counted, and erased once they leave.

One inventory (holdings) serves the merge refusal
(users._holds_personal_data), the erasure of a departed person, and the
person's own summary of what Skein holds about them. Three lists would drift,
and the drift is a merge that carries data nobody counted, or an erasure that
leaves it behind.

A deactivated account keeps everything for GRACE_DAYS, so a wrong
deactivation can be undone. Then the daily erase job deletes what only that
person could read. Records shared with a crew or the team stay, with the
name: the activity ledger names the person forever and cannot be rewritten,
so removing the name from the rows would hide it in one place while the
ledger still shows it."""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .. import db
from . import artifact_files, scope

log = logging.getLogger("skein.erasure")

GRACE_DAYS = 30

# Children before the rows they point at: tasks, milestones, memories and
# chat_threads reference engagements with NO ACTION (core_migrations), so
# engagements go last, after the links other rows keep to them are cleared.
# tests/test_erasure.py checks this names every scope.CLASSIFIED table.
_ORDER = (
    "task_worklog",
    "blockers",
    "promises",
    "questions",
    "decisions",
    "standups",
    "notes",
    "events",
    "absences",
    "lessons",
    "intake_requests",
    "tasks",
    "milestones",
    "memories",
    "artifacts",
    "engagements",
)
_ENGAGEMENT_LINKS = ("tasks", "milestones", "memories", "chat_threads")

# kinds a merge carries into the target account. Notifications are deleted
# by an administrator's merge, and the 1:1 journal has its own refusal
# (users.rename_user), so neither decides the merge refusal. Rooms left to
# one member and folder names were never part of that refusal either.
MERGE_CARRIED = frozenset({*scope.CLASSIFIED, "solo_chats", "private_proposals", "mcp_servers"})


def _private_rows(table: str, name: str) -> tuple[str, tuple]:
    if table == "memories":
        # addressed to the person at any tier: that person's alone
        # (review._addressed, search.visible_hits)
        return 'SELECT id FROM memories WHERE "user" = ?', (name,)
    column = scope.CLASSIFIED[table]
    return (
        f'SELECT id FROM {table} WHERE visibility = ? AND "{column}" = ?',  # noqa: S608 — table and column from scope.CLASSIFIED
        (scope.PRIVATE, name),
    )


def _queries(name: str) -> dict[str, tuple[str, tuple]]:
    queries = {table: _private_rows(table, name) for table in scope.CLASSIFIED}
    queries["solo_chats"] = (
        "SELECT id FROM chat_threads WHERE owner = ? AND kind = 'solo'",
        (name,),
    )
    # a room whose other people all left, with no invitation pending, is
    # readable by this person alone: an invitation accepted later would
    # grant the whole transcript, so a pending one keeps the room
    queries["rooms_alone"] = (
        "SELECT t.id FROM chat_threads t JOIN chat_members m ON m.thread_id = t.id"
        " WHERE t.kind = 'shared' AND m.person = ? AND m.left_at IS NULL"
        " AND NOT EXISTS (SELECT 1 FROM chat_members o JOIN users u ON u.name = o.person"
        "   WHERE o.thread_id = t.id AND o.person != m.person AND o.left_at IS NULL"
        "   AND u.kind = 'human')"
        " AND NOT EXISTS (SELECT 1 FROM chat_invitations i"
        "   WHERE i.thread_id = t.id AND i.status = 'pending')",
        (name,),
    )
    queries["chat_folders"] = ("SELECT name AS id FROM chat_folders WHERE owner = ?", (name,))
    # 'private' only: a crew review names an owner too, and its crew reads and
    # judges it (review._governing_tier)
    queries["private_proposals"] = (
        "SELECT id FROM pending_changes WHERE review_owner = ? AND review_visibility = 'private'",
        (name,),
    )
    queries["mcp_servers"] = ("SELECT id FROM mcp_servers WHERE owner = ?", (name,))
    queries["notifications"] = ('SELECT id FROM notifications WHERE "user" = ?', (name,))
    return queries


def holdings(name: str) -> dict[str, int]:
    """Counts, by kind, of what only `name` can read. Counts and no content:
    a caller learns how much, never what."""
    from .private_notes import author_note_count

    counts = {
        kind: int(db.query_row(f"SELECT COUNT(*) AS n FROM ({sql}) AS held", params)["n"])  # noqa: S608 — sql from _queries
        for kind, (sql, params) in _queries(name).items()
    }
    counts["journal_notes"] = author_note_count(name)
    return counts


def erase_on(deactivated_at: str) -> str:
    """The UTC date the erase job first erases an account deactivated then.
    _due_before makes that the day of the first erase, so the roster and the
    deactivate confirmation can say "on"."""
    return (datetime.fromisoformat(deactivated_at) + timedelta(days=GRACE_DAYS)).date().isoformat()


def _due_before() -> str:
    """Deactivations earlier than this are due today. erase_on(d) <= today
    holds exactly when d is before the start of the UTC day GRACE_DAYS - 1
    days ago."""
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return (start - timedelta(days=GRACE_DAYS - 1)).isoformat(timespec="seconds")


def erase(name: str, *, actor: str = "scheduler") -> dict[str, int]:
    """Delete what only `name` could read, and return what went, by kind.
    Empty when the account is not due or holds nothing.

    The due check runs again under the identity lock: erase_due reads its
    list before any lock, and a reactivation between the two must keep the
    account's data."""
    from . import chat_threads, private_notes, users
    from .search import deindex_record

    with db.transaction():
        db.name_lock(db.LOCK_IDENTITY, users.fold(name))
        # the quota lock uploads.delete_upload takes, after the identity lock
        # that rename_user takes first
        db.name_lock(db.LOCK_UPLOAD, name)
        person = db.query_one("SELECT * FROM users WHERE name = ? FOR UPDATE", (name,))
        if (
            not person
            or person["kind"] != "human"
            or person["active"]
            or not person["deactivated_at"]
            or person["deactivated_at"] >= _due_before()
        ):
            return {}
        queries = _queries(name)
        chats = [str(r["id"]) for r in db.query(*queries["solo_chats"])]
        rooms = [str(r["id"]) for r in db.query(*queries["rooms_alone"])]
        for thread_id in (*chats, *rooms):
            chat_threads.remove_thread(thread_id)
        erased: dict[str, int] = {"solo_chats": len(chats), "rooms_alone": len(rooms)}
        erased["chat_folders"] = db.execute_rowcount(
            "DELETE FROM chat_folders WHERE owner = ?", (name,)
        )
        for table in _ORDER:
            ids = [int(r["id"]) for r in db.query(*queries[table])]
            if not ids:
                continue
            if table == "engagements":
                for link in _ENGAGEMENT_LINKS:
                    db.execute(
                        f"UPDATE {link} SET engagement_id = NULL WHERE engagement_id = ANY(?)",  # noqa: S608 — table from _ENGAGEMENT_LINKS
                        (ids,),
                    )
            rows = db.query(
                f"DELETE FROM {table} WHERE id = ANY(?) RETURNING *",  # noqa: S608 — table from _ORDER
                (ids,),
            )
            if table == "artifacts":
                for row in rows:
                    if not row["path"]:
                        continue
                    try:
                        artifact_files.delete_after_commit(Path(row["path"]))
                    except RuntimeError:
                        # a stored path outside the artifact root (a moved or
                        # restored volume) must not keep every other row alive
                        log.warning("erase: artifact #%s is outside the artifact root", row["id"])
            if table == "memories":
                for row in rows:
                    deindex_record("memory", int(row["id"]))
            erased[table] = len(rows)
        proposals = [int(r["id"]) for r in db.query(*queries["private_proposals"])]
        if proposals:
            db.execute(
                "DELETE FROM extension_review_invocations WHERE change_id = ANY(?)", (proposals,)
            )
            erased["private_proposals"] = db.execute_rowcount(
                "DELETE FROM pending_changes WHERE id = ANY(?)", (proposals,)
            )
        erased["notifications"] = db.execute_rowcount(
            'DELETE FROM notifications WHERE "user" = ?', (name,)
        )
        db.execute('DELETE FROM notification_reads WHERE "user" = ?', (name,))
        erased["mcp_servers"] = db.execute_rowcount(
            "DELETE FROM mcp_servers WHERE owner = ?", (name,)
        )
        erased["journal_notes"] = private_notes.erase_author(name)
        # growth interests the person never shared, and their own theme
        db.execute(
            "UPDATE users SET erased_at = COALESCE(erased_at, ?), theme = '',"
            " growth_interests = CASE WHEN growth_shared THEN growth_interests ELSE '' END"
            " WHERE id = ?",
            (db.now(), person["id"]),
        )
        total = sum(erased.values())
        # a later run finds only what reached the account since, often nothing
        if total:
            db.log_activity(actor, "erase_private_data", f"{name}: {total} record(s)")
    return {kind: n for kind, n in erased.items() if n}


def erase_due() -> dict:
    """Erase every account whose erase date has come, and again on every
    later day: a notification, a memory addressed to the person, or a room
    whose other members left can reach the account after its first erase.

    Each person is their own transaction, so one failure leaves the others
    erased. The failure still fails the job, which records it (jobs.run_job)
    and retries tomorrow."""
    due = db.query(
        "SELECT name FROM users WHERE kind = 'human' AND active = 0"
        " AND deactivated_at IS NOT NULL AND deactivated_at < ? ORDER BY id",
        (_due_before(),),
    )
    erased = failed = 0
    for row in due:
        try:
            erased += bool(erase(str(row["name"])))
        except Exception:
            log.exception("erasing a deactivated account failed")
            failed += 1
    if failed:
        raise RuntimeError(f"{failed} of {len(due)} deactivated accounts could not be erased.")
    return {"erased": erased}
