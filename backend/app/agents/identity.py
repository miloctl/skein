"""Per-request agent identity for the chat tool surface.

The chat route sets the acting identity here per request, and every chat
tool reads it — a tool that hardcodes actor="agent" collapses every
chat-side agent into one identity, and the per-agent authority matrix and
trust scores cannot tell a persona from the default Chief of Staff.
Contextvars propagate through the async call chain, so concurrent chats
with different personas do not cross-attribute. The MCP server keeps its
own identity mechanism (SKEIN_MCP_USER).
"""

from contextvars import ContextVar, Token

_current_agent: ContextVar[str] = ContextVar("current_agent", default="agent")


def agent_identity() -> str:
    return _current_agent.get()


def set_agent_identity(name: str) -> Token:
    return _current_agent.set(name)


def reset_agent_identity(token: Token) -> None:
    _current_agent.reset(token)


_current_requester: ContextVar[str] = ContextVar("current_requester", default="")


def requester_identity() -> str:
    """The human whose message caused the acting agent's writes ('' when
    unknown, e.g. MCP where the agent IS the caller)."""
    return _current_requester.get()


def set_requester_identity(name: str) -> Token:
    return _current_requester.set(name)


def reset_requester_identity(token: Token) -> None:
    _current_requester.reset(token)


# The requesting human's Viewer, for the turn. Set beside the name above and
# read by the tool surface.
#
# A tool cannot build one: scope.Viewer carries the strong-identity bar and is
# constructed in routes/deps.py alone, so a tool that resolved its own would
# either bypass the bar or, from a machine name, come back empty. And it
# cannot be skipped — `/as <persona>` lets a HUMAN take an agent identity, so
# "the agent is reading its own inbox" stops being true the moment a person is
# driving the turn. Unset (None) means nobody is: MCP and the scheduler, where
# the agent really is the caller.
_current_requester_viewer: ContextVar[object | None] = ContextVar(
    "current_requester_viewer", default=None
)


def requester_viewer() -> object | None:
    """The Viewer of the human whose message caused this turn, or None when
    the agent is the caller (MCP, a scheduled job)."""
    return _current_requester_viewer.get()


def set_requester_viewer(v: object | None) -> Token:
    return _current_requester_viewer.set(v)


def reset_requester_viewer(token: Token) -> None:
    _current_requester_viewer.reset(token)


_force_review: ContextVar[bool] = ContextVar("force_review", default=False)


def force_review() -> bool:
    """True while the acting agent's writes must queue for a human whatever
    the authority matrix says. Set per flock member task and per consulted
    specialist (docs/FLOCKS.md, docs/PERSONAS.md): both turns are
    consultative, and one human message must not become N unreviewed writes
    because the agents earned autonomy one at a time. tools/_gate.py and
    refuse_when_consultative below are the only readers — a write path that
    reaches neither is ungoverned in both modes."""
    return _force_review.get()


def set_force_review(on: bool) -> Token:
    return _force_review.set(on)


def refuse_when_consultative(action: str) -> None:
    """Guard for the write paths that skip tools/_gate.py BY DESIGN — the
    delegation trio and the handoff generator (tests/test_gate_coverage.py
    holds that list). The gate is the only place force_review turns a write
    into a proposal, so a path that never reaches it would let a consultative
    agent write directly during a turn whose whole promise is that every such
    write is reviewed. Refusal, not a proposal: status motion and artifact
    projection have no proposal shape, and the agent was asked for an
    opinion, not for work.

    The message names the MODE, not the flock: a consulted specialist reaches
    this too (team_agent.py::build_agent), and a message naming a flock sends
    that reader looking for a flock they never started."""
    if _force_review.get():
        raise ValueError(
            f"this agent was asked for an opinion, not for work, and does not {action}"
            " — ask the agent directly in its own chat"
        )


# Per-turn consult budget. The bench roster sits in the orchestrator's prompt,
# so the MODEL chooses how many specialists run in one turn — the only number
# in the product that multiplies model spend and is not written by an operator
# (services/flocks.py records why a flock's member count is file-declared).
# The route seeds the budget from the specialists the USER named
# (routes/chat.py::_consult_budget); this bounds the ones it did not name. The
# rate limiter is charged per call inside the tool, not up front — unlike a
# flock, whose member count is known before the stream opens.
MAX_CONSULTS_PER_TURN = 2

_consults: ContextVar[list[int] | None] = ContextVar("consults", default=None)


def start_consults(budget: int = MAX_CONSULTS_PER_TURN) -> None:
    """Open the turn's consult budget.

    A LIST, not an int, for the reason agents/receipts.py holds one: strands
    runs each tool call in its own asyncio task, which COPIES the context, so
    an int incremented inside one consult is invisible to the next and the cap
    never binds.
    """
    _consults.set([0, max(1, budget)])


def reset_consults() -> None:
    """Close the turn's budget. Without this a box set by one turn survives
    into the next context that shares it — tests share worker threads, so
    conftest resets it the way it resets receipts."""
    _consults.set(None)


def take_consult() -> bool:
    """Claim one consult, or False when the turn's budget is spent.

    An unopened budget opens ITSELF at the default rather than waving the call
    through. Fails closed on purpose: the depth cap is structural, but this cap
    lives at a call site, so a future build_agent caller with persona == "" —
    a scheduled digest, a CLI turn — would otherwise get an unbounded
    model-chosen fan-out and no test would fail.

    The ContextVar default stays None because a mutable default is ONE list
    shared by every context in the process."""
    box = _consults.get()
    if box is None:
        box = [0, MAX_CONSULTS_PER_TURN]
        _consults.set(box)
    if box[0] >= box[1]:
        return False
    box[0] += 1
    return True
