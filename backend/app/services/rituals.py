"""C1: the manager's week, opened and closed by jobs instead of by hand.

Friday close-out sweeps what the week leaves dangling; Monday open briefs
each person on their OWN obligations. Both are pure SQL, produce a markdown
artifact, and notify — attention lands where the ritual used to be
assembled manually from five pages."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from .. import config, db
from . import scope, wording
from .scope import WORKSPACE_ONLY


def _clean(text: str, width: int = 80) -> str:
    """User text goes into markdown bullets — a newline in a promise must not
    forge a section header in the packet. The shared rule lives in
    services/wording.py::flatten, which every generator now uses."""
    return wording.flatten(text, width)


def _claim_week(job: str, week: str, force: bool) -> bool:
    """Claim for EVERY actor — a manual Monday run must stop the scheduler
    from double-briefing the team. Force reruns, but still stamps the claim."""
    claimed = db.claim_job(job, week)
    return claimed or force


def _release_claim(job: str, week: str) -> None:
    """A crash mid-ritual must not eat the week's slot."""
    db.execute("DELETE FROM job_runs WHERE job = ? AND run_key = ?", (job, week))


def _write_artifact(slug: str, title: str, markdown: str, actor: str) -> tuple[int, str]:
    """Returns (artifact id, path). The id is what a caller hands a reader:
    the path is a server-side filename that no browser can open, and the
    ritual's own response is the only place the id is knowable without
    re-listing every artifact and matching on the title."""
    day = db.today().isoformat()  # must match the heading and the claim key
    out_dir = Path(config.DATA_DIR) / "artifacts" / "rituals"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{day}-{slug}.md"
    path.write_text(markdown, encoding="utf-8")
    # forced same-day reruns overwrite the file — upsert the row to match
    existing = db.query_one("SELECT id FROM artifacts WHERE path = ?", (str(path),))
    if existing:
        db.execute(
            "UPDATE artifacts SET created_by = ?, created_at = ? WHERE id = ?",
            (actor, db.now(), existing["id"]),
        )
        return int(existing["id"]), str(path)
    aid = db.execute(
        "INSERT INTO artifacts (kind, title, path, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
        ("ritual", title, str(path), actor, db.now()),
    )
    return int(aid), str(path)


def week_close(*, actor: str = "scheduler", force: bool = False) -> dict:
    """Friday sweep: what this week leaves open — due/overdue promises,
    engagements stuck closing, proposals nobody has judged, questions still
    waiting. One packet, one notification, zero page-hopping."""
    today = db.today()
    week = f"{today.isocalendar().year}-W{today.isocalendar().week:02d}-close"
    if not _claim_week("week_close", week, force):
        return {"week": week, "skipped": "already ran this week"}
    try:
        return _week_close_run(today, week, actor)
    except Exception:
        _release_claim("week_close", week)
        raise


