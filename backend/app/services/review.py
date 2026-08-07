"""Pending-changes review flow: agents (and cautious humans) propose, humans
approve. Approval applies the payload through the same service registry the
rest of the platform uses, stamped origin='agent_verified'."""

import json

from .. import db
from . import lexicon, scope


def _registry() -> dict:
    from . import (
        absences,
        blockers,
        collab,
        delegation,
        engagements,
        intake,
        memory,
        playbooks,
        promises,
        schedule,
        weekly,
        work,
    )

    return {
        "milestone": {"create": work.create_milestone, "update": work.update_milestone},
        "task": {"create": work.create_task, "update": work.update_task},
        "question": {"create": collab.ask_question, "update": collab.answer_question},
        "question_assign": {"update": collab.assign_question},
        "decision": {"create": collab.record_decision, "update": collab.supersede_decision},
        "standup": {"create": collab.post_standup},
        "note": {"create": collab.save_note},
        "note_edit": {"update": collab.update_note},
        "note_delete": {"update": collab.delete_note},
        "event": {"create": schedule.schedule_event},
        "event_cancel": {"update": schedule.cancel_event},
        "blocker": {"create": blockers.raise_blocker, "update": blockers.resolve_blocker},
        "blocker_edit": {"update": blockers.edit_blocker},
        "engagement": {
            "create": engagements.create_engagement,
            "update": engagements.update_engagement,
        },
        "intake": {"create": intake.submit_request},
        "intake_edit": {"update": intake.edit_request},
        "lesson": {"create": engagements.record_lesson},
        "playbook": {"create": playbooks.instantiate},
        "weekly_plan": {"create": weekly.apply_plan},
        "promise": {
            "create": promises.add_promise,
            "update": promises.update_promise,
        },
        "promise_edit": {"update": promises.edit_promise},
        "promise_settle": {"update": promises.update_promise},
        "memory": {"create": memory.remember},
        "memory_forget": {"update": memory.forget},
        "delegation": {"create": delegation.delegate_task},
        "task_completion": {"update": delegation.accept_completion},
        "authority": {"create": delegation.set_authority},
        "absence": {"create": absences.add_absence},
    }


def propose_change(
    entity: str,
    action: str,
    payload: dict,
    summary: str = "",
    entity_id: int = 0,
    *,
    actor: str = "agent",
    origin: str = "agent",
    notify_team: bool = True,
    requested_by: str = "",
) -> dict:
    reg = _registry()
    if entity not in reg:
        raise ValueError(f"unknown entity — one of {sorted(reg)}")
    if action not in ("create", "update") or action not in reg[entity]:
        raise ValueError(f"unsupported action for {entity} — create or update")
    if action == "update" and not entity_id:
        raise ValueError("entity_id required for updates")
    # a proposal a reviewer must read is bounded like any other write —
    # oversized payloads would also fail at apply and wedge in the queue
    if len(json.dumps(payload)) > 20_000:
        raise ValueError("proposal payload too large — keep it under 20k characters")
    pid = db.execute(
        "INSERT INTO pending_changes (entity, entity_id, action, payload, summary,"
        " proposed_by, origin, created_at, requested_by)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            entity,
            entity_id or None,
            action,
            json.dumps(payload),
            summary or f"{action} {entity}",
            actor,
            origin,
            db.now(),
            requested_by or None,
        ),
    )
    db.log_activity(
        actor,
        "propose_change",
        f"#{pid} {action} {entity}" + (f" (asked by {requested_by})" if requested_by else ""),
    )
    if notify_team:  # bulk producers (ingestion) send ONE summary instead
        from .notifications import notify

        notify(
            "team",
            f"Review needed: #{pid} {summary or f'{action} {entity}'}",
            tier="digest",
            link="/review",
        )
    return {"id": pid, "status": "pending"}


def _check_reviewer(actor: str) -> None:
    """Verdicts are human work. No tool exposes approve/reject, but the REST
    path resolves any X-User — an agent identity must be refused here too."""
    from .users import is_agent

    if is_agent(actor):
        raise ValueError(f"'{actor}' is an agent identity — proposals are judged by humans")


def _sponsor_of(change: dict) -> str:
    """The task's CURRENT sponsor for a task_completion proposal ('' for
    everything else) — looked up at verdict time, so a re-delegation moves
    the verdict to the new sponsor."""
    if change["entity"] != "task_completion" or not change["entity_id"]:
        return ""
    task = db.query_one("SELECT sponsor FROM tasks WHERE id = ?", (change["entity_id"],))
    return (task["sponsor"] or "") if task else ""


