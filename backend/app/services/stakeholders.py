"""Open threads with people outside the team.

Four tables already record a name from outside — a promise's `to_whom`, an
intake request's `requester`, a question's `asked_by`, an event's `attendees`
— and nothing ever put them together. So "what is open with Acme" was
answerable only by remembering, and the answer arrived after the meeting with
Acme rather than before it.

READ ONLY. No table, no new write path, no new habit: every row here is
already being written by somebody doing their ordinary work, and this reads
it back. Nothing is inferred about the person — a name appears because a row
names it, and the brief quotes the row.
"""

from .. import db
from . import scope

# Outside the team means: not on the roster. Deliberately the whole roster
# rather than active members only — a teammate who left is not a stakeholder,
# and re-listing their old threads under a vendor heading would be worse than
# leaving them out.
#
# The cap is per table and the ORDER BY beside it is load-bearing: without one
# SQLite returns whichever rows it reaches first, so a party could vanish from
# a card whose title carries a count the reader reads as complete. Newest
# first, because an open thread from this week is the one a brief is for.
_LIMIT = 200


def _roster() -> set[str]:
    from .users import fold

    return {fold(u["name"]) for u in db.query("SELECT name FROM users")}


def _outside(name: str, roster: set[str]) -> bool:
    from .users import fold

    clean = (name or "").strip()
    # a bare initial or a punctuation fragment is not a party, and neither is
    # a system actor: the ledger's four reserved names appear in these
    # columns whenever a job wrote the row
    if len(clean) < 2 or clean.lower() in ("system", "scheduler", "team", "forge"):
        return False
    return fold(clean) not in roster


def open_threads(viewer: scope.Viewer = scope.NOBODY) -> list[dict]:
    """Every outside party with something open, and what it is.

    Viewer-scoped on all four reads: a brief assembled from rows a caller
    cannot see would leak both the row and the fact that the party exists.
    """
    roster = _roster()
    threads: dict[str, dict] = {}

    def add(party: str, kind: str, text: str, when: str) -> None:
        if not _outside(party, roster):
            return
        row = threads.setdefault(party.strip(), {"party": party.strip(), "items": []})
        row["items"].append({"kind": kind, "text": text[:120], "when": when or ""})

    pfrag, pp = scope.visible_filter(viewer, "promises")
    for p in db.query(
        f"SELECT * FROM promises WHERE status = 'open' AND {pfrag}"  # noqa: S608 — scope.visible_filter emits only bound marks
        " ORDER BY id DESC LIMIT ?",
        (*pp, _LIMIT),
    ):
        # both directions: what we owe them and what they owe us are the same
        # conversation from the other side of the table
        owed = "they owe us" if p["direction"] == "received" else "we owe them"
        add(p["to_whom"], f"promise ({owed})", p["promise"], p["due_date"] or "")

    ifrag, ip = scope.visible_filter(viewer, "intake_requests")
    for r in db.query(
        f"SELECT * FROM intake_requests WHERE status IN ('submitted', 'scored') AND {ifrag}"  # noqa: S608 — scope.visible_filter emits only bound marks
        " ORDER BY id DESC LIMIT ?",
        (*ip, _LIMIT),
    ):
        add(r["requester"], "request awaiting triage", r["title"], "")

    qfrag, qp = scope.visible_filter(viewer, "questions")
    for q in db.query(
        f"SELECT * FROM questions WHERE status = 'open' AND {qfrag}"  # noqa: S608 — scope.visible_filter emits only bound marks
        " ORDER BY id DESC LIMIT ?",
        (*qp, _LIMIT),
    ):
        add(q["asked_by"], "question waiting on us", q["question"], "")

    return sorted(threads.values(), key=lambda t: (-len(t["items"]), t["party"]))


def brief_for_event(event_id: int, viewer: scope.Viewer = scope.NOBODY) -> dict:
    """What is open with the outside people attending this meeting.

    Attached to the MEETING rather than sent as a digest: a stakeholder brief
    is only useful in the hour before you speak to them, and a list of every
    open thread with everybody is a report nobody reads.
    """
    frag, vp = scope.visible_filter(viewer, "events")
    ev = db.query_one(
        f"SELECT * FROM events WHERE id = ? AND {frag}",  # noqa: S608 — scope.visible_filter emits only bound marks
        (event_id, *vp),
    )
    if not ev:
        raise scope.missing("events", event_id)
    from .users import fold

    # attendees is free text, comma-separated by convention. Folded on BOTH
    # sides: `_outside` above compares folded names, so an exact-string match
    # here would silently return no threads whenever the attendee list and the
    # promise's `to_whom` disagree on case — "legal" against "Legal".
    names = {fold(a) for a in (ev["attendees"] or "").split(",") if a.strip()}
    threads = [t for t in open_threads(viewer) if fold(t["party"]) in names]
    return {
        "event_id": event_id,
        "title": ev["title"],
        "starts_at": ev["starts_at"],
        "threads": threads,
    }
