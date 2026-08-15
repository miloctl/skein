"""Engagement intake & triage. Scoring is programmatic RICE-lite:
score = reach * impact * confidence / effort (each 1-5, effort >= 1)."""

from .. import db
from . import scope
from .search import index_record

DISPOSITIONS = ("accepted", "deferred", "declined")


def _can_read(row: dict, person: str) -> bool:
    """assert_readable_by as a predicate. The notify path needs to SKIP a
    reader it cannot reach, not refuse the write that triggered it."""
    try:
        scope.assert_readable_by(
            row["visibility"],
            row["crew_id"],
            person,
            label="requester",
            author=row["created_by"],
        )
    except ValueError:
        return False
    return True


# The same bound routes/api.py::IntakeIn declares, enforced HERE because this
# is the only write path. Capture's `req:` prefix accepts 10,000 characters and
# handed the whole body through as `detail`, so a captured request stored a row
# the REST door refuses to accept — the create/edit asymmetry the bounded-input
# census exists to close (tests/test_bounded_routes.py).
DETAIL_LEN = 4000


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
    visibility: str = scope.WORKSPACE,
    crew_id: int = 0,
) -> dict:
    if len(detail) > DETAIL_LEN:
        raise ValueError(f"request detail must be {DETAIL_LEN} characters or fewer")
    if not title.strip():
        raise ValueError("request title is required")
    ts = db.now()
    # inside the transaction, like the other 13 resolve_write call sites: bare,
    # the membership check opens its own connection, so somebody removed from
    # the crew between the check and the INSERT still scopes a row into it
    # (services/scope.py::resolve_write says this at the point of temptation)
    with db.transaction():
        tier, crew = scope.resolve_write(visibility, crew_id, actor=actor or requester)
        rid = db.execute(
            "INSERT INTO intake_requests (title, detail, requester, project_class,"
            " origin, created_by, created_at, updated_at, visibility, crew_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " RETURNING id",
            (
                title,
                detail,
                requester or actor,
                project_class,
                origin,
                actor or requester,
                ts,
                ts,
                tier,
                crew,
            ),
        )
    db.log_activity(
        actor or requester or "system", "submit_intake", scope.detail(tier, f"#{rid}", title)
    )
    index_record("intake", rid, title, f"{detail} {requester} {project_class}")
    return {"id": rid, "status": "submitted"}


def edit_request(
    request_id: int, title: str = "", detail: str = "", *, actor: str = "", origin: str = "human"
) -> dict:
    """Fix a request's wording before triage — after a disposition the record
    is the reason the requester saw, so it stays put."""
    row = db.query_one("SELECT * FROM intake_requests WHERE id = ?", (request_id,))
    if not row:
        raise scope.missing("intake_requests", request_id)
    scope.assert_editable("intake_requests", row, actor, verb="edit")
    if row["status"] not in ("submitted", "scored"):
        raise ValueError(f"request #{request_id} is {row['status']} — history stays put")
    fields = {k: v for k, v in [("title", title.strip()), ("detail", detail)] if v}
    if not fields:
        raise ValueError("nothing to update")
    if fields.get("detail") == "-":
        fields["detail"] = ""
    sets = ", ".join(f"{k} = ?" for k in fields)
    db.execute(
        f"UPDATE intake_requests SET {sets}, updated_at = ? WHERE id = ?",  # noqa: S608 — keys hardcoded, id is a bound mark
        (*fields.values(), db.now(), request_id),
    )
    if title.strip() and title.strip() != row["title"]:
        db.log_activity(
            actor or "system",
            "edit_intake",
            scope.detail(
                row["visibility"], f"#{request_id}", f"'{row['title']}' -> '{title.strip()}'"
            ),
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
    current = db.query_one("SELECT * FROM intake_requests WHERE id = ?", (request_id,))
    if not current:
        raise scope.missing("intake_requests", request_id)
    scope.assert_editable("intake_requests", current, actor, verb="score")
    # scoring must not be a back door out of a terminal disposition — a
    # declined request re-entering triage could be accepted a second time
    if current["status"] not in ("submitted", "scored"):
        raise ValueError(
            f"request #{request_id} is {current['status']} — dispositioned"
            " requests stay put. Submit a new request instead"
        )
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
    current = db.query_one("SELECT * FROM intake_requests WHERE id = ?", (request_id,))
    if not current:
        raise scope.missing("intake_requests", request_id)
    scope.assert_editable("intake_requests", current, actor, verb="disposition")
    if current["status"] not in ("submitted", "scored"):
        raise ValueError(f"request #{request_id} already dispositioned ({current['status']})")
    db.execute(
        "UPDATE intake_requests SET status = ?, disposition_reason = ?, updated_at = ?"
        " WHERE id = ?",
        (disposition, reason, db.now(), request_id),
    )
    db.log_activity(actor, "disposition_intake", f"#{request_id} {disposition}")
    row = db.query_one("SELECT * FROM intake_requests WHERE id = ?", (request_id,))
    # the requester is a FREE field on the agent tool path, so it is not always
    # the author — and this message quotes the request title. Skipped, not
    # refused: the disposition is the decision, and a reader who cannot see the
    # request must not be able to block someone else from making it.
    if row and row["requester"] and row["requester"] != actor and _can_read(row, row["requester"]):
        from .notifications import notify

        notify(
            row["requester"],
            lambda source: (
                f"Your request #{source['id']} “{source['title']}” was"
                f" {source['status']}: {source['disposition_reason'][:140]}"
            ),
            tier="digest",
            link="/intake",
            source_entity="intake",
            source_id=request_id,
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
                # the engagement's name IS the request's title and its summary
                # IS the request's detail, so a workspace engagement here
                # republishes a scoped request in full, and indexes it
                visibility=row["visibility"],
                crew_id=row["crew_id"] or 0,
            )
        except (ValueError, db.IntegrityError) as exc:
            # a name collision must not read as "work has started" — say so.
            # IntegrityError is the RACE: create_engagement pre-checks the
            # name NOCASE and raises ValueError, but two accepts landing
            # together both pass that read and the loser hits
            # ux_engagements_name_nocase. Uncaught it is a 500 for a
            # caller-supplied name, which the 4xx rule forbids.
            # scope.detail, not the raw exception: `exc` is
            # "engagement '<name>' already exists", and that name is the
            # request's own title. The ledger is hash-chained, so a scoped
            # title written here has no delete and no redaction — the caller
            # still gets the full reason in `note` below.
            db.log_activity(
                actor,
                "accept_without_engagement",
                scope.detail(current["visibility"], f"#{request_id}", str(exc)),
            )
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


def list_requests(status: str = "", viewer: scope.Viewer = scope.NOBODY) -> list[dict]:
    frag, vp = scope.visible_filter(viewer, "intake_requests")
    if status:
        return db.query(
            f"SELECT * FROM intake_requests WHERE status = ? AND {frag}"  # noqa: S608 — scope.visible_filter emits only bound marks
            " ORDER BY score DESC, id DESC LIMIT 200",
            (status, *vp),
        )
    return db.query(
        f"SELECT * FROM intake_requests WHERE {frag}"  # noqa: S608 — scope.visible_filter emits only bound marks
        " ORDER BY CASE status WHEN 'submitted' THEN 0 WHEN 'scored' THEN 1 ELSE 2 END,"
        " score DESC, id DESC LIMIT 200",
        tuple(vp),
    )
