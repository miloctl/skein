"""Quick capture: freeform text -> routed record via rule-based classification.
No LLM required; an agent classifier can replace `classify` later behind the
same interface."""

import re

from . import blockers, collab, commitments, work

PATTERNS = [
    ("question", re.compile(r"^\s*(q:|question:)", re.I)),
    ("question", re.compile(r"\?\s*$")),
    ("blocker", re.compile(r"^\s*(blocked|blocker|stuck)\b[:\s]", re.I)),
    ("blocker", re.compile(r"\b(blocked (by|on)|waiting on)\b", re.I)),
    ("decision", re.compile(r"^\s*(decision:|decided\b)", re.I)),
    ("decision", re.compile(r"\bwe (decided|chose|are going with)\b", re.I)),
    ("commitment", re.compile(r"^\s*(promised?:|commitment:)", re.I)),
    ("commitment", re.compile(r"\bwe (promised|committed to)\b", re.I)),
    ("task", re.compile(r"^\s*(todo:|task:)", re.I)),
    ("task", re.compile(r"^\s*(fix|add|update|implement|write|ship|review|schedule)\b", re.I)),
    ("note", re.compile(r"^\s*(note:|fyi:|til:)", re.I)),
]

PREFIX = re.compile(
    r"^\s*(q|question|todo|task|note|fyi|til|decision|blocker|blocked|stuck|promised?|commitment):\s*",
    re.I,
)


def classify(text: str) -> str:
    for kind, pattern in PATTERNS:
        if pattern.search(text):
            return kind
    return "note"


def capture(text: str, *, actor: str = "system", origin: str = "human") -> dict:
    text = text.strip()
    if not text:
        raise ValueError("nothing to capture")
    kind = classify(text)
    body = PREFIX.sub("", text).strip() or text

    if kind == "question":
        result = collab.ask_question(body, asked_by=actor, actor=actor, origin=origin)
    elif kind == "blocker":
        result = blockers.raise_blocker(
            title=body[:120], detail=body, owner=actor, actor=actor, origin=origin
        )
    elif kind == "decision":
        result = collab.record_decision(
            title=body[:80], decision=body, decided_by=actor, actor=actor, origin=origin
        )
    elif kind == "commitment":
        result = commitments.add_commitment(body, actor=actor, origin=origin)
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
