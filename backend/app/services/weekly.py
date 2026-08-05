"""Weekly commitment line: the team commits tasks to an ISO week, and a
Monday job drafts the plan as a pending-changes proposal — the same review
inbox humans already work, so the plan is approved, not imposed."""

from datetime import UTC, datetime, timedelta

from .. import db
from . import wording
from .work import WEEK_RE, update_task

MAX_PER_PERSON = 5


def current_week(offset: int = 0) -> str:
    iso = (datetime.now(UTC).date() + timedelta(weeks=offset)).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def week_view(week: str = "") -> dict:
    week = week or current_week()
    if not WEEK_RE.match(week):
        raise ValueError("week must look like 2026-W31")
    tasks = db.query(
        "SELECT t.*, m.title AS milestone_title FROM tasks t"
        " LEFT JOIN milestones m ON m.id = t.milestone_id"
        " WHERE t.committed_week = ? ORDER BY t.assignee, t.id",
        (week,),
    )
    done = sum(1 for t in tasks if t["status"] == "done")
    return {
        "week": week,
        "committed": len(tasks),
        "done": done,
        "kept_percent": round(100 * done / len(tasks)) if tasks else None,
        "tasks": tasks,
    }


def draft_plan(week: str = "") -> dict:
    """Deterministic draft: per active human, their open tasks ranked by
    priority then due date, capped so the line stays honest."""
    week = week or current_week()
    if not WEEK_RE.match(week):
        raise ValueError("week must look like 2026-W31")
    items = []
    skipped: list[dict] = []
    from datetime import date as _date

    from .absences import weekday_overlap

    year, wk = week.split("-W")
    try:
        week_monday = _date.fromisocalendar(int(year), int(wk), 1)
    except ValueError as exc:  # W53 in a 52-week ISO year passes the regex
        raise ValueError(f"{year} has no ISO week {wk}") from exc
    humans = db.query(
        "SELECT name FROM users WHERE kind = 'human' AND active = 1"
        " AND name != 'anonymous' ORDER BY name"
    )
    for h in humans:
        away_days = weekday_overlap(h["name"], week_monday)
        if away_days >= 3:
            # committing tasks to someone away most of the week sets the
            # kept-% up to lie — skip them, say so
            skipped.append({"person": h["name"], "away_days": away_days})
            continue
        rows = db.query(
            "SELECT id, title, priority, due_date FROM tasks"
            " WHERE assignee = ? AND status IN ('todo', 'in_progress')"
            " AND (committed_week IS NULL OR committed_week != ?)"
            " ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1"
            " WHEN 'medium' THEN 2 ELSE 3 END, due_date IS NULL, due_date, id"
            " LIMIT ?",
            (h["name"], week, MAX_PER_PERSON),
        )
        items += [{"task_id": r["id"], "title": r["title"], "assignee": h["name"]} for r in rows]
    return {"week": week, "items": items, "skipped_absent": skipped}


def apply_plan(
    week: str, task_ids: list[int], *, actor: str = "system", origin: str = "human"
) -> dict:
    """Registry-applied: sets committed_week on each task. Called on approval
    of a weekly_plan proposal, or directly by a human."""
    if not WEEK_RE.match(week or ""):
        raise ValueError("week must look like 2026-W31")
    if not task_ids:
        raise ValueError("no tasks in the plan")
    # a task deleted between draft and commit must not wedge the plan: apply
    # what exists, report what didn't (no cross-op transactions in this app,
    # so skip-and-report beats a validate/apply TOCTOU window)
    committed, skipped = [], []
    for tid in task_ids:
        try:
            update_task(int(tid), committed_week=week, actor=actor, origin=origin)
            committed.append(int(tid))
        except ValueError:
            skipped.append(int(tid))
    if not committed:
        raise ValueError("every task in the plan is gone — draft the week again from current tasks")
    db.log_activity(
        actor,
        "apply_weekly_plan",
        f"{week}: {len(committed)} tasks" + (f", skipped {skipped}" if skipped else ""),
    )
    return {"week": week, "committed": len(committed), "task_ids": committed, "skipped": skipped}


def propose_weekly_plan(*, actor: str = "scheduler") -> dict:
    """Monday job: draft next promises and queue them for human approval.
    Once per week via claim_job — claimed only when there is actually a plan,
    so an empty Monday doesn't lock the week out."""
    week = current_week()
    draft = draft_plan(week)
    if not draft["items"]:
        return {"skipped": "nothing to commit"}
    if not db.claim_job("weekly-plan", week):
        return {"skipped": f"plan for {week} already drafted"}
    from .review import propose_change

    names = ", ".join(f"#{i['task_id']}" for i in draft["items"][:10])
    # the reviewer must see who was left out and why — a silent skip reads
    # as "covered everyone"
    skipped = ""
    if draft["skipped_absent"]:
        who = ", ".join(f"{s['person']} ({s['away_days']}d)" for s in draft["skipped_absent"])
        skipped = f" — skipped for absence: {who}"
    return propose_change(
        "weekly_plan",
        "create",
        {"week": week, "task_ids": [i["task_id"] for i in draft["items"]]},
        summary=f"Weekly commitment line {week}: {wording.count(len(draft['items']), 'task')} ({names}){skipped}",
        actor=actor,
        origin="agent",
    )
