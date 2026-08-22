"""One engagement, whole: what it is for, where it stands, and what is next.

An engagement's context was spread across seven surfaces. Its outcome lived on
Browse, its health on Work → Health, its blockers in the register, its
decisions on Charter, its reports on Reports, its agent work on Team → Agents,
and its plan drift only appeared at close. Answering "how is Atlas going" meant
touring all seven and assembling the answer by hand — which is the work this
product exists to remove, done by the person it exists to help.

The engagement CONTEXT PACK already compiles most of this, and it is the right
output for an agent: markdown, on demand, cheap tokens. It is the wrong output
for a screen, where a reader needs to open the milestone the health receipt
names. This is the same composition returning rows instead of prose, so the two
cannot drift about what an engagement is — both read the same services.

Composition only. No table, no write path. Every number here keeps its own home.
"""

from collections.abc import Callable
from datetime import timedelta

from . import refs, scope

# How many open tasks the brief lists. The page STATES this cap: a list that
# truncates in silence reads as "this is everything", and the engagement most
# worth reading is the one with too much open work.
TASK_CAP = 50

# How deep into the portfolio queue this brief looks before narrowing. The
# queue is ranked across every engagement, so a shallow window silently drops
# this engagement's rows behind another's — and the card would then say nothing
# is escalated directly above a blocker card showing one that is.
QUEUE_SCAN = 50


# both routes to an engagement: a task's own link, or through its milestone
_ON_ENGAGEMENT = (
    " AND (t.engagement_id = ? OR t.milestone_id IN"
    "      (SELECT id FROM milestones WHERE engagement_id = ?))"
)


