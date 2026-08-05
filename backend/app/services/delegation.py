"""Agents as first-class teammates: task delegation with a human sponsor,
an authority matrix per (agent, entity), a mission-control view, and trust
scores computed from the review inbox — promotion is suggested, never automatic."""

import json
from datetime import UTC

from .. import db
from .users import ensure_user

LEVELS = ("autonomous", "notify", "review", "forbidden")
TRUST_STREAK = 5  # consecutive approvals before we suggest promotion


def delegate_task(
    task_id: int, agent: str, sponsor: str, *, actor: str = "system", origin: str = "human"
) -> dict:
    if not agent.strip():
        raise ValueError("agent name is required")
    sponsor = sponsor.strip()
    sponsor_row = db.query_one("SELECT kind FROM users WHERE name = ? AND active = 1", (sponsor,))
    if not sponsor_row or sponsor_row["kind"] != "human":
        raise ValueError(
            "sponsor must be an active human teammate — the sponsor"
            " receives the acceptance proposal, so a typo here means nobody does"
        )
    # an agent naming itself is not a delegation, it's a land-grab; the
    # human-approved proposal path (origin agent_verified) stays open
    if agent.strip() == actor and origin != "agent_verified":
        raise ValueError("an agent cannot delegate a task to itself — propose it instead")
    task = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not task:
        raise db.NotFound(f"task #{task_id} not found")
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


def _check_not_forbidden(actor: str) -> None:
    """The delegation trio bypasses gated_write by design (working your own
    delegation is direct), but the kill switch must still hold."""
    if authority_level(actor, "task") == "forbidden":
        raise ValueError(f"'{actor}' is forbidden on tasks — ask a human to lift it")


def claim_task(task_id: int, *, actor: str, origin: str = "agent") -> dict:
    """The agent picks up its delegated task: todo -> in_progress. Direct
    (not review-gated) — status motion on the agent's own delegation is
    reversible and the sponsor is told."""
    _check_not_forbidden(actor)
    task = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not task:
        raise db.NotFound(f"task #{task_id} not found")
    if task["delegated_agent"] != actor:
        raise ValueError(f"task #{task_id} is not delegated to '{actor}'")
    if task["status"] not in ("todo", "blocked"):
        raise ValueError(f"task #{task_id} is {task['status']} — nothing to claim")
    db.execute(
        "UPDATE tasks SET status = 'in_progress', updated_at = ? WHERE id = ?",
        (db.now(), task_id),
    )
    db.log_activity(actor, "claim_task", f"#{task_id} {task['title']}")
    if task["sponsor"]:
        from .notifications import notify

        notify(
            task["sponsor"],
            f"{actor} started on task #{task_id} '{task['title']}'.",
            tier="digest",
            link="/agents",
        )
    return {"id": task_id, "status": "in_progress"}


def report_progress(task_id: int, note: str, *, actor: str, origin: str = "agent") -> dict:
    """Append a worklog entry — the agent's running account, readable by the
    sponsor before the acceptance verdict. Additive, so direct (like standups)
    — but only for the parties in the loop: the worklog is evidence the
    sponsor judges on, so nobody else may write into it."""
    note = note.strip()
    if not note:
        raise ValueError("the progress note is required")
    if len(note) > 2000:
        raise ValueError("keep progress notes under 2000 characters")
    _check_not_forbidden(actor)
    task = db.query_one(
        "SELECT delegated_agent, sponsor, status, title FROM tasks WHERE id = ?", (task_id,)
    )
    if not task:
        raise db.NotFound(f"task #{task_id} not found")
    if actor not in (task["delegated_agent"], task["sponsor"]):
        raise ValueError(f"task #{task_id}'s worklog is written by its delegate or sponsor only")
    if task["status"] == "done":
        raise ValueError(f"task #{task_id} is done — its worklog is history now")
    wid = db.execute(
        "INSERT INTO task_worklog (task_id, author, note, origin, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (task_id, actor, note, origin, db.now()),
    )
    db.log_activity(actor, "report_progress", f"task #{task_id}: {note[:80]}")
    return {"id": wid, "task_id": task_id}


