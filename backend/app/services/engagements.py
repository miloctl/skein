"""Engagements: the strike-team unit of work, with capacity allocations and
lessons captured at retro time."""

from .. import db
from .search import index_record

STATUSES = ("proposed", "active", "closing", "closed")


def create_engagement(name: str, project_class: str = "general", summary: str = "",
                      lead: str = "", *, actor: str = "system", origin: str = "human") -> dict:
    if db.query_one("SELECT id FROM engagements WHERE name = ?", (name,)):
        raise ValueError(f"engagement '{name}' already exists")
    ts = db.now()
    eid = db.execute(
        "INSERT INTO engagements (name, project_class, summary, lead, started_at,"
        " origin, created_by, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (name, project_class, summary, lead, ts, origin, actor, ts, ts),
    )
    db.log_activity(actor, "create_engagement", f"#{eid} {name} [{project_class}]")
    index_record("engagement", eid, name, f"{summary} {project_class} {lead}")
    return {"id": eid, "name": name, "project_class": project_class, "status": "active"}


def update_engagement(engagement_id: int, status: str = "", summary: str = "",
                      lead: str = "", *, actor: str = "system", origin: str = "human") -> dict:
    if status and status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    current = db.query_one("SELECT status FROM engagements WHERE id = ?", (engagement_id,))
    if not current:
        raise ValueError(f"engagement #{engagement_id} not found")
    freshly_closed = status == "closed" and current["status"] != "closed"
    fields = {k: v for k, v in
              [("status", status), ("summary", summary), ("lead", lead)] if v}
    if not fields:
        raise ValueError("nothing to update")
    if freshly_closed:
        fields["closed_at"] = db.now()  # re-closing must not re-fire ship-it
    sets = ", ".join(f"{k} = ?" for k in fields)
    db.execute(
        f"UPDATE engagements SET {sets}, updated_at = ? WHERE id = ?",
        (*fields.values(), db.now(), engagement_id),
    )
    db.log_activity(actor, "update_engagement", f"#{engagement_id} {status or 'edited'}")
    if freshly_closed:
        _ship_it(engagement_id, actor=actor)
    return {"id": engagement_id, "updated": list(fields)}


def _ship_it(engagement_id: int, *, actor: str) -> None:
    """The Ship It moment: recap card + team notification when an engagement
    closes. Deterministic — all counts from SQL."""
    eng = db.query_one("SELECT * FROM engagements WHERE id = ?", (engagement_id,))
    if not eng:
        return
    name = eng["name"]
    days = ""
    if eng["started_at"] and eng["closed_at"]:
        delta = (db.query_one(
            "SELECT ROUND(julianday(?) - julianday(?)) AS d",
            (eng["closed_at"], eng["started_at"]))or {}).get("d")
        days = f"{int(delta)} days" if delta is not None else ""
    stats = {
        "milestones": db.query_one(
            "SELECT COUNT(*) AS n FROM milestones WHERE project = ?", (name,)),
        "tasks_done": db.query_one(
            "SELECT COUNT(*) AS n FROM tasks t JOIN milestones m ON m.id = t.milestone_id"
            " WHERE m.project = ? AND t.status = 'done'", (name,)),
        # scoped to this engagement's lifetime — the recap must be honest
        "blockers_survived": db.query_one(
            "SELECT COUNT(*) AS n FROM blockers WHERE status = 'resolved'"
            " AND created_at >= ? AND created_at <= ?",
            (eng["started_at"] or "0", eng["closed_at"] or "9")),
    }
    recap = (
        f"🚢🪿 **Shipped: {name}**"
        + (f" — {days}" if days else "")
        + f" · {stats['milestones']['n']} milestones"
        + f" · {stats['tasks_done']['n']} tasks done"
        + f" · {stats['blockers_survived']['n']} blockers survived"
    )
    from .collab import save_note
    from .notifications import notify

    save_note(topic=f"shipped-{name}", content=recap, author=actor,
              actor=actor, origin="human")
    notify("team", recap, tier="immediate", link="/dashboard")


def list_engagements(status: str = "") -> list[dict]:
    if status:
        rows = db.query("SELECT * FROM engagements WHERE status = ? ORDER BY id DESC", (status,))
    else:
        rows = db.query("SELECT * FROM engagements ORDER BY status = 'closed', id DESC")
    for r in rows:
        r["allocations"] = db.query(
            "SELECT person, percent, starts_on, ends_on FROM allocations WHERE engagement_id = ?",
            (r["id"],),
        )
    return rows


def allocate(person: str, engagement_id: int, percent: int = 100,
             starts_on: str = "", ends_on: str = "",
             *, actor: str = "system", origin: str = "human") -> dict:
    if not 1 <= percent <= 100:
        raise ValueError("percent must be 1-100")
    if not db.query_one("SELECT id FROM engagements WHERE id = ?", (engagement_id,)):
        raise ValueError(f"engagement #{engagement_id} not found")
    aid = db.execute(
        "INSERT INTO allocations (person, engagement_id, percent, starts_on, ends_on, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (person, engagement_id, percent, starts_on or None, ends_on or None, db.now()),
    )
    db.log_activity(actor, "allocate", f"{person} -> engagement #{engagement_id} @{percent}%")
    return {"id": aid, "person": person, "percent": percent}


def capacity() -> list[dict]:
    """Total allocation per person across non-closed engagements; >100 = overcommitted."""
    return db.query(
        "SELECT a.person, SUM(a.percent) AS total_percent,"
        " GROUP_CONCAT(e.name || ' (' || a.percent || '%)', ', ') AS detail"
        " FROM allocations a JOIN engagements e ON e.id = a.engagement_id"
        " WHERE e.status != 'closed'"
        " GROUP BY a.person ORDER BY total_percent DESC"
    )


def record_lesson(lesson: str, recommendation: str = "", engagement_id: int = 0,
                  project_class: str = "general",
                  *, actor: str = "system", origin: str = "human") -> dict:
    if engagement_id and not db.query_one(
            "SELECT id FROM engagements WHERE id = ?", (engagement_id,)):
        raise ValueError(f"engagement #{engagement_id} not found")
    lid = db.execute(
        "INSERT INTO lessons (engagement_id, project_class, lesson, recommendation,"
        " origin, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (engagement_id or None, project_class, lesson, recommendation, origin, actor, db.now()),
    )
    db.log_activity(actor, "record_lesson", f"#{lid} [{project_class}]")
    index_record("lesson", lid, lesson[:120], f"{lesson} {recommendation}")
    return {"id": lid, "project_class": project_class}


def list_lessons(project_class: str = "") -> list[dict]:
    if project_class:
        return db.query(
            "SELECT * FROM lessons WHERE project_class = ? ORDER BY id DESC", (project_class,)
        )
    return db.query("SELECT * FROM lessons ORDER BY id DESC LIMIT 100")
