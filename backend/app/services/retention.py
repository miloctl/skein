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
}

FORECAST_SNAPSHOT_DAYS = 365
READ_NOTIFICATION_DAYS = 90
JOB_ROW_DAYS = 90


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
        "notifications": db.execute_rowcount(
            "DELETE FROM notifications WHERE read_at IS NOT NULL AND created_at < ?",
            (_cutoff(READ_NOTIFICATION_DAYS),),
        ),
        "job_runs": db.execute_rowcount(
            "DELETE FROM job_runs WHERE created_at < ?", (_cutoff(JOB_ROW_DAYS),)
        ),
        "job_outcomes": db.execute_rowcount(
            "DELETE FROM job_outcomes WHERE created_at < ?", (_cutoff(JOB_ROW_DAYS),)
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
