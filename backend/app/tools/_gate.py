"""Shared review gate for ALL mutating agent tools, now authority-aware.

Per (agent, entity) the authority matrix grants: autonomous (direct write),
notify (direct write + team notification), review (proposal when
STRANDS_AGENT_REVIEW=1, direct otherwise — the pre-matrix behavior), or
forbidden (always refused). Default is review — agents earn autonomy through
approved proposals, they don't start with it."""

import json

from .. import config
from ..agents.identity import agent_identity, requester_identity
from ..services import review
from ..services.delegation import authority_level

# irreversible verbs ALWAYS go through the review inbox, even with
# STRANDS_AGENT_REVIEW off — a prompt-injected agent must never hard-delete
# the knowledge base or its own steering evidence without a human verdict
# (edits stay reversible + old->new logged, so they follow the normal flag)
ALWAYS_REVIEW = {"note_delete", "memory_forget"}


def gated_write(
    entity: str,
    action: str,
    payload: dict,
    direct,
    entity_id: int = 0,
    summary: str = "",
    actor: str = "",
) -> str:
    """One gate for every agent write path (chat tools AND the MCP server) —
    per-agent authority and the review inbox see all agent traffic, so trust
    scores accrue no matter which door the agent came through."""
    actor = actor or agent_identity()
    level = authority_level(actor, entity)
    if level == "forbidden":
        return json.dumps(
            {"error": f"writes to {entity} are forbidden for '{actor}' by the authority matrix"}
        )
    if entity not in ALWAYS_REVIEW and (
        level == "autonomous" or level == "notify" or not config.AGENT_REVIEW
    ):
        try:
            result = direct()
        except ValueError as exc:
            return json.dumps({"error": str(exc)})
        if level == "notify":
            from ..services.notifications import notify

            notify(
                "team",
                f"Agent {actor} wrote {entity}.{action}: {summary or json.dumps(payload)[:120]}",
                tier="digest",
                link="/review",
            )
        return json.dumps(result)
    try:
        result = review.propose_change(
            entity,
            action,
            payload,
            summary=summary,
            entity_id=entity_id,
            actor=actor,
            origin="agent",
            requested_by=requester_identity(),
        )
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({**result, "note": "queued for human review"})


def blocked_when_gated(what: str) -> str | None:
    """For destructive actions that can't be represented as a proposal."""
    if config.AGENT_REVIEW:
        return json.dumps(
            {
                "error": f"{what} requires direct human action while review mode is on"
                " — ask the user to do it from the UI",
            }
        )
    return None
