"""Exec readout: curated executive projection — never a raw table dump.
Lives outside portfolio.py so portfolio and insights never import each other;
this module is the one place that composes both."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from .. import config, db
from .insights import digest_findings
from .portfolio import allocation_conflicts, engagement_health, flow_metrics
from .pulse import season


def _today() -> date:
    return datetime.now(UTC).date()


def exec_readout(*, actor: str = "system") -> dict:
    """Written as a markdown artifact so it can be forwarded as-is."""
    health = engagement_health()
    conflicts = allocation_conflicts()
    flow = flow_metrics()
    s = season()
    shipped = db.query(
        "SELECT name, closed_at FROM engagements WHERE status = 'closed'"
        " AND closed_at >= ? ORDER BY closed_at DESC",
        (s["start"],),
    )
    due_soon = db.query(
        "SELECT * FROM commitments WHERE status = 'open' AND audience = 'external'"
        " AND due_date IS NOT NULL AND due_date <= ? ORDER BY due_date",
        ((_today() + timedelta(days=14)).isoformat(),),
    )
    escalated = db.query("SELECT * FROM blockers WHERE status = 'escalated'")

    dot = {"red": "🔴", "yellow": "🟡", "green": "🟢"}
    lines = [f"# Exec readout — {_today().isoformat()} ({s['label']})", ""]
    lines.append("## Engagements")
    for h in health:
        lines.append(
            f"- {dot[h['health']]} **{h['name']}** ({h['status']}, lead: {h['lead'] or 'unset'})"
        )
        for r in h["receipts"][:3]:
            lines.append(f"  - {r}")
    if not health:
        lines.append("- none active")
    lines += ["", "## Shipped this season"]
    lines += [f"- {r['name']} ({r['closed_at'][:10]})" for r in shipped] or ["- none yet"]
    lines += ["", "## Top risks"]
    risk_lines = [f"- Escalated blocker #{b['id']}: {b['title']}" for b in escalated]
    risk_lines += [f"- {c['person']} at {c['total_percent']}% ({c['detail']})" for c in conflicts]
    lines += risk_lines or ["- none flagged"]
    findings = digest_findings()
    if findings:
        lines += ["", "## This week's findings"]
        lines += [f"- [{f['severity']}] {f['message']}" for f in findings]
    lines += ["", "## External promises due in 14 days"]
    lines += [
        f"- {c['due_date']}: {c['promise']} (to {c['to_whom'] or 'unspecified'})" for c in due_soon
    ] or ["- none recorded"]
    ct = flow["cycle_time"]
    lines += [
        "",
        "## Flow",
        f"- {ct['tasks_done']} tasks done in 8 weeks"
        + (
            f", median cycle {ct['median_days']}d, avg {ct['avg_days']}d"
            if ct["tasks_done"]
            else ""
        ),
        "- WIP: "
        + (", ".join(f"{w['person']} {w['in_progress']}" for w in flow["wip_by_person"]) or "none"),
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
        db.execute(
            "UPDATE artifacts SET created_by = ?, created_at = ? WHERE id = ?",
            (actor, db.now(), aid),
        )
    else:
        aid = db.execute(
            "INSERT INTO artifacts (engagement_id, kind, title, path, created_by, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (None, "readout", f"Exec readout {_today().isoformat()}", str(path), actor, db.now()),
        )
    db.log_activity(actor, "exec_readout", f"artifact #{aid}")
    return {"artifact_id": aid, "path": str(path), "markdown": markdown}
