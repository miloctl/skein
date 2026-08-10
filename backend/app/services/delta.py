"""What is NEW since the reader last looked, and nothing they have already read.

Skein's proactive layer is deterministic and it repeats itself. The digest
lists this week's findings every morning, health states its colour every load,
and the manager queue ranks the same rows until somebody acts. All of that is
correct — a condition that still holds is still true — and it is exactly what
teaches a reader to skim: on day three, the fourth identical list is noise
carrying one new line.

This answers the other question. What CHANGED: a condition that started, a
health call that moved, a finding that fired for the first time, a commitment
that broke since the last time this reader was told. Nothing that was already
true and already said.

The reader's own last-seen mark is the whole mechanism. It is a timestamp per
person, so two people who last read on different days get different briefs, and
a person who reads twice in one hour gets an empty second one — which is the
honest answer and the one that keeps the surface worth opening.

Composition only over rows that already exist. A model must never PRODUCE this
list — everything in it carries a receipt, and a summary with no receipt is the
thing this product refuses to ship.
"""

from datetime import date, timedelta

from .. import db
from . import refs, scope

# How many new findings one brief can carry. A reader who has been away a
# fortnight gets the first fifty and the Insights page holds the rest — the
# cap is on the ROWS THAT QUALIFY, so nothing new is cut off ahead of
# something already seen.
FINDING_CAP = 50


def since_mark(user: str) -> str:
    """When this reader last took a delta brief, or a day ago.

    A day, not "the beginning", for a first read: a brief that opens with three
    months of history is a report, and nobody reads a report to find out what
    changed this morning.
    """
    row = db.query_one("SELECT value FROM app_settings WHERE key = ?", (f"delta_seen:{user}",))
    return (row or {}).get("value") or db.local_midnight_utc(db.today() - timedelta(days=1))


def mark_seen(user: str, when: str = "") -> None:
    """Record that this reader has been told. Called by the READ, not by a job:
    a brief nobody opened must still be new tomorrow."""
    now = when or db.now()
    db.execute(
        "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
        " updated_at = excluded.updated_at",
        (f"delta_seen:{user}", now, db.now()),
    )


