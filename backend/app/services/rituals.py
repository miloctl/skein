"""C1: the manager's week, opened and closed by jobs instead of by hand.

Friday close-out sweeps what the week leaves dangling; Monday open briefs
each person on their OWN obligations. Both are pure SQL, produce a markdown
artifact, and notify — attention lands where the ritual used to be
assembled manually from five pages."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from .. import config, db


def _n(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _clean(text: str, width: int = 80) -> str:
    """User text goes into markdown bullets — a newline in a promise must not
    forge a section header in the packet."""
    return " ".join(str(text).split())[:width]


def _claim_week(job: str, week: str, force: bool) -> bool:
    """Claim for EVERY actor — a manual Monday run must stop the scheduler
    from double-briefing the team. Force reruns, but still stamps the claim."""
    claimed = db.claim_job(job, week)
    return claimed or force


def _release_claim(job: str, week: str) -> None:
    """A crash mid-ritual must not eat the week's slot."""
    db.execute("DELETE FROM job_runs WHERE job = ? AND run_key = ?", (job, week))


def _write_artifact(slug: str, title: str, markdown: str, actor: str) -> str:
    day = db.now()[:10]
    out_dir = Path(config.DATA_DIR) / "artifacts" / "rituals"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{day}-{slug}.md"
    path.write_text(markdown)
    # forced same-day reruns overwrite the file — upsert the row to match
    existing = db.query_one("SELECT id FROM artifacts WHERE path = ?", (str(path),))
    if existing:
        db.execute(
            "UPDATE artifacts SET created_by = ?, created_at = ? WHERE id = ?",
            (actor, db.now(), existing["id"]),
        )
    else:
        db.execute(
            "INSERT INTO artifacts (kind, title, path, created_by, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("ritual", title, str(path), actor, db.now()),
        )
    return str(path)


def week_close(*, actor: str = "scheduler", force: bool = False) -> dict:
    """Friday sweep: what this week leaves open — due/overdue promises,
    engagements stuck closing, proposals nobody has judged, questions still
    waiting. One packet, one notification, zero page-hopping."""
    today = datetime.now(UTC).date()
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
    due_commitments = db.query(
        "SELECT id, promise, to_whom, due_date FROM commitments WHERE status = 'open'"
        " AND due_date IS NOT NULL AND due_date <= ? ORDER BY due_date",
        (horizon,),
    )
    stuck_closing = db.query(
        "SELECT id, name, updated_at FROM engagements WHERE status = 'closing'"
        " AND updated_at < ? ORDER BY updated_at",
        ((datetime.now(UTC) - timedelta(days=7)).isoformat(timespec="seconds"),),
    )
    stale_proposals = db.query(
        "SELECT id, summary, proposed_by, created_at FROM pending_changes"
        " WHERE status = 'pending' AND created_at < ? ORDER BY id",
        ((datetime.now(UTC) - timedelta(days=3)).isoformat(timespec="seconds"),),
    )
    open_questions = db.query(
        "SELECT id, question, assigned_to FROM questions WHERE status = 'open' ORDER BY id"
    )

    lines = [f"# Week close-out — {today.isoformat()}", ""]
    sections = [
        (
            "Promises due or overdue",
            [
                f"- #{c['id']} {_clean(c['promise'])}"
                f" ({'to ' + _clean(c['to_whom'], 40) + ', ' if c['to_whom'] else ''}"
                f"due {c['due_date']})"
                for c in due_commitments
            ],
        ),
        (
            "Engagements stuck in 'closing' for 7+ days",
            [
                f"- #{e['id']} {_clean(e['name'])} (since {e['updated_at'][:10]})"
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
    path = _write_artifact("week-close", f"Week close-out {today.isoformat()}", markdown, actor)
    db.log_activity(actor, "week_close", _n(total, "open item"))
    if total:
        from .notifications import notify

        notify(
            "team",
            f"Week close-out: {_n(total, 'item')} need{'s' if total == 1 else ''}"
            f" a decision before Monday — {_n(len(due_commitments), 'promise')},"
            f" {_n(len(stuck_closing), 'stuck engagement')},"
            f" {_n(len(stale_proposals), 'stale proposal')},"
            f" {_n(len(open_questions), 'open question')}.",
            tier="digest",
            link="/portfolio",
        )
    return {"week": week, "items": total, "path": path, "markdown": markdown}


def week_open(*, actor: str = "scheduler", force: bool = False) -> dict:
    """Monday brief: each person's OWN obligations for the week — the
    promises they made, decisions they own past review-by, questions waiting
    on them, tasks due. Personal notifications, team artifact."""
    today = datetime.now(UTC).date()
    week = f"{today.isocalendar().year}-W{today.isocalendar().week:02d}-open"
    if not _claim_week("week_open", week, force):
        return {"week": week, "skipped": "already ran this week"}
    try:
        return _week_open_run(today, week, actor)
    except Exception:
        _release_claim("week_open", week)
        raise


def _week_open_run(today: date, week: str, actor: str) -> dict:
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
        commitments = db.query(
            "SELECT id, promise, due_date FROM commitments WHERE status = 'open'"
            " AND created_by = ? AND (due_date IS NULL OR due_date <= ?) ORDER BY due_date",
            (name, horizon),
        )
        decisions = db.query(
            "SELECT id, title FROM decisions WHERE status = 'stale' AND decided_by = ? ORDER BY id",
            (name,),
        )
        questions = db.query(
            "SELECT id, question FROM questions WHERE status = 'open' AND assigned_to = ?"
            " ORDER BY id",
            (name,),
        )
        tasks = db.query(
            "SELECT id, title, due_date FROM tasks WHERE assignee = ? AND status != 'done'"
            " AND due_date IS NOT NULL AND due_date <= ? ORDER BY due_date",
            (name, horizon),
        )
        n = len(commitments) + len(decisions) + len(questions) + len(tasks)
        if n == 0:
            continue
        briefed += 1
        lines.append(f"## {name} — {_n(n, 'obligation')}")
        lines += [
            f"- promise #{c['id']}: {_clean(c['promise'], 70)}"
            + (f" (due {c['due_date']})" if c["due_date"] else "")
            for c in commitments
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
        if commitments:
            parts.append(_n(len(commitments), "promise"))
        if decisions:
            parts.append(_n(len(decisions), "stale decision"))
        if questions:
            parts.append(_n(len(questions), "question"))
        if tasks:
            parts.append(f"{_n(len(tasks), 'task')} due")
        notify(
            name,
            f"Your week: {', '.join(parts)} carr{'ies' if n == 1 else 'y'}"
            " your name. Details on My Day.",
            tier="digest",
            link="/",
        )
    # commitments carry only created_by (the recorder) — a promise an agent
    # captured belongs to nobody in the loop above and must not go silent
    agent_recorded = db.query(
        "SELECT c.id, c.promise, c.due_date FROM commitments c"
        " JOIN users u ON u.name = c.created_by AND u.kind = 'agent'"
        " WHERE c.status = 'open' AND (c.due_date IS NULL OR c.due_date <= ?)"
        " ORDER BY c.due_date",
        (horizon,),
    )
    if agent_recorded:
        lines.append(
            f"## Recorded by agents — {_n(len(agent_recorded), 'promise')}"
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
    path = _write_artifact("week-open", f"Week open {today.isoformat()}", markdown, actor)
    db.log_activity(actor, "week_open", f"{_n(briefed, 'person')} briefed")
    return {"week": week, "briefed": briefed, "path": path, "markdown": markdown}
