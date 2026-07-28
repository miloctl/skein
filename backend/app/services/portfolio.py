"""Portfolio layer: engagement health, allocation conflicts, flow metrics,
slip forecasting, what-if intake, and the exec readout. All deterministic SQL
over data the team already records — receipts shown for every verdict."""

from datetime import date, datetime, timedelta, timezone

from .. import db
from .slas import SILENCE_DAYS, STALE_WIP_DAYS


def _today() -> date:
    return datetime.now(timezone.utc).date()


# a wait on a resolved/done/kept target is satisfied — it must stop yellowing
# the engagement the moment the dependency clears, without a manual unset
_WAIT_SATISFIED = {
    "task": "SELECT 1 FROM tasks WHERE id = ? AND status = 'done'",
    "blocker": "SELECT 1 FROM blockers WHERE id = ? AND status = 'resolved'",
    "commitment": "SELECT 1 FROM commitments WHERE id = ? AND status != 'open'",
}


def _unsatisfied_waits(sql: str, params: tuple) -> list[dict]:
    out = []
    for t in db.query(sql, params):
        satisfied = db.query_one(_WAIT_SATISFIED[t["waiting_on_type"]], (t["waiting_on_id"],))
        if not satisfied:
            out.append(t)
    return out


def _linked_blockers(engagement_id: int) -> list[dict]:
    return db.query(
        "SELECT b.* FROM blockers b JOIN tasks t ON t.id = b.task_id"
        " WHERE (t.engagement_id = ? OR t.milestone_id IN (SELECT id FROM milestones WHERE engagement_id = ?))"
        " AND b.status != 'resolved'",
        (engagement_id, engagement_id),
    )


def engagement_health() -> list[dict]:
    """R/Y/G per non-closed engagement, each signal listed as a receipt."""
    today = _today().isoformat()
    stale_cutoff = (_today() - timedelta(days=STALE_WIP_DAYS)).isoformat()
    out = []
    for eng in db.query("SELECT * FROM engagements WHERE status != 'closed' ORDER BY id"):
        name = eng["name"]
        receipts = []
        overdue = db.query(
            "SELECT id, title, due_date FROM milestones WHERE engagement_id = ?"
            " AND status != 'done' AND due_date IS NOT NULL AND due_date < ?",
            (eng["id"], today),
        )
        for m in overdue:
            receipts.append(f"milestone #{m['id']} '{m['title']}' overdue since {m['due_date']}")
        blocked = _linked_blockers(eng["id"])
        escalated = [b for b in blocked if b["status"] == "escalated"]
        for b in escalated:
            receipts.append(f"blocker #{b['id']} '{b['title']}' is escalated")
        for b in blocked:
            if b["status"] == "open":
                receipts.append(f"blocker #{b['id']} '{b['title']}' open")
        stale = db.query(
            "SELECT t.id, t.title, t.assignee FROM tasks t"
            " WHERE (t.engagement_id = ? OR t.milestone_id IN (SELECT id FROM milestones WHERE engagement_id = ?))"
            " AND t.status = 'in_progress' AND t.updated_at < ?",
            (eng["id"], eng["id"], stale_cutoff),
        )
        for t in stale:
            receipts.append(
                f"task #{t['id']} '{t['title']}' in progress >{STALE_WIP_DAYS}d"
                f" (@{t['assignee'] or 'unassigned'})"
            )
        for t in _unsatisfied_waits(
            "SELECT t.id, t.title, t.waiting_on_type, t.waiting_on_id FROM tasks t"
            " WHERE (t.engagement_id = ? OR t.milestone_id IN (SELECT id FROM milestones WHERE engagement_id = ?))"
            " AND t.status != 'done' AND t.waiting_on_type IS NOT NULL",
            (eng["id"], eng["id"]),
        ):
            receipts.append(
                f"task #{t['id']} '{t['title']}' waiting on"
                f" {t['waiting_on_type']} #{t['waiting_on_id']}"
            )
        last = db.query_one(
            "SELECT MAX(t.updated_at) AS ts FROM tasks t WHERE (t.engagement_id = ? OR t.milestone_id IN (SELECT id FROM milestones WHERE engagement_id = ?))",
            (eng["id"], eng["id"]),
        )
        open_tasks = db.query_one(
            "SELECT COUNT(*) AS n FROM tasks t"
            " WHERE (t.engagement_id = ? OR t.milestone_id IN (SELECT id FROM milestones WHERE engagement_id = ?))"
            " AND t.status != 'done'",
            (eng["id"], eng["id"]),
        )
        silence_cutoff = (_today() - timedelta(days=SILENCE_DAYS)).isoformat()
        silent = bool(
            open_tasks
            and open_tasks["n"]
            and last
            and last["ts"]
            and last["ts"][:10] < silence_cutoff
        )
        if silent and last:
            receipts.append(f"no task activity since {last['ts'][:10]}")

        if escalated or len(overdue) >= 2:
            color = "red"
        elif receipts:
            color = "yellow"
        else:
            color = "green"
        out.append(
            {
                "id": eng["id"],
                "name": name,
                "status": eng["status"],
                "lead": eng["lead"],
                "health": color,
                "receipts": receipts,
            }
        )
    return out


