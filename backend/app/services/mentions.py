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

# a roster name is matched whole and case-insensitively; a name with a space
# cannot be written as one @token and is therefore not mentionable
_MENTION = re.compile(r"@([a-z0-9][a-z0-9._-]*)", re.ASCII | re.IGNORECASE)


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
    already notified (the question assignee) — a mention must not double-ping.
    The actor is always excluded: a self-mention is not directed attention."""
    if not text or "@" not in text:
        return []
    roster = {
        u["name"].lower(): u["name"]
        for u in db.query("SELECT name FROM users WHERE active = 1 AND name != 'anonymous'")
    }
    skip = {actor.lower(), *(e.lower() for e in exclude if e)}
    from .notifications import notify

    notified = []
    for token in dict.fromkeys(m.group(1).lower() for m in _MENTION.finditer(text)):
        name = roster.get(token)
        if not name or token in skip:
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
                f"{actor or 'someone'} mentioned you on {entity} #{entity_id}",
                tier="immediate",
                link=link,
            )
            notified.append(name)
    return notified
