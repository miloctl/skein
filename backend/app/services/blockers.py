"""Blocker & escalation register. Escalation is programmatic: a scheduled
sweep flips open blockers past their escalate_after_hours to 'escalated'."""

from datetime import UTC, datetime, timedelta

from .. import db
from . import scope, work
from .search import index_record

IMPACTS = ("low", "medium", "high", "critical")
DEFAULT_ESCALATION_HOURS = {"low": 72, "medium": 24, "high": 8, "critical": 2}


def create_policy_context(
    task_id: int,
    visibility: str,
    crew_id: int,
    *,
    actor: str,
) -> dict[str, str]:
    """Resolve one proposed blocker and its task in the writer's scope."""
    tier, cid = scope.resolve_write(visibility, crew_id, actor=actor)
    result = {"classification": tier, "crew_id": str(cid or ""), "project_type": ""}
    if not task_id:
        return result
    viewer = scope.Viewer.for_actor(actor)
    task = work.get_task(task_id, viewer)
    scope.assert_relationship_contains(
        str(task["visibility"]),
        task["crew_id"],
        tier,
        cid,
        child_label="blocker",
    )
    result.update(work.task_read_policy_context(task, viewer))
    result["classification"] = tier
    result["crew_id"] = str(cid or "")
    return result


def existing_policy_context(blocker_id: int, *, actor: str) -> dict[str, str]:
    """Resolve one editable blocker and its authoritative linked project."""
    row = db.query_one("SELECT * FROM blockers WHERE id = ?", (blocker_id,))
    if row is None:
        raise scope.missing("blockers", blocker_id)
    scope.assert_editable("blockers", row, actor, verb="update")
    result = {
        "classification": str(row["visibility"]),
        "crew_id": str(row["crew_id"] or ""),
        "project_type": "",
    }
    task_id = int(row["task_id"] or 0)
    if task_id:
        viewer = scope.Viewer.for_actor(actor)
        try:
            task = work.get_task(task_id, viewer)
            result.update(work.task_read_policy_context(task, viewer))
        except (db.NotFound, ValueError) as exc:
            raise scope.missing("blockers", blocker_id) from exc
        result["classification"] = str(row["visibility"])
        result["crew_id"] = str(row["crew_id"] or "")
    return result


def raise_blocker(
    title: str,
    detail: str = "",
    owner: str = "",
    impact: str = "medium",
    task_id: int = 0,
    source: str = "",
    escalate_after_hours: int = 0,
    *,
    actor: str = "system",
    origin: str = "human",
    visibility: str = scope.WORKSPACE,
    crew_id: int = 0,
) -> dict:
    from .users import resolve_teammate

    owner = resolve_teammate(owner, actor, "owner")
    if not title.strip():
        raise ValueError("blocker title is required")
    if impact not in IMPACTS:
        raise ValueError(f"impact must be one of {IMPACTS}")
    hours = escalate_after_hours or DEFAULT_ESCALATION_HOURS[impact]
    ts = db.now()
    with db.transaction():
        tier, cid = scope.resolve_write(visibility, crew_id, actor=actor)
        if task_id:
            tfrag, tp = scope.visible_filter(scope.Viewer.for_actor(actor), "tasks", "task")
            task = db.query_one(
                f"SELECT task.* FROM tasks task WHERE task.id = ? AND {tfrag}",  # noqa: S608 -- scope emits bound marks
                (task_id, *tp),
            )
            if task is None:
                raise ValueError(scope.missing_text("tasks", task_id))
            scope.assert_relationship_contains(
                task["visibility"],
                task["crew_id"],
                tier,
                cid,
                child_label="blocker",
            )
        # author=actor: capture.py hardcodes owner=actor and post_standup
        # passes owner=author, so without the self-exemption every private
        # capture and standup that named a blocker was refused
        scope.assert_readable_by(tier, cid, owner, label="owner", author=actor)
        bid = db.execute(
            "INSERT INTO blockers (title, detail, owner, impact, task_id, source,"
            " escalate_after_hours, origin, created_by, created_at, updated_at,"
            " visibility, crew_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                title,
                detail,
                owner,
                impact,
                task_id or None,
                source,
                hours,
                origin,
                actor,
                ts,
                ts,
                tier,
                cid,
            ),
        )
        if task_id:
            from .work import update_task

            update_task(task_id, status="blocked", actor=actor, origin=origin)
        db.log_activity(actor, "raise_blocker", scope.detail(tier, f"#{bid}", title))
        index_record("blocker", bid, title, f"{detail} {owner}")
    return {"id": bid, "title": title, "status": "open", "escalate_after_hours": hours}


