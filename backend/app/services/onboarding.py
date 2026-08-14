"""First-run onboarding: a checklist computed from real state, not a wizard.
The team gives the tool one honest chance — the first ten minutes must land
someone in a workspace that describes THEIR work, not seed fiction."""

from .. import db

# (id, label, link, hint, scope) — every step must be actionable from the UI:
# the link goes where the step happens, the hint says HOW, so nobody needs to
# have read the docs to finish setup. A hint writes the capture shortcut as
# the literal ⌘K, which is a TOKEN: app/page.tsx renders hints through
# components/shortcut.tsx, which spells it for the reader's own keyboard
# (Ctrl+K off Apple hardware). A link starting with "#" names an action
# on the CURRENT page (app/page.tsx runStep) rather than a route: capture and
# standup both happen on My Day, and pointing them at "/" made the checklist
# item a dead self-link for the first-run reader it exists to help. Personal steps come first — a new
# teammate must never be routed into team-level workflows before they have
# captured a single todo; team facts render as a separate strip.
STEPS = (
    (
        "pick_name",
        "Pick your name so work is attributed to you",
        "/settings",
        "Settings → Identity — the 👤 menu in the top bar takes you there.",
        "you",
    ),
    (
        "first_capture",
        "Capture something",
        "#capture",
        "Select Capture in the top bar — try 'todo: …', 'blocked on …', or 'decision: …'.",
        "you",
    ),
    (
        "first_standup",
        "Post a standup",
        "#standup",
        "Open the Standup card on My Day and write one line. Blockers in it are"
        " filed with an escalation clock.",
        "you",
    ),
    (
        "setup_key",
        "Set up your personal API key",
        "/settings",
        "Needed for private surfaces (1:1s page) and the CLI. Settings"
        " shows the exact command to mint your first one.",
        "you",
    ),
    (
        "first_engagement",
        "Start the team's first engagement",
        "/intake",
        "Submit a request on Inbox → Requests, then accept it.",
        "team",
    ),
    (
        "invite_team",
        "Get a teammate in",
        "/settings",
        "Send them the URL. They pick a name in Settings.",
        "team",
    ),
)


def checklist(user: str) -> dict:
    named = bool(user and user != "anonymous")
    engagements = db.query_row("SELECT COUNT(*) AS n FROM engagements")["n"]
    captures = (
        db.query_row(
            "SELECT COUNT(*) AS n FROM activity WHERE action = 'capture' AND actor = ?", (user,)
        )["n"]
        if named
        else 0
    )
    standups = (
        db.query_row("SELECT COUNT(*) AS n FROM standups WHERE author = ?", (user,))["n"]
        if named
        else 0
    )
    humans = db.query_row(
        "SELECT COUNT(*) AS n FROM users WHERE kind = 'human' AND active = 1"
        " AND name != 'anonymous'"
    )["n"]
    keys = (
        db.query_row("SELECT COUNT(*) AS n FROM api_keys WHERE owner = ? AND active = 1", (user,))[
            "n"
        ]
        if named
        else 0
    )

    done = {
        "pick_name": named,
        "first_engagement": engagements > 0,
        "first_capture": captures > 0,
        "first_standup": standups > 0,
        "invite_team": humans > 1,
        "setup_key": keys > 0,
    }
    steps = [
        {"id": sid, "label": label, "done": done[sid], "link": link, "hint": hint, "scope": scope}
        for sid, label, link, hint, scope in STEPS
    ]
    remaining = [s for s in steps if not s["done"]]
    return {
        "steps": steps,
        "complete": not remaining,
        "next": remaining[0] if remaining else None,
        "progress": f"{len(steps) - len(remaining)}/{len(steps)}",
    }
