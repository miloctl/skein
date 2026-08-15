"""Engagements: the strike-team unit of work, with capacity allocations and
lessons captured at retro time."""

import logging

from .. import db
from . import scope
from .search import index_record

STATUSES = ("proposed", "active", "closing", "closed")
KINDS = ("delivery", "experiment")
# closing an engagement requires an honest conclusion — "shipped" is evidence
# of output, not of value; an invalidated experiment can be a success
CONCLUSIONS = ("achieved", "partial", "missed", "invalidated", "unmeasured", "stopped")


def create_engagement(
    name: str,
    project_class: str = "general",
    summary: str = "",
    lead: str = "",
    kind: str = "delivery",
    timebox_end: str = "",
    kill_criteria: str = "",
    outcome: str = "",
    *,
    actor: str = "system",
    origin: str = "human",
    visibility: str = scope.WORKSPACE,
    crew_id: int = 0,
) -> dict:
    name = name.strip()
    if not name:
        raise ValueError("engagement name is required")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if kind == "experiment" and not timebox_end:
        raise ValueError("experiments need a timebox_end date (YYYY-MM-DD)")
    db.validate_date("timebox_end", timebox_end, allow_clear=False)
    # NOCASE, and across ALL statuses including closed: the chat panel snaps
    # case-insensitively against the OPEN list, so a case-variant of a closed
    # engagement's name would otherwise slip past both checks and fork usage
    # rollups across two near-identical engagements
    if db.query_one("SELECT id FROM engagements WHERE lower(name) = lower(?)", (name,)):
        raise ValueError(f"engagement '{name}' already exists")
    ts = db.now()
    # one transaction: scope.resolve_write's membership check must not be able
    # to pass and then have the author leave the crew before the INSERT lands
    with db.transaction():
        tier, crew = scope.resolve_write(visibility, crew_id, actor=actor)
        eid = db.execute(
            "INSERT INTO engagements (name, project_class, summary, lead, started_at,"
            " kind, timebox_end, kill_criteria, outcome,"
            " origin, created_by, created_at, updated_at, visibility, crew_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " RETURNING id",
            (
                name,
                project_class,
                summary,
                lead,
                ts,
                kind,
                timebox_end or None,
                kill_criteria,
                outcome,
                origin,
                actor,
                ts,
                ts,
                tier,
                crew,
            ),
        )
        # Adoption preserves every audience and direct task relationship. An
        # incompatible orphan stays unlinked for an explicit repair.
        viewer = scope.Viewer.for_actor(actor)
        for milestone in db.query(
            "SELECT * FROM milestones WHERE project = ? AND engagement_id IS NULL",
            (name,),
        ):
            if not scope.can_read(
                milestone["visibility"],
                milestone["crew_id"],
                viewer,
                milestone["created_by"],
            ) or not scope.relationship_contains(
                tier,
                crew,
                milestone["visibility"],
                milestone["crew_id"],
            ):
                continue
            tasks = db.query(
                "SELECT engagement_id, visibility, crew_id FROM tasks WHERE milestone_id = ?",
                (milestone["id"],),
            )
            if any(
                task["engagement_id"]
                or not scope.relationship_contains(
                    tier,
                    crew,
                    task["visibility"],
                    task["crew_id"],
                )
                for task in tasks
            ):
                continue
            db.execute(
                "UPDATE milestones SET engagement_id = ? WHERE id = ? AND engagement_id IS NULL",
                (eid, milestone["id"]),
            )
        db.log_activity(
            actor, "create_engagement", scope.detail(tier, f"#{eid}", f"{name} [{project_class}]")
        )
        index_record("engagement", eid, name, f"{summary} {project_class} {lead}")
    return {
        "id": eid,
        "name": name,
        "project_class": project_class,
        "kind": kind,
        "status": "active",
    }


