"""Cross-thread memory tools for the agent."""

import json

from strands import tool

from ..agents.identity import agent_identity, requester_identity
from ..services import memory, scope
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
    # bounds checked BEFORE the gate: an oversized payload must fail on the
    # agent, not surface as an unapprovable proposal in a reviewer's queue
    if len(content) > 2000:
        return json.dumps({"error": "keep memories under 2000 characters"})
    if len(topic) > 100 or len(about_user) > 60:
        return json.dumps({"error": "topic is capped at 100 characters, about_user at 60"})
    payload = {"content": content, "topic": topic, "user": about_user}
    return gated_write(
        "memory",
        "create",
        payload,
        lambda: memory.remember(
            content, topic, user=about_user, actor=agent_identity(), origin="agent"
        ),
        summary=f"remember{f' [{topic}]' if topic else ''}: {content[:80]}",
    )


@tool
def recall_memories(query: str = "") -> str:
    """Search durable cross-thread memories.

    Args:
        query: What to look for; empty returns the most recent memories.
    """
    # `user=`, because recall's two axes are the person and the tier and this
    # door passed NEITHER: the model asked for "therapy" and got back a
    # teammate's memory, straight into the system prompt where nothing marks
    # it as somebody else's. Same shape as get_my_day and my_agent_inbox,
    # which take no name for exactly this reason.
    # No viewer: a tool carries no strong identity, so it reads the workspace
    # tier (docs/VISIBILITY.md decision 3).
    return json.dumps(memory.recall(query, user=requester_identity()))


@tool
def forget_memory(memory_id: int) -> str:
    """Remove a wrong or outdated cross-thread memory for good. Memories
    steer every future conversation, so removal goes through the same review
    gate as other corrections.

    Args:
        memory_id: ID of the memory (recall_memories shows ids).
    """
    row = memory.get_memory(memory_id)
    if not row:
        return json.dumps({"error": f"no memory #{memory_id}"})
    return gated_write(
        "memory_forget",
        "update",
        {},
        lambda: memory.forget(memory_id, actor=agent_identity(), origin="agent"),
        entity_id=memory_id,
        # scope.detail: same egress as tools/collab.py::delete_note — the review queue
        # and the team notification both carry this line
        summary=scope.detail(
            row["visibility"],
            f"forget memory #{memory_id}",
            f"[{row['topic']}]: {row['content'][:80]}",
        ),
    )