def brief(
    engagement_id: int,
    viewer: scope.Viewer = scope.NOBODY,
    resource_filter: Callable[[str, int, dict[str, str]], bool] | None = None,
) -> dict:
    """Everything about one engagement a person needs before they act.

    Viewer-scoped at every read, and the engagement itself is fetched through
    the filter first: an unreadable engagement raises `scope.missing` exactly
    as an absent one does, so a caller walking sequential ids cannot tell which
    engagements exist (services/scope.py::Viewer).
    """
    from .. import db
    from . import policy_context
    from .handoff import list_artifacts
    from .intervention import interventions
    from .playbooks import close_out_diff
    from .portfolio import _linked_blockers, engagement_health, health_changes
    from .work import consistent_task_rows, redact_task_relationships

    efrag, ep = scope.visible_filter(viewer, "engagements")
    eng = db.query_one(
        f"SELECT * FROM engagements WHERE id = ? AND {efrag}",  # noqa: S608 — scope.visible_filter emits only bound marks
        (engagement_id, *ep),
    )
    if not eng:
        raise scope.missing("engagements", engagement_id)

    # health and its movement come from the SAME pass every other surface
    # reads, filtered to this engagement — a second computation here would be
    # a second definition of red
    health = next(
        (
            h
            for h in engagement_health(viewer, resource_filter=resource_filter)
            if h["id"] == engagement_id
        ),
        None,
    )
    moved = next(
        (m for m in health_changes([health] if health else []) if m["id"] == engagement_id),
        None,
    )

    mfrag, mp = scope.visible_filter(viewer, "milestones")
    tfrag, tp = scope.visible_filter(viewer, "tasks", "t")
    lfrag, lp = scope.visible_filter(viewer, "lessons")

    milestones = db.query(
        f"SELECT * FROM milestones WHERE engagement_id = ? AND {mfrag}"  # noqa: S608 — scope.visible_filter emits only bound marks
        " ORDER BY due_date IS NULL, due_date",
        (engagement_id, *mp),
    )
    milestones = policy_context.filter_resource_rows(
        "milestone", milestones, viewer, resource_filter
    )
    # BOTH paths to the engagement, deduped by the query itself: a task reaches
    # one through its own engagement_id or through its milestone's, and
    # portfolio.engagement_health counts it the same way
    tasks = redact_task_relationships(
        consistent_task_rows(
            db.query(
                f"SELECT t.* FROM tasks t WHERE {tfrag}"  # noqa: S608 — scope.visible_filter emits only bound marks
                " AND (t.engagement_id = ? OR t.milestone_id IN"
                "      (SELECT id FROM milestones WHERE engagement_id = ?))"
                " AND t.status NOT IN ('done', 'void')"
                " ORDER BY CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1"
                " WHEN 'medium' THEN 2 ELSE 3 END, t.due_date IS NULL, t.due_date, t.id"
                # capped, and the page says so: an engagement with eighty open tasks
                # buried the drift and the reports under a wall of list
                f" LIMIT {TASK_CAP}",
                (*tp, engagement_id, engagement_id),
            ),
            viewer,
        ),
        viewer,
        resource_filter,
    )
    tasks = policy_context.filter_resource_rows("task", tasks, viewer, resource_filter)
    # what an agent is carrying right now, with its own last note — the
    # continuity a sponsor has nowhere else to read without opening each task.
    #
    # ONE query for every note, not one per task: `list_worklog` costs two or
    # three round trips each, and an engagement with fifty delegated tasks paid
    # for all of them to render one line apiece. The same batching shape
    # `portfolio._satisfied_targets` uses, for the same reason.
    #
    # Read on its OWN query rather than out of `tasks`, for the reason the
    # `task_ids` read below states: `tasks` is capped for display, and a
    # delegated task that sorts past the cap would vanish from the continuity
    # card while the acceptance queue and the worklog still name it. The
    # predicate keeps this bounded by what is actually delegated, not by the
    # engagement's size.
    delegated_rows = consistent_task_rows(
        db.query(
            f"SELECT t.* FROM tasks t WHERE {tfrag}"  # noqa: S608 — scope.visible_filter emits only bound marks
            " AND (t.engagement_id = ? OR t.milestone_id IN"
            "      (SELECT id FROM milestones WHERE engagement_id = ?))"
            " AND t.status NOT IN ('done', 'void') AND t.delegated_agent != ''"
            " ORDER BY t.id",
            (*tp, engagement_id, engagement_id),
        ),
        viewer,
    )
    delegated_rows = policy_context.filter_resource_rows(
        "task", delegated_rows, viewer, resource_filter
    )
    delegated = _delegated(
        redact_task_relationships(delegated_rows, viewer, resource_filter), viewer
    )

    # the matching set for `_mine` is EVERY open task, not the capped display
    # list: a queue row for task 51 would otherwise be dropped and the card
    # would then say nothing in the queue belongs here, which is false in
    # exactly the way that sentence was rewritten to avoid. Ids only, so the
    # extra read costs one column.
    all_task_rows = consistent_task_rows(
        db.query(
            f"SELECT t.id FROM tasks t WHERE {tfrag}"  # noqa: S608 — scope.visible_filter emits only bound marks
            " AND (t.engagement_id = ? OR t.milestone_id IN"
            "      (SELECT id FROM milestones WHERE engagement_id = ?))"
            " AND t.status NOT IN ('done', 'void')",
            (*tp, engagement_id, engagement_id),
        ),
        viewer,
    )
    all_task_rows = policy_context.filter_resource_rows(
        "task", all_task_rows, viewer, resource_filter
    )
    task_ids = {r["id"] for r in all_task_rows}
    blockers = _linked_blockers(engagement_id, viewer)
    blockers = policy_context.filter_resource_rows("blocker", blockers, viewer, resource_filter)
    lessons = db.query(
        f"SELECT * FROM lessons WHERE {lfrag}"  # noqa: S608 — scope.visible_filter emits only bound marks
        " AND (engagement_id = ? OR project_class = ?) ORDER BY id DESC LIMIT 10",
        (*lp, engagement_id, eng["project_class"]),
    )
    lessons = policy_context.filter_resource_rows("lesson", lessons, viewer, resource_filter)
    artifacts = list_artifacts(engagement_id, viewer)
    artifacts = policy_context.filter_resource_rows("artifact", artifacts, viewer, resource_filter)
    # What moved on THIS engagement since local midnight yesterday — the same
    # window delta.brief defaults to for a first read, and computed from the
    # rows this function already scopes, because activity ledger rows carry no
    # engagement id and delta.brief is reader-scoped and team-wide. Counts
    # only: the cards below carry the rows themselves.
    since = db.local_midnight_utc(db.today() - timedelta(days=1))
    tasks_done = db.query_one(
        f"SELECT COUNT(*) AS n FROM tasks t WHERE {tfrag}{_ON_ENGAGEMENT}"  # noqa: S608 — scope.visible_filter emits only bound marks
        " AND t.completed_at >= ?",
        (*tp, engagement_id, engagement_id, since),
    )
    bfrag2, bp2 = scope.visible_filter(viewer, "blockers", "b")
    # both sides carry the filter, exactly as portfolio._linked_blockers does
    blocker_join = (
        f"FROM blockers b JOIN tasks t ON t.id = b.task_id AND {tfrag}"
        f" WHERE {bfrag2}{_ON_ENGAGEMENT}"
    )
    blockers_opened = db.query_one(
        f"SELECT COUNT(*) AS n {blocker_join} AND b.created_at >= ?",
        (*tp, *bp2, engagement_id, engagement_id, since),
    )
    blockers_resolved = db.query_one(
        f"SELECT COUNT(*) AS n {blocker_join} AND b.status = 'resolved' AND b.resolved_at >= ?",
        (*tp, *bp2, engagement_id, engagement_id, since),
    )

    # A CLOSED engagement's page is an archive, and it showed none of the
    # work: eighteen finished tasks rendered as "No work is open. Capture one
    # with 'todo:'" — an invitation to add work to a closed engagement, over
    # the history a reader came for. Recent done tasks, newest first, capped
    # like the open list; absent for active engagements, whose done work has
    # its own surfaces (Recently shipped, flow metrics).
    done_work: list[dict] = []
    done_count = 0
    if eng["status"] == "closed":
        row = db.query_one(
            f"SELECT COUNT(*) AS c FROM tasks t WHERE {tfrag}{_ON_ENGAGEMENT}"  # noqa: S608 — scope.visible_filter emits only bound marks
            " AND t.status = 'done'",
            (*tp, engagement_id, engagement_id),
        )
        done_count = (row or {}).get("c", 0)
        done_work = db.query(
            f"SELECT t.id, t.title, t.assignee, t.completed_at FROM tasks t"  # noqa: S608 — scope.visible_filter emits only bound marks
            f" WHERE {tfrag}{_ON_ENGAGEMENT} AND t.status = 'done'"
            f" ORDER BY t.completed_at DESC NULLS LAST, t.id DESC LIMIT {TASK_CAP}",
            (*tp, engagement_id, engagement_id),
        )
        done_work = policy_context.filter_resource_rows("task", done_work, viewer, resource_filter)

    return {
        "engagement": eng,
        "done_work": done_work,
        "done_count": done_count,
        "since_yesterday": {
            "tasks_done": (tasks_done or {}).get("n", 0),
            "blockers_opened": (blockers_opened or {}).get("n", 0),
            "blockers_resolved": (blockers_resolved or {}).get("n", 0),
        },
        "health": {
            "color": health["health"] if health else "",
            # the same sentences health writes, plus what each one points at,
            # so the reader opens the milestone rather than hunting for its id
            "receipts": [refs.receipt(r) for r in (health or {}).get("receipts", [])],
            # None when this engagement has no earlier snapshot: it is new to
            # the comparison, and reporting it as a change from green would
            # invent a previous state (services/portfolio.py::health_changes)
            "moved_from": moved["from"] if moved else None,
        },
        "milestones": milestones,
        "tasks": tasks,
        "blockers": blockers,
        "delegated": delegated,
        "lessons": lessons,
        "artifacts": artifacts,
        # {} for an engagement that was not born from a playbook. The snapshot
        # is written at KICKOFF (`playbooks.instantiate`), so this is live
        # drift, not a post-mortem — which is the point: a plan the team can
        # still act on. Named rather than omitted, because an absent key reads
        # as "no drift" and "we never snapshotted a plan" is a different fact.
        "plan_diff": close_out_diff(engagement_id, viewer, resource_filter=resource_filter),
        # the queue's own rows, narrowed to this engagement and the work under
        # it. The manager's page ranks across the portfolio; here the same
        # rows answer "what does THIS need", with the same actions and
        # receipts, so the two cannot recommend different things.
        # QUEUE_SCAN, not the page's own cap: the queue ranks across the whole
        # portfolio and is narrowed here, so a small window drops this
        # engagement's rows behind other engagements' and the card then claims
        # nothing is escalated while the blockers card shows one that is. The
        # page states the window it read rather than asserting a fact.
        "next_actions": _mine(
            interventions(viewer, limit=QUEUE_SCAN, resource_filter=resource_filter),
            engagement_id,
            task_ids,
            blockers,
        ),
        "queue_scanned": QUEUE_SCAN,
    }


