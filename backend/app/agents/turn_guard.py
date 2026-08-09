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


def unnotified(
    message: str,
    wrote: bool,
    actor: str = "",
    invoked: str = "",
    consulted: tuple[str, ...] = (),
) -> dict | None:
    """The receipt for an @mention that reached nobody.

    A mention notifies through the row it is written on (services/mentions.py):
    the notification names an entity and an id, and the named person opens it.
    A chat turn that files nothing has no such row, so the mention reaches no
    one.

    NOT the same trigger as `unfiled` below, which needs a typed capture
    prefix. A handle is its own intent signal — nobody types a teammate's name
    by accident — so this fires on prose, and states what happened rather than
    demanding a write the author may not want.

    `invoked` catches a REPEATED mention of the persona answering
    (`/as scout ... @scout ...`). A leading `@slug` WITH a message never
    reaches here — the route rewrites it into the /as form and `message` keeps
    only the remainder. A bare `@slug` does, and is reported like any name.
    """
    if wrote:
        return None
    from ..services import mentions

    people, agents = mentions.names_in(message, actor=actor)
    skip = invoked.lower()
    # A specialist the turn CONSULTED already answered in this chat, which is
    # the most direct delivery there is. Reporting it unreached would tell the
    # reader nothing arrived while its answer sits above the receipt.
    spoke = {s.lower() for s in consulted}
    named = [n for n in [*people, *agents] if n.lower() != skip and n.lower() not in spoke]
    if not named:
        return None
    # capped: a pasted standup with twenty handles wrote a receipt longer than
    # the answer, into the SSE frame and the saved transcript both
    shown = ", ".join(named[:3])
    rest = len(named) - 3
    # A filed row reaches an agent as well as a person (services/mentions.py::
    # scan notifies agents on purpose, and tools/portfolio.py::my_agent_inbox
    # reads them), so the capture prefix is not wrong for a specialist — it is
    # incomplete. Without this line, "ask @growth-mentor about tomorrow" is
    # answered with instructions for filing a task, which is not what the
    # reader asked for.
    detail = "This turn filed nothing, so there is nothing to open."
    detail += " To reach them, start the message with a capture prefix such as `todo:`."
    # last, and "instead": placed before the sentence above, its "them" bound
    # to the specialist rather than to the names the receipt is about
    if any(a.lower() != skip and a.lower() not in spoke for a in agents):
        detail += " To ask a specialist instead, start the message with `@` and its name."
    return {
        "kind": "unnotified",
        "entity": f"{shown} and {rest} more" if rest > 0 else shown,
        "detail": detail,
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
        # names the action, never the key: this reaches the reader through
        # chat's markdown renderer, which does not pass components/shortcut.tsx,
        # so a ⌘K token here ships raw and is the wrong key on most keyboards
        "detail": f"To file it as a {kind}, use quick capture with the same text.",
        "ref": 0,
    }
