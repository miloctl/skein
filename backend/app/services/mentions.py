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


def _roster() -> dict[str, tuple[str, str]]:
    """@token (lowercased) -> (roster name, kind). Personas share this table
    with people: /as and /flock mint `kind='agent'` rows on demand
    (services/users.py::ensure_user), which also refuses a human name that
    collides with a bench slug — so one token never means two identities."""
    return {
        u["name"].lower(): (u["name"], u["kind"])
        for u in db.query("SELECT name, kind FROM users WHERE active = 1 AND name != 'anonymous'")
    }


# fenced and inline code, dropped before any token is read. Chat is where
# people paste shell and YAML, and `curl -H "X-User: @mira"` is not a mention
# — it notified mira, and the chat guard then told the author it had not.
_CODE = re.compile(r"```.*?```|`[^`]*`", re.S)


def _tokens(text: str) -> list[str]:
    text = _CODE.sub(" ", text)
    return list(dict.fromkeys(m.group(1).lower() for m in _MENTION.finditer(text)))


def _match(roster: dict[str, tuple[str, str]], token: str) -> tuple[str, str] | None:
    # "thanks @mira." binds the sentence-final punctuation into the token
    # (._- are legal name characters) — retry stripped, or the most common
    # mention position never matches
    return roster.get(token) or roster.get(token.rstrip("._-"))


def names_in(text: str, actor: str = "") -> tuple[list[str], list[str]]:
    """(people, agents) named by an @token, in roster casing.

    Shares _tokens and _match with scan() on purpose: a surface that reports
    what a mention WILL do must not use a second parser, or it names people
    scan never matches and stays silent about ones it does. `actor` is dropped
    for the same reason scan drops it — a self-mention is not directed
    attention, and reporting one tells the author to file something that would
    notify nobody.
    """
    if not text or "@" not in text:
        return [], []
    skip = actor.strip().lower()
    roster = _roster()
    people: list[str] = []
    agents: list[str] = []
    for token in _tokens(text):
        hit = _match(roster, token)
        if hit and hit[0].lower() != skip:
            (agents if hit[1] == "agent" else people).append(hit[0])
    return people, agents


def slugs_in(text: str, known: set[str]) -> list[str]:
    """@tokens naming something in `known`, WITHOUT consulting the roster.

    names_in above needs a users row, and a bench specialist only gets one
    after its first /as or consult (services/users.py::ensure_user) — so it
    cannot see a specialist nobody has called yet, which is exactly the first
    consult. Shares _tokens with scan() for the reason names_in does: a second
    parser matches names the first one does not.
    """
    out = []
    for token in _tokens(text):
        hit = token if token in known else token.rstrip("._-")
        if hit in known and hit not in out:
            out.append(hit)
    return out


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
    """Record mentions and create their notices in one transaction."""
    with db.transaction():
        return _scan_locked(
            entity,
            entity_id,
            text,
            actor=actor,
            exclude=exclude,
            link=link,
        )


def _scan_locked(
    entity: str,
    entity_id: int,
    text: str,
    *,
    actor: str,
    exclude: tuple,
    link: str,
) -> list[str]:
    """Returns the names notified. `exclude` names people the parent write
    already pinged (the assignee on a question, the asker on an answer) —
    a mention must not double-ping. The actor is always excluded: a
    self-mention is not directed attention."""
    if not text or "@" not in text:
        return []
    roster = _roster()
    skip = {actor.lower(), *(e.lower() for e in exclude if e)}
    from .notifications import notify
    from .search import _tier_of

    # resolved ONCE: the parent cannot change inside one scan
    parent_tier = _tier_of(entity, entity_id)

    notified = []
    for token in _tokens(text):
        hit = _match(roster, token)
        # agents are mentionable on purpose, NOT an oversight: an agent
        # identity reads its notifications through tools/portfolio.py::
        # my_agent_inbox, so `@scout take this one` on a task reaches scout
        # the way it reaches a person (pinned by test_mentions.py).
        if not hit:
            continue
        name = hit[0]
        if name.lower() in skip:
            continue
        if not _reaches(parent_tier, name):
            continue
        fresh = db.execute_rowcount(
            "INSERT INTO mention_log"
            " (entity, entity_id, person, mentioned_by, created_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT DO NOTHING",
            (entity, entity_id, name, actor or "system", db.now()),
        )
        if fresh:
            notify(
                name,
                lambda source: f"{actor or 'system'} mentioned you on {entity} #{source['id']}",
                tier="immediate",
                link=link,
                source_entity=entity,
                source_id=entity_id,
            )
            notified.append(name)
    return notified


def scan_shared_message(thread_id: str, message_id: int, text: str, *, actor: str) -> list[str]:
    """Notify active human room members named in one persisted message."""
    if not db.in_transaction():
        raise RuntimeError("shared-chat mention scan needs the message transaction")
    people, _agents = names_in(text, actor=actor)
    if not people:
        return []
    members = {
        row["person"]
        for row in db.query(
            "SELECT m.person FROM chat_members m JOIN users u ON u.name = m.person"
            " WHERE m.thread_id = ? AND m.left_at IS NULL"
            " AND u.kind = 'human' AND u.active = 1",
            (thread_id,),
        )
    }
    from .notifications import notify

    notified = []
    for person in people:
        if person not in members:
            continue
        fresh = db.execute_rowcount(
            "INSERT INTO mention_log"
            " (entity, entity_id, person, mentioned_by, created_at)"
            " VALUES ('chat_message', ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (message_id, person, actor, db.now()),
        )
        if fresh:
            notify(
                person,
                f"{actor} mentioned you in a private shared chat.",
                tier="immediate",
                link=f"/chat?shared={thread_id}#shared-message-{message_id}",
                source_entity="chat_message",
                source_id=message_id,
            )
            notified.append(person)
    return notified
