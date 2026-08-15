"""The Monday planning cockpit: one read, in meeting order.

Running the week meant touring three pages — /portfolio for the kept-% and the
draft, /intake for the queue, /charter for stale decisions, each with its own
load. The weekly operating rhythm is this product's spine and the person who
runs it had no room built for it.

Composition only. Every number here already had a home; this module owns the
ORDER, which is the part that was missing. Nothing is computed twice and no
new write path exists — a cockpit that could write would be a sixth place to
change a task.

Order is the meeting's order, and it is load-bearing: how last week went
(kept-%, carryover) comes before what this week already holds, which comes
before the weeks ahead, what wants in, what has gone stale, and the one write
the meeting ends with. Reversed, the manager commits the week before seeing
whether the last one landed, which is the mistake the ritual exists to
prevent.

The numbers a reader sees are the CARD TITLES in frontend/app/planning/
page.tsx, not a list here — two numberings of one order drift apart, and a
reader cross-referencing them lands on the wrong card.
"""

from datetime import timedelta

from .. import db
from . import collab, intake, portfolio, promises, scope, stakeholders, weekly, work

# How many direct-waiter leaders get the full transitive walk. Each walk is
# two queries at minimum, so this is the page's bound on a cost that was
# linear in the number of waiting-on edges.
_TOP_CANDIDATES = 5


def cockpit(viewer: scope.Viewer = scope.NOBODY, *, ahead_weeks: int = 6) -> dict:
    """Everything the Monday ritual reads, in the order it is read.

    `viewer` reaches every scoped read, so the cockpit shows exactly what its
    caller may see — a manager without a crew's membership does not learn that
    crew's work exists by opening this page.
    """
    # week_view reads the workspace tier and takes no viewer — the commitment
    # line is the team's shared plan by construction (services/weekly.py)
    week = weekly.week_view()
    last = weekly.week_view(weekly.current_week(-1))
    flow = portfolio.flow_metrics()
    # computed ONCE: engagement_health runs four batched scans, and the delta
    # below needs the same list rather than a second identical pass
    health = portfolio.engagement_health(viewer)
    return {
        # Last week's receipt, read BEFORE this week's plan. Its carryover is
        # the part a kept-% cannot show: 60% kept with four tasks rolling
        # forward is a different week from 60% kept with none.
        "last_week": {
            "week": last["week"],
            "committed": last["committed"],
            "done": last["done"],
            "kept_percent": last["kept_percent"],
            "carryover": [
                {"id": t["id"], "title": t["title"], "assignee": t["assignee"]}
                for t in last["tasks"]
                if t["status"] != "done"
            ],
        },
        "week": week,
        "interrupts": flow["interrupts"],
        "capacity_ahead": portfolio.capacity_ahead(ahead_weeks, viewer),
        "conflicts": portfolio.allocation_conflicts(viewer),
        # submitted AND scored: scoring is not a disposition, and every
        # accept/defer/decline path treats both as un-triaged (services/
        # intake.py). Filtered to "submitted" alone, a scored request was
        # invisible on the one page built so nothing is missed on Monday, and
        # the card counted it as zero.
        "intake": [
            r for r in intake.list_requests(viewer=viewer) if r["status"] in ("submitted", "scored")
        ],
        "stale_decisions": collab.list_decisions(status="stale", viewer=viewer),
        # what is open with people outside the roster, so the manager reads it
        # BEFORE the week's meetings rather than after them
        "stakeholders": stakeholders.open_threads(viewer),
        # what the team is WAITING ON, which is the half of the ledger the
        # Monday meeting could not see: a promise made to the team goes quiet
        # in exactly one way, and nobody was watching (migration 007)
        "awaiting": promises.list_promises(status="open", viewer=viewer, direction="received"),
        "health": health,
        # Back SEVEN DAYS (not "to last Monday" — that is only the same
        # thing when the page is opened on a Monday), matching the weekly
        # ritual this page serves: the default (yesterday) answers a question
        # nobody asked on a page opened once a week.
        #
        # `from` filtered out, the way readout.py filters it: a first-ever
        # observation is not a change, and under the heading "Moved in the
        # last week" it listed the whole portfolio as news for the first seven
        # days after health snapshots shipped.
        #
        # A CREW engagement never appears here at all, permanently: this
        # `health` list carries the caller's viewer, and snapshot_health writes
        # at the workspace tier only (services/scope.py records that jobs read
        # workspace-only). Without this note the absence reads as an oversight.
        "health_changes": [
            c for c in portfolio.health_changes(health, db.today() - timedelta(days=7)) if c["from"]
        ],
        # The one open task whose finish releases the most other work. A
        # waiting-on edge told the person who typed it nothing; this is the
        # meeting's use for it — "start here and three people move".
        "top_unblocking_move": _top_unblocking_move(viewer),
        "today": db.today().isoformat(),
    }


def _top_unblocking_move(viewer: scope.Viewer) -> dict | None:
    """The open task that releases the most work, or None when nothing waits.

    Two steps, because the honest score is transitive and the transitive walk
    is expensive. Step one ranks every candidate by its DIRECT waiter count in
    a single GROUP BY. Step two walks only the shortlist. Scoring every
    candidate cost two queries each — 602 on a workspace with 300 edges, each
    one its own round trip (services/scope.py records that the round trip
    costs far more than the SELECT it carries), on a page
    whose own docstring promises it computes nothing twice.

    The shortlist can be wrong in one direction: a task with few direct
    waiters but a long chain behind them can outrank one with many direct
    waiters and none. Bounded and occasionally second-best beats unbounded on
    the page a manager opens every Monday.
    """
    frag, vp = scope.visible_filter(viewer, "tasks", alias="t")
    # the waiter side is scoped too: an unscoped count would rank a task by
    # waiters its reader cannot see, and the number would not match the peek
    wfrag, wp = scope.visible_filter(viewer, "tasks", alias="w")
    ranked = db.query(
        f"SELECT t.id, t.title, t.assignee, COUNT(*) AS direct"  # noqa: S608 — scope.visible_filter emits only bound marks
        f" FROM tasks t JOIN tasks w ON w.waiting_on_type = 'task'"
        f" AND w.waiting_on_id = t.id AND w.status != 'done' AND {wfrag}"
        f" WHERE t.status != 'done' AND {frag}"
        " GROUP BY t.id ORDER BY direct DESC, t.id LIMIT ?",
        (*wp, *vp, _TOP_CANDIDATES),
    )
    best: dict | None = None
    for t in ranked:
        got = work.downstream(t["id"], viewer)
        if got["unblocks_total"] and (best is None or got["unblocks_total"] > best["unblocks"]):
            best = {
                "id": t["id"],
                "title": t["title"],
                "assignee": t["assignee"],
                "unblocks": got["unblocks_total"],
                # carried, not dropped: the cockpit states this number as a
                # count, and on a truncated walk it is a floor. The task peek
                # discloses the same fact, and the two must not read
                # differently about the same chain.
                "depth_capped": got["depth_capped"],
            }
    return best
