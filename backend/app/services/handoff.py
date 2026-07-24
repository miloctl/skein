"""Rotation handoff generator: deterministic assembly of everything the
incoming roster needs, written as a markdown artifact. LLM narrative polish
can be layered on later, same pattern as digest._narrate."""

from pathlib import Path

from .. import config, db


def generate_handoff(engagement_id: int, *, actor: str = "system") -> dict:
    eng = db.query_one("SELECT * FROM engagements WHERE id = ?", (engagement_id,))
    if not eng:
        raise ValueError(f"engagement #{engagement_id} not found")
    name = eng["name"]

    milestones = db.query(
        "SELECT * FROM milestones WHERE engagement_id = ? ORDER BY due_date IS NULL, due_date",
        (engagement_id,),
    )
    mil_ids = [m["id"] for m in milestones]
    tasks = (
        db.query(
            f"SELECT * FROM tasks WHERE milestone_id IN ({','.join('?' * len(mil_ids))})"  # noqa: S608
            " AND status != 'done'",
            tuple(mil_ids),
        )
        if mil_ids
        else []
    )
    from .portfolio import _linked_blockers

    blockers = _linked_blockers(engagement_id)  # this engagement's, not the whole platform's
    questions = db.query("SELECT * FROM questions WHERE status = 'open'")
    decisions = db.query("SELECT * FROM decisions ORDER BY id DESC LIMIT 20")
    pending = db.query("SELECT * FROM pending_changes WHERE status = 'pending'")
    lessons = db.query(
        "SELECT * FROM lessons WHERE engagement_id = ? OR project_class = ?",
        (engagement_id, eng["project_class"]),
    )

    lines = [
        f"# Handoff package — {name}",
        "",
        f"*Class: {eng['project_class']} · Lead: {eng['lead'] or 'unset'} ·"
        f" Status: {eng['status']} · Generated: {db.now()} by {actor}*",
        "",
        f"## Summary\n{eng['summary'] or '(none recorded)'}",
        "",
        "## Milestone status",
    ]
    for m in milestones:
        lines.append(
            f"- [{m['status']}] #{m['id']} {m['title']}"
            + (f" — due {m['due_date']}" if m["due_date"] else "")
        )
    lines += ["", "## Open tasks"]
    lines += [
        f"- [{t['status']}/{t['priority']}] #{t['id']} {t['title']}"
        f" (@{t['assignee'] or 'unassigned'})"
        for t in tasks
    ] or ["- none"]
    lines += ["", "## Unresolved blockers (this engagement)"]
    lines += [
        f"- [{b['status']}/{b['impact']}] #{b['id']} {b['title']}"
        f" (owner: {b['owner'] or 'unowned'})"
        for b in blockers
    ] or ["- none"]
    lines += ["", "## Unanswered questions (team-wide)"]
    lines += [
        f"- #{q['id']} {q['question']} (→ {q['assigned_to'] or 'unassigned'})" for q in questions
    ] or ["- none"]
    lines += ["", "## Recent decisions (with rationale)"]
    lines += [
        f"- **{d['title']}** — {d['decision']}"
        + (f" *(context: {d['context']})*" if d["context"] else "")
        for d in decisions
    ] or ["- none"]
    lines += ["", "## Pending reviews (team-wide)"]
    lines += [f"- #{p['id']} {p['summary']} (proposed by {p['proposed_by']})" for p in pending] or [
        "- none"
    ]
    lines += ["", "## Lessons relevant to this class"]
    lines += [
        f"- {les['lesson']}" + (f" → {les['recommendation']}" if les["recommendation"] else "")
        for les in lessons
    ] or ["- none"]

    markdown = "\n".join(lines)
    safe_name = name.replace("/", "_")
    if safe_name in (".", ".."):
        safe_name = f"engagement-{engagement_id}"
    artifacts_dir = Path(config.DATA_DIR) / "artifacts" / safe_name
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    path = artifacts_dir / f"{db.now()[:10]}-handoff.md"
    path.write_text(markdown)

    aid = db.execute(
        "INSERT INTO artifacts (engagement_id, kind, title, path, created_by, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (engagement_id, "handoff", f"Handoff — {name}", str(path), actor, db.now()),
    )
    db.log_activity(actor, "generate_handoff", f"engagement #{engagement_id} -> artifact #{aid}")
    return {"artifact_id": aid, "path": str(path), "markdown": markdown}


def list_artifacts(engagement_id: int = 0) -> list[dict]:
    if engagement_id:
        return db.query(
            "SELECT * FROM artifacts WHERE engagement_id = ? ORDER BY id DESC", (engagement_id,)
        )
    return db.query("SELECT * FROM artifacts ORDER BY id DESC LIMIT 50")
