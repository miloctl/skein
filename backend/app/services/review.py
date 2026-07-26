"""Pending-changes review flow: agents (and cautious humans) propose, humans
approve. Approval applies the payload through the same service registry the
rest of the platform uses, stamped origin='agent_verified'."""

import json

from .. import db


def _registry() -> dict:
    from . import (
        blockers,
        collab,
        commitments,
        delegation,
        engagements,
        intake,
        playbooks,
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
        "event": {"create": schedule.schedule_event},
        "blocker": {"create": blockers.raise_blocker, "update": blockers.resolve_blocker},
        "engagement": {
            "create": engagements.create_engagement,
            "update": engagements.update_engagement,
        },
        "intake": {"create": intake.submit_request},
        "lesson": {"create": engagements.record_lesson},
        "playbook": {"create": playbooks.instantiate},
        "weekly_plan": {"create": weekly.apply_plan},
        "commitment": {
            "create": commitments.add_commitment,
            "update": commitments.update_commitment,
        },
        "delegation": {"create": delegation.delegate_task},
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
        raise ValueError(f"unknown entity '{entity}'; one of {sorted(reg)}")
    if action not in ("create", "update") or action not in reg[entity]:
        raise ValueError(f"unsupported action '{action}' for {entity}")
    if action == "update" and not entity_id:
        raise ValueError("entity_id required for updates")
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


def _claim(change_id: int, new_status: str, note: str, actor: str) -> None:
    """Compare-and-swap the pending -> reviewed transition so concurrent
    approve/reject calls can't both act on the same change."""
    claimed = db.execute_rowcount(
        "UPDATE pending_changes SET status = ?, reviewed_by = ?, review_note = ?,"
        " reviewed_at = ? WHERE id = ? AND status = 'pending'",
        (new_status, actor, note, db.now(), change_id),
    )
    if not claimed:
        change = db.query_one("SELECT status FROM pending_changes WHERE id = ?", (change_id,))
        if not change:
            raise ValueError(f"pending change #{change_id} not found")
        raise ValueError(f"change #{change_id} already {change['status']}")


def approve_change(change_id: int, note: str = "", *, actor: str = "system") -> dict:
    change = db.query_one("SELECT * FROM pending_changes WHERE id = ?", (change_id,))
    if not change:
        raise ValueError(f"pending change #{change_id} not found")

    # resolve the handler BEFORE claiming — a stale entity/action must not
    # leave the row marked approved with nothing applied
    try:
        fn = _registry()[change["entity"]][change["action"]]
    except KeyError as exc:
        raise ValueError(f"no handler for {change['entity']}.{change['action']}") from exc
    payload = json.loads(change["payload"])
    _claim(change_id, "approved", note, actor)
    try:
        # compound applies (playbook, weekly_plan) land atomically or not at
        # all — a failed apply rolls back, so pending is safe for EVERY entity
        with db.transaction():
            if change["action"] == "update":
                result = fn(change["entity_id"], **payload, actor=actor, origin="agent_verified")
            else:
                result = fn(**payload, actor=actor, origin="agent_verified")
    except Exception as exc:
        # ANY failure (also IntegrityError, lock timeout) resets the claim —
        # an approved-but-never-applied proposal would vanish from the queue
        db.execute(
            "UPDATE pending_changes SET status = 'pending', reviewed_by = NULL,"
            " reviewed_at = NULL, review_note = ? WHERE id = ?",
            (f"apply failed: {exc}", change_id),
        )
        raise ValueError(f"could not apply {change['entity']}.{change['action']}: {exc}") from exc

    db.execute(
        "UPDATE pending_changes SET result_id = ? WHERE id = ?", (result.get("id"), change_id)
    )
    applied = f"#{result['id']}" if result.get("id") is not None else "applied"
    db.log_activity(actor, "approve_change", f"#{change_id} -> {change['entity']} {applied}")
    _clear_review_ping(change_id)
    return {"id": change_id, "status": "approved", "result": result}


def _clear_review_ping(change_id: int) -> None:
    """The review is handled — its "Review needed" ping must not keep
    nagging. Called AFTER the apply succeeds (a failed apply resets the
    proposal to pending and must keep its notification unread)."""
    from .notifications import mark_read_matching

    mark_read_matching(f"Review needed: #{change_id} ")


def reject_change(change_id: int, note: str = "", *, actor: str = "system") -> dict:
    _claim(change_id, "rejected", note, actor)
    db.log_activity(actor, "reject_change", f"#{change_id}")
    _clear_review_ping(change_id)
    return {"id": change_id, "status": "rejected"}


_DIFF_TABLES = {
    "task": "tasks",
    "milestone": "milestones",
    "engagement": "engagements",
    "commitment": "commitments",
    "blocker": "blockers",
    "question": "questions",
    "decision": "decisions",
}


def change_diff(change_id: int) -> dict:
    """Before/after view for update proposals: current row values for exactly
    the fields the payload would change."""
    change = db.query_one("SELECT * FROM pending_changes WHERE id = ?", (change_id,))
    if not change:
        raise ValueError(f"pending change #{change_id} not found")
    table = _DIFF_TABLES.get(change["entity"])
    if change["action"] != "update" or not table or not change["entity_id"]:
        return {"id": change_id, "diff": None}
    row = db.query_one(
        f"SELECT * FROM {table} WHERE id = ?",  # noqa: S608 — table from constant map
        (change["entity_id"],),
    )
    payload = json.loads(change["payload"])
    current = {k: (row.get(k) if row else None) for k in payload}
    return {"id": change_id, "diff": {"current": current, "proposed": payload}}


def mark_seen(ids: list[int], *, actor: str = "system") -> dict:
    """The review UI calls this when a human loads pending proposals —
    first-seen (claim_at) starts the active-review clock, so review burden
    can be measured as seen→verdict instead of created→verdict (which is
    dominated by queue wait, not human effort)."""
    n = 0
    for cid in ids[:200]:
        n += db.execute_rowcount(
            "UPDATE pending_changes SET claim_at = ?"
            " WHERE id = ? AND status = 'pending' AND claim_at IS NULL",
            (db.now(), cid),
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
    n = len(minutes)
    median = (
        round(minutes[n // 2] if n % 2 else (minutes[n // 2 - 1] + minutes[n // 2]) / 2, 1)
        if n
        else None
    )
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
            "SELECT * FROM pending_changes WHERE status = ? ORDER BY id DESC", (status,)
        )
    else:
        rows = db.query("SELECT * FROM pending_changes ORDER BY id DESC LIMIT 100")
    for r in rows:
        r["payload"] = json.loads(r["payload"])
    return rows
