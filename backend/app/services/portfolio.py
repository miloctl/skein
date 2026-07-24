"""Portfolio layer: engagement health, allocation conflicts, flow metrics,
slip forecasting, what-if intake, and the exec readout. All deterministic SQL
over data the team already records — receipts shown for every verdict."""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .. import config, db

STALE_WIP_DAYS = 7
SILENCE_DAYS = 7


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _linked_blockers(engagement_name: str) -> list[dict]:
    return db.query(
        "SELECT b.* FROM blockers b JOIN tasks t ON t.id = b.task_id"
        " JOIN milestones m ON m.id = t.milestone_id"
        " WHERE m.project = ? AND b.status != 'resolved'",
        (engagement_name,),
    )


def engagement_health() -> list[dict]:
    """R/Y/G per non-closed engagement, each signal listed as a receipt."""
    today = _today().isoformat()
    stale_cutoff = (_today() - timedelta(days=STALE_WIP_DAYS)).isoformat()
    out = []
    for eng in db.query(
            "SELECT * FROM engagements WHERE status != 'closed' ORDER BY id"):
        name = eng["name"]
        receipts = []
        overdue = db.query(
            "SELECT id, title, due_date FROM milestones WHERE project = ?"
            " AND status != 'done' AND due_date IS NOT NULL AND due_date < ?",
            (name, today),
        )
        for m in overdue:
            receipts.append(f"milestone #{m['id']} '{m['title']}' overdue since {m['due_date']}")
        blocked = _linked_blockers(name)
        escalated = [b for b in blocked if b["status"] == "escalated"]
        for b in escalated:
            receipts.append(f"blocker #{b['id']} '{b['title']}' is escalated")
        for b in blocked:
            if b["status"] == "open":
                receipts.append(f"blocker #{b['id']} '{b['title']}' open")
        stale = db.query(
            "SELECT t.id, t.title, t.assignee FROM tasks t"
            " JOIN milestones m ON m.id = t.milestone_id"
            " WHERE m.project = ? AND t.status = 'in_progress' AND t.updated_at < ?",
            (name, stale_cutoff),
        )
        for t in stale:
            receipts.append(
                f"task #{t['id']} '{t['title']}' in progress >{STALE_WIP_DAYS}d"
                f" (@{t['assignee'] or 'unassigned'})")
        last = db.query_one(
            "SELECT MAX(t.updated_at) AS ts FROM tasks t"
            " JOIN milestones m ON m.id = t.milestone_id WHERE m.project = ?",
            (name,),
        )
        open_tasks = db.query_one(
            "SELECT COUNT(*) AS n FROM tasks t JOIN milestones m ON m.id = t.milestone_id"
            " WHERE m.project = ? AND t.status != 'done'", (name,))
        silence_cutoff = (_today() - timedelta(days=SILENCE_DAYS)).isoformat()
        silent = bool(open_tasks and open_tasks["n"]
                      and last and last["ts"] and last["ts"][:10] < silence_cutoff)
        if silent:
            receipts.append(f"no task activity since {last['ts'][:10]}")

        if escalated or len(overdue) >= 2:
            color = "red"
        elif receipts:
            color = "yellow"
        else:
            color = "green"
        out.append({"id": eng["id"], "name": name, "status": eng["status"],
                    "lead": eng["lead"], "health": color, "receipts": receipts})
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
    return {"cycle_time": cycle,
            "throughput_by_week": dict(sorted(throughput.items())),
            "wip_by_person": wip, "stale_wip": stale}


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
        notify(person,
               f"{len(ts)} task(s) sitting in progress >{STALE_WIP_DAYS} days: {titles}."
               " Still real? Split it, unblock it, or put it back in the pool.",
               tier="digest", link="/")
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
            "SELECT m.* FROM milestones m JOIN engagements e ON e.name = m.project"
            " WHERE e.status != 'closed' AND m.status != 'done'"
            " AND m.due_date IS NOT NULL ORDER BY m.due_date"):
        forecast = (date.fromisoformat(m["due_date"])
                    + timedelta(days=round(applied))).isoformat()
        forecasts.append({
            "milestone_id": m["id"], "title": m["title"], "project": m["project"],
            "due_date": m["due_date"], "forecast_date": forecast,
            "at_risk": forecast < _today().isoformat() or m["due_date"] < _today().isoformat(),
        })
    return {"basis": {"milestones_measured": len(slips), "avg_slip_days": avg_slip},
            "forecasts": forecasts}


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
    current = {r["person"]: r["total_percent"] for r in db.query(
        "SELECT a.person, SUM(a.percent) AS total_percent"
        " FROM allocations a JOIN engagements e ON e.id = a.engagement_id"
        " WHERE e.status != 'closed'"
        " AND (a.starts_on IS NULL OR a.starts_on <= ?)"
        " AND (a.ends_on IS NULL OR a.ends_on >= ?)"
        " GROUP BY a.person",
        (today, today),
    )}
    projection = []
    for p in people:
        total = current.get(p, 0) + percent
        projection.append({"person": p, "current_percent": current.get(p, 0),
                           "projected_percent": total, "overcommitted": total > 100})
    return {"request": {"id": req["id"], "title": req["title"], "score": req["score"]},
            "assumed_percent": percent, "projection": projection,
            "conflicts": [p for p in projection if p["overcommitted"]]}