def _week_close_run(today: date, week: str, actor: str) -> dict:
    horizon = (today + timedelta(days=7)).isoformat()
    due_promises = db.query(
        # direction = 'given': the close-out asks what the team owes and has
        # not settled. A received promise is chased by its own hourly job.
        f"SELECT id, promise, to_whom, due_date FROM promises"  # noqa: S608 — scope filters emit only bound marks
        f" WHERE status = 'open' AND direction = 'given' AND {WORKSPACE_ONLY}"
        " AND due_date IS NOT NULL AND due_date <= ? ORDER BY due_date",
        (horizon,),
    )
    stuck_closing = db.query(
        f"SELECT id, name, updated_at FROM engagements WHERE status = 'closing' AND {WORKSPACE_ONLY}"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        " AND updated_at < ? ORDER BY updated_at",
        ((datetime.now(UTC) - timedelta(days=7)).isoformat(timespec="seconds"),),
    )
    # NOBODY, like every other query in this job: the week-close artifact is
    # workspace-tier and its body also reaches job_outcomes.detail. A
    # proposal's `summary` quotes its target row, so an unfiltered read here
    # publishes a crew row's text to the roster and to the export.
    from .review import _readable

    stale_proposals = _readable(
        db.query(
            "SELECT id, entity, entity_id, summary, proposed_by, created_at FROM pending_changes"
            " WHERE status = 'pending' AND created_at < ? ORDER BY id",
            ((datetime.now(UTC) - timedelta(days=3)).isoformat(timespec="seconds"),),
        ),
        scope.NOBODY,
    )
    open_questions = db.query(
        f"SELECT id, question, assigned_to FROM questions WHERE status = 'open'"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        f" AND {WORKSPACE_ONLY} ORDER BY id"
    )

    lines = [f"# Week close-out — {today.isoformat()}", ""]
    sections = [
        (
            "Promises due or overdue",
            [
                f"- #{c['id']} {_clean(c['promise'])}"
                f" ({'to ' + _clean(c['to_whom'], 40) + ', ' if c['to_whom'] else ''}"
                f"due {c['due_date']})"
                for c in due_promises
            ],
        ),
        (
            "Engagements stuck in 'closing' for 7+ days",
            [
                # local_day: updated_at is UTC, and this line goes into a
                # ritual artifact that leaves the team (services/readout.py
                # makes the same conversion for the same reason)
                f"- #{e['id']} {_clean(e['name'])} (since {db.local_day(e['updated_at'])})"
                for e in stuck_closing
            ],
        ),
        (
            "Proposals pending 3+ days",
            [
                f"- #{p['id']} {_clean(p['summary'])} (by {_clean(p['proposed_by'], 40)})"
                for p in stale_proposals
            ],
        ),
        (
            "Questions still open",
            [
                f"- #{q['id']} {_clean(q['question'])}"
                f" (→ {_clean(q['assigned_to'], 40) or 'unassigned'})"
                for q in open_questions
            ],
        ),
    ]
    total = 0
    for title, rows in sections:
        if rows:
            lines += [f"## {title}", *rows, ""]
            total += len(rows)
    if total == 0:
        lines.append("Nothing dangling. Close the laptop — the week is settled.")

    markdown = "\n".join(lines)
    aid, path = _write_artifact(
        "week-close", f"Week close-out {today.isoformat()}", markdown, actor
    )
    db.log_activity(actor, "week_close", wording.count(total, "open item"))
    if total:
        from .notifications import notify

        notify(
            "team",
            f"Week close-out: {wording.count(total, 'item')} need{'s' if total == 1 else ''}"
            f" a decision before Monday — {wording.count(len(due_promises), 'promise')},"
            f" {wording.count(len(stuck_closing), 'stuck engagement')},"
            f" {wording.count(len(stale_proposals), 'stale proposal')},"
            f" {wording.count(len(open_questions), 'open question')}.",
            tier="digest",
            link="/portfolio",
        )
    return {"week": week, "items": total, "artifact_id": aid, "path": path, "markdown": markdown}


def week_open(*, actor: str = "scheduler", force: bool = False) -> dict:
    """Monday brief: each person's OWN obligations for the week — the
    promises they made, decisions they own past review-by, questions waiting
    on them, tasks due. Personal notifications, team artifact."""
    today = db.today()
    week = f"{today.isocalendar().year}-W{today.isocalendar().week:02d}-open"
    if not _claim_week("week_open", week, force):
        return {"week": week, "skipped": "already ran this week"}
    try:
        return _week_open_run(today, week, actor)
    except Exception:
        _release_claim("week_open", week)
        raise


