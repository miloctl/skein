"""One ranked queue of what a manager could actually do something about.

Skein computes the manager's evidence in four engines that never met: the
findings rules, engagement health, the blocker register, and the decision
half-life sweep. Each has its own page, its own ordering and its own vocabulary,
so answering "what needs me most this week" meant opening four surfaces and
holding the answer in your head. The evidence was all there and the ranking was
the manager's job.

Composition only — no table, no write path, no new habit. Every row here is a
row one of those engines already produced, restated in one shape and ordered by
consequence. The receipt travels with it, so a reader can disagree with the
ranking without leaving the page.

Deliberately NOT a score anybody sees. The rank exists to order the list; the
row says what is true and what to do about it, and a manager who reads the
receipt and skips the item has used this correctly.
"""

from datetime import date, timedelta

from .. import db
from . import refs, scope
from .slas import STALE_WIP_DAYS

# What each condition is worth, before its own aging and reach multiply it.
# These are not measurements — they are an ordering the team can argue with,
# written in one place so the argument has somewhere to happen. The rule they
# encode: a commitment already broken outranks one about to break, and both
# outrank a thing that is merely untidy.
_WEIGHT = {
    "blocker_escalated": 100,
    "engagement_red": 90,
    "promise_overdue": 80,
    "finding_high": 70,
    "milestone_overdue": 60,
    "engagement_yellow": 40,
    "finding_medium": 35,
    "decision_stale": 30,
    "work_unowned": 25,
    "stale_wip": 20,
    "finding_low": 10,
}


def _age_days(stamp: str | None) -> int:
    """Whole days since a stored timestamp or date, floored at 0.

    A future date scores 0 rather than a negative: a due date three days out is
    not less urgent than one due tomorrow by the same arithmetic that makes an
    overdue one urgent, and a negative multiplier would sort it above genuine
    breaches.
    """
    if not stamp:
        return 0
    try:
        gap = db.today() - date.fromisoformat(stamp[:10])
    except ValueError:
        return 0
    return max(0, gap.days)


def _rank(kind: str, *, age: int = 0, reach: int = 0) -> int:
    """Weight, aged, then multiplied by how many people it holds up.

    Age is capped at 30 days of contribution. Past that a thing is not getting
    more urgent, it is getting ignored — and without the cap one forgotten row
    from last quarter sits permanently at the top of every manager's list,
    which is how a ranked queue stops being read.
    """
    return _WEIGHT.get(kind, 10) + min(age, 30) * 2 + reach * 5