def list_worklog(task_id: int, limit: int = 50) -> list[dict]:
    if not db.query_one("SELECT id FROM tasks WHERE id = ?", (task_id,)):
        raise db.NotFound(f"task #{task_id} not found")
    return db.query(
        "SELECT * FROM task_worklog WHERE task_id = ? ORDER BY id DESC LIMIT ?",
        (task_id, limit),
    )


def accept_completion(
    task_id: int, summary: str = "", *, actor: str = "", origin: str = ""
) -> dict:
    """Registry apply target for task_completion proposals: the sponsor's
    approval IS the acceptance — mark done, close the loop."""
    task = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not task:
        raise db.NotFound(f"task #{task_id} not found")
    if task["status"] == "done":
        raise ValueError(f"task #{task_id} is already done")
    # a reassignment between submit and verdict voids the proposal — the
    # acceptance must be for work the proposer still owns
    if actor and task["delegated_agent"] != actor:
        raise ValueError(f"task #{task_id} is no longer delegated to '{actor}'")
    # completed_at, not just status: flow metrics filter on it, so a delegated
    # task accepted here would otherwise count zero in cycle time AND in
    # throughput. The forge routes every delegated close to this function, so
    # the more work a team delegates, the more of its throughput disappears.
    db.execute(
        "UPDATE tasks SET status = 'done', completed_at = ?, updated_at = ? WHERE id = ?",
        (db.now(), db.now(), task_id),
    )
    if summary:
        db.execute(
            "INSERT INTO task_worklog (task_id, author, note, origin, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                task_id,
                actor or "agent",
                f"[accepted] {summary}",
                origin or "agent_verified",
                db.now(),
            ),
        )
    db.log_activity(actor or "agent", "complete_task", f"#{task_id} {task['title']}")
    return {"id": task_id, "status": "done"}


def submit_completion(task_id: int, summary: str, *, actor: str, requested_by: str = "") -> dict:
    """File the acceptance proposal. ALWAYS a proposal (never direct) — the
    sponsor's verdict is the whole point of the loop, and every verdict is a
    labeled trust signal for this agent."""
    if not summary.strip():
        raise ValueError("say what was done — the sponsor reads this summary")
    _check_not_forbidden(actor)
    task = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not task:
        raise db.NotFound(f"task #{task_id} not found")
    if task["delegated_agent"] != actor:
        raise ValueError(f"task #{task_id} is not delegated to '{actor}'")
    if task["status"] == "done":
        raise ValueError(f"task #{task_id} is already done")
    dup = db.query_one(
        "SELECT id FROM pending_changes WHERE entity = 'task_completion'"
        " AND entity_id = ? AND status = 'pending'",
        (task_id,),
    )
    if dup:
        raise ValueError(
            f"task #{task_id} already awaits acceptance (proposal #{dup['id']})"
            " — wait for the sponsor's verdict"
        )
    from .review import propose_change

    p = propose_change(
        "task_completion",
        "update",
        {"summary": summary.strip()},
        summary=f"accept task #{task_id} '{task['title']}': {summary.strip()[:80]}",
        entity_id=task_id,
        actor=actor,
        origin="agent",
        notify_team=False,
        requested_by=requested_by,
    )
    if task["sponsor"]:
        from .notifications import notify

        notify(
            task["sponsor"],
            f"{actor} submitted task #{task_id} '{task['title']}' for your"
            f" acceptance (proposal #{p['id']}).",
            tier="immediate",
            link="/review",
        )
    return {"proposal_id": p["id"], "task_id": task_id, "status": "pending"}


