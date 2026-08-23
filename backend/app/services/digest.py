"""Daily team digest. Assembly is deterministic (SQL); when a model provider
is configured with keys, the digest is additionally narrated by the agent —
otherwise the markdown is published as-is."""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from .. import config, db
from . import artifact_files, wording
from .scope import WORKSPACE_ONLY
from .slas import DIGEST_STALLED_DAYS


def _today() -> date:
    """The team's day (config.SKEIN_TZ), not the UTC day — see db.today()."""
    return db.today()


def _stalled_tasks(days: int = DIGEST_STALLED_DAYS) -> list[dict]:
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
    return db.query(
        f"SELECT * FROM tasks WHERE status = 'in_progress' AND updated_at < ? AND {WORKSPACE_ONLY}",  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        (cutoff,),
    )


# Deterministic voice: date-seeded so the whole team sees the same opener —
# shared jokes become team rituals. Suppressed when something is on fire.
OPENERS_CLEAR = (
    "All quiet. Suspiciously quiet. Enjoy it.",
    "Nothing is on fire. This is not a drill — there is genuinely no drill.",
    "The board is clean. Someone frame this digest.",
    "Zero escalations. The blockers fear this team.",
    "Green across the board. Ship something before it gets boring.",
)
OPENERS_BUSY = (
    "Coffee first. Then the blockers.",
    "A short list today — sharp, like the team.",
    "The work below is sorted by how much it wants your attention.",
    "Yesterday happened. Here's what it left behind.",
    "One list, no meetings required.",
)


def _opener(has_escalations: bool, all_clear: bool, seed: str) -> str:
    if has_escalations:
        return ""  # read the room — no jokes during a fire
    pool = OPENERS_CLEAR if all_clear else OPENERS_BUSY
    return pool[sum(ord(c) for c in seed) % len(pool)]


def build_digest() -> str:
    today = _today().isoformat()
    lines = [f"# Daily digest — {today}", ""]

    from .insights import digest_findings

    findings = digest_findings()
    if findings:
        lines.append("## 🔎 Findings this week")
        for f in findings:
            mark = {"high": "🔴", "medium": "🟡", "low": "·", "positive": "🟢"}[f["severity"]]
            lines.append(
                f"- {mark} {wording.flatten(f['message'])}"
                + (f" *(n={f['n']}, {f['window']})*" if f["n"] else "")
            )
        lines.append("")

    esc = db.query(f"SELECT * FROM blockers WHERE status = 'escalated' AND {WORKSPACE_ONLY}")  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
    if esc:
        lines.append("## ⛔ Escalated blockers")
        lines += [
            f"- #{b['id']} **{wording.quoted(b['title'])}** (owner: {b['owner'] or 'unowned'},"
            f" impact: {b['impact']})"
            for b in esc
        ]
        lines.append("")

    stalled = _stalled_tasks()
    if stalled:
        lines.append(f"## 🐌 Stalled tasks (no update in {DIGEST_STALLED_DAYS}+ days)")
        lines += [
            f"- #{t['id']} {wording.quoted(t['title'])} (@{t['assignee'] or 'unassigned'})"
            for t in stalled
        ]
        lines.append("")

    open_q = db.query(
        f"SELECT * FROM questions WHERE status = 'open' AND {WORKSPACE_ONLY} ORDER BY id LIMIT 10"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
    )
    if open_q:
        lines.append("## ❓ Unanswered questions")
        lines += [
            f"- #{q['id']} {wording.quoted(q['question'])} (→ {q['assigned_to'] or 'unassigned'})"
            for q in open_q
        ]
        lines.append("")

    week = (_today() + timedelta(days=7)).isoformat()
    due = db.query(
        f"SELECT * FROM milestones WHERE status != 'done' AND {WORKSPACE_ONLY}"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        " AND due_date IS NOT NULL AND due_date <= ? ORDER BY due_date",
        (week,),
    )
    if due:
        lines.append("## 🎯 Milestones due within a week")
        lines += [
            f"- #{m['id']} {wording.quoted(m['title'])} — due {m['due_date']} ({m['status']})"
            for m in due
        ]
        lines.append("")

    pending = db.query_one("SELECT COUNT(*) AS n FROM pending_changes WHERE status = 'pending'")
    events = db.query(
        f"SELECT * FROM events WHERE starts_at >= ? AND starts_at < ? AND {WORKSPACE_ONLY}"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        " ORDER BY starts_at",
        db.local_event_window(_today()),
    )
    if _today().weekday() == 0:  # Monday: the one-question pulse
        lines.append(
            "## 🌡️ Weekly pulse\n"
            "- Did Skein reduce or increase coordination effort last week?"
            " 👍/👎 on the My Day pulse line — counted as a team tally, never per person.\n"
        )

    lines.append("## 📋 Today")
    lines.append(f"- Pending reviews awaiting a human: {pending['n'] if pending else 0}")
    lines += [
        f"- 📅 {e['starts_at'][11:16] if len(e['starts_at']) > 10 else ''} {wording.quoted(e['title'])}"
        for e in events
    ]
    all_clear = not (esc or stalled or open_q or due)
    if all_clear:
        lines.append("- All clear: no escalations, stalls, or overdue work. 🎉")
    opener = _opener(bool(esc), all_clear, today)
    if opener:
        lines.insert(2, f"*{opener}*\n")
    return "\n".join(lines)


# LLM enhancement point: the agent layer registers a narrator at startup
# (agents/narrator.py) — services never import agents, so publishing works
# identically when the digest runs without an app process (tests, CLI).
_narrator: Callable[[str], str] | None = None


def set_narrator(fn: Callable[[str], str] | None) -> None:
    global _narrator
    _narrator = fn


def _narrate(markdown: str) -> str:
    if _narrator is None:
        return markdown
    try:
        return _narrator(markdown)
    except Exception:
        return markdown


def publish_digest(*, actor: str = "scheduler", force: bool = False) -> dict:
    today = _today().isoformat()
    # Build before the transaction. A narration failure must not take the
    # logical artifact lock or the day's claim.
    markdown = _narrate(build_digest())
    title = f"Daily digest {today}"
    logical = Path(config.DATA_DIR) / "artifacts" / "digests" / f"{today}-digest.md"
    with db.transaction():
        db.name_lock(db.LOCK_ARTIFACT, f"digest:{today}")
        if actor == "scheduler" and not force and not db.claim_job("digest", today):
            return {"date": today, "skipped": "already published today"}
        existing = db.query_one(
            "SELECT id, path FROM artifacts WHERE kind = 'digest' AND title = ?"
            " ORDER BY id LIMIT 1",
            (title,),
        )
        path = artifact_files.unique_revision(logical)
        artifact_files.publish(
            path,
            markdown.encode("utf-8"),
            old=Path(existing["path"]) if existing else None,
        )
        if existing:
            db.execute(
                "UPDATE artifacts SET path = ?, created_by = ?, created_at = ? WHERE id = ?",
                (str(path), actor, db.now(), existing["id"]),
            )
        else:
            db.execute(
                "INSERT INTO artifacts (kind, title, path, created_by, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                ("digest", title, str(path), actor, db.now()),
            )
        # Archived as an artifact only. A note would duplicate every FTS hit.
        db.log_activity(actor, "publish_digest", today)
        return {"date": today, "path": str(path), "markdown": markdown}
