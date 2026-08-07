"""The promise ledger: promises made to people outside the team.
The exec readout reads from here — a promise that isn't recorded is a
promise the team can't keep on purpose."""

from .. import db
from . import scope
from .search import index_record

STATUSES = ("open", "kept", "missed", "withdrawn")
# 'external': promises to people outside the team (exec readout material).
# 'team': the manager's own promises TO the team — visible so they get kept.
AUDIENCES = ("external", "team")


def add_promise(
    promise: str,
    to_whom: str = "",
    due_date: str = "",
    engagement_id: int = 0,
    audience: str = "external",
    *,
    actor: str = "system",
    origin: str = "human",
    visibility: str = scope.WORKSPACE,
    crew_id: int = 0,
) -> dict:
    db.validate_date("due_date", due_date, allow_clear=False)
    if not promise.strip():
        raise ValueError("the promise text is required")
    if audience not in AUDIENCES:
        raise ValueError(f"audience must be one of {AUDIENCES}")
    if engagement_id and not db.query_one(
        "SELECT id FROM engagements WHERE id = ?", (engagement_id,)
    ):
        raise ValueError(f"engagement #{engagement_id} not found")
    ts = db.now()
    with db.transaction():
        tier, crew = scope.resolve_write(visibility, crew_id, actor=actor)
        # NO assert_readable_by on to_whom: unlike an assignee or an owner,
        # a promise recipient is deliberately not a roster name. The default
        # audience is `external` and to_whom holds "the board" or a customer,
        # so checking it against crew membership refuses the ordinary case.
        cid = db.execute(
            "INSERT INTO promises (promise, to_whom, engagement_id, due_date, audience,"
            " origin, created_by, created_at, updated_at, visibility, crew_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                promise,
                to_whom,
                engagement_id or None,
                due_date or None,
                audience,
                origin,
                actor,
                ts,
                ts,
                tier,
                crew,
            ),
        )
        db.log_activity(actor, "add_promise", scope.detail(tier, f"#{cid}", promise[:80]))
        index_record("promise", cid, promise[:120], f"{promise} {to_whom}")
    return {"id": cid, "promise": promise, "status": "open"}


def update_promise(
    promise_id: int, status: str, *, actor: str = "system", origin: str = "human"
) -> dict:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    row = db.query_one("SELECT * FROM promises WHERE id = ?", (promise_id,))
    if not row:
        raise scope.missing("promises", promise_id)
    scope.assert_editable("promises", row, actor, verb="settle")
    if row["status"] != "open":
        raise ValueError(f"promise #{promise_id} already {row['status']}")
    db.execute(
        "UPDATE promises SET status = ?, updated_at = ? WHERE id = ?",
        (status, db.now(), promise_id),
    )
    db.log_activity(actor, "update_promise", f"#{promise_id} {status}")
    return {"id": promise_id, "status": status}


def edit_promise(
    promise_id: int,
    promise: str = "",
    due_date: str = "",
    to_whom: str = "",
    *,
    actor: str = "system",
    origin: str = "human",
) -> dict:
    """Correct the wording/date of an OPEN promise — old→new logged; settled
    promises stay as history."""
    db.validate_date("due_date", due_date)
    row = db.query_one("SELECT * FROM promises WHERE id = ?", (promise_id,))
    if not row:
        raise scope.missing("promises", promise_id)
    scope.assert_editable("promises", row, actor, verb="edit")
    if row["status"] != "open":
        raise ValueError(f"promise #{promise_id} is {row['status']} — history stays put")
    fields = {
        k: v for k, v in [("promise", promise), ("due_date", due_date), ("to_whom", to_whom)] if v
    }
    if not fields:
        raise ValueError("nothing to update")
    if fields.get("due_date") == "-":
        fields["due_date"] = None  # type: ignore[assignment]
    if fields.get("to_whom") == "-":
        fields["to_whom"] = ""
    sets = ", ".join(f"{k} = ?" for k in fields)
    db.execute(
        f"UPDATE promises SET {sets}, updated_at = ? WHERE id = ?",  # noqa: S608 — keys hardcoded
        (*fields.values(), db.now(), promise_id),
    )
    if promise and promise != row["promise"]:
        # both strings are the promise's own text, so a scoped rewording logs
        # the identifier only (services/scope.py::detail)
        db.log_activity(
            actor,
            "edit_promise",
            scope.detail(row["visibility"], f"#{promise_id}", f"'{row['promise']}' -> '{promise}'"),
        )
    else:
        db.log_activity(actor, "edit_promise", f"#{promise_id} {' '.join(fields)}")
    return {"id": promise_id, "updated": list(fields)}


def list_promises(
    status: str = "", audience: str = "", viewer: scope.Viewer = scope.NOBODY
) -> list[dict]:
    frag, vp = scope.visible_filter(viewer, "promises")
    where, params = [frag], list(vp)
    if status:
        where.append("status = ?")
        params.append(status)
    if audience:
        where.append("audience = ?")
        params.append(audience)
    return db.query(
        f"SELECT * FROM promises WHERE {' AND '.join(where)}"  # noqa: S608 — clauses hardcoded, and scope.visible_filter emits only bound marks
        " ORDER BY status != 'open', due_date IS NULL, due_date, id DESC LIMIT 100",
        tuple(params),
    )
