"""Agents as first-class teammates: task delegation with a human sponsor,
an authority matrix per (agent, entity), a mission-control view, and trust
scores computed from the review inbox — promotion is suggested, never automatic."""

from .. import db
from .users import ensure_user

LEVELS = ("autonomous", "notify", "review", "forbidden")
TRUST_STREAK = 5  # consecutive approvals before we suggest promotion


def delegate_task(
    task_id: int, agent: str, sponsor: str, *, actor: str = "system", origin: str = "human"
) -> dict:
    if not agent.strip():
        raise ValueError("agent name is required")
    if not sponsor.strip():
        raise ValueError("every delegation needs a human sponsor")
    task = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not task:
        raise ValueError(f"task #{task_id} not found")
    ensure_user(agent, kind="agent")
    db.execute(
        "UPDATE tasks SET delegated_agent = ?, sponsor = ?, assignee = ?, updated_at = ?"
        " WHERE id = ?",
        (agent, sponsor, agent, db.now(), task_id),
    )
    db.log_activity(actor, "delegate_task", f"#{task_id} -> {agent} (sponsor: {sponsor})")
    from .notifications import notify

    notify(
        sponsor,
        f"You sponsor task #{task_id} '{task['title']}' delegated to {agent}.",
        tier="digest",
        link="/agents",
    )
    return {"id": task_id, "delegated_agent": agent, "sponsor": sponsor}


def set_authority(agent: str, entity: str, level: str, *, actor: str = "system") -> dict:
    agent = agent.strip()
    if not agent or agent == "anonymous":
        raise ValueError("agent name is required")
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}")
    # the kill switch must not be self-serviceable: an agent identity (e.g. a
    # key issued to one) can never grant or lift authority — humans only
    actor_row = db.query_one("SELECT kind FROM users WHERE name = ?", (actor,))
    if (actor_row and actor_row["kind"] == "agent") or actor == agent:
        raise ValueError("authority levels are set by humans, not by the agent itself")
    from .review import _registry

    if entity not in _registry():
        raise ValueError(f"unknown entity '{entity}'; one of {sorted(_registry())}")
    ensure_user(agent, kind="agent")
    # authority half-life: elevated grants carry a review-by date (90d
    # default) — "nothing in Skein is trusted forever, not decisions, not
    # agents." The authority_stale findings rule nags past it; reconfirm by
    # re-granting.
    review_by = None
    if level in ("autonomous", "notify"):
        from datetime import date, timedelta

        review_by = (date.fromisoformat(db.now()[:10]) + timedelta(days=90)).isoformat()
    db.execute(
        "INSERT INTO agent_authority (agent, entity, level, updated_by, updated_at, review_by)"
        " VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (agent, entity) DO UPDATE SET level = excluded.level,"
        " updated_by = excluded.updated_by, updated_at = excluded.updated_at,"
        " review_by = excluded.review_by",
        (agent, entity, level, actor, db.now(), review_by),
    )
    db.log_activity(actor, "set_authority", f"{agent}/{entity} -> {level}")
    return {"agent": agent, "entity": entity, "level": level, "review_by": review_by}


def authority_level(agent: str, entity: str) -> str:
    """Default is 'review' — new agents earn autonomy, they don't start with it."""
    row = db.query_one(
        "SELECT level FROM agent_authority WHERE agent = ? AND entity = ?",
        (agent, entity),
    )
    return row["level"] if row else "review"


def authority_matrix(agent: str = "") -> list[dict]:
    if agent:
        return db.query("SELECT * FROM agent_authority WHERE agent = ? ORDER BY entity", (agent,))
    return db.query("SELECT * FROM agent_authority ORDER BY agent, entity")


def trust_scores() -> list[dict]:
    """Approval stats per (proposer, entity) from pending_changes — every
    review verdict is already a labeled trust signal."""
    rows = db.query(
        "SELECT proposed_by AS agent, entity,"
        " COUNT(*) AS proposed,"
        " SUM(status = 'approved') AS approved,"
        " SUM(status = 'rejected') AS rejected"
        " FROM pending_changes WHERE status != 'pending'"
        " GROUP BY proposed_by, entity ORDER BY proposed DESC"
    )
    for r in rows:
        # promotion suggestions count only strong-identity verdicts — a
        # spoofed X-User must not be able to walk an agent to autonomous
        recent = db.query(
            "SELECT status FROM pending_changes WHERE proposed_by = ? AND entity = ?"
            " AND status != 'pending' AND reviewed_strong = 1 ORDER BY id DESC LIMIT ?",
            (r["agent"], r["entity"], TRUST_STREAK),
        )
        streak = 0
        for row in recent:
            if row["status"] != "approved":
                break
            streak += 1
        r["approval_rate"] = round(r["approved"] / r["proposed"], 2) if r["proposed"] else 0
        r["recent_streak"] = streak
        r["current_level"] = authority_level(r["agent"], r["entity"])
        r["suggestion"] = (
            f"{streak} straight approvals — consider promoting to autonomous"
            if streak >= TRUST_STREAK and r["current_level"] == "review"
            else ""
        )
    return rows


def mission_control() -> list[dict]:
    """One row per agent identity: what it holds, what it's waiting on."""
    agents = db.query("SELECT name FROM users WHERE kind = 'agent' AND active = 1 ORDER BY name")
    out = []
    for a in agents:
        name = a["name"]
        open_tasks = db.query_one(
            "SELECT COUNT(*) AS n FROM tasks WHERE delegated_agent = ? AND status != 'done'",
            (name,),
        )
        pending = db.query_one(
            "SELECT COUNT(*) AS n FROM pending_changes WHERE proposed_by = ?"
            " AND status = 'pending'",
            (name,),
        )
        last = db.query_one("SELECT MAX(created_at) AS ts FROM activity WHERE actor = ?", (name,))
        out.append(
            {
                "agent": name,
                "open_tasks": open_tasks["n"] if open_tasks else 0,
                "pending_proposals": pending["n"] if pending else 0,
                "last_seen": last["ts"] if last else None,
                "authority": authority_matrix(name),
            }
        )
    return out


def agent_inbox(agent: str) -> dict:
    """Ambient inbox: everything an agent should look at when it wakes up.
    Deterministic — the same view a human gets from my_day, agent-shaped."""
    if not db.query_one("SELECT id FROM users WHERE name = ?", (agent,)):
        raise ValueError(f"no such agent '{agent}' — a typo here would read as an empty inbox")
    tasks = db.query(
        "SELECT id, title, status, priority, sponsor FROM tasks"
        " WHERE delegated_agent = ? AND status != 'done'"
        " ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1"
        " WHEN 'medium' THEN 2 ELSE 3 END, id",
        (agent,),
    )
    questions = db.query(
        "SELECT id, question, asked_by FROM questions WHERE assigned_to = ?"
        " AND status = 'open' ORDER BY id",
        (agent,),
    )
    rejected = db.query(
        "SELECT id, entity, summary, review_note, reviewed_by FROM pending_changes"
        " WHERE proposed_by = ? AND status = 'rejected' ORDER BY id DESC LIMIT 10",
        (agent,),
    )
    notifications = db.query(
        "SELECT id, message, link, created_at FROM notifications"
        " WHERE user = ? AND read_at IS NULL ORDER BY id DESC LIMIT 20",
        (agent,),
    )
    return {
        "agent": agent,
        "delegated_tasks": tasks,
        "open_questions": questions,
        "rejected_proposals": rejected,
        "notifications": notifications,
    }
