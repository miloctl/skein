"""First-run onboarding: a checklist computed from real state, not a wizard.
The team gives the tool one honest chance — the first ten minutes must land
someone in a workspace that describes THEIR work, not seed fiction."""

from .. import db

STEPS = (
    ("pick_name", "Pick your name (top right) so work is attributed to you"),
    ("first_engagement",
     "Start your first real engagement — instantiate a playbook or accept an intake request"),
    ("first_capture",
     "Capture something with ⌘K — try 'todo: …', 'blocked on …', or 'decision: …'"),
    ("first_standup", "Post a standup — blockers in it are auto-filed"),
    ("invite_team", "Get a teammate in — the platform is a team sport"),
    ("create_key", "Create your API key for the CLI / git hooks / MCP"),
)


def checklist(user: str) -> dict:
    named = bool(user and user != "anonymous")
    engagements = db.query_row("SELECT COUNT(*) AS n FROM engagements")["n"]
    captures = db.query_row(
        "SELECT COUNT(*) AS n FROM activity WHERE action = 'capture' AND actor = ?",
        (user,))["n"] if named else 0
    standups = db.query_row(
        "SELECT COUNT(*) AS n FROM standups WHERE author = ?", (user,))["n"] \
        if named else 0
    humans = db.query_row(
        "SELECT COUNT(*) AS n FROM users WHERE kind = 'human' AND active = 1"
        " AND name != 'anonymous'")["n"]
    keys = db.query_row(
        "SELECT COUNT(*) AS n FROM api_keys WHERE owner = ? AND active = 1",
        (user,))["n"] if named else 0

    done = {
        "pick_name": named,
        "first_engagement": engagements > 0,
        "first_capture": captures > 0,
        "first_standup": standups > 0,
        "invite_team": humans > 1,
        "create_key": keys > 0,
    }
    steps = [{"id": sid, "label": label, "done": done[sid]} for sid, label in STEPS]
    remaining = [s for s in steps if not s["done"]]
    return {
        "steps": steps,
        "complete": not remaining,
        "next": remaining[0] if remaining else None,
        "progress": f"{len(steps) - len(remaining)}/{len(steps)}",
    }