def update_engagement(
    engagement_id: int,
    status: str = "",
    name: str = "",
    summary: str = "",
    lead: str = "",
    conclusion: str = "",
    outcome: str = "",
    timebox_end: str = "",
    kill_criteria: str = "",
    *,
    actor: str = "system",
    origin: str = "human",
) -> dict:
    """Update an engagement and complete its close-out in one transaction."""
    with db.transaction():
        return _update_engagement_locked(
            engagement_id,
            status,
            name,
            summary,
            lead,
            conclusion,
            outcome,
            timebox_end,
            kill_criteria,
            actor=actor,
            origin=origin,
        )


def _update_engagement_locked(
    engagement_id: int,
    status: str = "",
    name: str = "",
    summary: str = "",
    lead: str = "",
    conclusion: str = "",
    outcome: str = "",
    timebox_end: str = "",
    kill_criteria: str = "",
    *,
    actor: str = "system",
    origin: str = "human",
) -> dict:
    if status and status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    if conclusion and conclusion not in CONCLUSIONS:
        raise ValueError(f"conclusion must be one of {CONCLUSIONS}")
    db.validate_date("timebox_end", timebox_end)
    current = db.query_one("SELECT * FROM engagements WHERE id = ?", (engagement_id,))
    if not current:
        raise scope.missing("engagements", engagement_id)
    scope.assert_editable("engagements", current, actor, verb="update")
    name = name.strip()
    renaming = bool(name and name != current["name"])
    # NOCASE and id-excluded, matching create: the case-variant fork the
    # create check closes must not reopen through rename. The id exclusion
    # keeps re-casing an engagement's OWN name ("alpha" -> "Alpha") legal.
    if renaming and db.query_one(
        "SELECT id FROM engagements WHERE lower(name) = lower(?) AND id != ?",
        (name, engagement_id),
    ):
        raise ValueError(f"engagement '{name}' already exists")
    freshly_closed = status == "closed" and current["status"] != "closed"
    if freshly_closed and not (conclusion or current["conclusion"]):
        raise ValueError(
            f"closing needs a conclusion — one of {CONCLUSIONS}."
            " 'invalidated' is a fine outcome for an experiment. 'unmeasured' is honest too."
        )
    fields = {
        k: v
        for k, v in [
            ("status", status),
            ("name", name if renaming else ""),
            ("summary", summary),
            ("lead", lead),
            ("conclusion", conclusion),
            ("outcome", outcome),
            # extending a timebox on purpose is the answer to the
            # experiment_overdue finding — it must be possible via the API
            ("timebox_end", timebox_end),
            ("kill_criteria", kill_criteria),
        ]
        if v
    }
    if not fields:
        raise ValueError("nothing to update")
    # "-" clears any clearable field — same convention as tasks/milestones;
    # a mis-set timebox must be removable, not only movable
    if fields.get("timebox_end") == "-" and current["kind"] == "experiment":
        raise ValueError(
            "experiments keep a timebox — move the date instead of clearing"
            " it, or close the experiment with a conclusion"
        )
    for clearable, empty in (
        ("timebox_end", None),
        ("kill_criteria", ""),
        ("summary", ""),
        ("lead", ""),
        ("outcome", ""),
    ):
        if fields.get(clearable) == "-":
            fields[clearable] = empty  # type: ignore[assignment]
    if freshly_closed:
        fields["closed_at"] = db.now()  # re-closing must not re-fire ship-it
    sets = ", ".join(f"{k} = ?" for k in fields)
    # rename propagation rides the same transaction AFTER all validation — a
    # rejected PATCH must never leave milestones labeled with a name no
    # engagement has
    with db.transaction():
        if renaming:
            db.execute(
                "UPDATE milestones SET project = ? WHERE engagement_id = ?",
                (name, engagement_id),
            )
        db.execute(
            f"UPDATE engagements SET {sets}, updated_at = ? WHERE id = ?",  # noqa: S608 — keys hardcoded
            (*fields.values(), db.now(), engagement_id),
        )
    db.log_activity(actor, "update_engagement", f"#{engagement_id} {status or 'edited'}")
    if "name" in fields:
        row = db.query_one("SELECT * FROM engagements WHERE id = ?", (engagement_id,))
        if row:
            index_record(
                "engagement", engagement_id, row["name"], f"{row['summary']} {row['lead']}"
            )
    if freshly_closed:
        _ship_it(engagement_id, actor=actor, origin=origin)
        if current["kind"] == "experiment":
            _experiment_lesson(engagement_id, actor=actor, origin=origin)
        lesson_proposal = _playbook_lesson(engagement_id, actor=actor)
        # closing over live work must be loud, not blocking: orphaned tasks
        # silently stop counting anywhere once their engagement is closed
        open_tasks = db.query_one(
            "SELECT COUNT(*) AS n FROM tasks WHERE status NOT IN ('done')"
            " AND (engagement_id = ? OR milestone_id IN"
            " (SELECT id FROM milestones WHERE engagement_id = ?))",
            (engagement_id, engagement_id),
        )
        if open_tasks and open_tasks["n"]:
            from .notifications import notify

            notify(
                "team",
                f"Engagement #{engagement_id} closed with {open_tasks['n']}"
                f" open task{'' if open_tasks['n'] == 1 else 's'} — rehome or close"
                f" {'it' if open_tasks['n'] == 1 else 'them'}.",
                tier="digest",
                link="/dashboard",
                # This count is derived from several task rows. It has no
                # single authoritative source, so policy-aware readers treat
                # it as unclassified instead of checking only the engagement.
            )
            return {
                "id": engagement_id,
                "updated": list(fields),
                "open_tasks": open_tasks["n"],
                "lesson_proposal_id": lesson_proposal,
            }
        return {
            "id": engagement_id,
            "updated": list(fields),
            "lesson_proposal_id": lesson_proposal,
        }
    return {"id": engagement_id, "updated": list(fields)}


