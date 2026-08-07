"""Turn-end guard: the deterministic check that runs as a chat turn closes.

The platform's promise is that an agent write is either applied or queued for a
verdict, and the UI states which. That promise has a mirror image nobody was
covering: a turn that ends with NOTHING written, on a message that plainly
asked for a write, says nothing at all — and silence reads as success. The
guard makes the absence of a write as visible as a write.

Keyless and model-free. `unfiled` matches prefixes only: content heuristics
(capture.PATTERNS) are deliberately NOT reused here — one of them classifies
any message ending in "?" as a question, which would fire the guard on ordinary
conversation. A typed prefix is an unambiguous request to file; nothing else
qualifies. `unnotified` reads the roster, which is a deterministic lookup, not
a heuristic.
"""

from .. import config
from ..services import capture

# What the agent is told when the re-prompt is on. Framed as a question about
# intent, not an instruction to write: the user may have been talking ABOUT a
# task rather than asking for one, and the model is better placed to know.
OBJECTION = (
    "The message you just answered starts with a capture prefix, and this turn"
    " wrote nothing. If the user asked you to file something, file it now with"
    " the matching tool. If they did not, reply with one short line saying what"
    " you did instead."
)


def reprompt_enabled() -> bool:
    """Off by default, and never on the mock provider — a re-prompt there costs
    a round trip and buys nothing, because the mock reply is deterministic."""
    return config.TURN_GUARD and config.EFFECTIVE_PROVIDER != "mock"


def unnotified(message: str, wrote: bool, invoked: str = "") -> dict | None:
    """The receipt for an @mention that reached nobody.

    A mention notifies through the row it is written on (services/mentions.py):
    the notification names an entity and an id, and the named person opens it.
    A chat turn that files nothing has no such row, so the mention reaches no
    one — the same silence `unfiled` exists to break, one step earlier.

    `invoked` is the persona this turn answered as. A leading `@slug` IS the
    delivery for that name, so warning about it would contradict the answer
    the reader is looking at.
    """
    if wrote:
        return None
    from ..services import mentions

    people, agents = mentions.names_in(message)
    named = [n for n in [*people, *agents] if n.lower() != invoked.lower()]
    if not named:
        return None
    return {
        "kind": "unnotified",
        "entity": ", ".join(named),
        "detail": "Nothing was filed, so there is nothing for them to open."
        " To reach them, start the message with `q:` or `todo:`.",
        "ref": 0,
    }


def unfiled(message: str, wrote: bool) -> dict | None:
    """The receipt to show when a filing request produced no write at all.

    `wrote` is true if ANY receipt was emitted this turn, including refused and
    failed ones. Those already told the user the truth; only total silence is
    the case this guard exists for.
    """
    if wrote:
        return None
    if not capture.PREFIX.match(message):
        return None
    kind = capture.classify(message)
    return {
        "kind": "nothing",
        "entity": kind,
        "detail": f"To file it as a {kind}, press ⌘K and use the same text.",
        "ref": 0,
    }
