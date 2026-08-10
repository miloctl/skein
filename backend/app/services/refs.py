"""Entity references inside a generated sentence.

Skein's deterministic producers write receipts as prose — "milestone #4 'Cutover'
overdue since 2026-08-01", "task #12 'Wire the gate' waiting on blocker #3". The
prose is the right output for an artifact, which is markdown on disk and has
nowhere to put a link. It is the wrong output for a screen: a reader given a row
id and no way to open it goes hunting by eye, which is the cost this whole layer
exists to remove.

Rather than rewrite every producer to emit structured rows — thirty call sites,
each with an artifact reader that still needs the sentence — this parses the
grammar the producers already share. One grammar, one place, and a receipt gains
its links on the day its producer is written rather than the day somebody
remembers to convert it.

The grammar is `<entity> #<id>`, case-insensitive, where `<entity>` is a word
this file knows. An unknown word is NOT a reference: `PR #42` and `sprint #3`
name somebody else's numbering, and a link that opens the wrong row is worse
than no link. `#42` bare is deliberately not matched either — git trailers give
it the task meaning, but a receipt that means a task says "task".
"""

import re

# entity word -> the surface that renders one row of it. `task` is the only
# one with a panel (`?task=` opens it over whatever page the reader is on);
# the rest land on a page with an anchor, and the anchor is the row's id.
#
# Kept in step with frontend/lib/entity-ref.ts, which turns these into hrefs.
# A word added here and not there renders as plain text, which is the safe
# direction — the reverse invents a link to a page that cannot show the row.
TARGETS = {
    "task": "task",
    "milestone": "milestone",
    "blocker": "blocker",
    "question": "question",
    "decision": "decision",
    "promise": "promise",
    "proposal": "proposal",
    "engagement": "engagement",
    "lesson": "lesson",
    "finding": "finding",
    "intake": "intake",
}

_REF = re.compile(r"\b(" + "|".join(TARGETS) + r")\s+#(\d+)\b", re.IGNORECASE)

# A row's own title, as every producer quotes it: `task #12 'Wire the gate' …`
# (services/portfolio.py, services/intervention.py). The title is free text a
# person typed and it must NOT be parsed — a task called "Follow up on decision
# #4" otherwise linked its receipt to whatever decision holds id 4, which is
# the wrong-row link this whole module exists to avoid.
_QUOTED = re.compile(r"'[^']*'")


def refs(text: str) -> list[dict]:
    """Every entity reference in one generated sentence, in reading order.

    Only in the GENERATED frame. Single-quoted spans are the row titles the
    producers interpolate, and a reference found inside one was written by a
    teammate about something else.

    Deduped on (entity, id): "task #12 waiting on task #12" is a cycle a
    producer can emit, and two chips for one row reads as two rows.
    """
    # blanked, not removed: the offsets must keep matching the original string,
    # because `splitReceipt` walks the same sentence to place the links. It
    # matches the FIRST occurrence of each reference, so when the frame and a
    # quoted title both name one row the link lands on the quoted copy — the
    # right target, one occurrence early, and the only cost of parsing here
    # rather than shipping offsets on every receipt.
    frame = _QUOTED.sub(lambda m: " " * len(m.group(0)), text or "")
    out: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for m in _REF.finditer(frame):
        entity, rid = m.group(1).lower(), int(m.group(2))
        if (entity, rid) in seen:
            continue
        seen.add((entity, rid))
        out.append({"entity": entity, "id": rid})
    return out


def receipt(text: str) -> dict:
    """One receipt as the UI reads it: the sentence, and what it points at.

    The sentence is preserved verbatim and stays first. A reader must be able
    to understand a receipt without following anything, because the artifact
    on disk carries the same words with no links at all.
    """
    return {"message": text, "refs": refs(text)}
