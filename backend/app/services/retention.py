"""Monthly retention pruning. activity is the provenance ledger — kept
forever; everything pruned here is derivable telemetry or already-consumed
claims/notifications."""

from datetime import UTC, datetime, timedelta

from .. import db
from . import wording

# every key of `removed` needs an entry: a missing one raises KeyError while
# building the feed sentence, which is louder and earlier than shipping a
# table name to a reader
PRUNE_LABEL = {
    "forecast_snapshots": "forecast snapshot",
    "health_snapshots": "health snapshot",
    "notifications": "read notification",
    "job_runs": "job run",
    "job_outcomes": "job outcome",
    "mention_log": "orphan mention record",
    "extension_outbox": "delivered extension event",
}

FORECAST_SNAPSHOT_DAYS = 365
READ_NOTIFICATION_DAYS = 90
JOB_ROW_DAYS = 90
EXTENSION_EVENT_DAYS = 90

# Every public table carries one recorded retention decision: pruned here
# (PRUNE_LABEL), pruned by cascade with its parent (CASCADED), or kept with
# the reason (KEPT). tests/test_retention.py enumerates the live schema
# against these maps, so a new table fails CI until its migration author
# records the decision — silence would default it to kept-forever unread.
_USER_DELETED = "user-deleted content: removal is a person's decision, never an age prune"
_CHAT_LIFECYCLE = "chat lifecycle owns deletion (delete_thread and folder operations)"
_DERIVED = "derived from content: rebuilt on demand, rows leave with their entity"
KEPT = {
    "activity": "hash-chained provenance ledger, kept forever",
    "usage_log": "cost history nothing else reconstructs, kept forever",
    "flock_traces": "per-turn token counts usage_log cannot reconstruct",
    "tool_usage": "one row per day/user/surface; the adoption trend is the read",
    "schema_version": "migration receipts the boot depends on",
    "users": "the roster: deactivation, never deletion, keeps provenance resolvable",
    "api_keys": "credential audit rows: revocation deactivates in place",
    "app_settings": "admin-set values: the settings form owns their lifecycle",
    "agent_authority": "the authority matrix the tool gate reads",
    "feature_unlocks": "one row per unlocked feature",
    "pending_changes": "review provenance beside the ledger",
    "extension_review_invocations": "execution outcome of a reviewed remote write",
    "extension_command_receipts": "extension write receipts: provenance",
    "memories": "owner-forgettable (memory.forget), never age-pruned",
    "search_index": _DERIVED,
    "embeddings": _DERIVED,
    "context_packs": "published pack registry: the pack writer prunes its archives",
    **dict.fromkeys(
        (
            "chat_folders",
            "chat_threads",
            "chat_messages",
            "sessions",
            "session_agents",
            "session_messages",
            "session_multi_agents",
        ),
        _CHAT_LIFECYCLE,
    ),
    **dict.fromkeys(
        (
            "tasks",
            "notes",
            "questions",
            "decisions",
            "blockers",
            "milestones",
            "engagements",
            "promises",
            "standups",
            "task_worklog",
            "absences",
            "allocations",
            "events",
            "intake_requests",
            "lessons",
            "artifacts",
            "findings",
            "finding_dispositions",
            "feedback",
            "crews",
            "crew_members",
        ),
        _USER_DELETED,
    ),
}
# child table -> the pruned-or-kept parent whose ON DELETE CASCADE removes it.
# The test verifies the cascade exists in the live schema, so this map cannot
# claim a cleanup the database does not perform.
CASCADED = {
    "extension_event_attempts": "extension_outbox",
    "extension_event_deliveries": "extension_outbox",
    "notification_reads": "notifications",
}


