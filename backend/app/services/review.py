"""Pending-changes review flow: agents (and cautious humans) propose, humans
approve. Approval applies the payload through the same service registry the
rest of the platform uses, stamped origin='agent_verified'."""

import json

from .. import db


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


def approve_change(
    change_id: int, note: str = "", *, actor: str = "system", strong: bool = False
) -> dict:
    _check_reviewer(actor)
    change = db.query_one("SELECT * FROM pending_changes WHERE id = ?", (change_id,))
    if not change:
        raise db.NotFound(f"pending change #{change_id} not found")
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
    change_id: int, note: str = "", *, actor: str = "system", strong: bool = False
) -> dict:
    _check_reviewer(actor)
    change = db.query_one("SELECT * FROM pending_changes WHERE id = ?", (change_id,))
    if not change:
        raise db.NotFound(f"pending change #{change_id} not found")
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

# columns worth showing a reviewer when a proposal would DESTROY the row —
# an empty payload must never mean an uninformed verdict
_DESTRUCTIVE_VIEW = {
    "note_delete": ("topic", "content"),
    "memory_forget": ("topic", "content", "user"),
    "event_cancel": ("title", "starts_at", "attendees"),
}


def change_diff(change_id: int) -> dict:
    """Before/after view for update proposals: current row values for exactly
    the fields the payload would change."""
    change = db.query_one("SELECT * FROM pending_changes WHERE id = ?", (change_id,))
    if not change:
        raise db.NotFound(f"pending change #{change_id} not found")
    table = _DIFF_TABLES.get(change["entity"])
    if change["action"] != "update" or not table or not change["entity_id"]:
        return {"id": change_id, "diff": None}
    row = db.query_one(
        f"SELECT * FROM {table} WHERE id = ?",  # noqa: S608 — table from constant map
        (change["entity_id"],),
    )
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


def review_stats() -> dict:
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
    rejection_reasons = db.query(
        "SELECT entity, summary, review_note, reviewed_by FROM pending_changes"
        " WHERE status = 'rejected' AND review_note != '' ORDER BY id DESC LIMIT 20"
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


def list_changes(status: str = "pending") -> list[dict]:
    if status:
        rows = db.query(
            "SELECT * FROM pending_changes WHERE status = ? ORDER BY id DESC LIMIT 200",
            (status,),
        )
    else:
        rows = db.query("SELECT * FROM pending_changes ORDER BY id DESC LIMIT 100")
    for r in rows:
        r["payload"] = json.loads(r["payload"])
        # the UI shows whose verdict this is — acceptance belongs to the sponsor
        if r["entity"] == "task_completion":
            r["sponsor"] = _sponsor_of(r)
    return rows
