"""Pending-changes review flow: agents (and cautious humans) propose, humans
approve. Approval applies the payload through the same service registry the
rest of the platform uses, stamped origin='agent_verified'."""

import json

from .. import db


def _registry() -> dict:
    from . import (blockers, collab, commitments, delegation, engagements,
                   intake, playbooks, schedule, weekly, work)

    return {
        "milestone": {"create": work.create_milestone, "update": work.update_milestone},
        "task": {"create": work.create_task, "update": work.update_task},
        "question": {"create": collab.ask_question, "update": collab.answer_question},
        "decision": {"create": collab.record_decision,
                      "update": collab.supersede_decision},
        "standup": {"create": collab.post_standup},
        "note": {"create": collab.save_note},
        "event": {"create": schedule.schedule_event},
        "blocker": {"create": blockers.raise_blocker, "update": blockers.resolve_blocker},
        "engagement": {"create": engagements.create_engagement,
                       "update": engagements.update_engagement},
        "intake": {"create": intake.submit_request},
        "lesson": {"create": engagements.record_lesson},
        "playbook": {"create": playbooks.instantiate},
        "weekly_plan": {"create": weekly.apply_plan},
        "commitment": {"create": commitments.add_commitment,
                       "update": commitments.update_commitment},
        "delegation": {"create": delegation.delegate_task},
    }


def propose_change(entity: str, action: str, payload: dict, summary: str = "",
                   entity_id: int = 0, *, actor: str = "agent", origin: str = "agent") -> dict:
    reg = _registry()
    if entity not in reg:
        raise ValueError(f"unknown entity '{entity}'; one of {sorted(reg)}")
    if action not in ("create", "update") or action not in reg[entity]:
        raise ValueError(f"unsupported action '{action}' for {entity}")
    if action == "update" and not entity_id:
        raise ValueError("entity_id required for updates")
    pid = db.execute(
        "INSERT INTO pending_changes (entity, entity_id, action, payload, summary,"
        " proposed_by, origin, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (entity, entity_id or None, action, json.dumps(payload),
         summary or f"{action} {entity}", actor, origin, db.now()),
    )
    db.log_activity(actor, "propose_change", f"#{pid} {action} {entity}")
    from .notifications import notify

    notify("team", f"Review needed: #{pid} {summary or f'{action} {entity}'}",
           tier="digest", link="/review")
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

    _claim(change_id, "approved", note, actor)
    fn = _registry()[change["entity"]][change["action"]]
    payload = json.loads(change["payload"])
    try:
        if change["action"] == "update":
            result = fn(change["entity_id"], **payload, actor=actor, origin="agent_verified")
        else:
            result = fn(**payload, actor=actor, origin="agent_verified")
    except (TypeError, ValueError) as exc:
        # compound applies (many writes, no transaction) may have partially
        # landed — resetting those to pending creates a permanently wedged
        # proposal ("engagement already exists" forever). Fail them terminally.
        if change["entity"] in ("playbook", "weekly_plan"):
            db.execute(
                "UPDATE pending_changes SET review_note = ? WHERE id = ?",
                (f"apply failed (may be partial — check and repropose): {exc}", change_id),
            )
            raise ValueError(
                f"{change['entity']} apply failed and was closed as approved-with-error:"
                f" {exc} — review what landed and repropose the remainder")
        db.execute(
            "UPDATE pending_changes SET status = 'pending', reviewed_by = NULL,"
            " reviewed_at = NULL, review_note = ? WHERE id = ?",
            (f"apply failed: {exc}", change_id),
        )
        raise ValueError(f"could not apply {change['entity']}.{change['action']}: {exc}")

    db.execute("UPDATE pending_changes SET result_id = ? WHERE id = ?",
               (result.get("id"), change_id))
    applied = f"#{result['id']}" if result.get("id") is not None else "applied"
    db.log_activity(actor, "approve_change", f"#{change_id} -> {change['entity']} {applied}")
    return {"id": change_id, "status": "approved", "result": result}


def reject_change(change_id: int, note: str = "", *, actor: str = "system") -> dict:
    _claim(change_id, "rejected", note, actor)
    db.log_activity(actor, "reject_change", f"#{change_id}")
    return {"id": change_id, "status": "rejected"}


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
    return {"by_entity": by_entity, "by_proposer": by_proposer,
            "recent_rejections": rejection_reasons}


def list_changes(status: str = "pending") -> list[dict]:
    if status:
        rows = db.query("SELECT * FROM pending_changes WHERE status = ? ORDER BY id DESC", (status,))
    else:
        rows = db.query("SELECT * FROM pending_changes ORDER BY id DESC LIMIT 100")
    for r in rows:
        r["payload"] = json.loads(r["payload"])
    return rows
