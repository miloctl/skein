"""Monthly retention pruning. activity is the provenance ledger — kept
forever; everything pruned here is derivable telemetry or already-consumed
claims/notifications."""

import json
from datetime import datetime, timedelta, timezone

from .. import db

FORECAST_SNAPSHOT_DAYS = 365
READ_NOTIFICATION_DAYS = 90
JOB_ROW_DAYS = 90


def _cutoff(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def prune(*, actor: str = "scheduler") -> dict:
    month = db.now()[:7]
    if not db.claim_job("retention-prune", month):
        return {"skipped": "already pruned this month"}
    removed = {
        "forecast_snapshots": db.execute_rowcount(
            "DELETE FROM forecast_snapshots WHERE created_at < ?",
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
    }
    db.log_activity(actor, "retention_prune", json.dumps(removed))
    return removed