def exec_readout(*, actor: str = "system") -> dict:
    """Curated executive projection — never a raw table dump. Written as a
    markdown artifact so it can be forwarded as-is."""
    from .pulse import season

    health = engagement_health()
    conflicts = allocation_conflicts()
    flow = flow_metrics()
    s = season()
    shipped = db.query(
        "SELECT name, closed_at FROM engagements WHERE status = 'closed'"
        " AND closed_at >= ? ORDER BY closed_at DESC", (s["start"],))
    due_soon = db.query(
        "SELECT * FROM commitments WHERE status = 'open' AND due_date IS NOT NULL"
        " AND due_date <= ? ORDER BY due_date",
        ((_today() + timedelta(days=14)).isoformat(),))
    escalated = db.query("SELECT * FROM blockers WHERE status = 'escalated'")

    dot = {"red": "🔴", "yellow": "🟡", "green": "🟢"}
    lines = [f"# Exec readout — {_today().isoformat()} ({s['label']})", ""]
    lines.append("## Engagements")
    for h in health:
        lines.append(f"- {dot[h['health']]} **{h['name']}** ({h['status']},"
                     f" lead: {h['lead'] or 'unset'})")
        for r in h["receipts"][:3]:
            lines.append(f"  - {r}")
    if not health:
        lines.append("- none active")
    lines += ["", "## Shipped this season"]
    lines += [f"- {r['name']} ({r['closed_at'][:10]})" for r in shipped] or ["- none yet"]
    lines += ["", "## Top risks"]
    risk_lines = [f"- Escalated blocker #{b['id']}: {b['title']}" for b in escalated]
    risk_lines += [f"- {c['person']} at {c['total_percent']}% ({c['detail']})"
                   for c in conflicts]
    lines += risk_lines or ["- none flagged"]
    lines += ["", "## External commitments due in 14 days"]
    lines += [f"- {c['due_date']}: {c['promise']} (to {c['to_whom'] or 'unspecified'})"
              for c in due_soon] or ["- none recorded"]
    ct = flow["cycle_time"]
    lines += ["", "## Flow",
              f"- {ct['tasks_done']} tasks done in 8 weeks"
              + (f", median cycle {ct['median_days']}d, avg {ct['avg_days']}d"
                 if ct["tasks_done"] else ""),
              f"- WIP: " + (", ".join(f"{w['person']} {w['in_progress']}"
                                      for w in flow["wip_by_person"]) or "none"),
              ]
    markdown = "\n".join(lines)

    readout_dir = Path(config.DATA_DIR) / "artifacts" / "portfolio"
    readout_dir.mkdir(parents=True, exist_ok=True)
    path = readout_dir / f"{_today().isoformat()}-exec-readout.md"
    path.write_text(markdown)
    # same-day reruns overwrite the file, so upsert the artifact row too —
    # N rows pointing at one file would imply history that doesn't exist
    existing = db.query_one("SELECT id FROM artifacts WHERE path = ?", (str(path),))
    if existing:
        aid = existing["id"]
        db.execute("UPDATE artifacts SET created_by = ?, created_at = ? WHERE id = ?",
                   (actor, db.now(), aid))
    else:
        aid = db.execute(
            "INSERT INTO artifacts (engagement_id, kind, title, path, created_by, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (None, "readout", f"Exec readout {_today().isoformat()}", str(path), actor, db.now()),
        )
    db.log_activity(actor, "exec_readout", f"artifact #{aid}")
    return {"artifact_id": aid, "path": str(path), "markdown": markdown}
