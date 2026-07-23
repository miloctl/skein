"""Shared review gate for ALL mutating agent tools. With STRANDS_AGENT_REVIEW=1
the write becomes a pending_changes proposal instead of applying directly."""

import json

from .. import config
from ..services import review


def gated_write(entity: str, action: str, payload: dict, direct,
                entity_id: int = 0, summary: str = "") -> str:
    if config.AGENT_REVIEW:
        try:
            result = review.propose_change(entity, action, payload, summary=summary,
                                           entity_id=entity_id, actor="agent",
                                           origin="agent")
        except ValueError as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps({**result, "note": "queued for human review"})
    try:
        return json.dumps(direct())
    except ValueError as exc:
        return json.dumps({"error": str(exc)})


def blocked_when_gated(what: str) -> str | None:
    """For destructive actions that can't be represented as a proposal."""
    if config.AGENT_REVIEW:
        return json.dumps({
            "error": f"{what} requires direct human action while review mode is on"
                     " — ask the user to do it from the UI",
        })
    return None
