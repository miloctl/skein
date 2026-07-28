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
    if not title.strip():
        raise ValueError("request title is required")
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


def edit_request(
    request_id: int, title: str = "", detail: str = "", *, actor: str = "", origin: str = "human"
) -> dict:
    """Fix a request's wording before triage — after a disposition the record
    is the reason the requester saw, so it stays put."""
    row = db.query_one("SELECT title, status FROM intake_requests WHERE id = ?", (request_id,))
    if not row:
        raise ValueError(f"request #{request_id} not found")
    if row["status"] not in ("submitted", "scored"):
        raise ValueError(f"request #{request_id} is {row['status']} — history stays put")
    fields = {k: v for k, v in [("title", title.strip()), ("detail", detail)] if v}
    if not fields:
        raise ValueError("nothing to update")
    if fields.get("detail") == "-":
        fields["detail"] = ""
    sets = ", ".join(f"{k} = ?" for k in fields)
    db.execute(
        f"UPDATE intake_requests SET {sets}, updated_at = ? WHERE id = ?",  # noqa: S608
        (*fields.values(), db.now(), request_id),
    )
    if title.strip() and title.strip() != row["title"]:
        db.log_activity(
            actor or "system",
            "edit_intake",
            f"#{request_id}: '{row['title']}' -> '{title.strip()}'",
        )
    else:
        db.log_activity(actor or "system", "edit_intake", f"#{request_id} {' '.join(fields)}")
    new = db.query_one("SELECT * FROM intake_requests WHERE id = ?", (request_id,))
    if new:
        index_record("intake", request_id, new["title"], f"{new['detail']} {new['requester']}")
    return {"id": request_id, "updated": list(fields)}


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
    request_id: int,
    disposition: str,
    reason: str,
    kind: str = "delivery",
    timebox_end: str = "",
    outcome: str = "",
    lead: str = "",
    kill_criteria: str = "",
    *,
    actor: str = "system",
    origin: str = "human",
) -> dict:
    if disposition not in DISPOSITIONS:
        raise ValueError(f"disposition must be one of {DISPOSITIONS}")
    if not reason.strip():
        raise ValueError("a reason is required — requesters see it")
    with db.transaction():
        return _disposition(
            request_id,
            disposition,
            reason,
            kind=kind,
            timebox_end=timebox_end,
            outcome=outcome,
            lead=lead,
            kill_criteria=kill_criteria,
            actor=actor,
            origin=origin,
        )


def _disposition(
    request_id: int,
    disposition: str,
    reason: str,
    *,
    kind: str = "delivery",
    timebox_end: str = "",
    outcome: str = "",
    lead: str = "",
    kill_criteria: str = "",
    actor: str,
    origin: str,
) -> dict:
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
    if row and row["requester"] and row["requester"] != actor:
        from .notifications import notify

        notify(
            row["requester"],
            f"Your request #{request_id} “{row['title']}” was {disposition}: {reason[:140]}",
            tier="digest",
            link="/intake",
        )
    if disposition == "accepted" and row:
        from .engagements import create_engagement

        try:
            create_engagement(
                name=row["title"],
                project_class=row["project_class"] or "general",
                summary=row["detail"],
                kind=kind,
                timebox_end=timebox_end,
                outcome=outcome,
                lead=lead,
                kill_criteria=kill_criteria,
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
