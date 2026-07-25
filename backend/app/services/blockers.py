"""Blocker & escalation register. Escalation is programmatic: a scheduled
sweep flips open blockers past their escalate_after_hours to 'escalated'."""

from datetime import datetime, timedelta, timezone

from .. import db
from .search import index_record

IMPACTS = ("low", "medium", "high", "critical")
DEFAULT_ESCALATION_HOURS = {"low": 72, "medium": 24, "high": 8, "critical": 2}


def raise_blocker(
    title: str,
    detail: str = "",
    owner: str = "",
    impact: str = "medium",
    task_id: int = 0,
    source: str = "",
    escalate_after_hours: int = 0,
    *,
    actor: str = "system",
    origin: str = "human",
) -> dict:
    if not title.strip():
        raise ValueError("blocker title is required")
    if impact not in IMPACTS:
        raise ValueError(f"impact must be one of {IMPACTS}")
    if task_id and not db.query_one("SELECT id FROM tasks WHERE id = ?", (task_id,)):
        raise ValueError(f"task #{task_id} not found")
    hours = escalate_after_hours or DEFAULT_ESCALATION_HOURS[impact]
    ts = db.now()
    bid = db.execute(
        "INSERT INTO blockers (title, detail, owner, impact, task_id, source,"
        " escalate_after_hours, origin, created_by, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (title, detail, owner, impact, task_id or None, source, hours, origin, actor, ts, ts),
    )
    if task_id:
        from .work import update_task

        update_task(task_id, status="blocked", actor=actor, origin=origin)
    db.log_activity(actor, "raise_blocker", f"#{bid} {title}")
    index_record("blocker", bid, title, f"{detail} {owner}")
    return {"id": bid, "title": title, "status": "open", "escalate_after_hours": hours}


def resolve_blocker(
    blocker_id: int, resolution: str = "", *, actor: str = "system", origin: str = "human"
) -> dict:
    row = db.query_one("SELECT * FROM blockers WHERE id = ?", (blocker_id,))
    if not row:
        raise ValueError(f"blocker #{blocker_id} not found")
    if row["status"] == "resolved":
        raise ValueError(f"blocker #{blocker_id} is already resolved")
    db.execute(
        "UPDATE blockers SET status = 'resolved', resolved_at = ?, updated_at = ?,"
        " detail = detail || CASE WHEN ? != '' THEN char(10) || 'Resolved: ' || ? ELSE '' END"
        " WHERE id = ?",
        (db.now(), db.now(), resolution, resolution, blocker_id),
    )
    if row["task_id"]:
        # un-block the linked task that raise_blocker flipped
        db.execute_rowcount(
            "UPDATE tasks SET status = 'in_progress', updated_at = ?"
            " WHERE id = ? AND status = 'blocked'",
            (db.now(), row["task_id"]),
        )
    db.log_activity(actor, "resolve_blocker", f"#{blocker_id}")

    # tasks explicitly waiting on this blocker can move again — tell their
    # owners, or the unblock is a tree falling in an empty forest
    waiting = db.query(
        "SELECT id, title, assignee FROM tasks"
        " WHERE waiting_on_type = 'blocker' AND waiting_on_id = ?",
        (blocker_id,),
    )
    if waiting:
        from .notifications import notify

        for t in waiting:
            if t["assignee"] and t["assignee"] != actor:
                notify(
                    t["assignee"],
                    f"Blocker #{blocker_id} resolved — task #{t['id']}"
                    f" “{t['title']}” can move again.",
                    tier="immediate",
                    link="/dashboard",
                )

    created = datetime.fromisoformat(row["created_at"])
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - created
    if age >= timedelta(days=3):  # the Blocker Funeral
        from .notifications import notify

        days = age.days
        notify(
            "team",
            f"🪦 Here lies blocker #{blocker_id} “{row['title']}”."
            f" It fought hard. It lost. {days} days.",
            tier="digest",
            link="/dashboard",
        )
    return {"id": blocker_id, "status": "resolved"}


def list_blockers(status: str = "", owner: str = "") -> list[dict]:
    sql, params = "SELECT * FROM blockers WHERE 1=1", []
    if status:
        sql += " AND status = ?"
        params.append(status)
    else:
        sql += " AND status != 'resolved'"
    if owner:
        sql += " AND owner = ?"
        params.append(owner)
    sql += (
        " ORDER BY CASE impact WHEN 'critical' THEN 0 WHEN 'high' THEN 1"
        " WHEN 'medium' THEN 2 ELSE 3 END, created_at"
    )
    return db.query(sql, tuple(params))


def sweep_escalations() -> list[dict]:
    """Flip aged open blockers to escalated; called by the scheduler and tests."""
    escalated = []
    now_dt = datetime.now(timezone.utc)
    for b in db.query("SELECT * FROM blockers WHERE status = 'open'"):
        created = datetime.fromisoformat(b["created_at"])
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if now_dt - created >= timedelta(hours=b["escalate_after_hours"]):
            claimed = db.execute_rowcount(
                "UPDATE blockers SET status = 'escalated', escalated_at = ?, updated_at = ?"
                " WHERE id = ? AND status = 'open'",
                (db.now(), db.now(), b["id"]),
            )
            if not claimed:  # resolved between our read and write
                continue
            from .notifications import notify

            notify(
                b["owner"] or "team",
                f"Blocker #{b['id']} escalated: {b['title']}",
                tier="immediate",
                link="/",
            )
            db.log_activity(
                "scheduler",
                "escalate_blocker",
                f"#{b['id']} {b['title']} (open {b['escalate_after_hours']}h, owner: {b['owner'] or 'unowned'})",
            )
            escalated.append(b)
    return escalated