def _sponsor_override(change: dict, actor: str, note: str) -> str:
    """Acceptance verdicts belong to the sponsor — they judged the work, so
    their verdict is the trust label. Anyone else may still act, but only
    with a reason on record (the sponsor is away, gone, or asked them to),
    and the verdict is marked an override so it never feeds a streak.
    Returns a label for the activity log when this verdict is an override,
    else ''."""
    if change["entity"] != "task_completion":
        return ""
    sponsor = _sponsor_of(change)
    if sponsor and actor == sponsor:
        return ""
    if not sponsor:
        # reassignment cleared the delegation: nobody sponsors this proposal
        # anymore, so NO verdict on it is a trust signal — reason required
        if not note.strip():
            raise ValueError(
                f"task #{change['entity_id']}'s delegation was cleared —"
                " judging this orphaned acceptance needs a note saying why"
            )
        return "orphaned delegation"
    if not note.strip():
        raise ValueError(
            f"task #{change['entity_id']} is sponsored by {sponsor} — acting"
            " for them needs a note saying why (it goes on the record)"
        )
    return sponsor


def _claim(
    change_id: int,
    new_status: str,
    note: str,
    actor: str,
    strong: bool = False,
    override: bool = False,
) -> None:
    """Compare-and-swap the pending -> reviewed transition so concurrent
    approve/reject calls can't both act on the same change."""
    claimed = db.execute_rowcount(
        "UPDATE pending_changes SET status = ?, reviewed_by = ?, review_note = ?,"
        " reviewed_at = ?, reviewed_strong = ?, reviewed_override = ?"
        " WHERE id = ? AND status = 'pending'",
        (new_status, actor, note, db.now(), int(strong), int(override), change_id),
    )
    if not claimed:
        change = db.query_one("SELECT status FROM pending_changes WHERE id = ?", (change_id,))
        if not change:
            raise db.NotFound(f"pending change #{change_id} not found")
        raise ValueError(f"change #{change_id} already {change['status']}")


def _assert_judgeable(change: dict, viewer: scope.Viewer) -> None:
    """A verdict on a row you cannot read is refused, in the same words as a
    proposal that does not exist.

    `_readable` hides these from the queue and `change_diff` returns no diff,
    but the VERDICT endpoints took a bare id — so a caller in no crew could
    walk ids and approve a proposal whose payload then overwrote a crew note's
    content, or applied a note_delete / memory_forget / event_cancel. The
    apply runs as `change["proposed_by"]`, an agent slug, and scope.is_machine
    lets a machine work a crew row on purpose, so assert_editable inside the
    handler never refuses it. This is the only place that can.

    NotFound with the same sentence as an absent proposal: any other wording
    tells the caller that #12 exists and is scoped (scope.missing).
    """
    table = _TARGET_TABLE.get(change["entity"])
    if table not in scope.CLASSIFIED or not change["entity_id"]:
        return
    author = scope.CLASSIFIED[table]
    row = db.query_one(
        f"SELECT visibility, crew_id, {author} AS author FROM {table} WHERE id = ?",  # noqa: S608 — table and column from constant maps
        (change["entity_id"],),
    )
    # a VANISHED target is not refused here, unlike _readable: there is no row
    # left to protect, and approve_change has its own auto-reject path for it
    # ("target vanished") that a 404 would hide behind the wrong sentence.
    # _readable still drops it from the LIST, where the summary would show.
    if row and not scope.can_read(row["visibility"], row["crew_id"], viewer, row["author"] or ""):
        raise db.NotFound(f"pending change #{change['id']} not found")


