"""Quick capture: freeform text -> routed record via rule-based classification.
No LLM required; an agent classifier can replace `classify` later behind the
same interface."""

import re

from . import blockers, collab, commitments, work

# explicit prefixes first, content heuristics second — a typed prefix always
# wins ("req: blocked on X" is a request, not a blocker)
PATTERNS = [
    ("question", re.compile(r"^\s*(q:|question:)", re.I)),
    ("blocker", re.compile(r"^\s*(blocked|blocker|stuck)\b[:\s]", re.I)),
    ("decision", re.compile(r"^\s*(decision:|decided\b)", re.I)),
    ("commitment", re.compile(r"^\s*(promised?:|commitment:)", re.I)),
    ("request", re.compile(r"^\s*(req:|request:)", re.I)),
    ("task", re.compile(r"^\s*(todo:|task:)", re.I)),
    ("note", re.compile(r"^\s*(note:|fyi:|til:)", re.I)),
    ("question", re.compile(r"\?\s*$")),
    ("blocker", re.compile(r"\b(blocked (by|on)|waiting on)\b", re.I)),
    ("decision", re.compile(r"\bwe (decided|chose|are going with)\b", re.I)),
    ("commitment", re.compile(r"\bwe (promised|committed to)\b", re.I)),
    ("task", re.compile(r"^\s*(fix|add|update|implement|write|ship|review|schedule)\b", re.I)),
]

PREFIX = re.compile(
    r"^\s*(q|question|todo|task|note|fyi|til|decision|blocker|blocked|stuck"
    r"|promised?|commitment|req|request):\s*",
    re.I,
)

# `q: mira — where do we log?` assigns to mira — same person-separator grammar
# as fb:, but only when the name matches an active user (else it stays text).
# The known-user gate is LOAD-BEARING: with re.S the person group can span
# newlines, and only the gate keeps arbitrary text from becoming an assignee.
_Q_ASSIGN = re.compile(
    r"^(?P<person>[^\s\u2014\u2013:-][^\u2014\u2013:]{0,40}?)"
    r"\s*(?:\u2014|\u2013|:|\s-\s)\s*(?P<body>.+)$",
    re.S,
)
# `decision: … review by 2026-10-01` feeds the half-life sweep
_REVIEW_BY = re.compile(r"[\s,;\u2014\u2013-]*\breview by\s+(?P<date>\d{4}-\d{2}-\d{2})\s*$", re.I)


def _known_user(name: str) -> str:
    from . import users

    candidates = {u["name"].lower(): u["name"] for u in users.list_users()}
    return candidates.get(name.strip().lower(), "")


def split_assignee(body: str) -> tuple[str, str]:
    """(assignee, rest) when the body starts with a known user + separator."""
    m = _Q_ASSIGN.match(body)
    if m:
        person = _known_user(m.group("person"))
        if person:
            return person, m.group("body").strip()
    return "", body


def split_review_by(body: str) -> tuple[str, str]:
    """(review_by, rest) when the body ends with `review by YYYY-MM-DD`."""
    m = _REVIEW_BY.search(body)
    if m:
        return m.group("date"), body[: m.start()].strip()
    return "", body


def classify(text: str) -> str:
    for kind, pattern in PATTERNS:
        if pattern.search(text):
            return kind
    return "note"


def is_private_feedback(text: str) -> bool:
    """Does this text carry an fb: line? Any surface that could route text to
    a team-visible record must refuse first — chat, the session bridge, ingest
    and MCP all ask here rather than importing the private module, so the
    privacy canary in test_privacy can stay a strict source-level rule."""
    from . import private_notes

    return any(private_notes.FB_GUARD.match(ln) for ln in text.splitlines())


