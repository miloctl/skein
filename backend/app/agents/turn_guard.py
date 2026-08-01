"""Turn-end guard: the deterministic check that runs as a chat turn closes.

The platform's promise is that an agent write is either applied or queued for a
verdict, and the UI states which. That promise has a mirror image nobody was
covering: a turn that ends with NOTHING written, on a message that plainly
asked for a write, says nothing at all — and silence reads as success. The
guard makes the absence of a write as visible as a write.

Keyless and model-free: prefix matching only. Content heuristics
(capture.PATTERNS) are deliberately NOT reused here — one of them classifies
any message ending in "?" as a question, which would fire the guard on ordinary
conversation. A typed prefix is an unambiguous request to file; nothing else
qualifies.
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
