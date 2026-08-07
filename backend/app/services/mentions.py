"""@mentions: `@name` in prose files a notification for the named person.

scan() runs inside the prose-writing services (task descriptions, notes,
questions and answers, decisions) after their own write. It adds no mutating
surface of its own — no tool, no gate row, no separate activity row: the
parent write carries the provenance, mention_log carries the mention's.
The primary key (entity, entity_id, person) is the dedupe: every edit
re-parses the full text, and a typo fix must not notify twice.
"""

import re

from .. import db
from . import crews, scope

# a roster name is matched whole and case-insensitively; a name with a space
# cannot be written as one @token and is therefore not mentionable.
# The lookbehind keeps an email localpart or ssh target (root@scout) from
# pinging scout — a mention starts a token, it never continues one.
_MENTION = re.compile(r"(?<![a-z0-9])@([a-z0-9][a-z0-9._-]*)", re.ASCII | re.IGNORECASE)


def _reaches(tier: tuple[str, int | None] | None, person: str) -> bool:
    """Can `person` open the row this mention points at.

    Looked up HERE rather than passed in, the same choice search.index_record
    makes and for the same reason: seven callers write prose, and a tier
    threaded through all seven is a tier one of them forgets. The notify below
    names the entity, its id and the author, so without this a `@bo` inside
    Ava's private note told Bo that note #7 exists and who wrote it — and
    opening it 404s. That is the one fact scope.missing exists to withhold.

    A PRIVATE row reaches nobody: its only reader is the author, and the
    author is the actor, who is already in `skip`.

    Takes the resolved tier, it does not look it up: the parent row is the
    same for every name in one scan, and reading it per name made a note with
    12 mentions 12 identical primary-key reads, each on its own connection.
    """
    if tier is None or tier[0] == scope.WORKSPACE:
        return True
    if tier[0] == scope.PRIVATE:
        return False
    return tier[1] in crews.crews_of(person)


def scan(
    entity: str,
    entity_id: int,
    text: str,
    *,
    actor: str = "",
    exclude: tuple = (),
    link: str = "/",
) -> list[str]:
    """Returns the names notified. `exclude` names people the parent write
    already pinged (the assignee on a question, the asker on an answer) —
    a mention must not double-ping. The actor is always excluded: a
    self-mention is not directed attention."""
    if not text or "@" not in text:
        return []
    roster = {
        u["name"].lower(): u["name"]
        for u in db.query("SELECT name FROM users WHERE active = 1 AND name != 'anonymous'")
    }
    skip = {actor.lower(), *(e.lower() for e in exclude if e)}
    from .notifications import notify
    from .search import _tier_of

    # resolved ONCE: the parent cannot change inside one scan
    parent_tier = _tier_of(entity, entity_id)

    notified = []
    for token in dict.fromkeys(m.group(1).lower() for m in _MENTION.finditer(text)):
        # "thanks @mira." binds the sentence-final punctuation into the
        # token (._- are legal name characters) — retry stripped, or the
        # most common mention position never notifies
        name = roster.get(token) or roster.get(token.rstrip("._-"))
        if not name or name.lower() in skip:
            continue
        if not _reaches(parent_tier, name):
            continue
        fresh = db.execute_rowcount(
            "INSERT OR IGNORE INTO mention_log"
            " (entity, entity_id, person, mentioned_by, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (entity, entity_id, name, actor or "system", db.now()),
        )
        if fresh:
            notify(
                name,
                f"{actor or 'system'} mentioned you on {entity} #{entity_id}",
                tier="immediate",
                link=link,
            )
            notified.append(name)
    return notified