def edit_blocker(
    blocker_id: int,
    title: str = "",
    detail: str = "",
    owner: str = "",
    *,
    actor: str = "system",
    origin: str = "human",
) -> dict:
    """Correct an open blocker's wording/owner — resolution stays its own verb."""
    row = db.query_one("SELECT * FROM blockers WHERE id = ?", (blocker_id,))
    if not row:
        raise scope.missing("blockers", blocker_id)
    scope.assert_editable("blockers", row, actor, verb="edit")
    if row["status"] == "resolved":
        raise ValueError(f"blocker #{blocker_id} is resolved — history stays put")
    fields = {k: v for k, v in [("title", title), ("detail", detail), ("owner", owner)] if v}
    if not fields:
        raise ValueError("nothing to update")
    for clearable in ("detail", "owner"):
        if fields.get(clearable) == "-":
            fields[clearable] = ""
    # the NEW owner is checked as a reader, not just the one raise_blocker
    # checked: sweep_escalations quotes this blocker's title to whoever `owner`
    # names at sweep time, and its comment claims the owner was already
    # verified. Without this, an edit hands a crew blocker to a non-member and
    # the sweep tells them its title.
    if fields.get("owner"):
        scope.assert_readable_by(
            row["visibility"], row["crew_id"], fields["owner"], label="owner", author=actor
        )
    sets = ", ".join(f"{k} = ?" for k in fields)
    db.execute(
        f"UPDATE blockers SET {sets} WHERE id = ?",  # noqa: S608 — keys hardcoded
        (*fields.values(), blocker_id),
    )
    if title and title != row["title"]:
        # both titles are the blocker's own text, so a scoped rename logs the
        # identifier only — the chain is append-only (services/scope.py::detail)
        db.log_activity(
            actor,
            "edit_blocker",
            scope.detail(row["visibility"], f"#{blocker_id}", f"'{row['title']}' -> '{title}'"),
        )
    else:
        db.log_activity(actor, "edit_blocker", f"#{blocker_id} {' '.join(fields)}")
    new = db.query_one("SELECT title, detail, owner FROM blockers WHERE id = ?", (blocker_id,))
    if new:
        index_record("blocker", blocker_id, new["title"], f"{new['detail']} {new['owner']}")
    return {"id": blocker_id, "updated": list(fields)}


def resolve_blocker(
    blocker_id: int, resolution: str = "", *, actor: str = "system", origin: str = "human"
) -> dict:
    row = db.query_one("SELECT * FROM blockers WHERE id = ?", (blocker_id,))
    if not row:
        raise scope.missing("blockers", blocker_id)
    scope.assert_editable("blockers", row, actor, verb="resolve")
    if row["status"] == "resolved":
        raise ValueError(f"blocker #{blocker_id} is already resolved")
    with db.transaction():
        db.execute(
            "UPDATE blockers SET status = 'resolved', resolved_at = ?, updated_at = ?,"
            " detail = detail || CASE WHEN ? != '' THEN char(10) || 'Resolved: ' || ? ELSE '' END"
            " WHERE id = ?",
            (db.now(), db.now(), resolution, resolution, blocker_id),
        )
        task_unblocked = 0
        if row["task_id"]:
            # un-block the linked task that raise_blocker flipped
            task_unblocked = db.execute_rowcount(
                "UPDATE tasks SET status = 'in_progress', updated_at = ?"
                " WHERE id = ? AND status = 'blocked'",
                (db.now(), row["task_id"]),
            )
            if task_unblocked:
                from .work import _emit_task_event

                task = db.query_one("SELECT visibility FROM tasks WHERE id = ?", (row["task_id"],))
                if task:
                    _emit_task_event(
                        "skein.task.updated",
                        int(row["task_id"]),
                        actor=actor,
                        origin=origin,
                        visibility=str(task["visibility"]),
                        changes=("status",),
                        correlation_id="",
                        actor_kind="",
                    )
        db.log_activity(actor, "resolve_blocker", f"#{blocker_id}")

    # tasks explicitly waiting on this blocker can move again — tell their
    # owners, or the unblock is a tree falling in an empty forest
    waiting = db.query(
        "SELECT id, title, assignee FROM tasks"
        " WHERE waiting_on_type = 'blocker' AND waiting_on_id = ?",
        (blocker_id,),
    )
    if waiting:
        from .notifications import notify

        for t in waiting:
            assignee = str(t["assignee"] or "")
            assignee_reads_blocker = scope.can_read(
                row["visibility"],
                row["crew_id"],
                scope.Viewer.for_actor(assignee),
                row["created_by"],
            )
            if assignee and assignee != actor and assignee_reads_blocker:
                notify(
                    assignee,
                    f"Blocker #{blocker_id} resolved — task #{t['id']}"
                    f" “{t['title']}” can move again.",
                    tier="immediate",
                    link="/dashboard",
                )

    created = datetime.fromisoformat(row["created_at"])
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    age = datetime.now(UTC) - created
    # the workspace tier only: the funeral is addressed to "team", which is
    # every person on the roster, and it quotes the blocker's own title
    if age >= timedelta(days=3) and row["visibility"] == scope.WORKSPACE:  # the Blocker Funeral
        from .notifications import notify

        days = age.days
        notify(
            "team",
            f"🪦 Here lies blocker #{blocker_id} “{row['title']}”."
            f" It fought hard. It lost. {days} days.",
            tier="digest",
            link="/dashboard",
        )
    return {"id": blocker_id, "status": "resolved"}


