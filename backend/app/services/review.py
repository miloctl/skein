"""Pending-changes review flow: agents (and cautious humans) propose, humans
approve. Approval applies the payload through the same service registry the
rest of the platform uses, stamped origin='agent_verified'."""

import json

from .. import db


def _registry() -> dict:
    from . import blockers, collab, engagements, schedule, work

    return {
        "milestone": {"create": work.create_milestone, "update": work.update_milestone},
        "task": {"create": work.create_task, "update": work.update_task},
        "question": {"create": collab.ask_question},
        "decision": {"create": collab.record_decision},
        "standup": {"create": collab.post_standup},
        "note": {"create": collab.save_note},
        "event": {"create": schedule.schedule_event},
        "blocker": {"create": blockers.raise_blocker},
        "engagement": {"create": engagements.create_engagement},
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
    return {"id": pid, "status": "pending"}


def approve_change(change_id: int, note: str = "", *, actor: str = "system") -> dict:
    change = db.query_one("SELECT * FROM pending_changes WHERE id = ?", (change_id,))
    if not change:
        raise ValueError(f"pending change #{change_id} not found")
    if change["status"] != "pending":
        raise ValueError(f"change #{change_id} already {change['status']}")

    fn = _registry()[change["entity"]][change["action"]]
    payload = json.loads(change["payload"])
    try:
        if change["action"] == "update":
            result = fn(change["entity_id"], **payload, actor=actor, origin="agent_verified")
        else:
            result = fn(**payload, actor=actor, origin="agent_verified")
    except TypeError as exc:
        raise ValueError(f"payload does not fit {change['entity']}.{change['action']}: {exc}")

    db.execute(
        "UPDATE pending_changes SET status = 'approved', reviewed_by = ?, review_note = ?,"
        " reviewed_at = ?, result_id = ? WHERE id = ?",
        (actor, note, db.now(), result.get("id"), change_id),
    )
    db.log_activity(actor, "approve_change", f"#{change_id} -> {change['entity']} #{result.get('id')}")
    return {"id": change_id, "status": "approved", "result": result}


def reject_change(change_id: int, note: str = "", *, actor: str = "system") -> dict:
    change = db.query_one("SELECT * FROM pending_changes WHERE id = ?", (change_id,))
    if not change:
        raise ValueError(f"pending change #{change_id} not found")
    if change["status"] != "pending":
        raise ValueError(f"change #{change_id} already {change['status']}")
    db.execute(
        "UPDATE pending_changes SET status = 'rejected', reviewed_by = ?, review_note = ?,"
        " reviewed_at = ? WHERE id = ?",
        (actor, note, db.now(), change_id),
    )
    db.log_activity(actor, "reject_change", f"#{change_id}")
    return {"id": change_id, "status": "rejected"}


def list_changes(status: str = "pending") -> list[dict]:
    if status:
        rows = db.query("SELECT * FROM pending_changes WHERE status = ? ORDER BY id DESC", (status,))
    else:
        rows = db.query("SELECT * FROM pending_changes ORDER BY id DESC LIMIT 100")
    for r in rows:
        r["payload"] = json.loads(r["payload"])
    return rows