def approve_change(
    change_id: int,
    note: str = "",
    *,
    actor: str = "system",
    strong: bool = False,
    viewer: scope.Viewer = scope.NOBODY,
) -> dict:
    _check_reviewer(actor)
    change = db.query_one("SELECT * FROM pending_changes WHERE id = ?", (change_id,))
    if not change:
        raise db.NotFound(f"pending change #{change_id} not found")
    _assert_judgeable(change, viewer)
    # settle the already-reviewed case before any gating, so a non-sponsor
    # isn't told to fetch a note for a verdict that already happened
    if change["status"] != "pending":
        raise ValueError(f"change #{change_id} already {change['status']}")
    # the direct authority endpoint requires a personal key; the proposal
    # path must not be the weaker door to the same lever
    if change["entity"] == "authority" and not strong:
        raise ValueError(
            "authority changes need a strong identity — approve with your personal API key"
        )

    # resolve the handler BEFORE claiming — a stale entity/action must not
    # leave the row marked approved with nothing applied
    try:
        fn = _registry()[change["entity"]][change["action"]]
    except KeyError as exc:
        raise ValueError(f"no handler for {change['entity']}.{change['action']}") from exc
    payload = json.loads(change["payload"])
    sponsor = _sponsor_override(change, actor, note)
    _claim(change_id, "approved", note, actor, strong, override=bool(sponsor))
    try:
        # compound applies (playbook, weekly_plan) land atomically or not at
        # all — a failed apply rolls back, so pending is safe for EVERY entity
        with db.transaction():
            # authorship stays with the proposer: created_by must say who
            # wrote it, not who clicked approve (the verdict is recorded on
            # the pending_changes row + activity)
            author = change["proposed_by"] or actor
            if change["action"] == "update":
                result = fn(change["entity_id"], **payload, actor=author, origin="agent_verified")
            else:
                result = fn(**payload, actor=author, origin="agent_verified")
    except db.NotFound as exc:
        # the proposal's own target vanished (event cancelled via REST, row
        # hard-deleted): re-approving can never succeed, so a pending reset
        # would boomerang forever — settle it as rejected, on the record
        db.execute(
            "UPDATE pending_changes SET status = 'rejected', review_note = ? WHERE id = ?",
            (f"auto-rejected — target vanished: {exc}", change_id),
        )
        db.log_activity(actor, "reject_change", f"#{change_id} (target vanished)")
        _clear_review_ping(change_id)
        raise ValueError(
            f"could not apply {change['entity']}.{change['action']}: {exc}"
            " — proposal auto-rejected (its target no longer exists)"
        ) from exc
    except db.TerminalReject as exc:
        # a permanent policy block (an agent's own delegated-done proposal):
        # re-approving can never succeed, so settle it rejected like a vanished
        # target instead of resetting to pending, where it would clutter the
        # queue until a human rejected it by hand
        db.execute(
            "UPDATE pending_changes SET status = 'rejected', review_note = ? WHERE id = ?",
            (f"auto-rejected — {exc}", change_id),
        )
        db.log_activity(actor, "reject_change", f"#{change_id} (not applicable)")
        _clear_review_ping(change_id)
        raise ValueError(f"could not apply and auto-rejected: {exc}") from exc
    except Exception as exc:
        # ANY OTHER failure (IntegrityError, lock timeout, stale state)
        # resets the claim — an approved-but-never-applied proposal would
        # vanish from the queue. The reviewer's note survives the reset.
        db.execute(
            "UPDATE pending_changes SET status = 'pending', reviewed_by = NULL,"
            " reviewed_at = NULL, reviewed_strong = 0, reviewed_override = 0,"
            " review_note = ? WHERE id = ?",
            (f"apply failed: {exc}" + (f" (reviewer note: {note})" if note else ""), change_id),
        )
        raise ValueError(f"could not apply {change['entity']}.{change['action']}: {exc}") from exc

    db.execute(
        "UPDATE pending_changes SET result_id = ? WHERE id = ?", (result.get("id"), change_id)
    )
    applied = f"#{result['id']}" if result.get("id") is not None else "applied"
    db.log_activity(
        actor,
        "approve_change",
        f"#{change_id} -> {change['entity']} {applied}"
        + (f" (accepted for {sponsor})" if sponsor else ""),
    )
    _clear_review_ping(change_id)
    return {"id": change_id, "status": "approved", "result": result}


def _clear_review_ping(change_id: int) -> None:
    """The review is handled — its "Review needed" ping must not keep
    nagging. Called AFTER the apply succeeds (a failed apply resets the
    proposal to pending and must keep its notification unread)."""
    from .notifications import mark_read_matching

    mark_read_matching(f"Review needed: #{change_id} ")


