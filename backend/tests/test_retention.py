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
        "INSERT INTO notifications (\"user\", message, read_at, created_at) VALUES ('a', 'm', ?, ?)",
        (old, old),
    )
    fresh_db.execute(
        "INSERT INTO notifications (\"user\", message, created_at) VALUES ('a', 'unread', ?)",
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


def test_prune_logs_a_sentence_not_a_payload(fresh_db):
    """The activity detail renders verbatim in the My Day feed, so a
    reader on the landing page saw a raw dict: {"forecast_snapshots": 0,
    "notifications": 0, ...}. It must be a sentence, and it must count."""
    from app.services import retention

    retention.prune(actor="scheduler")
    detail = fresh_db.query_row("SELECT detail FROM activity WHERE action = 'retention_prune'")[
        "detail"
    ]
    assert not detail.startswith("{") and ":" not in detail, detail
    assert detail == "nothing old enough to remove"  # empty database
