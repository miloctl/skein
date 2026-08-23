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


def test_retention_accounts_for_every_table(fresh_db):
    """A migration decides each new table's retention fate explicitly —
    an unrecorded table silently defaults to kept-forever."""
    from app.services.retention import CASCADED, KEPT, PRUNE_LABEL

    rows = fresh_db.query(
        "SELECT table_name AS name FROM information_schema.tables"
        " WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
    )
    real = {r["name"] for r in rows}
    pruned, kept, cascaded = set(PRUNE_LABEL), set(KEPT), set(CASCADED)

    undecided = real - pruned - kept - cascaded
    assert not undecided, f"tables with no recorded retention decision: {sorted(undecided)}"
    ghosts = (pruned | kept | cascaded) - real
    assert not ghosts, f"retention maps name tables that do not exist: {sorted(ghosts)}"
    doubled = (pruned & kept) | (pruned & cascaded) | (kept & cascaded)
    assert not doubled, f"tables with two retention decisions: {sorted(doubled)}"

    # a cascade claim needs a real parent decision and a real cascade —
    # otherwise the map documents a cleanup the database does not perform
    orphaned = set(CASCADED.values()) - pruned - kept
    assert not orphaned, f"cascade parents with no decision of their own: {sorted(orphaned)}"
    live_cascades = {
        (r["child"], r["parent"])
        for r in fresh_db.query(
            "SELECT tc.table_name AS child, ccu.table_name AS parent"
            " FROM information_schema.table_constraints tc"
            " JOIN information_schema.referential_constraints rc"
            "   ON rc.constraint_name = tc.constraint_name"
            " JOIN information_schema.constraint_column_usage ccu"
            "   ON ccu.constraint_name = tc.constraint_name"
            " WHERE tc.constraint_type = 'FOREIGN KEY' AND rc.delete_rule = 'CASCADE'"
        )
    }
    for child, parent in CASCADED.items():
        assert (child, parent) in live_cascades, (
            f"{child} claims cascade cleanup from {parent}, but no ON DELETE CASCADE exists"
        )
