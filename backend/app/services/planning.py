"""The Monday planning cockpit: one read, in meeting order.

Running the week meant touring three pages — /portfolio for the kept-% and the
draft, /intake for the queue, /charter for stale decisions, each with its own
load. The weekly operating rhythm is this product's spine and the person who
runs it had no room built for it.

Composition only. Every number here already had a home; this module owns the
ORDER, which is the part that was missing. Nothing is computed twice and no
new write path exists — a cockpit that could write would be a sixth place to
change a task.

Order is the meeting's order, and it is load-bearing:
  1. how last week went          (kept-%, carryover)
  2. what the week already holds (the draft, capacity against it)
  3. what wants in               (intake awaiting triage)
  4. what has gone stale         (decisions past review_by)
Reversed, the manager commits the week before seeing whether the last one
landed, which is the mistake the ritual exists to prevent.
"""

from datetime import timedelta

from .. import db
from . import collab, intake, portfolio, scope, weekly


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
        "today": db.today().isoformat(),
    }
