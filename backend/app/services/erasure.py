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
# (users.rename_user), so neither decides the merge refusal.
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
    queries["private_proposals"] = (
        "SELECT id FROM pending_changes WHERE review_owner = ?"
        " AND review_visibility != 'workspace'",
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
    """The UTC date the erase job erases an account deactivated then."""
    return (datetime.fromisoformat(deactivated_at) + timedelta(days=GRACE_DAYS)).date().isoformat()


def erase(name: str, *, actor: str = "scheduler") -> dict[str, int]:
    """Delete what only `name` could read. Refuses an active account: the
    grace period and the deactivation are what make this safe to run."""
    from . import chat_threads, private_notes, users
    from .search import deindex_record

    with db.transaction():
        db.name_lock(db.LOCK_IDENTITY, users.fold(name))
        # the quota lock uploads.delete_upload takes, after the identity lock
        # that rename_user takes first
        db.name_lock(db.LOCK_UPLOAD, name)
        person = db.query_one("SELECT * FROM users WHERE name = ? FOR UPDATE", (name,))
        if not person or person["kind"] != "human" or person["active"]:
            raise ValueError("Only a deactivated person's data can be erased.")
        queries = _queries(name)
        chats = [str(r["id"]) for r in db.query(*queries["solo_chats"])]
        for thread_id in chats:
            chat_threads.remove_thread(thread_id)
        erased: dict[str, int] = {"solo_chats": len(chats)}
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
                    if row["path"]:
                        artifact_files.delete_after_commit(Path(row["path"]))
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
            "UPDATE users SET erased_at = ?, theme = '',"
            " growth_interests = CASE WHEN growth_shared THEN growth_interests ELSE '' END"
            " WHERE id = ?",
            (db.now(), person["id"]),
        )
        total = sum(erased.values())
        db.log_activity(actor, "erase_private_data", f"{name}: {total} record(s)")
    return {kind: n for kind, n in erased.items() if n}


def erase_due() -> dict:
    """Erase every account deactivated GRACE_DAYS ago or more. Each person
    is their own transaction, so one failure leaves the others erased; the
    failure still fails the job, which records it (jobs.run_job) and retries
    it tomorrow."""
    cutoff = (datetime.now(UTC) - timedelta(days=GRACE_DAYS)).isoformat(timespec="seconds")
    due = db.query(
        "SELECT name FROM users WHERE kind = 'human' AND active = 0 AND erased_at IS NULL"
        " AND deactivated_at IS NOT NULL AND deactivated_at <= ? ORDER BY id",
        (cutoff,),
    )
    failed = 0
    for row in due:
        try:
            erase(str(row["name"]))
        except Exception:
            log.exception("erasing a deactivated account failed")
            failed += 1
    if failed:
        raise RuntimeError(f"{failed} of {len(due)} deactivated accounts could not be erased.")
    return {"erased": len(due)}