def _week_open_run(today: date, week: str, actor: str) -> dict:
    """Every query below that reads a CLASSIFIED table takes WORKSPACE_ONLY.
    They render into ONE markdown artifact, written at the workspace tier by
    _write_artifact — so a scoped row quoted here reaches the whole roster
    through GET /api/artifacts, the file on disk, and job_outcomes.detail.
    The roster query reads `users`, which carries no tier (scope.UNSCOPED).
    """
    horizon = (today + timedelta(days=7)).isoformat()
    humans = db.query(
        "SELECT name FROM users WHERE kind = 'human' AND active = 1"
        " AND name != 'anonymous' ORDER BY name"
    )
    lines = [f"# Week open — {today.isoformat()}", ""]
    from .notifications import notify

    briefed = 0
    for h in humans:
        name = h["name"]
        promises = db.query(
            # direction = 'given', like the close-out above: this brief tells a
            # person what THEY owe
            f"SELECT id, promise, due_date FROM promises"  # noqa: S608 — scope filters emit only bound marks
            f" WHERE status = 'open' AND direction = 'given' AND {WORKSPACE_ONLY}"
            " AND created_by = ? AND (due_date IS NULL OR due_date <= ?) ORDER BY due_date",
            (name, horizon),
        )
        decisions = db.query(
            f"SELECT id, title FROM decisions WHERE status = 'stale' AND {WORKSPACE_ONLY}"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
            " AND decided_by = ? ORDER BY id",
            (name,),
        )
        questions = db.query(
            f"SELECT id, question FROM questions WHERE status = 'open' AND {WORKSPACE_ONLY}"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
            " AND assigned_to = ? ORDER BY id",
            (name,),
        )
        tasks = db.query(
            f"SELECT id, title, due_date FROM tasks WHERE {WORKSPACE_ONLY} AND assignee = ?"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
            " AND status != 'done' AND due_date IS NOT NULL AND due_date <= ? ORDER BY due_date",
            (name, horizon),
        )
        n = len(promises) + len(decisions) + len(questions) + len(tasks)
        if n == 0:
            continue
        briefed += 1
        lines.append(f"## {name} — {wording.count(n, 'obligation')}")
        lines += [
            f"- promise #{c['id']}: {_clean(c['promise'], 70)}"
            + (f" (due {c['due_date']})" if c["due_date"] else "")
            for c in promises
        ]
        lines += [
            f"- stale decision #{d['id']}: {_clean(d['title'], 70)} — reconfirm or supersede"
            for d in decisions
        ]
        lines += [f"- question #{q['id']}: {_clean(q['question'], 70)}" for q in questions]
        lines += [
            f"- task #{t['id']}: {_clean(t['title'], 70)} (due {t['due_date']})" for t in tasks
        ]
        lines.append("")
        parts = []
        if promises:
            parts.append(wording.count(len(promises), "promise"))
        if decisions:
            parts.append(wording.count(len(decisions), "stale decision"))
        if questions:
            parts.append(wording.count(len(questions), "question"))
        if tasks:
            parts.append(f"{wording.count(len(tasks), 'task')} due")
        notify(
            name,
            f"Your week: {', '.join(parts)} carr{'ies' if n == 1 else 'y'}"
            " your name. Details on My Day.",
            tier="digest",
            link="/",
        )
    # promises carry only created_by (the recorder) — a promise an agent
    # captured belongs to nobody in the loop above and must not go silent
    agent_recorded = db.query(
        "SELECT c.id, c.promise, c.due_date FROM promises c"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        " JOIN users u ON u.name = c.created_by AND u.kind = 'agent'"
        # 'given' only, like every other reader of this table: a RECEIVED
        # promise needs no owner on this side, and the chaser
        # (services/promises.py::chase_received) already watches it
        f" WHERE c.{WORKSPACE_ONLY} AND c.status = 'open' AND c.direction = 'given'"
        " AND (c.due_date IS NULL OR c.due_date <= ?)"
        " ORDER BY c.due_date",
        (horizon,),
    )
    if agent_recorded:
        lines.append(
            f"## Recorded by agents — {wording.count(len(agent_recorded), 'promise')}"
            f" need{'s' if len(agent_recorded) == 1 else ''} an owner"
        )
        lines += [
            f"- promise #{c['id']}: {_clean(c['promise'], 70)}"
            + (f" (due {c['due_date']})" if c["due_date"] else "")
            for c in agent_recorded
        ]
        lines.append("")
    if briefed == 0 and not agent_recorded:
        lines.append("No outstanding obligations — a clean slate of a Monday.")

    markdown = "\n".join(lines)
    aid, path = _write_artifact("week-open", f"Week open {today.isoformat()}", markdown, actor)
    db.log_activity(actor, "week_open", f"{wording.count(briefed, 'person')} briefed")
    return {
        "week": week,
        "briefed": briefed,
        "artifact_id": aid,
        "path": path,
        "markdown": markdown,
    }
