"""Blocker & escalation register. Escalation is programmatic: a scheduled
sweep flips open blockers past their escalate_after_hours to 'escalated'."""

from datetime import UTC, datetime, timedelta

from .. import db
from . import scope
from .search import index_record

IMPACTS = ("low", "medium", "high", "critical")
DEFAULT_ESCALATION_HOURS = {"low": 72, "medium": 24, "high": 8, "critical": 2}


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
    tfrag, tp = scope.visible_filter(scope.Viewer.for_actor(actor), "tasks")
    if task_id and not db.query_one(
        f"SELECT id FROM tasks WHERE id = ? AND {tfrag}",  # noqa: S608 — scope.visible_filter emits only bound marks
        (task_id, *tp),
    ):
        raise ValueError(scope.missing_text("tasks", task_id))
    hours = escalate_after_hours or DEFAULT_ESCALATION_HOURS[impact]
    ts = db.now()
    with db.transaction():
        tier, cid = scope.resolve_write(visibility, crew_id, actor=actor)
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
    db.execute(
        "UPDATE blockers SET status = 'resolved', resolved_at = ?, updated_at = ?,"
        " detail = detail || CASE WHEN ? != '' THEN char(10) || 'Resolved: ' || ? ELSE '' END"
        " WHERE id = ?",
        (db.now(), db.now(), resolution, resolution, blocker_id),
    )
    if row["task_id"]:
        # un-block the linked task that raise_blocker flipped
        db.execute_rowcount(
            "UPDATE tasks SET status = 'in_progress', updated_at = ?"
            " WHERE id = ? AND status = 'blocked'",
            (db.now(), row["task_id"]),
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
            if t["assignee"] and t["assignee"] != actor:
                notify(
                    t["assignee"],
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
    frag, vp = scope.visible_filter(viewer, "blockers")
    sql, params = f"SELECT * FROM blockers WHERE {frag}", list(vp)  # noqa: S608 — scope.visible_filter emits only bound marks
    if status:
        sql += " AND status = ?"
        params.append(status)
    else:
        sql += " AND status != 'resolved'"
    if owner:
        sql += " AND owner = ?"
        params.append(owner)
    sql += (
        " ORDER BY CASE impact WHEN 'critical' THEN 0 WHEN 'high' THEN 1"
        " WHEN 'medium' THEN 2 ELSE 3 END, created_at LIMIT 200"
    )
    return db.query(sql, tuple(params))


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
            # creation AND by edit_blocker on every change to it.
            if b["owner"] or b["visibility"] == scope.WORKSPACE:
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