def _playbook_lesson(engagement_id: int, *, actor: str) -> int:
    """Close-out variance from a playbook, drafted as a PROPOSAL.

    A proposal, never a direct write, and that is the point rather than a
    formality: the diff is arithmetic but the lesson it implies is a judgment
    ("the milestone was optimistic" or "that quarter was unusual"), and only
    somebody who ran the engagement can tell those apart. The reviewer's
    verdict is what earns it a place the next kickoff will read.

    WORKSPACE TIER ONLY, and checked here rather than left to fail inside the
    diff. `close_out_diff` reads with NOBODY — the drafted lesson becomes a
    proposal, and a proposal assembled from rows the drafter had to be
    privileged to see is how a scoped title reaches a reviewer. So a crew or
    private engagement gets no draft. `drafts_lesson` on the plan-diff
    endpoint reports that to the close-out control, which is what keeps the
    panel from promising a lesson nobody will get.

    Returns the proposal id, or 0 when there is nothing to say — not a
    playbook engagement, a scoped one, or a plan that held. Never raises: a
    close must not fail because the lesson could not be drafted.
    """
    try:
        # The lesson is optional, but its proposal, notice, ledger entry, and
        # deferred effects are one unit. A caught failure must remove the
        # whole unit before the engagement close commits.
        with db.savepoint():
            return _playbook_lesson_locked(engagement_id, actor=actor)
    except Exception:
        logging.getLogger("skein").warning(
            "close-out lesson could not be drafted for engagement #%s", engagement_id, exc_info=True
        )
        return 0