def set_authority(
    agent: str,
    entity: str,
    level: str,
    expected_current: str = "",
    *,
    actor: str = "system",
    origin: str = "human",
) -> dict:
    agent = agent.strip()
    if not agent or agent == "anonymous":
        raise ValueError("agent name is required")
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}")
    # streak-filed proposals pin the from-level: a stale proposal must never
    # override what a human set in the meantime — above all the kill switch
    if expected_current and authority_level(agent, entity) != expected_current:
        raise ValueError(
            f"{agent}/{entity} is now '{authority_level(agent, entity)}',"
            f" not '{expected_current}' — this proposal is stale. Re-run the review"
        )
    # the kill switch must not be self-serviceable: an agent identity (e.g. a
    # key issued to one) can never grant or lift authority — humans only
    actor_row = db.query_one("SELECT kind FROM users WHERE name = ?", (actor,))
    if (actor_row and actor_row["kind"] == "agent") or actor == agent:
        raise ValueError("authority levels are set by humans, not by the agent itself")
    from .review import _registry

    if entity not in _registry():
        raise ValueError(f"unknown entity — one of {sorted(_registry())}")
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
            " AND status != 'pending' AND reviewed_strong = 1 AND reviewed_override = 0"
            " ORDER BY reviewed_at DESC, id DESC LIMIT ?",
            (r["agent"], r["entity"], TRUST_STREAK),
        )
        streak = 0
        for row in recent:
            if row["status"] != "approved":
                break
            streak += 1
        rejection_streak = 0
        for row in recent:
            if row["status"] != "rejected":
                break
            rejection_streak += 1
        r["approval_rate"] = round(r["approved"] / r["proposed"], 2) if r["proposed"] else 0
        r["recent_streak"] = streak
        r["rejection_streak"] = rejection_streak
        r["current_level"] = authority_level(r["agent"], r["entity"])
        r["suggestion"] = (
            f"{streak} straight approvals — consider promoting to autonomous"
            if streak >= TRUST_STREAK and r["current_level"] == "review"
            else ""
        )
    return rows


DEMOTION_STREAK = 3


def review_authority(*, actor: str = "scheduler") -> dict:
    """A2: turn earned trust into FILED PROPOSALS instead of a buried hint.
    Promotions climb one rung (review -> notify) on a strong-verdict approval
    streak; demotions to review fire on a strong-verdict rejection streak.
    The system only proposes — a human approves, and agents can never
    approve anything, so there is no self-promotion path."""
    from datetime import datetime, timedelta

    filed = []
    # don't refile what's pending, and don't nag weekly about what a human
    # just declined — a rejection buys 28 days of silence for that pair
    recent_cutoff = (datetime.now(UTC) - timedelta(days=28)).isoformat(timespec="seconds")
    seen = {
        (json.loads(p["payload"]).get("agent"), json.loads(p["payload"]).get("entity"))
        for p in db.query(
            "SELECT payload FROM pending_changes WHERE entity = 'authority'"
            " AND (status = 'pending' OR (status = 'rejected' AND reviewed_at > ?))",
            (recent_cutoff,),
        )
    }
    from .users import is_agent

    # authority levels only mean something for agent identities on entities
    # the gate consults: streaks from humans, the scheduler, or the meta
    # entities would mint nonsense agent rows if proposed
    skip_entities = {"authority", "task_completion"}
    for r in trust_scores():
        if r["entity"] in skip_entities or not is_agent(r["agent"]):
            continue
        target = None
        why = ""
        if r["recent_streak"] >= TRUST_STREAK and r["current_level"] == "review":
            target = "notify"
            why = f"{r['recent_streak']} straight strong-verdict approvals"
        elif r["rejection_streak"] >= DEMOTION_STREAK and r["current_level"] in (
            "autonomous",
            "notify",
        ):
            target = "review"
            why = f"{r['rejection_streak']} straight strong-verdict rejections"
        if not target or (r["agent"], r["entity"]) in seen:
            continue
        from .review import propose_change

        p = propose_change(
            "authority",
            "create",
            {
                "agent": r["agent"],
                "entity": r["entity"],
                "level": target,
                "expected_current": r["current_level"],
            },
            summary=f"authority: {r['agent']}/{r['entity']}"
            f" {r['current_level']} -> {target} ({why})"
            + (
                " — notify means direct writes with an FYI, no pre-review"
                if target == "notify"
                else ""
            ),
            actor=actor,
            origin="agent",
            notify_team=False,
        )
        filed.append({"proposal_id": p["id"], "agent": r["agent"], "entity": r["entity"]})
    if filed:
        from .notifications import notify

        notify(
            "team",
            f"{len(filed)} authority change{'' if len(filed) == 1 else 's'}"
            " proposed from review history —"
            " promote or demote in Inbox → Approvals.",
            tier="digest",
            link="/review",
        )
    return {"filed": len(filed), "proposals": filed}


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
        raise db.NotFound(
            f"no such agent '{agent}'. Check the name: a typo reads as an empty inbox."
        )
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
