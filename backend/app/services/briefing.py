"""My Day / attention inbox: pure SQL, answers "what changed and what needs me?"
in one call. An LLM narrative can be layered on top later (see digest.py)."""

from datetime import datetime, timedelta, timezone

from .. import db


def my_day(user: str) -> dict:
    # UTC dates to match db.now() timestamps on the rows
    utc_today = datetime.now(timezone.utc).date()
    today = utc_today.isoformat()
    week = (utc_today + timedelta(days=7)).isoformat()
    yesterday = (utc_today - timedelta(days=1)).isoformat()

    return {
        "user": user,
        "date": today,
        "needs_you": {
            "open_questions": db.query(
                "SELECT * FROM questions WHERE status = 'open' AND assigned_to = ? ORDER BY id",
                (user,),
            ),
            "pending_reviews": db.query(
                "SELECT id, entity, action, summary, proposed_by, created_at"
                " FROM pending_changes WHERE status = 'pending' ORDER BY id"
            ),
            "your_blockers": db.query(
                "SELECT * FROM blockers WHERE status != 'resolved' AND owner = ? ORDER BY created_at",
                (user,),
            ),
            "intake_to_triage": db.query(
                "SELECT id, title, requester, status, score FROM intake_requests"
                " WHERE status IN ('submitted', 'scored') ORDER BY score DESC LIMIT 10"
            ),
            "notifications": db.query(
                "SELECT * FROM notifications WHERE user IN (?, 'team') AND read_at IS NULL"
                " ORDER BY id DESC LIMIT 20",
                (user,),
            ),
        },
        "your_work": {
            "tasks": db.query(
                "SELECT * FROM tasks WHERE assignee = ? AND status IN ('todo', 'in_progress', 'blocked')"
                " ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1"
                " WHEN 'medium' THEN 2 ELSE 3 END, due_date IS NULL, due_date",
                (user,),
            ),
            "due_soon": db.query(
                "SELECT * FROM tasks WHERE status != 'done' AND due_date IS NOT NULL"
                " AND due_date <= ? ORDER BY due_date",
                (week,),
            ),
        },
        "team": {
            "recently_shipped": db.query(
                "SELECT id, name, closed_at FROM engagements WHERE status = 'closed'"
                " AND closed_at >= ?",
                ((utc_today - timedelta(days=2)).isoformat(),),
            ),
            "escalated_blockers": db.query(
                "SELECT * FROM blockers WHERE status = 'escalated' ORDER BY created_at"
            ),
            "todays_events": db.query(
                "SELECT * FROM events WHERE starts_at >= ? AND starts_at < ? ORDER BY starts_at",
                (today, (utc_today + timedelta(days=1)).isoformat()),
            ),
            "recent_activity": db.query(
                "SELECT * FROM activity WHERE created_at >= ? ORDER BY id DESC LIMIT 20",
                (yesterday,),
            ),
        },
    }


def attention_count(user: str) -> int:
    row = db.query_one(
        "SELECT"
        " (SELECT COUNT(*) FROM questions WHERE status = 'open' AND assigned_to = ?)"
        " + (SELECT COUNT(*) FROM pending_changes WHERE status = 'pending')"
        " + (SELECT COUNT(*) FROM blockers WHERE status != 'resolved' AND owner = ?)"
        " + (SELECT COUNT(*) FROM notifications WHERE user IN (?, 'team') AND read_at IS NULL)"
        " AS n",
        (user, user, user),
    )
    return row["n"] if row else 0