def _delegated(tasks: list[dict], viewer: scope.Viewer) -> list[dict]:
    """Delegated tasks with their latest worklog note, in one pass.

    The note is filtered on `task_worklog`'s OWN tier, never on the task's: a
    worklog row inherits the task's tier at write time (`scope.inherit`), and
    reading through the parent instead would trust a link rather than a filter.
    The delegation door `list_worklog` opens for a sponsor is deliberately NOT
    reproduced here — this list is a summary line, and a reader who needs the
    log opens the task panel, which carries the door.
    """
    from .. import db

    delegated = [t for t in tasks if t["delegated_agent"]]
    if not delegated:
        return []
    marks = ",".join("?" * len(delegated))
    # aliased on BOTH sides: the fragment's columns are bare, so in the
    # subquery they would bind to whichever table the planner resolves them
    # against. Naming the alias makes the two filters unambiguous.
    outer, vp = scope.visible_filter(viewer, "task_worklog", alias="w")
    inner, vp2 = scope.visible_filter(viewer, "task_worklog", alias="x")
    ids = [t["id"] for t in delegated]
    latest: dict[int, dict] = {}
    # ONE row per task, chosen in SQL. Reading every note and keeping the last
    # in Python pulled a month of an agent's progress notes — up to 2000
    # characters each — to render fifty lines. The tier filter is INSIDE the
    # subquery as well: computing MAX(id) over unfiltered rows and filtering
    # after would drop the note entirely whenever the newest one is a row this
    # viewer cannot read.
    for row in db.query(
        f"SELECT w.task_id, w.note, w.created_at FROM task_worklog w"  # noqa: S608 — marks are bound, visible_filter emits only bound marks
        f" WHERE w.task_id IN ({marks}) AND {outer}"
        f" AND w.id = (SELECT MAX(x.id) FROM task_worklog x"
        f"             WHERE x.task_id = w.task_id AND {inner})",
        (*ids, *vp, *vp2),
    ):
        latest[row["task_id"]] = row
    return [
        {
            "task_id": t["id"],
            "title": t["title"],
            "agent": t["delegated_agent"],
            "sponsor": t["sponsor"],
            "status": t["status"],
            "last_note": (latest.get(t["id"]) or {}).get("note", ""),
            "last_note_at": (latest.get(t["id"]) or {}).get("created_at", ""),
        }
        for t in delegated
    ]