def _playbook_lesson_locked(engagement_id: int, *, actor: str) -> int:
    from . import playbooks, review

    eng = db.query_one("SELECT * FROM engagements WHERE id = ?", (engagement_id,))
    if not eng or eng["visibility"] != scope.WORKSPACE:
        return 0
    diff = playbooks.close_out_diff(engagement_id)
    if not diff:
        return 0
    lesson, recommendation = playbooks._variance_lesson(diff, eng["name"])
    if not lesson:
        return 0
    # Reopening and re-closing must not file a second identical draft.
    # `freshly_closed` already makes a repeated PATCH a no-op, but a
    # reopen resets it, and two approvable copies of one lesson is two
    # verdicts and two rows in the next kickoff note.
    if db.query_one(
        "SELECT id FROM pending_changes WHERE entity = 'lesson' AND status = 'pending'"
        " AND payload LIKE ?",
        (f'%"engagement_id": {engagement_id},%',),
    ):
        return 0
    prop = review.propose_change(
        "lesson",
        "create",
        {
            "lesson": lesson,
            "recommendation": recommendation,
            "engagement_id": engagement_id,
            "project_class": eng["project_class"],
            "visibility": eng["visibility"],
            "crew_id": eng["crew_id"] or 0,
        },
        summary=f"Close-out lesson from the {diff['playbook']} playbook",
        actor="system",
        origin="agent",
        requested_by=actor,
    )
    # the closer, not "system": this is what the field guide's
    # `playbook_closeout` card reads to know the person has tied it
    db.log_activity(actor, "playbook_closeout", f"#{engagement_id} -> proposal #{prop['id']}")
    return int(prop["id"])


def _experiment_lesson(engagement_id: int, *, actor: str, origin: str) -> None:
    """Closing an experiment auto-drafts a lesson — the whole point of
    running one is what it taught."""
    eng = db.query_one("SELECT * FROM engagements WHERE id = ?", (engagement_id,))
    if not eng:
        return
    record_lesson(
        lesson=f"Experiment '{eng['name']}' concluded: {eng['conclusion']}."
        + (f" Outcome: {eng['outcome']}" if eng["outcome"] else ""),
        recommendation="",
        engagement_id=engagement_id,
        project_class=eng["project_class"],
        actor=actor,
        origin=origin,
        # the lesson text IS the engagement name, conclusion and outcome, and
        # lessons reach the context pack, the handoff file and the FTS index
        visibility=eng["visibility"],
        crew_id=eng["crew_id"] or 0,
    )


def _ship_it(engagement_id: int, *, actor: str, origin: str = "human") -> None:
    with db.transaction():
        _ship_it_locked(engagement_id, actor=actor, origin=origin)


