"""Cross-thread memory tools for the agent."""

import json

from strands import tool

from ..agents.identity import agent_identity
from ..services import memory
from ._gate import gated_write


@tool
def remember(content: str, topic: str = "", about_user: str = "") -> str:
    """Persist a durable memory that survives across chat threads — user
    preferences, working styles, standing context, rationale worth keeping.
    Use when someone says "remember that ..." or you learn something that
    future conversations will need.

    Args:
        content: The fact to remember, written to be useful later.
        topic: Short slug for the memory.
        about_user: Team member the memory concerns, if any.
    """
    try:
        return json.dumps(memory.remember(content, topic, user=about_user, actor=agent_identity()))
    except ValueError as exc:
        return json.dumps({"error": str(exc)})


@tool
def recall_memories(query: str = "") -> str:
    """Search durable cross-thread memories.

    Args:
        query: What to look for; empty returns the most recent memories.
    """
    return json.dumps(memory.recall(query))


@tool
def forget_memory(memory_id: int) -> str:
    """Remove a wrong or outdated cross-thread memory for good. Memories
    steer every future conversation, so removal goes through the same review
    gate as other corrections.

    Args:
        memory_id: ID of the memory (recall_memories shows ids).
    """
    return gated_write(
        "memory_forget",
        "update",
        {},
        lambda: memory.forget(memory_id, actor=agent_identity(), origin="agent"),
        entity_id=memory_id,
        summary=f"forget memory #{memory_id}",
    )
