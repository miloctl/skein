"""Meeting-notes ingestion: paste raw notes, get review-queue proposals.

Deterministic only - each line runs through the capture grammar; lines that
match a pattern become pending_changes proposals (NEVER direct writes), the
rest are returned as unclassified for the human to skim. `fb:` lines are
counted and skipped: private feedback never transits the team-visible
review queue. The raw transcript is not persisted."""

import re

from .. import db
from . import review, scope
from .capture import PATTERNS, PREFIX

MAX_BYTES = 64 * 1024
MAX_LINES = 500
MIN_LINE_CHARS = 8

_BULLET = re.compile(r"^\s*(?:[-*•>]|\d+[.)]|\[[ xX]\]|\d{1,2}:\d{2}(?::\d{2})?)\s*")
_FB_LINE = re.compile(r"^\s*fb:", re.I)


def _classify_strict(line: str) -> str | None:
    """Pattern match only - no note fallback. Unmatched lines are the
    human's call, not silent note-spam."""
    for kind, pattern in PATTERNS:
        if pattern.search(line):
            return kind
    return None


def _payload(kind: str, body: str, actor: str) -> dict:
    from .capture import split_assignee, split_by_date, split_party, split_review_by

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
    if kind == "awaiting":
        # the other direction of the same table (migration 007). Built here
        # rather than reusing capture.py's branch because ingest proposes and
        # capture writes - the payload has to survive review.unappliable and
        # then reach promises.add_promise unchanged at apply time.
        who, rest = split_party(body)
        due, rest = split_by_date(rest or body)
        return {
            "promise": rest or body,
            "to_whom": who,
            "due_date": due,
            "direction": "received",
        }
    if kind == "task":
        return {"title": body[:120], "description": body if len(body) > 120 else ""}
    if kind == "request":
        return {"title": body[:120], "detail": body, "requester": actor}
    return {"topic": body[:60], "content": body, "author": actor}


# capture kinds → review-registry entities where the names differ.
# Every kind services/capture.py::PATTERNS can classify MUST resolve here or
# fall through to a real entity name: an unmapped kind reaches
# review.propose_change, which raises, which abandons the rest of the paste
# after some of its lines are already stored. `awaiting` shipped in capture
# without this line and did exactly that.
_ENTITY = {"request": "intake", "awaiting": "promise"}


def _meeting(event_id: int, actor: str, strong: bool) -> dict:
    """What every proposal from one meeting's notes carries: the link, and
    the meeting's own tier. Without the tier each record lands at the
    workspace default, a crew or private meeting refuses the link at approval
    (schedule.check_event_link), and the proposal goes back to pending with
    no way forward. Checked here, before anything is proposed.

    A weak name reads the workspace tier only, so for it a narrower meeting
    reads like an absent one. Past this check its proposals would sit in the
    team queue declared at a tier no reviewer reads, and the team notice
    below would count them for readers who cannot see them."""
    if not event_id:
        return {}
    frag, vp = scope.visible_filter(scope.Viewer.for_actor(actor), "events")
    event = db.query_one(
        f"SELECT visibility, crew_id FROM events WHERE id = ? AND {frag}",  # noqa: S608 - scope.visible_filter emits only bound marks
        (event_id, *vp),
    )
    if event is None or (not strong and event["visibility"] != scope.WORKSPACE):
        raise ValueError(scope.missing_text("events", event_id))
    return {
        "event_id": event_id,
        "visibility": event["visibility"],
        "crew_id": int(event["crew_id"] or 0),
    }


def _assignee_reads(payload: dict, meeting: dict, actor: str) -> bool:
    """Whether a question's assignee can read it at the meeting's tier. The
    service refuses one who cannot (scope.assert_readable_by), so the
    proposal would come back on every approval. Handed back unclassified
    instead, the line is still in front of the person who pasted it."""
    assignee = str(payload.get("assigned_to") or "")
    if not meeting or not assignee:
        return True
    try:
        scope.assert_readable_by(
            meeting["visibility"],
            meeting["crew_id"] or None,
            assignee,
            label="assignee",
            author=actor,
        )
    except ValueError:
        return False
    return True


def ingest_notes(text: str, *, actor: str, private: bool = False, event_id: int = 0) -> dict:
    """`private` is the REST personal default: a strong caller's proposals are
    theirs alone to approve. A weak name reads no private row, so its
    proposals stay in the team queue with the team notice.

    `event_id` names the meeting the notes came from, and every record an
    approval writes links back to it."""
    if not text.strip():
        raise ValueError("nothing to ingest")
    if len(text.encode()) > MAX_BYTES:
        raise ValueError(f"notes too large (max {MAX_BYTES // 1024} KB)")
    lines = text.splitlines()
    if len(lines) > MAX_LINES:
        raise ValueError(f"too many lines (max {MAX_LINES})")

    meeting = _meeting(event_id, actor, private)
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
        if _FB_LINE.match(line):  # before the length gate - short fb: lines still count
            skipped_private += 1  # counted, flagged, never stored or routed
            continue
        if len(line) < MIN_LINE_CHARS:
            continue
        kind = _classify_strict(line)
        if kind is None:
            unclassified.append(line)
            continue
        body = PREFIX.sub("", line).strip() or line
        # Asked BEFORE proposing, and answered by review.py so the agent gate
        # and this ingester cannot drift apart. propose_change would raise on
        # the same payload, which would abandon the rest of the paste - hand
        # the line back as unclassified instead, because that list is shown to
        # the person who pasted it while they still have the text.
        payload = {**_payload(kind, body, actor), **meeting}
        if review.unappliable(_ENTITY.get(kind, kind), payload) or not _assignee_reads(
            payload, meeting, actor
        ):
            unclassified.append(line)
            continue
        try:
            p = review.propose_change(
                _ENTITY.get(kind, kind),
                "create",
                payload,
                summary=line[:80],
                actor=actor,
                origin="human",
                notify_team=False,
                # the paster's alone: the payloads reached every reader of the
                # queue before anyone approved them, and an approval is the
                # share, at the tier the paster picks
                review_visibility=scope.PRIVATE if private else scope.WORKSPACE,
                review_owner=actor if private else "",
            )
        except review.DuplicateProposal as dup:
            # the same notes pasted twice: name the row that already waits
            # instead of filing a twin the reviewer would have to reject
            proposals.append(
                {"id": dup.pending_id, "kind": kind, "line": line[:80], "already_pending": True}
            )
            continue
        proposals.append({"id": p["id"], "kind": kind, "line": line[:80]})

    # the rows this paste filed; a line already pending counts nowhere, or
    # a re-paste told the team twice about the same five
    new = [p for p in proposals if not p.get("already_pending")]
    db.log_activity(
        actor,
        "ingest_notes",
        f"{len(new)} proposal{'' if len(new) == 1 else 's'} from pasted notes",
    )
    if new and not private:
        from .notifications import notify

        notify(
            "team",
            f"{actor} ingested meeting notes: {len(new)}"
            f" proposal{'' if len(new) == 1 else 's'} awaiting review",
            tier="digest",
            link="/review",
        )
    return {
        "proposals": proposals,
        "unclassified": unclassified,
        "skipped_private": skipped_private,
    }
