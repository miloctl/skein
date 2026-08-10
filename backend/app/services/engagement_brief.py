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

from . import refs, scope


def brief(engagement_id: int, viewer: scope.Viewer = scope.NOBODY) -> dict:
    """Everything about one engagement a person needs before they act.

    Viewer-scoped at every read, and the engagement itself is fetched through
    the filter first: an unreadable engagement raises `scope.missing` exactly
    as an absent one does, so a caller walking sequential ids cannot tell which
    engagements exist (services/scope.py::Viewer).
    """
    from .. import db
    from .delegation import list_worklog
    from .handoff import list_artifacts
    from .intervention import interventions
    from .playbooks import close_out_diff
    from .portfolio import _linked_blockers, engagement_health, health_changes

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
        (h for h in engagement_health(viewer) if h["id"] == engagement_id),
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
    # BOTH paths to the engagement, deduped by the query itself: a task reaches
    # one through its own engagement_id or through its milestone's, and
    # portfolio.engagement_health counts it the same way
    tasks = db.query(
        f"SELECT t.* FROM tasks t WHERE {tfrag}"  # noqa: S608 — scope.visible_filter emits only bound marks
        " AND (t.engagement_id = ? OR t.milestone_id IN"
        "      (SELECT id FROM milestones WHERE engagement_id = ?))"
        " AND t.status != 'done'"
        " ORDER BY CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1"
        " WHEN 'medium' THEN 2 ELSE 3 END, t.due_date IS NULL, t.due_date",
        (*tp, engagement_id, engagement_id),
    )
    # what an agent is carrying right now, with its own last note — the
    # continuity a sponsor has nowhere else to read without opening each task
    delegated = []
    for t in tasks:
        if not t["delegated_agent"]:
            continue
        notes = list_worklog(t["id"], limit=1, viewer=viewer, actor=viewer.name)
        delegated.append(
            {
                "task_id": t["id"],
                "title": t["title"],
                "agent": t["delegated_agent"],
                "sponsor": t["sponsor"],
                "status": t["status"],
                "last_note": notes[0]["note"] if notes else "",
                "last_note_at": notes[0]["created_at"] if notes else "",
            }
        )

    blockers = _linked_blockers(engagement_id, viewer)
    return {
        "engagement": eng,
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
        "lessons": db.query(
            f"SELECT * FROM lessons WHERE {lfrag}"  # noqa: S608 — scope.visible_filter emits only bound marks
            " AND (engagement_id = ? OR project_class = ?) ORDER BY id DESC LIMIT 10",
            (*lp, engagement_id, eng["project_class"]),
        ),
        "artifacts": list_artifacts(engagement_id, viewer),
        # {} until this engagement is closed AND was born from a playbook.
        # Named rather than omitted: an absent key reads as "no drift", and
        # "we never snapshotted a plan" is a different fact.
        "plan_diff": close_out_diff(engagement_id, viewer),
        # the queue's own rows, narrowed to this engagement and the work under
        # it. The manager's page ranks across the portfolio; here the same
        # rows answer "what does THIS need", with the same actions and
        # receipts, so the two cannot recommend different things.
        "next_actions": _mine(
            interventions(viewer, limit=50), engagement_id, tasks, milestones, blockers
        ),
    }


def _mine(
    queue: list[dict],
    engagement_id: int,
    tasks: list[dict],
    milestones: list[dict],
    blockers: list[dict],
) -> list[dict]:
    """The queue rows that belong to this engagement.

    Matched against the rows this brief already listed, never by re-running the
    queries: a second set of predicates would let the portfolio queue and this
    one recommend different things about the same engagement.
    """
    mine = {
        "engagement": {engagement_id},
        "task": {t["id"] for t in tasks},
        "milestone": {m["id"] for m in milestones},
        # by id, not by walking the receipt's refs: a blocker receipt names the
        # BLOCKER, so a ref walk matched nothing and every escalated blocker on
        # the engagement fell out of its own next-actions list
        "blocker": {b["id"] for b in blockers},
    }
    return [r for r in queue if r["entity_id"] in mine.get(r["entity"], set())]
