"""Exec readout: curated executive projection — never a raw table dump.
Lives outside portfolio.py so portfolio and insights never import each other;
this module is the one place that composes both."""

from datetime import date, timedelta
from pathlib import Path

from .. import config, db
from .insights import digest_findings
from .portfolio import allocation_conflicts, engagement_health, flow_metrics, health_changes
from .pulse import season
from .scope import WORKSPACE_ONLY
from .wording import count


def _today() -> date:
    """The team's day (config.SKEIN_TZ), not the UTC day — see db.today()."""
    return db.today()


def _wip_summary(tasks: int, people: int) -> str:
    """Team totals for a forwardable artifact — see the call site for why the
    names do not travel. Takes the COUNTS, not the per-person list: this
    module asks flow_metrics for the aggregated shape, so the list it used to
    sum is empty here by design. Sentence-form counts agree with their nouns
    (CLAUDE.md wording): "1 task across 1 person", "4 tasks across 2 people"."""
    if not people:
        return "none in progress"
    return (
        f"{tasks} task{'' if tasks == 1 else 's'} in progress"
        f" across {people} {'person' if people == 1 else 'people'}"
    )


def exec_readout(*, actor: str = "system") -> dict:
    """Written as a markdown artifact so it can be forwarded as-is."""
    # name_assignees=False: this markdown is built to be forwarded
    health = engagement_health(name_assignees=False)
    conflicts = allocation_conflicts()
    # name_people=False for the same reason engagement_health takes
    # name_assignees=False below: this markdown is built to be forwarded
    flow = flow_metrics(name_people=False)
    s = season()
    shipped = db.query(
        f"SELECT name, closed_at FROM engagements WHERE status = 'closed' AND {WORKSPACE_ONLY}"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        " AND closed_at >= ? ORDER BY closed_at DESC",
        (s["start_ts"],),  # a timestamp column — services/pulse.py::season
    )
    due_soon = db.query(
        # direction = 'given' — the readout LEAVES, and a promise made TO the
        # team listed under "our external promises" tells a stakeholder the
        # opposite of the truth
        f"SELECT * FROM promises WHERE status = 'open' AND audience = 'external'"  # noqa: S608 — scope filters emit only bound marks
        f" AND direction = 'given' AND {WORKSPACE_ONLY}"
        " AND due_date IS NOT NULL AND due_date <= ? ORDER BY due_date",
        ((_today() + timedelta(days=14)).isoformat(),),
    )
    escalated = db.query(
        f"SELECT * FROM blockers WHERE status = 'escalated' AND {WORKSPACE_ONLY}"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
    )

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
    # Direction, before state. A readout that lists colors makes the reader
    # diff it against the last one by eye across two markdown files, which is
    # the work this section exists to remove.

    # the date the PREVIOUS readout covered, so the heading is true. Without
    # it the section compares back to yesterday and silently swallows every
    # change that happened earlier in the week — for a weekly artifact, six
    # days out of seven.
    prior = db.query_one(
        "SELECT created_at FROM artifacts WHERE kind = 'readout' AND created_at < ?"
        " ORDER BY created_at DESC LIMIT 1",
        (db.now(),),
    )
    # local_day, never [:10]: artifacts.created_at is a UTC timestamp and
    # health_snapshots.day is the TEAM day (migrations/006). Sliced, a readout
    # written after 20:00 Eastern yields tomorrow's key, health_changes then
    # selects TODAY's snapshot, every engagement compares equal, and the whole
    # delta section vanishes with no error.
    since = date.fromisoformat(db.local_day(prior["created_at"])) if prior else None
    # filtered BEFORE the heading: a first-ever observation is not a change,
    # and on the first run after health snapshots shipped every engagement had
    # one — the section printed a verdict heading with nothing under it, in a
    # document built to be forwarded
    moved = [m for m in health_changes(health, since) if m["from"]]
    if moved:
        lines += [
            "",
            # "yesterday", not "the last check": `check` is reserved for the user
            # action (docs/LEXICON.md), and the fallback fires exactly when
            # health_changes defaulted to yesterday — so any other word here
            # claims a window the code did not use, in a forwardable document
            f"## What changed since {since.isoformat() if since else 'yesterday'}",
        ]
        for m in moved:
            lines.append(f"- {dot[m['to']]} **{m['name']}**: {m['from']} → {m['to']}")

    lines += ["", "## Shipped this season"]
    # local_day, not [:10] — the rule this file states 19 lines above and
    # then broke here. closed_at is a UTC timestamp, and this artifact is
    # forwarded outside the team, so the slice ships a date a reader in the
    # team's zone did not experience.
    lines += [f"- {r['name']} ({db.local_day(r['closed_at'])})" for r in shipped] or ["- none yet"]
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
        f"- {count(ct['tasks_done'], 'task')} done in 8 weeks"
        + (
            f", median cycle {ct['median_days']}d, avg {ct['avg_days']}d"
            if ct["tasks_done"]
            else ""
        ),
        # AGGREGATED on purpose, never per person. This artifact is built to
        # be forwarded outside the team, and the anti-surveillance rule allows
        # person-level data only for planning the future. Named WIP counts
        # here are person-level data judging the past, in front of the exact
        # audience the rule exists to keep it from — and the honest standups
        # the rest of the product runs on are what that costs.
        # flow["wip_by_person"] stays available to /portfolio, which is a
        # planning surface with a viewer. Do not re-expand this line.
        "- WIP: " + _wip_summary(flow["wip_total"], flow["wip_people"]),
    ]
    markdown = "\n".join(lines)

    readout_dir = Path(config.DATA_DIR) / "artifacts" / "portfolio"
    readout_dir.mkdir(parents=True, exist_ok=True)
    path = readout_dir / f"{_today().isoformat()}-exec-readout.md"
    path.write_text(markdown, encoding="utf-8")
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
