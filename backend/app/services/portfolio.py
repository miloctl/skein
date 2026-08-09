"""Portfolio layer: engagement health, allocation conflicts, flow metrics,
slip forecasting, what-if intake, and the exec readout. All deterministic SQL
over data the team already records — receipts shown for every verdict."""

from datetime import date, timedelta

from .. import db
from . import scope
from .scope import WORKSPACE_ONLY
from .slas import SILENCE_DAYS, STALE_WIP_DAYS, VERDICT_FLOOR_N
from .stats import median as _median


def _today() -> date:
    """The team's day (config.SKEIN_TZ), not the UTC day — see db.today()."""
    return db.today()


# a wait on a resolved/done/kept target is satisfied — it must stop yellowing
# the engagement the moment the dependency clears, without a manual unset.
# Keys mirror work.WAITING_ON_TYPES (the write path's whitelist): a type
# added there without a query here KeyErrors _satisfied_targets → /portfolio
_WAIT_SATISFIED = {
    "task": "SELECT id FROM tasks WHERE status = 'done' AND id IN ({marks})",
    "blocker": "SELECT id FROM blockers WHERE status = 'resolved' AND id IN ({marks})",
    "promise": "SELECT id FROM promises WHERE status != 'open' AND id IN ({marks})",
}


def health_changes(health: list[dict], since: date | None = None) -> list[dict]:
    """Which engagements MOVED since their last snapshot, and which way.

    The first question asked of a readout is direction, not state: "is Atlas
    newly yellow" has a different answer from "is Atlas yellow", and only one
    of them tells anybody to do something. engagement_health computes state at
    request time and keeps no history, so this reads the daily snapshots
    (services/adoption.py::snapshot_health) written for exactly this.

    `since` is the moment being compared BACK TO, and it must match what the
    caller's heading claims. Defaulting to yesterday answers "moved since
    yesterday" — which for a WEEKLY artifact is the wrong 24 hours six days
    out of seven: an engagement that went yellow on Wednesday reads as
    unchanged in Monday's readout, under a heading promising exactly that
    change. Callers pass the date their last one covered.

    Compared against the most recent snapshot at or before `since`, never
    today's: today's snapshot is today's state, and nothing would ever appear
    to have moved. An engagement with no earlier snapshot is new to the
    comparison and is reported as such rather than as a change from green —
    it never was green, and inventing a previous state is inventing a trend.
    """
    since = since or (_today() - timedelta(days=1))
    if not health:
        return []
    ids = [h["id"] for h in health]
    marks = ",".join("?" * len(ids))
    prior = {
        r["engagement_id"]: r["health"]
        for r in db.query(
            "SELECT engagement_id, health FROM health_snapshots s"  # noqa: S608 — placeholders only
            f" WHERE engagement_id IN ({marks}) AND day <= ?"
            " AND day = (SELECT MAX(day) FROM health_snapshots"
            "            WHERE engagement_id = s.engagement_id AND day <= ?)",
            (*ids, since.isoformat(), since.isoformat()),
        )
    }
    out = []
    for h in health:
        was = prior.get(h["id"])
        if was == h["health"]:
            continue
        out.append(
            {
                "id": h["id"],
                "name": h["name"],
                "from": was,  # None = first time this engagement is scored
                "to": h["health"],
            }
        )
    return out


def _iso_week(day: str) -> str:
    """The ISO week label a YYYY-MM-DD belongs to, in the shape committed_week
    stores (2026-W31). Takes a DATE STRING, which is what makes it worth a
    helper: the interrupt ratio compares its output against that column, and
    the conversion from a stored timestamp has to happen first. Callers that
    already hold a date object format it inline."""
    iso = date.fromisoformat(day).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


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


def _linked_blockers(engagement_id: int, viewer: scope.Viewer = scope.NOBODY) -> list[dict]:
    # BOTH sides of the join carry the filter. Only the blockers side would let
    # a workspace blocker on a crew task through, and only the tasks side would
    # let a crew blocker on a workspace task through — and the engagement
    # pack (services/context_pack.py) and the handoff both read this, the
    # second of which writes its body to an artifact file on disk.
    bfrag, bp = scope.visible_filter(viewer, "blockers", "b")
    tfrag, tp = scope.visible_filter(viewer, "tasks", "t")
    return db.query(
        f"SELECT b.* FROM blockers b JOIN tasks t ON t.id = b.task_id AND {tfrag}"  # noqa: S608 — scope.visible_filter emits only bound marks
        f" WHERE {bfrag}"
        " AND (t.engagement_id = ? OR t.milestone_id IN (SELECT id FROM milestones WHERE engagement_id = ?))"
        " AND b.status != 'resolved'",
        (*tp, *bp, engagement_id, engagement_id),
    )