def allocation_conflicts() -> list[dict]:
    """People allocated >100% across non-closed engagements whose windows
    cover today (open-ended allocations always count)."""
    today = _today().isoformat()
    return db.query(
        "SELECT a.person, SUM(a.percent) AS total_percent,"
        " GROUP_CONCAT(e.name || ' (' || a.percent || '%)', ', ') AS detail"
        " FROM allocations a JOIN engagements e ON e.id = a.engagement_id"
        " WHERE e.status != 'closed'"
        " AND (a.starts_on IS NULL OR a.starts_on <= ?)"
        " AND (a.ends_on IS NULL OR a.ends_on >= ?)"
        " GROUP BY a.person HAVING total_percent > 100"
        " ORDER BY total_percent DESC",
        (today, today),
    )


def flow_metrics(weeks: int = 8) -> dict:
    """Cycle time / throughput / WIP from timestamps the platform already has.
    No estimates, no story points."""
    cutoff = (_today() - timedelta(weeks=weeks)).isoformat()
    done = db.query(
        "SELECT created_at, completed_at,"
        " ROUND(julianday(completed_at) - julianday(created_at), 1) AS days"
        " FROM tasks WHERE completed_at IS NOT NULL AND completed_at >= ?",
        (cutoff,),
    )
    cycle_days = sorted(r["days"] for r in done)
    n = len(cycle_days)
    cycle = {
        "tasks_done": n,
        "avg_days": round(sum(cycle_days) / n, 1) if n else None,
        "median_days": cycle_days[n // 2] if n else None,
    }
    throughput: dict[str, int] = {}
    for r in done:
        d = date.fromisoformat(r["completed_at"][:10]).isocalendar()
        throughput[f"{d.year}-W{d.week:02d}"] = throughput.get(f"{d.year}-W{d.week:02d}", 0) + 1
    wip = db.query(
        "SELECT COALESCE(NULLIF(assignee, ''), 'unassigned') AS person,"
        " COUNT(*) AS in_progress FROM tasks WHERE status = 'in_progress'"
        " GROUP BY person ORDER BY in_progress DESC"
    )
    stale_cutoff = (_today() - timedelta(days=STALE_WIP_DAYS)).isoformat()
    stale = db.query(
        "SELECT id, title, assignee,"
        " CAST(julianday('now') - julianday(updated_at) AS INTEGER) AS days_stale"
        " FROM tasks WHERE status = 'in_progress' AND updated_at < ?"
        " ORDER BY updated_at",
        (stale_cutoff,),
    )
    return {
        "cycle_time": cycle,
        "throughput_by_week": dict(sorted(throughput.items())),
        "wip_by_person": wip,
        "stale_wip": stale,
    }


def nudge_stale_wip() -> dict:
    """Weekly soft nudge to owners of stale in-progress tasks. Once per ISO
    week via claim_job — a restart can't re-ping anyone. The claim is only
    taken when there is someone to nudge, so a quiet Monday doesn't burn it."""
    stale = flow_metrics()["stale_wip"]
    by_person: dict[str, list[dict]] = {}
    for t in stale:
        if t["assignee"]:
            by_person.setdefault(t["assignee"], []).append(t)
    if not by_person:
        return {"nudged": 0}
    iso = _today().isocalendar()
    if not db.claim_job("stale-wip-nudge", f"{iso.year}-W{iso.week:02d}"):
        return {"nudged": 0, "skipped": "already nudged this week"}
    from .notifications import notify

    for person, ts in by_person.items():
        titles = "; ".join(f"#{t['id']} {t['title']}" for t in ts[:3])
        notify(
            person,
            f"{len(ts)} task(s) sitting in progress >{STALE_WIP_DAYS} days: {titles}."
            " Still real? Split it, unblock it, or put it back in the pool.",
            tier="digest",
            link="/",
        )
    return {"nudged": len(by_person)}


def slip_forecast() -> dict:
    """Forecast open milestone dates from the team's own slip history.
    Labeled heuristic: avg days late across done milestones that had a due date."""
    history = db.query(
        "SELECT ROUND(julianday(date(updated_at)) - julianday(due_date), 1) AS slip"
        " FROM milestones WHERE status = 'done' AND due_date IS NOT NULL"
    )
    slips = [r["slip"] for r in history]
    avg_slip = round(sum(slips) / len(slips), 1) if slips else 0.0
    applied = max(0.0, avg_slip)
    forecasts = []
    for m in db.query(
        "SELECT m.* FROM milestones m JOIN engagements e ON e.id = m.engagement_id"
        " WHERE e.status != 'closed' AND e.kind != 'experiment'"  # timeboxed, not deadlined
        " AND m.status != 'done' AND m.due_date IS NOT NULL ORDER BY m.due_date"
    ):
        forecast = (date.fromisoformat(m["due_date"]) + timedelta(days=round(applied))).isoformat()
        waiting = _unsatisfied_waits(
            "SELECT id, waiting_on_type, waiting_on_id FROM tasks"
            " WHERE milestone_id = ? AND status != 'done' AND waiting_on_type IS NOT NULL",
            (m["id"],),
        )
        forecasts.append(
            {
                "milestone_id": m["id"],
                "title": m["title"],
                "project": m["project"],
                "due_date": m["due_date"],
                "forecast_date": forecast,
                "at_risk": forecast < _today().isoformat() or m["due_date"] < _today().isoformat(),
                "waiting_on": [
                    f"{w['waiting_on_type']} #{w['waiting_on_id']} (task #{w['id']})"
                    for w in waiting
                ],
            }
        )
    return {
        "basis": {"milestones_measured": len(slips), "avg_slip_days": avg_slip},
        "forecasts": forecasts,
    }


def what_if(request_id: int, people: list[str], percent: int = 50) -> dict:
    """Project team capacity if this intake request were accepted and staffed
    with `people` at `percent` each."""
    if not 1 <= percent <= 100:
        raise ValueError("percent must be 1-100")
    if not people:
        raise ValueError("name at least one person to staff")
    req = db.query_one("SELECT * FROM intake_requests WHERE id = ?", (request_id,))
    if not req:
        raise ValueError(f"intake request #{request_id} not found")
    # window-aware like allocation_conflicts — an allocation that ended last
    # quarter must not veto today's intake decision
    today = _today().isoformat()
    current = {
        r["person"]: r["total_percent"]
        for r in db.query(
            "SELECT a.person, SUM(a.percent) AS total_percent"
            " FROM allocations a JOIN engagements e ON e.id = a.engagement_id"
            " WHERE e.status != 'closed'"
            " AND (a.starts_on IS NULL OR a.starts_on <= ?)"
            " AND (a.ends_on IS NULL OR a.ends_on >= ?)"
            " GROUP BY a.person",
            (today, today),
        )
    }
    interests = {
        r["name"]: r["growth_interests"]
        for r in db.query("SELECT name, growth_interests FROM users WHERE growth_interests != ''")
    }
    from .absences import list_absences

    away: dict[str, str] = {}
    for a in list_absences():  # ordered by starts_on — keep the NEAREST window
        if a["kind"] == "pto":
            away.setdefault(a["person"], f"{a['kind']} {a['starts_on']}..{a['ends_on']}")
    projection = []
    for p in people:
        total = current.get(p, 0) + percent
        projection.append(
            {
                "person": p,
                "current_percent": current.get(p, 0),
                "projected_percent": total,
                "overcommitted": total > 100,
                # display-only: the human weighs growth fit, no matching logic
                "growth_interests": interests.get(p, ""),
                # upcoming PTO is a staffing fact, not a veto — shown, not scored
                "upcoming_absence": away.get(p, ""),
            }
        )
    return {
        "request": {"id": req["id"], "title": req["title"], "score": req["score"]},
        "assumed_percent": percent,
        "projection": projection,
        "conflicts": [p for p in projection if p["overcommitted"]],
    }
