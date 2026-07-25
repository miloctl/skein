"""External commitment ledger: promises made to people outside the team.
The exec readout reads from here — a promise that isn't recorded is a
promise the team can't keep on purpose."""

from .. import db
from .search import index_record

STATUSES = ("open", "kept", "missed", "withdrawn")
# 'external': promises to people outside the team (exec readout material).
# 'team': the manager's own promises TO the team — visible so they get kept.
AUDIENCES = ("external", "team")


def add_commitment(
    promise: str,
    to_whom: str = "",
    due_date: str = "",
    engagement_id: int = 0,
    audience: str = "external",
    *,
    actor: str = "system",
    origin: str = "human",
) -> dict:
    if not promise.strip():
        raise ValueError("the promise text is required")
    if audience not in AUDIENCES:
        raise ValueError(f"audience must be one of {AUDIENCES}")
    if engagement_id and not db.query_one(
        "SELECT id FROM engagements WHERE id = ?", (engagement_id,)
    ):
        raise ValueError(f"engagement #{engagement_id} not found")
    ts = db.now()
    cid = db.execute(
        "INSERT INTO commitments (promise, to_whom, engagement_id, due_date, audience,"
        " origin, created_by, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        ),
    )
    db.log_activity(actor, "add_commitment", f"#{cid} {promise[:80]}")
    index_record("commitment", cid, promise[:120], f"{promise} {to_whom}")
    return {"id": cid, "promise": promise, "status": "open"}


def update_commitment(
    commitment_id: int, status: str, *, actor: str = "system", origin: str = "human"
) -> dict:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    row = db.query_one("SELECT * FROM commitments WHERE id = ?", (commitment_id,))
    if not row:
        raise ValueError(f"commitment #{commitment_id} not found")
    if row["status"] != "open":
        raise ValueError(f"commitment #{commitment_id} already {row['status']}")
    db.execute(
        "UPDATE commitments SET status = ?, updated_at = ? WHERE id = ?",
        (status, db.now(), commitment_id),
    )
    db.log_activity(actor, "update_commitment", f"#{commitment_id} {status}")
    return {"id": commitment_id, "status": status}


def list_commitments(status: str = "", audience: str = "") -> list[dict]:
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    if audience:
        where.append("audience = ?")
        params.append(audience)
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    return db.query(
        f"SELECT * FROM commitments{clause}"  # noqa: S608 — clauses hardcoded
        " ORDER BY status != 'open', due_date IS NULL, due_date, id DESC LIMIT 100",
        tuple(params),
    )