def reject_change(
    change_id: int,
    note: str = "",
    *,
    actor: str = "system",
    strong: bool = False,
    viewer: scope.Viewer = scope.NOBODY,
) -> dict:
    _check_reviewer(actor)
    change = db.query_one("SELECT * FROM pending_changes WHERE id = ?", (change_id,))
    if not change:
        raise db.NotFound(f"pending change #{change_id} not found")
    # a reject is a verdict too: it feeds rejection streaks and demotion, and
    # it settles a proposal against a row this caller cannot read
    _assert_judgeable(change, viewer)
    if change["status"] != "pending":
        raise ValueError(f"change #{change_id} already {change['status']}")
    # symmetric with approve: a non-sponsor reject feeds rejection streaks
    # (demotion input), so it needs the same reason-on-record
    sponsor = _sponsor_override(change, actor, note)
    _claim(change_id, "rejected", note, actor, strong, override=bool(sponsor))
    db.log_activity(
        actor,
        "reject_change",
        f"#{change_id}" + (f" (rejected for {sponsor})" if sponsor else ""),
    )
    _clear_review_ping(change_id)
    return {"id": change_id, "status": "rejected"}


_DIFF_TABLES = {
    "task": "tasks",
    "milestone": "milestones",
    "engagement": "engagements",
    "promise": "promises",
    "promise_edit": "promises",
    "promise_settle": "promises",
    "blocker": "blockers",
    "blocker_edit": "blockers",
    "question": "questions",
    "decision": "decisions",
    "note_edit": "notes",
    "note_delete": "notes",
    "event_cancel": "events",
    "intake_edit": "intake_requests",
    "memory_forget": "memories",
}

# What table an entity's proposal TARGETS. A superset of _DIFF_TABLES, which
# answers a narrower question (what can be rendered as a before/after) and so
# omits the two entities whose payload is not a column set: task_completion
# carries a free-text work summary, question_assign carries an assignee.
# _readable needs the TARGET, and reading it off _DIFF_TABLES let both of
# those bypass the tier check entirely — every reader saw a crew task's
# acceptance payload in the review queue, on the dashboard, and in the stats.
_TARGET_TABLE = {
    **_DIFF_TABLES,
    # updates _DIFF_TABLES cannot render (their payload is not a column set)
    "task_completion": "tasks",
    "question_assign": "questions",
    # every CREATE. These have no target row yet, so _DIFF_TABLES never needed
    # them — but the tier the row WOULD take is in the payload, and without an
    # entry here _readable had nothing to look up and kept the proposal, body
    # and all, for every reader.
    "task": "tasks",
    "milestone": "milestones",
    "question": "questions",
    "decision": "decisions",
    "standup": "standups",
    "note": "notes",
    "event": "events",
    "blocker": "blockers",
    "engagement": "engagements",
    "intake": "intake_requests",
    "lesson": "lessons",
    "promise": "promises",
    "memory": "memories",
    "absence": "absences",
    "delegation": "tasks",
}

# Entities that address no scoped row at all, and why. Kept as an explicit
# list rather than an absence, so tests/test_review.py can prove _registry
# gained nothing that silently skips the tier check.
_UNTARGETED = {
    "playbook": "instantiates a whole engagement tree, no single target row",
    "weekly_plan": "commits a set of task ids, each already tier-checked on its own",
    "authority": "an agent's permission matrix, which carries no tier",
}

# columns worth showing a reviewer when a proposal would DESTROY the row —
# an empty payload must never mean an uninformed verdict
_DESTRUCTIVE_VIEW = {
    "note_delete": ("topic", "content"),
    "memory_forget": ("topic", "content", "user"),
    "event_cancel": ("title", "starts_at", "attendees"),
}


def change_diff(change_id: int, viewer: scope.Viewer = scope.NOBODY) -> dict:
    """Before/after view for update proposals: current row values for exactly
    the fields the payload would change.

    Filtered on the TARGET row, not on the proposal. `pending_changes` carries
    no tier of its own (scope.UNSCOPED) and this endpoint is CurrentUser, so
    an unfiltered read here handed any reader the current body of a private
    note or memory verbatim — _DESTRUCTIVE_VIEW renders `content` and `topic`
    for exactly the two entities whose deletion always files a proposal.
    A reader who cannot see the row gets no diff at all: the proposed half is
    the payload, which for an edit holds the new body.
    """
    change = db.query_one("SELECT * FROM pending_changes WHERE id = ?", (change_id,))
    if not change:
        raise db.NotFound(f"pending change #{change_id} not found")
    table = _DIFF_TABLES.get(change["entity"])
    if change["action"] != "update" or not table or not change["entity_id"]:
        return {"id": change_id, "diff": None}
    frag, fp = scope.visible_filter(viewer, table) if table in scope.CLASSIFIED else ("1 = 1", [])
    row = db.query_one(
        f"SELECT * FROM {table} WHERE id = ? AND {frag}",  # noqa: S608 — table from constant map, and scope.visible_filter emits only bound marks
        (change["entity_id"], *fp),
    )
    if not row:
        return {"id": change_id, "diff": None}
    payload = json.loads(change["payload"])
    doomed = _DESTRUCTIVE_VIEW.get(change["entity"])
    if doomed:
        # deletion diff: show what would be destroyed; proposed side is empty
        current = {k: (row.get(k) if row else None) for k in doomed}
        return {
            "id": change_id,
            "diff": {"current": current, "proposed": dict.fromkeys(doomed, "")},
        }
    current = {k: (row.get(k) if row else None) for k in payload}
    return {"id": change_id, "diff": {"current": current, "proposed": payload}}