def _ship_it_locked(engagement_id: int, *, actor: str, origin: str) -> None:
    """The Ship It moment: recap card + team notification when an engagement
    closes. Deterministic — all counts from SQL."""
    eng = db.query_one("SELECT * FROM engagements WHERE id = ?", (engagement_id,))
    if not eng:
        return
    name = eng["name"]
    days = ""
    if eng["started_at"] and eng["closed_at"]:
        delta = (
            db.query_one(
                "SELECT ROUND((EXTRACT(epoch FROM ?::timestamptz - ?::timestamptz) / 86400.0)::numeric) AS d",
                (eng["closed_at"], eng["started_at"]),
            )
            or {}
        ).get("d")
        # same-day closes skip the duration — "— 0 days" reads as a bug
        days = f"{int(delta)} days" if delta else ""
    stats = {
        "milestones": db.query_row(
            "SELECT COUNT(*) AS n FROM milestones WHERE engagement_id = ?", (engagement_id,)
        ),
        # BOTH link paths — direct tasks.engagement_id and via milestones —
        # the same predicate the open-task warning above uses; an engagement
        # worked without milestones must not recap as zero
        "tasks_done": db.query_row(
            "SELECT COUNT(*) AS n FROM tasks t WHERE t.status = 'done'"
            " AND (t.engagement_id = ? OR t.milestone_id IN"
            " (SELECT id FROM milestones WHERE engagement_id = ?))",
            (engagement_id, engagement_id),
        ),
        # scoped to THIS engagement's linked blockers — the recap must be honest
        # (a time-window count silently absorbed unrelated blockers)
        "blockers_survived": db.query_row(
            "SELECT COUNT(*) AS n FROM blockers b JOIN tasks t ON t.id = b.task_id"
            " WHERE b.status = 'resolved' AND (t.engagement_id = ? OR t.milestone_id IN"
            " (SELECT id FROM milestones WHERE engagement_id = ?))",
            (engagement_id, engagement_id),
        ),
    }
    if eng["kind"] == "experiment":
        # an invalidated hypothesis that finished on time is a success
        head = f"🧪 **Experiment concluded: {name}** — {eng['conclusion'] or 'unmeasured'}"
    else:
        head = f"🚢🪿 **Shipped: {name}**"
    # zero-valued stats are noise in a celebration line — say only what happened
    parts = [
        f"{stats['milestones']['n']} milestones" if stats["milestones"]["n"] else "",
        f"{stats['tasks_done']['n']} tasks done" if stats["tasks_done"]["n"] else "",
        f"{stats['blockers_survived']['n']} blockers survived"
        if stats["blockers_survived"]["n"]
        else "",
    ]
    tail = " · ".join(p for p in parts if p)
    recap = head + (f" — {days}" if days else "") + (f" · {tail}" if tail else "")
    from .collab import save_note
    from .notifications import notify

    # origin threads through, like _experiment_lesson beside the call site:
    # hardcoded "human", an engagement closed by the agent path writes a
    # machine-generated note attributed to a person, in the same transaction
    # where the lesson correctly records agent_verified
    # the note takes the engagement's tier: its topic IS the engagement name
    # and its body quotes it, so a workspace child republished a crew closure
    # to the whole roster and put it in the FTS index besides
    save_note(
        topic=f"shipped-{name}",
        content=recap,
        author=actor,
        actor=actor,
        origin=origin,
        visibility=eng["visibility"],
        crew_id=eng["crew_id"] or 0,
    )
    # the note renders markdown; notifications land on plain-text surfaces.
    # "team" is every person on the roster, so a scoped closure is not
    # announced at all — the crew reads it on the note above.
    if eng["visibility"] == scope.WORKSPACE:
        event = "concluded" if eng["kind"] == "experiment" else "shipped"
        notify(
            "team",
            lambda source: (
                f"Engagement #{source['id']} '{source['name']}' {event}. Open Skein for the recap."
            ),
            tier="immediate",
            link="/dashboard",
            source_entity="engagement",
            source_id=engagement_id,
        )


def list_engagements(status: str = "", viewer: scope.Viewer = scope.NOBODY) -> list[dict]:
    frag, vp = scope.visible_filter(viewer, "engagements")
    if status:
        rows = db.query(
            f"SELECT * FROM engagements WHERE status = ? AND {frag} ORDER BY id DESC LIMIT 200",  # noqa: S608 — scope.visible_filter emits only bound marks
            (status, *vp),
        )
    else:
        rows = db.query(
            f"SELECT * FROM engagements WHERE {frag} ORDER BY status = 'closed', id DESC LIMIT 200",  # noqa: S608 — scope.visible_filter emits only bound marks
            tuple(vp),
        )
    # One query for every engagement's allocations, not one per engagement:
    # this ran up to 201 queries per GET /api/engagements. Placeholders are
    # generated from the row count, never interpolated from caller input.
    if not rows:
        return rows
    marks = ",".join("?" * len(rows))
    grouped: dict[int, list[dict]] = {r["id"]: [] for r in rows}
    for a in db.query(
        "SELECT engagement_id, person, percent, starts_on, ends_on FROM allocations"  # noqa: S608 — marks below are generated ?s, never caller input
        f" WHERE engagement_id IN ({marks})",
        tuple(r["id"] for r in rows),
    ):
        grouped[a["engagement_id"]].append(
            {k: a[k] for k in ("person", "percent", "starts_on", "ends_on")}
        )
    for r in rows:
        r["allocations"] = grouped[r["id"]]
    return rows