def plan(text: str, *, actor: str = "system") -> tuple[str, str, dict]:
    """(kind, review-registry entity, payload) for one capture.

    The agent path PROPOSES this payload and the review registry applies it by
    calling the create handler with **payload, so the keys here must be the
    handler's own kwargs. A generic {"text": ...} was applicable by nothing:
    every captured proposal failed at apply and reset to pending, wedging the
    review inbox forever. capture() takes its kind from this function, so the
    two paths cannot classify the same text differently — but its branch
    calls mirror these payloads by hand, so a key change here must be made
    there too. test_authority.py::
    test_every_classified_capture_kind_produces_an_applicable_proposal pins
    that every payload above applies through the registry.
    """
    kind = classify(text)
    body = PREFIX.sub("", text).strip() or text
    if kind == "question":
        assignee, q = split_assignee(body)
        return kind, "question", {"question": q, "asked_by": actor, "assigned_to": assignee}
    if kind == "blocker":
        return kind, "blocker", {"title": body[:120], "detail": body, "owner": actor}
    if kind == "decision":
        review_by, d = split_review_by(body)
        return (
            kind,
            "decision",
            {
                "title": d[:80],
                "decision": d,
                "decided_by": actor,
                "review_by": review_by,
            },
        )
    if kind == "commitment":
        return kind, "commitment", {"promise": body}
    if kind == "request":
        return kind, "intake", {"title": body[:120], "detail": body}
    if kind == "task":
        return (
            kind,
            "task",
            {
                "title": body[:120],
                "description": body if len(body) > 120 else "",
                "assignee": "",
            },
        )
    return kind, "note", {"topic": body[:60], "content": body, "author": actor}


def capture(
    text: str, *, actor: str = "system", origin: str = "human", strong_auth: bool = False
) -> dict:
    text = text.strip()
    if not text:
        raise ValueError("nothing to capture")
    # fb: short-circuits BEFORE classification — feedback never becomes a
    # team-visible, FTS-indexed record. Human-only, strong-identity-only;
    # no index_record, no activity log (audit lives inside private.db).
    # Line-oriented and fail-closed: an fb: line buried in a multi-line
    # capture must never ride along into a task/note (it would land in FTS).
    from . import private_notes

    if any(private_notes.FB_LINE.match(ln) for ln in text.splitlines()):
        if len(text.splitlines()) > 1:
            raise ValueError(
                "fb: lines must be captured alone — they are private and the"
                " rest of a capture is team-visible. Use /ingest for"
                " multi-line notes (fb: lines are skipped there)."
            )
        if origin != "human":
            raise ValueError("feedback notes are human-only — agents cannot write them")
        if not strong_auth:
            raise ValueError(
                "feedback notes require a personal API key"
                " (bootstrap: python -m app.bootstrap_key <you>; then 🔑 in the UI)"
            )
        person, body = private_notes.parse_feedback(text)  # raises on bad format
        result = private_notes.add_note(actor, person, body, kind="feedback")
        return {"kind": "feedback", **result}
    kind, _entity, _payload = plan(text, actor=actor)
    body = PREFIX.sub("", text).strip() or text

    if kind == "question":
        assignee, body = split_assignee(body)
        result = collab.ask_question(
            body, asked_by=actor, assigned_to=assignee, actor=actor, origin=origin
        )
    elif kind == "blocker":
        result = blockers.raise_blocker(
            title=body[:120], detail=body, owner=actor, actor=actor, origin=origin
        )
    elif kind == "decision":
        review_by, body = split_review_by(body)
        result = collab.record_decision(
            title=body[:80],
            decision=body,
            decided_by=actor,
            review_by=review_by,
            actor=actor,
            origin=origin,
        )
    elif kind == "commitment":
        result = commitments.add_commitment(body, actor=actor, origin=origin)
    elif kind == "request":
        # requests arrive where people already type — route them into intake
        # instead of letting them die as notes
        from . import intake

        result = intake.submit_request(body[:120], detail=body, actor=actor, origin=origin)
    elif kind == "task":
        result = work.create_task(
            title=body[:120],
            description=body if len(body) > 120 else "",
            assignee="",
            actor=actor,
            origin=origin,
        )
    else:
        result = collab.save_note(
            topic=body[:60], content=body, author=actor, actor=actor, origin=origin
        )
    from .. import db

    db.log_activity(actor, "capture", f"{kind} #{result.get('id')}")
    return {"kind": kind, **result}