def mark_seen(ids: list[int], *, actor: str = "system") -> dict:
    """The review UI calls this when a human loads pending proposals —
    first-seen (claim_at) starts the active-review clock, so review burden
    can be measured as seen→verdict instead of created→verdict (which is
    dominated by queue wait, not human effort)."""
    batch = ids[:200]
    if not batch:
        return {"seen": 0}
    marks = ", ".join("?" for _ in batch)
    n = db.execute_rowcount(
        f"UPDATE pending_changes SET claim_at = ?"  # noqa: S608 — placeholders built above
        f" WHERE id IN ({marks}) AND status = 'pending' AND claim_at IS NULL",
        (db.now(), *batch),
    )
    return {"seen": n}


def review_stats(viewer: scope.Viewer = scope.NOBODY) -> dict:
    """The review inbox as a flywheel: every verdict is a labeled example.
    These stats show which proposal types earn trust and which waste reviewer
    time — the input to authority-matrix decisions."""
    by_entity = db.query(
        "SELECT entity,"
        " COUNT(*) AS proposed,"
        " SUM(status = 'approved') AS approved,"
        " SUM(status = 'rejected') AS rejected,"
        " SUM(status = 'pending') AS pending,"
        " ROUND(AVG(CASE WHEN reviewed_at IS NOT NULL THEN"
        " (julianday(reviewed_at) - julianday(created_at)) * 24 END), 1) AS avg_review_hours"
        " FROM pending_changes GROUP BY entity ORDER BY proposed DESC"
    )
    by_proposer = db.query(
        "SELECT proposed_by,"
        " COUNT(*) AS proposed,"
        " SUM(status = 'approved') AS approved,"
        " SUM(status = 'rejected') AS rejected"
        " FROM pending_changes GROUP BY proposed_by ORDER BY proposed DESC"
    )
    # the only list here that carries row TEXT. The aggregates above count
    # rows per entity and per proposer, which discloses nothing; `summary` is
    # built by the producer out of the target row's own title, so a rejected
    # proposal against a crew note republished it to the whole roster.
    rejection_reasons = _readable(
        db.query(
            "SELECT entity, entity_id, summary, review_note, reviewed_by FROM pending_changes"
            " WHERE status = 'rejected' AND review_note != '' ORDER BY id DESC LIMIT 20"
        ),
        viewer,
    )
    minutes = sorted(
        r["m"]
        for r in db.query(
            "SELECT (julianday(reviewed_at) - julianday(claim_at)) * 24 * 60 AS m"
            " FROM pending_changes WHERE reviewed_at IS NOT NULL AND claim_at IS NOT NULL"
        )
        if r["m"] is not None
    )
    # the shared primitive, not a third inline copy — services/stats.py exists
    # because two of these had already drifted apart
    from . import stats

    n = len(minutes)
    median = stats.median(minutes)
    return {
        "by_entity": by_entity,
        "by_proposer": by_proposer,
        "recent_rejections": rejection_reasons,
        # medians over means (docs/INSIGHTS.md contract)
        "active_review_minutes": {"median": median, "n": n},
    }


