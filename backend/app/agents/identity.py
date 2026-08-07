"""Per-request agent identity for the chat tool surface.

Chat tools used to hardcode actor="agent", collapsing every chat-side agent
into one identity — the per-agent authority matrix and trust scores could
not tell a persona from the default Chief of Staff. The chat route sets the
acting identity here per request; contextvars propagate through the async
call chain, so concurrent chats with different personas do not cross-
attribute. The MCP server keeps its own identity mechanism (SKEIN_MCP_USER).
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
    the authority matrix says. Set per flock member task (docs/FLOCKS.md): a
    flock turn is consultative, and one human message must not become N
    unreviewed writes because the members earned autonomy one at a time.
    tools/_gate.py and refuse_in_flock below are the only readers — a write
    path that reaches neither is ungoverned in a flock."""
    return _force_review.get()


def set_force_review(on: bool) -> Token:
    return _force_review.set(on)


def refuse_in_flock(action: str) -> None:
    """Guard for the write paths that skip tools/_gate.py BY DESIGN — the
    delegation trio and the handoff generator (tests/test_gate_coverage.py
    holds that list). The gate is the only place force_review turns a write
    into a proposal, so a path that never reaches it would let a flock member
    write directly during a turn whose whole promise is that every member
    write is reviewed. Refusal, not a proposal: status motion and artifact
    projection have no proposal shape, and the member was asked for an
    opinion, not for work. See docs/FLOCKS.md."""
    if _force_review.get():
        raise ValueError(f"a flock member does not {action} — ask this agent directly")
