"""Monthly retention pruning. The activity ledger is kept forever."""

from datetime import UTC, datetime, timedelta


def _iso_hours_ago(hours: int) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat(timespec="seconds")


def test_retention_prune(fresh_db):
    from app.services.retention import prune

    old = _iso_hours_ago(24 * 400)
    fresh_db.execute(
        "INSERT INTO forecast_snapshots (day, milestone_id, due_date, forecast_date, created_at)"
        " VALUES ('2025-01-01', 1, '2025-01-01', '2025-01-01', ?)",
        (old,),
    )
    fresh_db.execute(
        "INSERT INTO notifications (user, message, read_at, created_at) VALUES ('a', 'm', ?, ?)",
        (old, old),
    )
    fresh_db.execute(
        "INSERT INTO notifications (user, message, created_at) VALUES ('a', 'unread', ?)",
        (old,),
    )
    fresh_db.execute(
        "INSERT INTO job_runs (job, run_key, created_at) VALUES ('digest', '2025-01-01', ?)",
        (old,),
    )
    removed = prune(actor="tester")
    assert removed["forecast_snapshots"] == 1
    assert removed["notifications"] == 1  # unread rows are never pruned
    assert removed["job_runs"] == 1
    assert prune(actor="tester") == {"skipped": "already pruned this month"}
    assert fresh_db.query_row("SELECT COUNT(*) AS n FROM notifications")["n"] == 1