def _cutoff(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")


def prune(*, actor: str = "scheduler") -> dict:
    # the TEAM month, matching the local 1st-of-month the scheduler now fires
    # on (config.TZ_NAME). Keyed on the UTC month, a zone more than 4 hours
    # east of UTC computes the PREVIOUS month at 04:00 local on the 1st — the
    # key is already claimed, the prune silently never runs, and the trigger
    # does not come back for a month.
    month = db.today().isoformat()[:7]
    if not db.claim_job("retention-prune", month):
        return {"skipped": "already pruned this month"}
    # tool_usage is deliberately absent: one row per (day, user, surface), so
    # a year of a ten-person team is a few thousand rows, and the adoption
    # trend is the read it exists for — pruning it deletes the trend.
    # usage_log is deliberately absent from this list: it is the platform's
    # cost history (spend per thread and engagement over time), it is not
    # derivable from anything else, and its ranged reads ride
    # idx_usage_log_created — kept forever, like activity.
    # flock_traces is absent for the same reason: it carries the per-turn token
    # counts that usage_log cannot reconstruct (usage rows key on thread +
    # agent, so two flock turns in one thread are indistinguishable there). It
    # is bounded in practice by the chat cap, and chat_threads.delete_thread
    # removes a thread's traces with the thread.
    removed = {
        "forecast_snapshots": db.execute_rowcount(
            "DELETE FROM forecast_snapshots WHERE created_at < ?",
            (_cutoff(FORECAST_SNAPSHOT_DAYS),),
        ),
        # the same one-row-per-entity-per-day growth, and the same horizon:
        # the readout compares back weeks, never years
        "health_snapshots": db.execute_rowcount(
            "DELETE FROM health_snapshots WHERE created_at < ?",
            (_cutoff(FORECAST_SNAPSHOT_DAYS),),
        ),
        # "read" means read_at for a personal row and a `notification_reads`
        # row for a 'team' one (009). A prune that tests read_at alone makes
        # every team announcement immortal — mark_read never stamps that
        # column on a shared record — and drags its dismissal rows along with
        # it, so the two tables grow together and forever. A team row is
        # prunable once EVERY active human has dismissed it: one straggler
        # keeps it, which is the same promise the unread query makes them.
        "notifications": db.execute_rowcount(
            "DELETE FROM notifications WHERE created_at < ? AND ("
            " (\"user\" != 'team' AND read_at IS NOT NULL)"
            # read_at counts for a team row too. `mark_read_matching` stamps it
            # when the THING a notification points at is settled — a fact about
            # the world, not about one reader — and that write also hides the
            # row from every unread list, so nobody can ever add the per-person
            # dismissal the arm below waits for. Without this disjunct every
            # "Review needed: #N" notification the product sends is permanent.
            " OR (\"user\" = 'team' AND read_at IS NOT NULL)"
            " OR (\"user\" = 'team' AND NOT EXISTS ("
            "   SELECT 1 FROM users u WHERE u.kind = 'human' AND u.active = 1"
            "   AND u.name != 'anonymous'"
            "   AND NOT EXISTS (SELECT 1 FROM notification_reads r"
            '     WHERE r.notification_id = notifications.id AND r."user" = u.name)))'
            ")",
            (_cutoff(READ_NOTIFICATION_DAYS),),
        ),
        # This also expires capture idempotency receipts (`capture:<user>`),
        # so an outbox row re-sent after the horizon files a duplicate —
        # at-least-once, the safe direction, and old enough to notice.
        "job_runs": db.execute_rowcount(
            "DELETE FROM job_runs WHERE created_at < ?", (_cutoff(JOB_ROW_DAYS),)
        ),
        "job_outcomes": db.execute_rowcount(
            "DELETE FROM job_outcomes WHERE created_at < ?", (_cutoff(JOB_ROW_DAYS),)
        ),
        # pending too: zero-composition dispatch leaves rows pending on
        # purpose (a disabled extension's backlog survives re-enable), so on
        # the core-only default deployment nothing else ever finalizes them
        # and the table grew without bound.
        "extension_outbox": db.execute_rowcount(
            "DELETE FROM extension_outbox"
            " WHERE status IN ('delivered', 'dead', 'pending') AND created_at < ?",
            (_cutoff(EXTENSION_EVENT_DAYS),),
        ),
        # orphans only, never by age: the (entity, entity_id, person) key is
        # the notify-once promise, and an age prune would let an edit of an
        # old row ping the same person again. AUTOINCREMENT ids never come
        # back, so an orphan can never suppress a mention on a future row.
        "mention_log": db.execute_rowcount(
            "DELETE FROM mention_log WHERE"
            " (entity = 'task' AND entity_id NOT IN (SELECT id FROM tasks))"
            " OR (entity = 'note' AND entity_id NOT IN (SELECT id FROM notes))"
            " OR (entity = 'question' AND entity_id NOT IN (SELECT id FROM questions))"
            " OR (entity = 'decision' AND entity_id NOT IN (SELECT id FROM decisions))"
        ),
    }
    # this detail renders verbatim in the My Day feed, so it is a sentence,
    # not a payload — json.dumps put a raw dict in front of every reader
    gone = [wording.count(n, PRUNE_LABEL[table]) for table, n in removed.items() if n]
    db.log_activity(
        actor,
        "retention_prune",
        ", ".join(gone) if gone else "nothing old enough to remove",
    )
    return removed
