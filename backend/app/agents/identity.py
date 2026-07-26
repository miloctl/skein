"""Per-request agent identity for the chat tool surface.

Chat tools used to hardcode actor="agent", collapsing every chat-side agent
into one identity — the per-agent authority matrix and trust scores could
not tell a persona from the default Chief of Staff. The chat route sets the
acting identity here per request; contextvars propagate through the async
call chain, so concurrent chats with different personas do not cross-
attribute. The MCP server keeps its own identity mechanism (STRANDS_MCP_USER).
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
