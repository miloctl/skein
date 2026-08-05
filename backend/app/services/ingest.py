"""Meeting-notes ingestion: paste raw notes, get review-queue proposals.

Deterministic only — each line runs through the capture grammar; lines that
match a pattern become pending_changes proposals (NEVER direct writes), the
rest are returned as unclassified for the human to skim. `fb:` lines are
counted and skipped: private feedback never transits the team-visible
review queue. The raw transcript is not persisted."""

import re

from .. import db
from . import review
from .capture import PATTERNS, PREFIX

MAX_BYTES = 64 * 1024
MAX_LINES = 500
MIN_LINE_CHARS = 8

_BULLET = re.compile(r"^\s*(?:[-*•>]|\d+[.)]|\[[ xX]\]|\d{1,2}:\d{2}(?::\d{2})?)\s*")
_FB_LINE = re.compile(r"^\s*fb:", re.I)


def _classify_strict(line: str) -> str | None:
    """Pattern match only — no note fallback. Unmatched lines are the
    human's call, not silent note-spam."""
    for kind, pattern in PATTERNS:
        if pattern.search(line):
            return kind
    return None


def _payload(kind: str, body: str, actor: str) -> dict:
    from .capture import split_assignee, split_review_by

    if kind == "question":
        assignee, body = split_assignee(body)
        return {"question": body, "asked_by": actor, "assigned_to": assignee}
    if kind == "blocker":
        return {"title": body[:120], "detail": body, "owner": actor}
    if kind == "decision":
        review_by, body = split_review_by(body)
        return {"title": body[:80], "decision": body, "decided_by": actor, "review_by": review_by}
    if kind == "promise":
        return {"promise": body}
    if kind == "task":
        return {"title": body[:120], "description": body if len(body) > 120 else ""}
    if kind == "request":
        return {"title": body[:120], "detail": body, "requester": actor}
    return {"topic": body[:60], "content": body, "author": actor}


# capture kinds → review-registry entities where the names differ
_ENTITY = {"request": "intake"}


def ingest_notes(text: str, *, actor: str) -> dict:
    if not text.strip():
        raise ValueError("nothing to ingest")
    if len(text.encode()) > MAX_BYTES:
        raise ValueError(f"notes too large (max {MAX_BYTES // 1024} KB)")
    lines = text.splitlines()
    if len(lines) > MAX_LINES:
        raise ValueError(f"too many lines (max {MAX_LINES})")

    proposals: list[dict] = []
    unclassified: list[str] = []
    skipped_private = 0
    for raw in lines:
        line = raw
        for _ in range(3):  # nested bullets / "1. [ ] item"
            stripped = _BULLET.sub("", line)
            if stripped == line:
                break
            line = stripped
        line = line.strip()
        if _FB_LINE.match(line):  # before the length gate — short fb: lines still count
            skipped_private += 1  # counted, flagged, never stored or routed
            continue
        if len(line) < MIN_LINE_CHARS:
            continue
        kind = _classify_strict(line)
        if kind is None:
            unclassified.append(line)
            continue
        body = PREFIX.sub("", line).strip() or line
        p = review.propose_change(
            _ENTITY.get(kind, kind),
            "create",
            _payload(kind, body, actor),
            summary=line[:80],
            actor=actor,
            origin="human",
            notify_team=False,
        )
        proposals.append({"id": p["id"], "kind": kind, "line": line[:80]})

    db.log_activity(
        actor,
        "ingest_notes",
        f"{len(proposals)} proposal{'' if len(proposals) == 1 else 's'} from pasted notes",
    )
    if proposals:
        from .notifications import notify

        notify(
            "team",
            f"{actor} ingested meeting notes: {len(proposals)}"
            f" proposal{'' if len(proposals) == 1 else 's'} awaiting review",
            tier="digest",
            link="/review",
        )
    return {
        "proposals": proposals,
        "unclassified": unclassified,
        "skipped_private": skipped_private,
    }
