"""Open threads with people outside the team.

Three tables already record a name from outside — a promise's `to_whom`, an
intake request's `requester`, a question's `asked_by` — and nothing ever put
them together. So "what is open with Acme" was
answerable only by remembering, and the answer arrived after the meeting with
Acme rather than before it.

READ ONLY. No table, no new write path, no new habit: every row here is
already being written by somebody doing their ordinary work, and this reads
it back. Nothing is inferred about the person — a name appears because a row
names it, and the brief quotes the row.
"""

from .. import db
from . import activity, scope

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

# the ledger's reserved actor names, from the one place that defines them
_SYSTEM = {a.lower() for a in activity.SYSTEM_ACTORS}


def _fold(name: str) -> str:
    from .users import fold

    return fold(name)


def _roster() -> set[str]:
    return {_fold(u["name"]) for u in db.query("SELECT name FROM users")}


def _outside(name: str, roster: set[str]) -> bool:
    from .users import fold

    clean = (name or "").strip()
    # a bare initial or a punctuation fragment is not a party, and neither is
    # a system actor: the ledger's reserved names appear in these columns
    # whenever a job wrote the row. Imported, not copied — a fifth actor added
    # to activity.py would otherwise be a stakeholder here forever.
    if len(clean) < 2 or clean.lower() in _SYSTEM:
        return False
    folded = fold(clean)
    if folded in roster:
        return False
    # TOKEN overlap, not just the exact fold. These columns are free text by
    # design, so a teammate is written "Dana W." as often as in full, and an
    # exact match let that row onto a card headed "Open outside the team" with
    # a past-due date beside it — a person-level judgment of the past on a
    # workspace surface. Erring toward exclusion is the safe direction here: a
    # vendor contact who shares a first name with a teammate is left off a
    # card, which costs a reader one lookup. The reverse costs the moat.
    tokens = {t for t in folded.replace(".", " ").split() if len(t) > 2}
    return not any(
        tokens & {t for t in mate.replace(".", " ").split() if len(t) > 2} for mate in roster
    )


def open_threads(viewer: scope.Viewer = scope.NOBODY) -> list[dict]:
    """Every outside party with something open, and what it is.

    Viewer-scoped on all three reads: a brief assembled from rows a caller
    cannot see would leak both the row and the fact that the party exists.
    """
    roster = _roster()
    threads: dict[str, dict] = {}
    # Exclude the roster in SQL, BEFORE the cap. Filtering in Python after
    # `LIMIT 200` let roster-directed rows — the majority on any real
    # instance — eat the whole budget, and the brief for a meeting you are
    # about to walk into answered "nothing open".
    # one sentinel rather than an empty IN (): zero placeholders with one
    # bound value is a binding-count mismatch, and `IN ()` is a syntax error
    mates = tuple(roster) or ("",)
    fold_marks = ", ".join("?" for _ in mates)

    def add(party: str, kind: str, text: str, when: str) -> None:
        if not _outside(party, roster):
            return
        # keyed on the FOLD, displayed as first seen: "Acme", "acme" and
        # "ACME" are one conversation, and three cards each claiming one open
        # item is the thing this module exists to stop
        row = threads.setdefault(_fold(party), {"party": party.strip(), "items": []})
        row["items"].append({"kind": kind, "text": text[:120], "when": when or ""})

    pfrag, pp = scope.visible_filter(viewer, "promises")
    for p in db.query(
        f"SELECT * FROM promises WHERE status = 'open' AND {pfrag}"  # noqa: S608 — scope.visible_filter emits only bound marks
        f" AND LOWER(TRIM(to_whom)) NOT IN ({fold_marks})"
        " ORDER BY id DESC LIMIT ?",
        (*pp, *mates, _LIMIT),
    ):
        # both directions: what we owe them and what they owe us are the same
        # conversation from the other side of the table
        owed = "they owe us" if p["direction"] == "received" else "we owe them"
        add(p["to_whom"], f"promise ({owed})", p["promise"], p["due_date"] or "")

    ifrag, ip = scope.visible_filter(viewer, "intake_requests")
    for r in db.query(
        f"SELECT * FROM intake_requests WHERE status IN ('submitted', 'scored') AND {ifrag}"  # noqa: S608 — scope.visible_filter emits only bound marks
        f" AND LOWER(TRIM(requester)) NOT IN ({fold_marks})"
        " ORDER BY id DESC LIMIT ?",
        (*ip, *mates, _LIMIT),
    ):
        add(r["requester"], "request awaiting triage", r["title"], "")

    qfrag, qp = scope.visible_filter(viewer, "questions")
    for q in db.query(
        f"SELECT * FROM questions WHERE status = 'open' AND {qfrag}"  # noqa: S608 — scope.visible_filter emits only bound marks
        f" AND LOWER(TRIM(asked_by)) NOT IN ({fold_marks})"
        " ORDER BY id DESC LIMIT ?",
        (*qp, *mates, _LIMIT),
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
