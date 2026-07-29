"""Write receipts for the chat stream.

The platform's central promise is that an agent write is either applied or
queued for a human verdict — and until now the UI stated that only because
the system prompt asked the model to. Strands' ToolResultEvent is not a
callback event, so results never reach the SSE consumer; instead the gate
(the one choke point every agent write passes through) records what it did
here, and the chat route drains it into the stream.

Per-task contextvar: two concurrent chats never see each other's receipts.
"""

from contextvars import ContextVar

_receipts: ContextVar[list[dict] | None] = ContextVar("receipts", default=None)


def start() -> None:
    """Begin collecting for this turn."""
    _receipts.set([])


def record(kind: str, entity: str, detail: str = "", ref: int = 0) -> None:
    """kind: queued (needs a verdict) | wrote (applied) | refused (authority)
    | failed (validation). ref is the proposal id for queued, else the row id."""
    box = _receipts.get()
    if box is None:  # not a chat turn (REST, MCP, scheduler) — nothing to show
        return
    box.append({"kind": kind, "entity": entity, "detail": detail[:160], "ref": ref})


def drain() -> list[dict]:
    """Take everything recorded since the last drain."""
    box = _receipts.get()
    if not box:
        return []
    out = box[:]
    box.clear()
    return out
