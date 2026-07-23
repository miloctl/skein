"""Cross-thread memory tools for the agent."""

import json

from strands import tool

from ..services import memory


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
        return json.dumps(memory.remember(content, topic, user=about_user))
    except ValueError as exc:
        return json.dumps({"error": str(exc)})


@tool
def recall_memories(query: str = "") -> str:
    """Search durable cross-thread memories.

    Args:
        query: What to look for; empty returns the most recent memories.
    """
    return json.dumps(memory.recall(query))