def list_blockers(
    status: str = "", owner: str = "", viewer: scope.Viewer = scope.NOBODY
) -> list[dict]:
    frag, vp = scope.visible_filter(viewer, "blockers", "blocker")
    task_visible, task_params = scope.visible_filter(viewer, "tasks", "task")
    sql, params = (
        f"SELECT blocker.*, task.id AS visible_task_id"  # noqa: S608 -- scope emits bound marks
        " FROM blockers blocker LEFT JOIN tasks task ON task.id = blocker.task_id"
        f" AND {task_visible} WHERE {frag}",
        [*task_params, *vp],
    )
    if status:
        sql += " AND blocker.status = ?"
        params.append(status)
    else:
        sql += " AND blocker.status != 'resolved'"
    if owner:
        sql += " AND blocker.owner = ?"
        params.append(owner)
    sql += (
        " ORDER BY CASE blocker.impact WHEN 'critical' THEN 0 WHEN 'high' THEN 1"
        " WHEN 'medium' THEN 2 ELSE 3 END, blocker.created_at LIMIT 200"
    )
    rows = db.query(sql, tuple(params))
    for row in rows:
        visible_task = row.pop("visible_task_id", None)
        if row.get("task_id") and visible_task is None:
            row["task_id"] = None
    return rows


def blocker_collection_policy_contexts(
    rows: list[dict], viewer: scope.Viewer
) -> dict[int, dict[str, str]]:
    """Resolve each visible blocker's linked project without hidden-row oracles."""
    blocker_ids = sorted({int(row["id"]) for row in rows})
    if not blocker_ids:
        return {}
    marks = ",".join("?" for _ in blocker_ids)
    links = {
        int(row["id"]): int(row.get("task_id") or 0)
        for row in db.query(
            f"SELECT id, task_id FROM blockers WHERE id IN ({marks})",  # noqa: S608 -- marks are controlled
            tuple(blocker_ids),
        )
    }
    task_ids = sorted(set(links.values()) - {0})
    task_rows: list[dict] = []
    if task_ids:
        task_marks = ",".join("?" for _ in task_ids)
        visible, params = scope.visible_filter(viewer, "tasks", "task")
        task_rows = db.query(
            f"SELECT task.* FROM tasks task WHERE task.id IN ({task_marks})"  # noqa: S608 -- marks and scope are controlled
            f" AND {visible}",
            (*task_ids, *params),
        )
    task_contexts = work.task_collection_policy_contexts(task_rows, viewer)
    result: dict[int, dict[str, str]] = {}
    for row in rows:
        blocker_id = int(row["id"])
        task_id = links.get(blocker_id, 0)
        attributes = {
            "classification": str(row.get("visibility") or ""),
            "project_type": "",
        }
        if task_id:
            task_context = task_contexts.get(task_id)
            if task_context is None or task_context.get("relationship_conflict"):
                attributes["relationship_conflict"] = "true"
            else:
                attributes["project_type"] = str(task_context.get("project_type") or "")
        result[blocker_id] = attributes
    return result


def sweep_escalations() -> list[dict]:
    """Flip aged open blockers to escalated; called by the scheduler and tests."""
    escalated = []
    now_dt = datetime.now(UTC)
    for b in db.query("SELECT * FROM blockers WHERE status = 'open'"):
        created = datetime.fromisoformat(b["created_at"])
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        if now_dt - created >= timedelta(hours=b["escalate_after_hours"]):
            claimed = db.execute_rowcount(
                "UPDATE blockers SET status = 'escalated', escalated_at = ?, updated_at = ?"
                " WHERE id = ? AND status = 'open'",
                (db.now(), db.now(), b["id"]),
            )
            if not claimed:  # resolved between our read and write
                continue
            from .notifications import notify

            # Every tier escalates — a crew blocker that silently never
            # escalates is a worse outcome than one nobody is told about. But
            # the message quotes the title, so it goes to the owner alone: the
            # "team" fallback addresses the whole roster, and an owner is the
            # only name here checked as a reader — by raise_blocker at
            # creation and by edit_blocker on every change to it.
            #
            # Re-checked HERE too, because both of those are WRITE-time and
            # this job runs hourly forever: somebody removed from the crew
            # afterwards was a legitimate owner when the blocker was raised,
            # and scope.audience makes the opposite promise about removal.
            owner_reads = b["owner"] and scope.can_read(
                b["visibility"],
                b["crew_id"],
                scope.Viewer.for_actor(b["owner"]),
                b["created_by"],
            )
            if owner_reads or b["visibility"] == scope.WORKSPACE:
                notify(
                    b["owner"] or "team",
                    f"Blocker #{b['id']} escalated: {b['title']}",
                    tier="immediate",
                    link="/",
                )
            db.log_activity(
                "scheduler",
                "escalate_blocker",
                scope.detail(
                    b["visibility"],
                    f"#{b['id']}",
                    f"{b['title']} (open {b['escalate_after_hours']}h,"
                    f" owner: {b['owner'] or 'unowned'})",
                ),
            )
            escalated.append(b)
    return escalated