def _mine(
    queue: list[dict],
    engagement_id: int,
    task_ids: set[int],
    blockers: list[dict],
) -> list[dict]:
    """The queue rows that belong to this engagement.

    Matched against the rows this brief already listed, never by re-running the
    queries: a second set of predicates would let the portfolio queue and this
    one recommend different things about the same engagement.
    """
    # `promise` and `decision` are deliberately absent: neither carries an
    # engagement, so no honest narrowing exists and a guess would put another
    # engagement's overdue promise on this page. They reach a reader through
    # the portfolio queue, which is where a row with no engagement belongs.
    # `milestone` is absent because the queue emits none.
    mine = {
        "engagement": {engagement_id},
        "task": task_ids,
        # by id, not by walking the receipt's refs: a blocker receipt names the
        # BLOCKER, so a ref walk matched nothing and every escalated blocker on
        # the engagement fell out of its own next-actions list
        "blocker": {b["id"] for b in blockers},
    }
    kept = [r for r in queue if r["entity_id"] in mine.get(r["entity"], set())]
    # A finding DOES carry an engagement when its rule wrote one into the
    # receipt — `plan_drift` and `experiment_overdue` both do, and
    # `intervention.py` links those rows straight to this page for that reason.
    # Dropping them made the two headline fixes disagree at the seam: the
    # manager followed a drift row here and read "nothing in the queue belongs
    # to this engagement" four cards above the drift itself.
    kept += [
        r
        for r in queue
        if r["entity"] == "finding" and (r.get("engagement_id") or 0) == engagement_id
    ]
    return kept