def brief(user: str, viewer: scope.Viewer = scope.NOBODY, mark: bool = False) -> dict:
    """What changed for this reader since their last brief.

    Viewer-scoped at every read. `mark` is False by default so a caller can
    preview the brief — the chat command shows it without consuming it, and
    only the surface that actually displays it moves the mark.
    """
    from .portfolio import engagement_health, health_changes

    since = since_mark(user)
    # db.local_day, never since[:10]: `since` is a UTC timestamp and every
    # value it is compared against below is derived from db.today(), the TEAM
    # day. The slice answers a different question, and for any zone behind UTC
    # an evening read stores tomorrow's UTC date — after which the promise
    # window is `due_date < today AND due_date >= tomorrow`, empty forever, and
    # health_changes admits today's own snapshot so nothing ever reads as moved.
    # Two of the four arms go silent and the brief renders as "quiet".
    day = db.local_day(since)
    items: list[dict] = []

    # 1. Health that MOVED. `health_changes` compares against the most recent
    #    snapshot at or before `since`, so a colour that has been red all month
    #    is correctly silent here — it is not news, and the manager queue is
    #    where a standing condition belongs.
    for moved in health_changes(engagement_health(viewer), date.fromisoformat(day)):
        worse = _WORSE.get((moved["from"] or "", moved["to"]))
        # a FIRST score of green is not news. Every engagement is unscored
        # until the daily snapshot job has run once, so without this the first
        # brief on a fresh deployment is a list of every green engagement —
        # the exact wall of already-true rows this surface exists to avoid.
        if not moved["from"] and moved["to"] == "green":
            continue
        items.append(
            {
                "kind": "health_moved",
                "entity": "engagement",
                "entity_id": moved["id"],
                "headline": (
                    f"{moved['name']} went {moved['to']}"
                    if moved["from"]
                    else f"{moved['name']} is scored {moved['to']} for the first time"
                ),
                "direction": "worse" if worse else "better" if worse is False else "new",
                "receipts": [
                    refs.receipt(
                        f"engagement #{moved['id']} {moved['from'] or 'unscored'} to {moved['to']}"
                    )
                ],
                "link": f"/engagement/{moved['id']}",
            }
        )

    # 2. Findings that fired for the FIRST time in this window. A rule
    #    re-firing weekly on the same subject is the same news, and the
    #    (rule, subject, week) key means a repeat is a different row — so the
    #    comparison is on the subject, not on the row id.
    seen_subjects = {
        (r["rule_id"], r["subject"])
        for r in db.query("SELECT rule_id, subject FROM findings WHERE created_at < ?", (since,))
    }
    # `created_at >= since` and the disposition test live in the SQL, so the
    # cap applies to the rows that can actually appear. Read through
    # list_findings instead, the LIMIT lands first and its ordering is week
    # then severity — so a busy fortnight cuts a NEW low-severity finding off
    # the end, and the brief reports "quiet" about a window that had news.
    for f in db.query(
        "SELECT id, rule_id, subject, severity, message FROM findings"
        " WHERE created_at >= ? AND id NOT IN (SELECT finding_id FROM finding_dispositions)"
        " ORDER BY created_at, id LIMIT ?",
        (since, FINDING_CAP),
    ):
        if (f["rule_id"], f["subject"]) in seen_subjects:
            continue
        items.append(
            {
                "kind": "finding_new",
                "entity": "finding",
                "entity_id": f["id"],
                "headline": f["message"],
                "direction": "worse",
                "receipts": [refs.receipt(f"finding #{f['id']} ({f['severity']})")],
                "link": "/insights",
            }
        )

    # 3. Commitments that broke in the window. A promise already overdue when
    #    the reader last looked is not news; the day it PASSED its date is.
    for p in db.query(
        f"SELECT id, promise, to_whom, due_date FROM promises"  # noqa: S608 — module constant
        f" WHERE status = 'open' AND direction = 'given' AND {scope.WORKSPACE_ONLY}"
        " AND due_date IS NOT NULL AND due_date < ? AND due_date >= ?"
        " ORDER BY due_date",
        (db.today().isoformat(), day),
    ):
        items.append(
            {
                "kind": "promise_broke",
                "entity": "promise",
                "entity_id": p["id"],
                "headline": f"The promise to {p['to_whom'] or 'the team'} passed its date",
                "direction": "worse",
                "receipts": [refs.receipt(f"promise #{p['id']} was due {p['due_date']}")],
                "link": "/portfolio#promises",
            }
        )

    # 4. Work this reader sponsors that an agent finished asking about. The
    #    sponsor is notified once at submission; this is the standing answer to
    #    "is anything waiting on my verdict", which a dismissed notification
    #    otherwise took away for good.
    tfrag, tp = scope.visible_filter(viewer, "tasks", alias="t")
    for c in db.query(
        # the task carries the tier and this quotes its TITLE, so the join side
        # takes its own filter — being the sponsor is not the same fact as
        # being able to read the row, and only one of them governs a title
        "SELECT p.id, p.entity_id, t.title FROM pending_changes p"  # noqa: S608 — scope.visible_filter emits only bound marks
        f" JOIN tasks t ON t.id = p.entity_id AND {tfrag}"
        " WHERE p.entity = 'task_completion' AND p.status = 'pending'"
        " AND t.sponsor = ? AND p.created_at >= ?",
        (*tp, user, since),
    ):
        items.append(
            {
                "kind": "acceptance_waiting",
                "entity": "proposal",
                "entity_id": c["id"],
                "headline": f"An agent submitted '{c['title']}' for your acceptance",
                "direction": "new",
                "receipts": [refs.receipt(f"proposal #{c['id']} on task #{c['entity_id']}")],
                "link": "/review",
            }
        )

    if mark:
        mark_seen(user)
    return {
        "user": user,
        "since": since,
        "items": items,
        # stated, never implied: an empty brief and a brief nobody computed
        # look identical, and only one of them means the week is quiet
        "quiet": not items,
    }


# Which way a health move went. `None` for a first score: an engagement that
# never had a colour did not get worse, and calling it worse would invent a
# previous state the snapshot never held.
_WORSE = {
    ("green", "yellow"): True,
    ("green", "red"): True,
    ("yellow", "red"): True,
    ("yellow", "green"): False,
    ("red", "yellow"): False,
    ("red", "green"): False,
}