def allocate(
    person: str,
    engagement_id: int,
    percent: int = 100,
    starts_on: str = "",
    ends_on: str = "",
    *,
    actor: str = "system",
    origin: str = "human",
) -> dict:
    from .users import resolve_teammate

    person = resolve_teammate(person, actor, "person", allow_team=False)
    if not person:
        raise ValueError("person is required")
    db.validate_date("starts_on", starts_on, allow_clear=False)
    db.validate_date("ends_on", ends_on, allow_clear=False)
    if not 1 <= percent <= 100:
        raise ValueError("percent must be 1-100")
    # filtered like every other link probe: unfiltered, it accepts a scoped id
    # and refuses an absent one, and ids are sequential
    # (services/scope.py::Viewer.for_actor names the attack)
    afrag, ap = scope.visible_filter(scope.Viewer.for_actor(actor), "engagements")
    if not db.query_one(
        f"SELECT id FROM engagements WHERE id = ? AND {afrag}",  # noqa: S608 — scope.visible_filter emits only bound marks
        (engagement_id, *ap),
    ):
        raise scope.missing("engagements", engagement_id)
    aid = db.execute(
        "INSERT INTO allocations (person, engagement_id, percent, starts_on, ends_on,"
        " origin, created_by, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        " RETURNING id",
        (
            person,
            engagement_id,
            percent,
            starts_on or None,
            ends_on or None,
            origin,
            actor,
            db.now(),
        ),
    )
    db.log_activity(actor, "allocate", f"{person} -> engagement #{engagement_id} @{percent}%")
    return {"id": aid, "person": person, "percent": percent}


def deallocate(allocation_id: int, *, actor: str = "system") -> dict:
    """Allocations were append-only — one fat-fingered percent permanently
    skewed capacity, conflicts, and what-if staffing."""
    # gated on the ENGAGEMENT, like allocate: `allocations` carries no tier of
    # its own (scope.UNSCOPED), so without this any caller could walk
    # allocation ids and delete staffing off an engagement they cannot read —
    # and read its existence off the refusal while doing it.
    dfrag, dp = scope.visible_filter(scope.Viewer.for_actor(actor), "engagements", "e")
    row = db.query_one(
        "SELECT a.person, a.engagement_id, a.percent FROM allocations a"  # noqa: S608 — scope.visible_filter emits only bound marks
        f" JOIN engagements e ON e.id = a.engagement_id AND {dfrag}"
        " WHERE a.id = ?",
        (*dp, allocation_id),
    )
    if not row:
        raise db.NotFound(f"no allocation #{allocation_id}")
    db.execute("DELETE FROM allocations WHERE id = ?", (allocation_id,))
    db.log_activity(
        actor,
        "deallocate",
        f"#{allocation_id} {row['person']} -> engagement #{row['engagement_id']}"
        f" @{row['percent']}%",
    )
    return {"id": allocation_id, "deleted": True}


def list_allocations(
    engagement_id: int = 0, limit: int = 500, viewer: scope.Viewer = scope.NOBODY
) -> list[dict]:
    """An allocation is a person and a percent, and `allocations` carries no
    tier of its own (scope.UNSCOPED). The engagement NAME it joins to does —
    so the rows all stay and the name is masked (scope.visible_name)."""
    name, np = scope.visible_name(viewer, "engagements", "e.name", alias="e")
    if engagement_id:
        return db.query(
            f"SELECT a.*, {name} AS engagement FROM allocations a"  # noqa: S608 — scope.visible_name emits only bound marks
            " JOIN engagements e ON e.id = a.engagement_id WHERE a.engagement_id = ?"
            " ORDER BY a.id DESC LIMIT ?",
            (*np, engagement_id, limit),
        )
    return db.query(
        f"SELECT a.*, {name} AS engagement FROM allocations a"  # noqa: S608 — scope.visible_name emits only bound marks
        " JOIN engagements e ON e.id = a.engagement_id WHERE e.status != 'closed'"
        " ORDER BY a.id DESC LIMIT ?",
        (*np, limit),
    )


