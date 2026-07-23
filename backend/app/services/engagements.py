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
    fields = {k: v for k, v in
              [("status", status), ("summary", summary), ("lead", lead)] if v}
    if not fields:
        raise ValueError("nothing to update")
    if status == "closed":
        fields["closed_at"] = db.now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    db.execute(
        f"UPDATE engagements SET {sets}, updated_at = ? WHERE id = ?",
        (*fields.values(), db.now(), engagement_id),
    )
    db.log_activity(actor, "update_engagement", f"#{engagement_id} {status or 'edited'}")
    return {"id": engagement_id, "updated": list(fields)}


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
