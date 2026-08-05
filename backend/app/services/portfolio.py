"""Portfolio layer: engagement health, allocation conflicts, flow metrics,
slip forecasting, what-if intake, and the exec readout. All deterministic SQL
over data the team already records — receipts shown for every verdict."""

from datetime import UTC, date, datetime, timedelta

from .. import db
from .slas import SILENCE_DAYS, STALE_WIP_DAYS
from .stats import median as _median


def _today() -> date:
    return datetime.now(UTC).date()


# a wait on a resolved/done/kept target is satisfied — it must stop yellowing
# the engagement the moment the dependency clears, without a manual unset.
# Keys mirror work.WAITING_ON_TYPES (the write path's whitelist): a type
# added there without a query here KeyErrors _satisfied_targets → /portfolio
_WAIT_SATISFIED = {
    "task": "SELECT id FROM tasks WHERE status = 'done' AND id IN ({marks})",
    "blocker": "SELECT id FROM blockers WHERE status = 'resolved' AND id IN ({marks})",
    "commitment": "SELECT id FROM commitments WHERE status != 'open' AND id IN ({marks})",
}


def _satisfied_targets(waits: list[dict]) -> set[tuple[str, int]]:
    """(waiting_on_type, waiting_on_id) pairs whose dependency has cleared.
    One IN query per target type — the per-wait probe this replaces was one
    query per waiting task, on every /portfolio load."""
    by_type: dict[str, set[int]] = {}
    for w in waits:
        by_type.setdefault(w["waiting_on_type"], set()).add(w["waiting_on_id"])
    done: set[tuple[str, int]] = set()
    for typ, ids in by_type.items():
        marks = ", ".join("?" for _ in ids)
        sql = _WAIT_SATISFIED[typ].format(marks=marks)
        done.update((typ, r["id"]) for r in db.query(sql, tuple(ids)))
    return done


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
    silence_cutoff = (_today() - timedelta(days=SILENCE_DAYS)).isoformat()
    engagements = db.query("SELECT * FROM engagements WHERE status != 'closed' ORDER BY id")
    # Four batched scans grouped in Python, not per-engagement queries: at
    # ~6 queries per engagement plus one per waiting task, a growing
    # portfolio multiplies /portfolio and exec-readout latency.
    # A task can reach an engagement two ways (its own engagement_id, or its
    # milestone's) — the id set below dedups the two paths, and a task whose
    # two paths reach DIFFERENT engagements counts toward both.
    overdue_by: dict[int, list[dict]] = {}
    for m in db.query(
        "SELECT id, title, due_date, engagement_id FROM milestones"
        " WHERE status != 'done' AND due_date IS NOT NULL AND due_date < ? ORDER BY id",
        (today,),
    ):
        overdue_by.setdefault(m["engagement_id"], []).append(m)
    blockers_by: dict[int, list[dict]] = {}
    for b in db.query(
        "SELECT b.*, t.engagement_id AS t_eng, m.engagement_id AS m_eng"
        " FROM blockers b JOIN tasks t ON t.id = b.task_id"
        " LEFT JOIN milestones m ON m.id = t.milestone_id"
        " WHERE b.status != 'resolved' ORDER BY b.id"
    ):
        for eng_id in {b.pop("t_eng"), b.pop("m_eng")} - {None}:
            blockers_by.setdefault(eng_id, []).append(b)
    stale_by: dict[int, list[dict]] = {}
    waits_by: dict[int, list[dict]] = {}
    last_by: dict[int, str] = {}
    open_by: dict[int, int] = {}
    all_waits: list[dict] = []
    for t in db.query(
        "SELECT t.id, t.title, t.assignee, t.status, t.updated_at,"
        " t.waiting_on_type, t.waiting_on_id,"
        " t.engagement_id AS t_eng, m.engagement_id AS m_eng"
        " FROM tasks t LEFT JOIN milestones m ON m.id = t.milestone_id ORDER BY t.id"
    ):
        engs = {t["t_eng"], t["m_eng"]} - {None}
        if not engs:
            continue
        is_wait = t["status"] != "done" and t["waiting_on_type"] is not None
        if is_wait:
            all_waits.append(t)
        for eng_id in engs:
            if t["status"] == "in_progress" and t["updated_at"] and t["updated_at"] < stale_cutoff:
                stale_by.setdefault(eng_id, []).append(t)
            if is_wait:
                waits_by.setdefault(eng_id, []).append(t)
            if t["updated_at"] and t["updated_at"] > last_by.get(eng_id, ""):
                last_by[eng_id] = t["updated_at"]
            if t["status"] != "done":
                open_by[eng_id] = open_by.get(eng_id, 0) + 1
    satisfied = _satisfied_targets(all_waits)
    out = []
    for eng in engagements:
        name = eng["name"]
        receipts = []
        overdue = overdue_by.get(eng["id"], [])
        for m in overdue:
            receipts.append(f"milestone #{m['id']} '{m['title']}' overdue since {m['due_date']}")
        blocked = blockers_by.get(eng["id"], [])
        escalated = [b for b in blocked if b["status"] == "escalated"]
        for b in escalated:
            receipts.append(f"blocker #{b['id']} '{b['title']}' is escalated")
        for b in blocked:
            if b["status"] == "open":
                receipts.append(f"blocker #{b['id']} '{b['title']}' open")
        for t in stale_by.get(eng["id"], []):
            receipts.append(
                f"task #{t['id']} '{t['title']}' in progress >{STALE_WIP_DAYS}d"
                f" (@{t['assignee'] or 'unassigned'})"
            )
        for t in waits_by.get(eng["id"], []):
            if (t["waiting_on_type"], t["waiting_on_id"]) in satisfied:
                continue
            receipts.append(
                f"task #{t['id']} '{t['title']}' waiting on"
                f" {t['waiting_on_type']} #{t['waiting_on_id']}"
            )
        last_ts = last_by.get(eng["id"], "")
        silent = bool(open_by.get(eng["id"]) and last_ts and last_ts[:10] < silence_cutoff)
        if silent:
            receipts.append(f"no task activity since {last_ts[:10]}")

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
    # a NULL cycle time (an unparseable created_at from a restore or import)
    # would blow up sorted()/sum() on None — a 500 on /portfolio and in the
    # exec readout for one bad row
    cycle_days = sorted(r["days"] for r in done if r["days"] is not None)
    n = len(cycle_days)
    cycle = {
        "tasks_done": n,
        "avg_days": round(sum(cycle_days) / n, 1) if n else None,
        # stats.median, not cycle_days[n // 2]: the latter takes the UPPER
        # of the two middle values, so [1, 9] read as 9.0 instead of 5.0 — a
        # systematically inflated cycle time on every even-n window, on the
        # headline number of /portfolio and the exec readout. One median
        # implementation, per the "one service layer" principle.
        "median_days": _median(cycle_days),
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
        titles = ", ".join(f"#{t['id']} {t['title']}" for t in ts[:3])
        notify(
            person,
            f"{len(ts)} task{'' if len(ts) == 1 else 's'} in progress more than"
            f" {STALE_WIP_DAYS} days: {titles}."
            " Split, unblock, or put back in the pool.",
            tier="digest",
            link="/",
        )
    return {"nudged": len(by_person)}


def slip_forecast() -> dict:
    """Forecast open milestone dates from the team's own slip history.
    Labeled heuristic: MEDIAN days late across done milestones that had a
    due date (median, not mean — see the note below)."""
    # completed_at, not updated_at: post-done corrections (relinks, title
    # fixes) bump updated_at and would inflate every forecast
    history = db.query(
        "SELECT ROUND(julianday(date(COALESCE(completed_at, updated_at)))"
        " - julianday(due_date), 1) AS slip"
        " FROM milestones WHERE status = 'done' AND due_date IS NOT NULL"
    )
    slips = [r["slip"] for r in history if r["slip"] is not None]
    # MEDIAN, not mean: docs/INSIGHTS.md says "medians over means everywhere",
    # and a mean let one pathological milestone rewrite the whole portfolio —
    # nine delivered on time plus one 200 days late pushed EVERY open
    # milestone 20 days. The median of that history is 0.
    med = _median(slips)
    median_slip = round(med, 1) if med is not None else 0.0
    applied = max(0.0, median_slip)
    open_ms = db.query(
        "SELECT m.* FROM milestones m JOIN engagements e ON e.id = m.engagement_id"
        " WHERE e.status != 'closed' AND e.kind != 'experiment'"  # timeboxed, not deadlined
        " AND m.status != 'done' AND m.due_date IS NOT NULL ORDER BY m.due_date"
    )
    # one waiting-task query for all open milestones, one resolution query
    # per target type — per-milestone and per-wait probes multiply with the
    # portfolio, and this runs in the daily forecast snapshot too
    waits_by: dict[int, list[dict]] = {}
    if open_ms:
        marks = ", ".join("?" for _ in open_ms)
        for w in db.query(
            f"SELECT id, milestone_id, waiting_on_type, waiting_on_id FROM tasks"  # noqa: S608 — placeholders built above
            f" WHERE milestone_id IN ({marks}) AND status != 'done'"
            f" AND waiting_on_type IS NOT NULL ORDER BY id",
            tuple(m["id"] for m in open_ms),
        ):
            waits_by.setdefault(w["milestone_id"], []).append(w)
    satisfied = _satisfied_targets([w for ws in waits_by.values() for w in ws])
    forecasts = []
    for m in open_ms:
        forecast = (date.fromisoformat(m["due_date"]) + timedelta(days=round(applied))).isoformat()
        waiting = [
            w
            for w in waits_by.get(m["id"], [])
            if (w["waiting_on_type"], w["waiting_on_id"]) not in satisfied
        ]
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
        # median, and the key says so — an "avg_" key holding a median is
        # the same kind of quiet lie the median fix was for
        "basis": {"milestones_measured": len(slips), "median_slip_days": median_slip},
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
        raise db.NotFound(f"intake request #{request_id} not found")
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