def capacity(viewer: scope.Viewer = scope.NOBODY) -> list[dict]:
    """Total allocation per person across non-closed engagements; >100 =
    overcommitted. Window-aware like allocation_conflicts: rows whose date
    window excludes today don't count (capacity and conflicts must agree).
    Absence-aware: people away today carry an `away` marker so the math is
    read with the right eyes (a PTO'd 80% is not 80%).

    The percent sums over every tier and the NAME is masked per row — see
    scope.visible_name for why the total must stay honest.
    """
    today = db.today().isoformat()  # vs absences.starts_on/ends_on, date columns
    name, np = scope.visible_name(viewer, "engagements", "e.name", alias="e")
    rows = db.query(
        "SELECT a.person, SUM(a.percent) AS total_percent,"  # noqa: S608 — scope.visible_name emits only bound marks
        f" string_agg({name} || ' (' || a.percent || '%)', ', ') AS detail"
        " FROM allocations a JOIN engagements e ON e.id = a.engagement_id"
        " WHERE e.status != 'closed'"
        " AND (a.starts_on IS NULL OR a.starts_on <= ?)"
        " AND (a.ends_on IS NULL OR a.ends_on >= ?)"
        " GROUP BY a.person ORDER BY total_percent DESC",
        (*np, today, today),
    )
    from .absences import away_today

    away = away_today()
    for r in rows:
        r["away"] = away.get(r["person"], "")
    return rows


def record_lesson(
    lesson: str,
    recommendation: str = "",
    engagement_id: int = 0,
    project_class: str = "general",
    *,
    actor: str = "system",
    origin: str = "human",
    visibility: str = scope.WORKSPACE,
    crew_id: int = 0,
) -> dict:
    if not lesson.strip():
        raise ValueError("the lesson text is required")
    # filtered like the other link probes: an unfiltered probe accepts a
    # scoped id and refuses an absent one, and it also let a non-reader attach
    # a lesson to an engagement they cannot read
    lfrag, lp = scope.visible_filter(scope.Viewer.for_actor(actor), "engagements")
    if engagement_id and not db.query_one(
        f"SELECT id FROM engagements WHERE id = ? AND {lfrag}",  # noqa: S608 — scope.visible_filter emits only bound marks
        (engagement_id, *lp),
    ):
        raise ValueError(scope.missing_text("engagements", engagement_id))
    with db.transaction():
        tier, crew = scope.resolve_write(visibility, crew_id, actor=actor)
        lid = db.execute(
            "INSERT INTO lessons (engagement_id, project_class, lesson, recommendation,"
            " origin, created_by, created_at, visibility, crew_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " RETURNING id",
            (
                engagement_id or None,
                project_class,
                lesson,
                recommendation,
                origin,
                actor,
                db.now(),
                tier,
                crew,
            ),
        )
        db.log_activity(actor, "record_lesson", f"#{lid} [{project_class}]")
        index_record("lesson", lid, lesson[:120], f"{lesson} {recommendation}")
    return {"id": lid, "project_class": project_class}


def list_lessons(
    project_class: str = "", limit: int = 100, viewer: scope.Viewer = scope.NOBODY
) -> list[dict]:
    frag, vp = scope.visible_filter(viewer, "lessons")
    if project_class:
        return db.query(
            f"SELECT * FROM lessons WHERE project_class = ? AND {frag}"  # noqa: S608 — scope.visible_filter emits only bound marks
            " ORDER BY id DESC LIMIT ?",
            (project_class, *vp, limit),
        )
    return db.query(
        f"SELECT * FROM lessons WHERE {frag} ORDER BY id DESC LIMIT ?",  # noqa: S608 — scope.visible_filter emits only bound marks
        (*vp, limit),
    )