def engagement_health(
    viewer: scope.Viewer = scope.NOBODY, *, name_assignees: bool = True
) -> list[dict]:
    """R/Y/G per non-closed engagement, each signal listed as a receipt.

    Filtered, defaulting to NOBODY: /portfolio passes the caller's viewer, and
    the exec readout (services/readout.py) writes a markdown file with no
    viewer at all — which is exactly the workspace tier this default gives it.

    name_assignees=False drops the one receipt that carries a person's name.
    Callers that EGRESS their output pass it — see the stale-WIP receipt below.
    """
    today = _today().isoformat()
    # stale_cutoff and silence_cutoff bound updated_at, a timestamp column, so
    # they are instants. Bound to a bare local date they begin at UTC midnight
    # and call work stale up to a day early west of UTC.
    stale_cutoff = db.local_midnight_utc(_today() - timedelta(days=STALE_WIP_DAYS))
    silence_cutoff = db.local_midnight_utc(_today() - timedelta(days=SILENCE_DAYS))
    frag, vp = scope.visible_filter(viewer, "engagements")
    engagements = db.query(
        f"SELECT * FROM engagements WHERE status != 'closed' AND {frag} ORDER BY id",  # noqa: S608 — scope.visible_filter emits only bound marks
        tuple(vp),
    )
    # Four batched scans grouped in Python, not per-engagement queries: at
    # ~6 queries per engagement plus one per waiting task, a growing
    # portfolio multiplies /portfolio and exec-readout latency.
    # A task can reach an engagement two ways (its own engagement_id, or its
    # milestone's) — the id set below dedups the two paths, and a task whose
    # two paths reach DIFFERENT engagements counts toward both.
    overdue_by: dict[int, list[dict]] = {}
    # each receipt scan carries the SAME viewer as the engagement list above,
    # not WORKSPACE_ONLY: a receipt quotes the row's own title, and the exec
    # readout reaches here with NOBODY, which is the workspace tier anyway
    mfrag, mp = scope.visible_filter(viewer, "milestones")
    for m in db.query(
        f"SELECT id, title, due_date, engagement_id FROM milestones WHERE {mfrag}"  # noqa: S608 — scope.visible_filter emits only bound marks
        " AND status != 'done' AND due_date IS NOT NULL AND due_date < ? ORDER BY id",
        (*mp, today),
    ):
        overdue_by.setdefault(m["engagement_id"], []).append(m)
    blockers_by: dict[int, list[dict]] = {}
    bfrag, bp = scope.visible_filter(viewer, "blockers", "b")
    tfrag, tp = scope.visible_filter(viewer, "tasks", "t")
    for b in db.query(
        "SELECT b.*, t.engagement_id AS t_eng, m.engagement_id AS m_eng"  # noqa: S608 — scope.visible_filter emits only bound marks
        f" FROM blockers b JOIN tasks t ON t.id = b.task_id AND {tfrag}"
        " LEFT JOIN milestones m ON m.id = t.milestone_id"
        f" WHERE b.status != 'resolved' AND {bfrag} ORDER BY b.id",
        (*tp, *bp),
    ):
        for eng_id in {b.pop("t_eng"), b.pop("m_eng")} - {None}:
            blockers_by.setdefault(eng_id, []).append(b)
    stale_by: dict[int, list[dict]] = {}
    waits_by: dict[int, list[dict]] = {}
    last_by: dict[int, str] = {}
    open_by: dict[int, int] = {}
    all_waits: list[dict] = []
    for t in db.query(
        "SELECT t.id, t.title, t.assignee, t.status, t.updated_at,"  # noqa: S608 — scope.visible_filter emits only bound marks
        " t.waiting_on_type, t.waiting_on_id,"
        " t.engagement_id AS t_eng, m.engagement_id AS m_eng"
        " FROM tasks t LEFT JOIN milestones m ON m.id = t.milestone_id"
        f" WHERE {tfrag} ORDER BY t.id",
        tuple(tp),
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
            # The assignee is named for a surface that asks "who do I go talk
            # to" — planning the future, the permitted direction. It is
            # dropped for a caller that will WRITE this receipt into a
            # forwardable artifact: "@ava let this sit for over a week", in
            # front of an audience outside the team, is the past-judging use
            # the anti-surveillance rule exists to prevent (readout.py passes
            # name_assignees=False, and the aggregate WIP line there has the
            # full reasoning).
            who = f" (@{t['assignee'] or 'unassigned'})" if name_assignees else ""
            receipts.append(f"task #{t['id']} '{t['title']}' in progress >{STALE_WIP_DAYS}d{who}")
        for t in waits_by.get(eng["id"], []):
            if (t["waiting_on_type"], t["waiting_on_id"]) in satisfied:
                continue
            receipts.append(
                f"task #{t['id']} '{t['title']}' waiting on"
                f" {t['waiting_on_type']} #{t['waiting_on_id']}"
            )
        last_ts = last_by.get(eng["id"], "")
        # the WHOLE timestamp against a timestamp bound: last_ts[:10] is a
        # UTC date, and silence_cutoff is now an instant, so the sliced
        # form compares a 10-character prefix and calls an engagement
        # silent up to a day early
        silent = bool(open_by.get(eng["id"]) and last_ts and last_ts < silence_cutoff)
        if silent:
            # local_day, not [:10]: the comment above says that slice is a
            # UTC date, and this receipt is READ BY A HUMAN in the forwarded
            # exec readout. Naming the hazard on one line and committing it on
            # the next is the strongest possible invitation to fix the wrong
            # one — so the render converts, like every other date a person sees.
            receipts.append(f"no task activity since {db.local_day(last_ts)}")

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


def allocation_conflicts(viewer: scope.Viewer = scope.NOBODY) -> list[dict]:
    """People allocated >100% across non-closed engagements whose windows
    cover today (open-ended allocations always count). Sums every tier and
    masks the names the viewer may not read (scope.visible_name)."""
    today = _today().isoformat()
    name, np = scope.visible_name(viewer, "engagements", "e.name", alias="e")
    return db.query(
        "SELECT a.person, SUM(a.percent) AS total_percent,"  # noqa: S608 — scope.visible_name emits only bound marks
        f" GROUP_CONCAT({name} || ' (' || a.percent || '%)', ', ') AS detail"
        " FROM allocations a JOIN engagements e ON e.id = a.engagement_id"
        " WHERE e.status != 'closed'"
        " AND (a.starts_on IS NULL OR a.starts_on <= ?)"
        " AND (a.ends_on IS NULL OR a.ends_on >= ?)"
        " GROUP BY a.person HAVING total_percent > 100"
        " ORDER BY total_percent DESC",
        (*np, today, today),
    )


def capacity_ahead(weeks: int = 6, viewer: scope.Viewer = scope.NOBODY) -> list[dict]:
    """Allocation per person per week, for the next `weeks` weeks.

    allocation_conflicts and what_if both bind their window predicates to
    TODAY, so they answer one question: who is over right now. The staffing
    questions a manager actually holds are forward-shaped — "who frees up when
    Atlas closes in three weeks", "does accepting this conflict with Dana's
    allocation that STARTS next month" — and a request accepted today against
    today's numbers is a conflict Skein notices only when the date arrives.

    A table, one row per week, never a Gantt: docs/ROADMAP.md records the Gantt
    as a deliberate refusal, and a week grid answers the staffing question
    without pretending to schedule the work.

    Person-level and forward-looking, which is the direction the
    anti-surveillance rule permits — this plans the future, it does not score
    the past, so `a.person` is served raw. It is the ENGAGEMENT name that
    scope.visible_name masks, the way allocation_conflicts masks the same
    column.
    """
    weeks = max(1, min(weeks, 26))
    monday = _today() - timedelta(days=_today().weekday())
    name, np = scope.visible_name(viewer, "engagements", "e.name", alias="e")
    out = []
    for i in range(weeks):
        start = monday + timedelta(weeks=i)
        end = start + timedelta(days=6)
        rows = db.query(
            "SELECT a.person, SUM(a.percent) AS total_percent,"  # noqa: S608 — scope.visible_name emits only bound marks
            f" GROUP_CONCAT({name} || ' (' || a.percent || '%)', ', ') AS detail"
            " FROM allocations a JOIN engagements e ON e.id = a.engagement_id"
            " WHERE e.status != 'closed'"
            # overlap, not containment: an allocation that covers ANY day of
            # the week loads that week. Containment would hide every
            # allocation shorter than seven days.
            " AND (a.starts_on IS NULL OR a.starts_on <= ?)"
            " AND (a.ends_on IS NULL OR a.ends_on >= ?)"
            " GROUP BY a.person ORDER BY total_percent DESC",
            (*np, end.isoformat(), start.isoformat()),
        )
        # visibility rides along, because the KIND is masked on a scoped row.
        # services/absences.py::away_today makes the same split at length: the
        # unavailability is the honest core, the REASON is not, and a private
        # `focus` or `oncall` window that announced itself by name is the leak
        # that rule exists to stop. Unmasked here it reached every CurrentUser
        # through GET /api/planning.
        away = db.query(
            "SELECT person, kind, visibility FROM absences WHERE starts_on <= ? AND ends_on >= ?",
            (end.isoformat(), start.isoformat()),
        )
        iso = start.isocalendar()
        out.append(
            {
                "week": f"{iso.year}-W{iso.week:02d}",
                "starts_on": start.isoformat(),
                "people": rows,
                "over": [r["person"] for r in rows if (r["total_percent"] or 0) > 100],
                # away is capacity the allocations do not know about — a
                # person at 100% who is on PTO that week is not staffed, and
                # a grid that showed only the percent would say they were
                "away": [
                    {
                        "person": a["person"],
                        "kind": a["kind"] if a["visibility"] == scope.WORKSPACE else "away",
                    }
                    for a in away
                ],
            }
        )
    return out


def flow_metrics(weeks: int = 8, *, name_people: bool = True) -> dict:
    """Cycle time / throughput / WIP from timestamps the platform already has.
    No estimates, no story points.

    name_people=False aggregates the two person-named parts — wip_by_person
    and the stale_wip assignees — for a caller whose output EGRESSES. Same
    split, and the same reason, as engagement_health's name_assignees: an
    agent's reply is text a manager pastes somewhere, and this function is
    judging the past. `team_capacity` is the planning-shaped read an agent
    should reach for when the question is who has room."""
    cutoff = db.local_midnight_utc(_today() - timedelta(weeks=weeks))
    # WORKSPACE_ONLY, like the `stale` read below and like every rule in
    # services/insights.py. This function has no viewer — every caller is
    # team-wide or egressing (the exec readout, the interrupt_load finding,
    # the cockpit, the agent tool) — so counting private and crew tasks put
    # work the audience cannot see into a number presented to them as theirs.
    # A denominator that includes rows a reader will never find is a wrong
    # number before it is a leak.
    done = db.query(
        "SELECT created_at, completed_at, committed_week,"
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
        # local_day, not [:10] — that slice is the UTC day, and the current
        # week this chart is plotted against comes from db.today(). Mixed, a
        # task finished on Sunday evening in New York lands in NEXT week's bar
        d = date.fromisoformat(db.local_day(r["completed_at"])).isocalendar()
        throughput[f"{d.year}-W{d.week:02d}"] = throughput.get(f"{d.year}-W{d.week:02d}", 0) + 1
    # Interrupt ratio: work that arrived AND finished inside the same week,
    # having never been on that week's committed line. It is the receipt
    # behind a weak kept-% — "we planned badly" and "we absorbed an incident"
    # have opposite remedies, and the commitment line alone cannot tell them
    # apart. Derived, so it costs nobody a new habit.
    #
    # TEAM ratio only, never per person: this judges the past, and the
    # anti-surveillance rule allows person-level data only for planning the
    # future (docs/FEATURES.md).
    planned = unplanned = 0
    for r in done:
        if not r["completed_at"]:
            continue
        finished_week = _iso_week(db.local_day(r["completed_at"]))
        if r["committed_week"] == finished_week:
            planned += 1
        elif r["created_at"] and _iso_week(db.local_day(r["created_at"])) == finished_week:
            unplanned += 1
    settled = planned + unplanned
    interrupts = {
        "planned": planned,
        "unplanned": unplanned,
        # NAMED for what it measures. The denominator is planned + unplanned,
        # and a task that CARRIED OVER — committed to an earlier week, finished
        # in this one — is in neither, so this is not a share of all finished
        # work. Carryover is exactly what a weak kept-% is about, so a field
        # called `unplanned_share` beside a sentence saying "of finished work"
        # would overstate on the number people quote.
        "same_week_unplanned_share": (
            round(unplanned / settled, 2)
            # withheld under the n floor docs/INSIGHTS.md sets for every other
            # verdict here: "50% of work was unplanned" over two tasks is noise
            # wearing a percentage sign, and this renders on the manager's page
            if settled >= VERDICT_FLOOR_N
            else None
        ),
        "n": settled,
        "carried_over": len(done) - settled,
        "window_weeks": weeks,
    }
    wip = db.query(
        "SELECT COALESCE(NULLIF(assignee, ''), 'unassigned') AS person,"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        f" COUNT(*) AS in_progress FROM tasks WHERE status = 'in_progress' AND {WORKSPACE_ONLY}"
        " GROUP BY person ORDER BY in_progress DESC"
    )
    stale_cutoff = db.local_midnight_utc(_today() - timedelta(days=STALE_WIP_DAYS))
    stale = db.query(
        # this list carries TITLES on top of the tier the counts above now
        # share, and nudge_stale_wip notifies each assignee by name from it
        "SELECT id, title, assignee,"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        " CAST(julianday('now') - julianday(updated_at) AS INTEGER) AS days_stale"
        f" FROM tasks WHERE status = 'in_progress' AND updated_at < ? AND {WORKSPACE_ONLY}"
        " ORDER BY updated_at",
        (stale_cutoff,),
    )
    return {
        "cycle_time": cycle,
        "throughput_by_week": dict(sorted(throughput.items())),
        # the two person-named parts, collapsed for an egressing caller
        "wip_by_person": wip if name_people else [],
        "wip_total": sum(int(w["in_progress"]) for w in wip),
        "wip_people": len(wip),
        "stale_wip": (
            stale
            if name_people
            else [{k: v for k, v in t.items() if k != "assignee"} for t in stale]
        ),
        "interrupts": interrupts,
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
            # four options, not three: the question this replaced ("Still
            # real?") carried "the task may no longer matter", which a bare
            # list of actions drops. Stated, because the string carries a
            # number and a count is never phrased as a question.
            " Split it, unblock it, put it back in the pool, or close it if"
            " it no longer matters.",
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
        # both sides of the join: the forecast names milestone TITLES and is
        # written to a snapshot table by the daily job, which has no viewer
        f"SELECT m.* FROM milestones m JOIN engagements e ON e.id = m.engagement_id AND e.{WORKSPACE_ONLY}"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        f" WHERE m.{WORKSPACE_ONLY} AND e.status != 'closed' AND e.kind != 'experiment'"  # timeboxed, not deadlined
        " AND m.status != 'done' AND m.due_date IS NOT NULL ORDER BY m.due_date"
    )
    # one waiting-task query for all open milestones, one resolution query
    # per target type — per-milestone and per-wait probes multiply with the
    # portfolio, and this runs in the daily forecast snapshot too
    waits_by: dict[int, list[dict]] = {}
    if open_ms:
        marks = ", ".join("?" for _ in open_ms)
        for w in db.query(
            # WORKSPACE_ONLY even though open_ms is already workspace-filtered:
            # a PRIVATE task can link to a workspace milestone, and this emits
            # the id it waits on into forecast_snapshots, which the daily job
            # writes with no viewer. The forecast DATE is the median slip and
            # does not read this list, so dropping the row costs an annotation
            # and not a number.
            f"SELECT id, milestone_id, waiting_on_type, waiting_on_id FROM tasks"  # noqa: S608 — placeholders built above, and scope.WORKSPACE_ONLY is a module constant
            f" WHERE {WORKSPACE_ONLY} AND milestone_id IN ({marks}) AND status != 'done'"
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


def what_if(
    request_id: int, people: list[str], percent: int = 50, viewer: scope.Viewer = scope.NOBODY
) -> dict:
    """Project team capacity if this intake request were accepted and staffed
    with `people` at `percent` each."""
    if not 1 <= percent <= 100:
        raise ValueError("percent must be 1-100")
    if not people:
        raise ValueError("name at least one person to staff")
    # what_if reports the request's TITLE and the id comes straight off the
    # URL, so this is filtered on the CALLER. Hardcoded to the workspace
    # tier it refused a crew member the request GET /api/intake had just shown
    # them, with a message that says the request does not exist.
    frag, fp = scope.visible_filter(viewer, "intake_requests")
    req = db.query_one(
        f"SELECT * FROM intake_requests WHERE id = ? AND {frag}",  # noqa: S608 — scope.visible_filter emits only bound marks
        (request_id, *fp),
    )
    if not req:
        raise scope.missing("intake_requests", request_id)
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