def interventions(viewer: scope.Viewer = scope.NOBODY, limit: int = 12) -> list[dict]:
    """The manager's queue, most consequential first.

    Viewer-scoped at every read: this composes rows that carry tiers, and a
    queue assembled from rows the caller cannot open would leak both the row
    and the fact that it exists.
    """
    from .insights import list_findings
    from .portfolio import engagement_health
    from .work import downstream

    out: list[dict] = []
    today = db.today().isoformat()

    # 1. Engagement health. The receipts are already written; this adds the
    #    ordering and the one thing health never said — what to do about it.
    for eng in engagement_health(viewer):
        if eng["health"] == "green":
            continue
        red = eng["health"] == "red"
        out.append(
            {
                "kind": "engagement_red" if red else "engagement_yellow",
                "entity": "engagement",
                "entity_id": eng["id"],
                "title": eng["name"],
                "condition": f"{eng['name']} is {eng['health']}",
                "owner": eng["lead"] or "",
                "action": (
                    "Read the receipts and decide: re-plan, re-staff, or accept the date"
                    if red
                    else "Check the receipts before this becomes a re-plan"
                ),
                "receipts": [refs.receipt(r) for r in eng["receipts"]],
                "rank": _rank(
                    "engagement_red" if red else "engagement_yellow",
                    reach=len(eng["receipts"]),
                ),
                "link": f"/dashboard#engagement-{eng['id']}",
            }
        )

    # 2. Escalated blockers. The register escalates on a clock and nothing
    #    ranked the result against anything else the manager was looking at.
    bfrag, bp = scope.visible_filter(viewer, "blockers", alias="b")
    for b in db.query(
        f"SELECT b.id, b.title, b.owner, b.impact, b.escalated_at, b.created_at, b.task_id"  # noqa: S608 — scope.visible_filter emits only bound marks
        f" FROM blockers b WHERE b.status = 'escalated' AND {bfrag} ORDER BY b.created_at",
        tuple(bp),
    ):
        # how much work this blocker is holding up, through the task it blocks
        reach = len(downstream(b["task_id"], viewer)["unblocks"]) if b["task_id"] else 0
        out.append(
            {
                "kind": "blocker_escalated",
                "entity": "blocker",
                "entity_id": b["id"],
                "title": b["title"],
                "condition": f"blocker #{b['id']} escalated at {b['impact']} impact",
                "owner": b["owner"] or "",
                "action": (
                    f"Unblock {b['owner']} or take it off them"
                    if b["owner"]
                    else "Give it an owner — nobody holds this one"
                ),
                "receipts": [
                    refs.receipt(
                        f"blocker #{b['id']} '{b['title']}' escalated"
                        + (f", holding up {reach} task(s)" if reach else "")
                    )
                ],
                "rank": _rank("blocker_escalated", age=_age_days(b["escalated_at"]), reach=reach),
                "link": "/dashboard#blockers",
            }
        )

    # 3. Overdue external promises. The one class of commitment whose reader is
    #    outside the team and cannot be re-planned by talking to each other.
    for p in db.query(
        f"SELECT id, promise, to_whom, due_date, created_by FROM promises"  # noqa: S608 — module constant
        f" WHERE status = 'open' AND direction = 'given' AND {scope.WORKSPACE_ONLY}"
        " AND due_date IS NOT NULL AND due_date < ? ORDER BY due_date",
        (today,),
    ):
        out.append(
            {
                "kind": "promise_overdue",
                "entity": "promise",
                "entity_id": p["id"],
                "title": p["promise"][:80],
                "condition": f"promise to {p['to_whom'] or 'the team'} is past its date",
                "owner": p["created_by"] or "",
                "action": "Settle it or renegotiate the date — the other side is still waiting",
                "receipts": [refs.receipt(f"promise #{p['id']} was due {p['due_date']}")],
                "rank": _rank("promise_overdue", age=_age_days(p["due_date"])),
                "link": "/portfolio#promises",
            }
        )

    # 4. Work with no owner. Not a findings rule and not a health receipt —
    #    it falls between them, which is exactly why nobody sees it.
    tfrag, tp = scope.visible_filter(viewer, "tasks", alias="t")
    for t in db.query(
        f"SELECT t.id, t.title, t.due_date FROM tasks t"  # noqa: S608 — scope.visible_filter emits only bound marks
        f" WHERE t.assignee = '' AND t.status != 'done' AND {tfrag}"
        " AND t.due_date IS NOT NULL AND t.due_date <= ? ORDER BY t.due_date LIMIT 10",
        (*tp, today),
    ):
        out.append(
            {
                "kind": "work_unowned",
                "entity": "task",
                "entity_id": t["id"],
                "title": t["title"],
                "condition": "due and nobody owns it",
                "owner": "",
                "action": "Assign it or drop it — an unowned due date is nobody's problem",
                "receipts": [refs.receipt(f"task #{t['id']} was due {t['due_date']}")],
                "rank": _rank("work_unowned", age=_age_days(t["due_date"])),
                "link": f"?task={t['id']}",
            }
        )

    # 5. Findings the team has not dispositioned. A dismissed or converted
    #    finding is a decision already made, and re-ranking it here would ask
    #    the manager to make it twice.
    for f in list_findings(weeks=4, limit=30):
        if f["disposition"]:
            continue
        kind = f"finding_{f['severity']}"
        if kind not in _WEIGHT:
            continue
        out.append(
            {
                "kind": kind,
                "entity": "finding",
                "entity_id": f["id"],
                "title": f["message"][:80],
                "condition": f["message"],
                "owner": f["subject"] if f["subject"] and "@" not in f["subject"] else "",
                "action": "Convert it to work, defer it with a date, or dismiss it with a reason",
                "receipts": [refs.receipt(f["message"])],
                "rank": _rank(kind),
                "link": "/insights",
            }
        )

    # 6. Stale decisions the team never re-confirmed. My Day shows a person
    #    only their OWN (services/briefing.py); the ones whose author has moved
    #    on are the ones with nobody left to notice them.
    for d in db.query(
        f"SELECT id, title, decided_by, review_by FROM decisions"  # noqa: S608 — module constant
        f" WHERE status = 'stale' AND {scope.WORKSPACE_ONLY} ORDER BY review_by LIMIT 10"
    ):
        out.append(
            {
                "kind": "decision_stale",
                "entity": "decision",
                "entity_id": d["id"],
                "title": d["title"],
                "condition": "past its review-by date",
                "owner": d["decided_by"] or "",
                "action": "Reconfirm it, supersede it, or hand it to somebody who can",
                "receipts": [
                    refs.receipt(f"decision #{d['id']} was due for review {d['review_by']}")
                ],
                "rank": _rank("decision_stale", age=_age_days(d["review_by"])),
                "link": f"/charter#charter-entry-{d['id']}",
            }
        )

    # 7. Work in progress that has not moved. The flow metrics count it; only
    #    the count was ever shown, and a count names nobody to talk to.
    cutoff = db.local_midnight_utc(db.today() - timedelta(days=STALE_WIP_DAYS))
    for t in db.query(
        f"SELECT t.id, t.title, t.assignee, t.updated_at FROM tasks t"  # noqa: S608 — scope.visible_filter emits only bound marks
        f" WHERE t.status = 'in_progress' AND {tfrag} AND t.updated_at < ?"
        " ORDER BY t.updated_at LIMIT 10",
        (*tp, cutoff),
    ):
        out.append(
            {
                "kind": "stale_wip",
                "entity": "task",
                "entity_id": t["id"],
                "title": t["title"],
                "condition": f"in progress and untouched for over {STALE_WIP_DAYS} days",
                "owner": t["assignee"] or "",
                "action": "Ask what it needs — this is a question, not a nudge",
                "receipts": [
                    refs.receipt(f"task #{t['id']} last moved {db.local_day(t['updated_at'])}")
                ],
                "rank": _rank("stale_wip", age=_age_days(t["updated_at"])),
                "link": f"?task={t['id']}",
            }
        )

    out.sort(key=lambda r: (-r["rank"], r["entity"], r["entity_id"]))
    return out[: max(1, min(int(limit), 50))]
