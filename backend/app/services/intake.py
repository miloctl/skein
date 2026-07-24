"""Engagement intake & triage. Scoring is programmatic RICE-lite:
score = reach * impact * confidence / effort (each 1-5, effort >= 1)."""

from .. import db
from .search import index_record

DISPOSITIONS = ("accepted", "deferred", "declined")


def _score(reach: int, impact: int, confidence: int, effort: int) -> float:
    effort = max(1, effort)
    return round(reach * impact * confidence / effort, 2)


def submit_request(
    title: str,
    detail: str = "",
    requester: str = "",
    project_class: str = "",
    *,
    actor: str = "",
    origin: str = "human",
) -> dict:
    ts = db.now()
    rid = db.execute(
        "INSERT INTO intake_requests (title, detail, requester, project_class,"
        " origin, created_by, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (title, detail, requester or actor, project_class, origin, actor or requester, ts, ts),
    )
    db.log_activity(actor or requester or "system", "submit_intake", f"#{rid} {title}")
    index_record("intake", rid, title, f"{detail} {requester} {project_class}")
    return {"id": rid, "status": "submitted"}


def score_request(
    request_id: int,
    reach: int,
    impact: int,
    confidence: int,
    effort: int,
    *,
    actor: str = "system",
    origin: str = "human",
) -> dict:
    for name, v in (
        ("reach", reach),
        ("impact", impact),
        ("confidence", confidence),
        ("effort", effort),
    ):
        if not 1 <= v <= 5:
            raise ValueError(f"{name} must be 1-5")
    if not db.query_one("SELECT id FROM intake_requests WHERE id = ?", (request_id,)):
        raise ValueError(f"intake request #{request_id} not found")
    score = _score(reach, impact, confidence, effort)
    db.execute(
        "UPDATE intake_requests SET reach = ?, impact = ?, confidence = ?, effort = ?,"
        " score = ?, status = 'scored', updated_at = ? WHERE id = ?",
        (reach, impact, confidence, effort, score, db.now(), request_id),
    )
    db.log_activity(actor, "score_intake", f"#{request_id} score={score}")
    return {"id": request_id, "score": score, "status": "scored"}


def disposition_request(
    request_id: int, disposition: str, reason: str, *, actor: str = "system", origin: str = "human"
) -> dict:
    if disposition not in DISPOSITIONS:
        raise ValueError(f"disposition must be one of {DISPOSITIONS}")
    if not reason.strip():
        raise ValueError("a reason is required — requesters see it")
    current = db.query_one("SELECT status FROM intake_requests WHERE id = ?", (request_id,))
    if not current:
        raise ValueError(f"intake request #{request_id} not found")
    if current["status"] not in ("submitted", "scored"):
        raise ValueError(f"request #{request_id} already dispositioned ({current['status']})")
    db.execute(
        "UPDATE intake_requests SET status = ?, disposition_reason = ?, updated_at = ?"
        " WHERE id = ?",
        (disposition, reason, db.now(), request_id),
    )
    db.log_activity(actor, "disposition_intake", f"#{request_id} {disposition}")
    row = db.query_one("SELECT * FROM intake_requests WHERE id = ?", (request_id,))
    if disposition == "accepted" and row:
        from .engagements import create_engagement

        try:
            create_engagement(
                name=row["title"],
                project_class=row["project_class"] or "general",
                summary=row["detail"],
                actor=actor,
                origin=origin,
            )
        except ValueError as exc:
            # a name collision must not read as "work has started" — say so
            db.log_activity(actor, "accept_without_engagement", f"#{request_id}: {exc}")
            return {
                "id": request_id,
                "status": disposition,
                "engagement_created": False,
                "note": f"accepted, but no new engagement: {exc}",
            }
    return {
        "id": request_id,
        "status": disposition,
        **({"engagement_created": True} if disposition == "accepted" else {}),
    }


def list_requests(status: str = "") -> list[dict]:
    if status:
        return db.query(
            "SELECT * FROM intake_requests WHERE status = ? ORDER BY score DESC, id DESC",
            (status,),
        )
    return db.query(
        "SELECT * FROM intake_requests"
        " ORDER BY CASE status WHEN 'submitted' THEN 0 WHEN 'scored' THEN 1 ELSE 2 END,"
        " score DESC, id DESC"
    )