def _readable(rows: list[dict], viewer: scope.Viewer) -> list[dict]:
    """Drop proposals whose target row the viewer may not read.

    `pending_changes` carries no tier of its own (scope.UNSCOPED) and this
    list returns the whole row — `payload` parsed, and a `summary` that four
    producers build out of the row's own text (delegation.submit_completion,
    tools/collab.py::delete_note, tools/memory.py::forget,
    tools/schedule.py::cancel_event). Served unfiltered to every CurrentUser,
    the review queue is a full-text mirror of every scoped row somebody
    proposed a change to.

    Dropped, not blanked: a reviewer who cannot read the row cannot judge the
    change, so a redacted entry is a verdict taken blind. The crew keeps
    seeing its own.

    An entity with no tier at all (weekly_plan, authority, playbook) stays —
    those carry no row to be scoped by, and defaulting them out would empty
    the queue the whole review flow runs on.

    One query per TABLE, not per row: at LIMIT 200 the per-row shape put 200
    round trips on GET /api/review, its only caller. Six other readers quote
    `summary` out of this table and each has to call this explicitly —
    briefing.my_day, delegation.agent_inbox, review_stats, handoff, rituals
    and two insights rules. They do not arrive here on their own.
    """
    want: dict[str, set[int]] = {}
    for r in rows:
        table = _TARGET_TABLE.get(r["entity"])
        if table in scope.CLASSIFIED and r["entity_id"]:
            want.setdefault(table, set()).add(r["entity_id"])
    tiers: dict[tuple[str, int], tuple[str, int | None, str]] = {}
    for table, ids in want.items():
        marks = ", ".join("?" for _ in ids)
        # the author column too: a private row is readable by whoever wrote
        # it, and scope.CLASSIFIED is the only place that mapping lives
        author = scope.CLASSIFIED[table]
        for row in db.query(
            f"SELECT id, visibility, crew_id, {author} AS author FROM {table} WHERE id IN ({marks})",  # noqa: S608 — table and column from constant maps, ids are bound marks
            tuple(ids),
        ):
            tiers[table, row["id"]] = (row["visibility"], row["crew_id"], row["author"] or "")
    out = []
    for r in rows:
        table = _TARGET_TABLE.get(r["entity"])
        if table in scope.CLASSIFIED and not r["entity_id"]:
            # a CREATE: there is no target row yet, and the tier the row WOULD
            # land at is in the payload. Every _registry create handler takes
            # visibility=/crew_id= and approve_change splats **payload into
            # them, so a proposal to create a private note carries its whole
            # body here and said nothing about its tier.
            # `.get`, because not every caller selects payload — my_day,
            # review_stats, rituals and insights read `summary` only. Those
            # rows cannot disclose a create's body, and a create's summary is
            # producer-built ("create note"), so treating an absent payload as
            # workspace keeps them without opening anything. list_changes is
            # the reader that returns the payload, and it selects *.
            try:
                payload = json.loads(r.get("payload") or "{}")
            except (TypeError, ValueError):
                payload = {}
            declared = str(payload.get("visibility") or scope.WORKSPACE)
            if declared != scope.WORKSPACE and not scope.can_read(
                declared, payload.get("crew_id"), viewer, str(payload.get("author") or "")
            ):
                continue
            out.append(r)
            continue
        if table not in scope.CLASSIFIED or not r["entity_id"]:
            # genuinely no row to be scoped by (weekly_plan, authority,
            # playbook). Keeping these is what makes the queue usable.
            out.append(r)
            continue
        tier = tiers.get((table, r["entity_id"]))
        if tier is None:
            # the target row is GONE. We cannot prove the viewer could read
            # it, and the summary still quotes it, so this fails closed —
            # anything else makes deleting the row the way to publish it.
            continue
        if scope.can_read(tier[0], tier[1], viewer, tier[2]):
            out.append(r)
    return out


def list_changes(status: str = "pending", viewer: scope.Viewer = scope.NOBODY) -> list[dict]:
    if status:
        rows = db.query(
            "SELECT * FROM pending_changes WHERE status = ? ORDER BY id DESC LIMIT 200",
            (status,),
        )
    else:
        rows = db.query("SELECT * FROM pending_changes ORDER BY id DESC LIMIT 100")
    rows = _readable(rows, viewer)
    for r in rows:
        r["payload"] = json.loads(r["payload"])
        # what this proposal is CALLED, resolved here so the header, the
        # checkbox label and the notification cannot drift apart
        r["label"] = lexicon.phrase(r["entity"], r["action"])
        # the UI shows whose verdict this is — acceptance belongs to the sponsor
        if r["entity"] == "task_completion":
            r["sponsor"] = _sponsor_of(r)
    return rows
