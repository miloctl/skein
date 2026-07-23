"""Daily team digest. Assembly is deterministic (SQL); when a model provider
is configured with keys, the digest is additionally narrated by the agent —
otherwise the markdown is published as-is."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import config, db
from . import collab


def _utc_today():
    return datetime.now(timezone.utc).date()


def _stalled_tasks(days: int = 3) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    return db.query(
        "SELECT * FROM tasks WHERE status = 'in_progress' AND updated_at < ?", (cutoff,)
    )


def build_digest() -> str:
    today = _utc_today().isoformat()
    lines = [f"# Daily digest — {today}", ""]

    esc = db.query("SELECT * FROM blockers WHERE status = 'escalated'")
    if esc:
        lines.append("## ⛔ Escalated blockers")
        lines += [f"- #{b['id']} **{b['title']}** (owner: {b['owner'] or 'unowned'},"
                  f" impact: {b['impact']})" for b in esc]
        lines.append("")

    stalled = _stalled_tasks()
    if stalled:
        lines.append("## 🐌 Stalled tasks (no update in 3+ days)")
        lines += [f"- #{t['id']} {t['title']} (@{t['assignee'] or 'unassigned'})" for t in stalled]
        lines.append("")

    open_q = db.query("SELECT * FROM questions WHERE status = 'open' ORDER BY id LIMIT 10")
    if open_q:
        lines.append("## ❓ Unanswered questions")
        lines += [f"- #{q['id']} {q['question']} (→ {q['assigned_to'] or 'unassigned'})"
                  for q in open_q]
        lines.append("")

    week = (_utc_today() + timedelta(days=7)).isoformat()
    due = db.query(
        "SELECT * FROM milestones WHERE status != 'done' AND due_date IS NOT NULL"
        " AND due_date <= ? ORDER BY due_date", (week,),
    )
    if due:
        lines.append("## 🎯 Milestones due within a week")
        lines += [f"- #{m['id']} {m['title']} — due {m['due_date']} ({m['status']})" for m in due]
        lines.append("")

    pending = db.query_one("SELECT COUNT(*) AS n FROM pending_changes WHERE status = 'pending'")
    events = db.query(
        "SELECT * FROM events WHERE starts_at >= ? AND starts_at < ? ORDER BY starts_at",
        (today, (_utc_today() + timedelta(days=1)).isoformat()),
    )
    lines.append("## 📋 Today")
    lines.append(f"- Pending reviews awaiting a human: {pending['n'] if pending else 0}")
    lines += [f"- 📅 {e['starts_at'][11:16] if len(e['starts_at']) > 10 else ''}"
              f" {e['title']}" for e in events]
    if not (esc or stalled or open_q or due):
        lines.append("- All clear: no escalations, stalls, or overdue work. 🎉")
    return "\n".join(lines)


def _narrate(markdown: str) -> str:
    """LLM enhancement point: summarize the digest in 3 sentences on top.
    Silently skipped when no provider/keys are available (mock included)."""
    if config.MODEL_PROVIDER == "mock":
        return markdown
    try:
        from ..agents.team_agent import _model
        from strands import Agent

        agent = Agent(model=_model(), callback_handler=None,
                      system_prompt="You summarize team status digests. Reply with exactly"
                                    " a 2-3 sentence executive summary, nothing else.")
        summary = str(agent(f"Summarize this digest:\n\n{markdown}")).strip()
        return f"> {summary}\n\n{markdown}"
    except Exception:
        return markdown


def publish_digest(*, actor: str = "scheduler", force: bool = False) -> dict:
    today = _utc_today().isoformat()
    if actor == "scheduler" and not force and not db.claim_job("digest", today):
        return {"date": today, "skipped": "already published today"}
    markdown = _narrate(build_digest())

    artifacts_dir = Path(config.DATA_DIR) / "artifacts" / "digests"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    path = artifacts_dir / f"{today}-digest.md"
    path.write_text(markdown)

    db.execute(
        "INSERT INTO artifacts (kind, title, path, created_by, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        ("digest", f"Daily digest {today}", str(path), actor, db.now()),
    )
    collab.save_note(topic=f"digest-{today}", content=markdown, author=actor,
                     actor=actor, origin="agent")
    db.log_activity(actor, "publish_digest", today)
    return {"date": today, "path": str(path), "markdown": markdown}
