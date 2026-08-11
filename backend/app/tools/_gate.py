"""Shared review gate for ALL mutating agent tools, now authority-aware.

Per (agent, entity) the authority matrix grants: autonomous (direct write),
notify (direct write + team notification), review (proposal when
SKEIN_AGENT_REVIEW=1, direct otherwise — the pre-matrix behavior), or
forbidden (always refused). Default is review — agents earn autonomy through
approved proposals, they don't start with it.

One thing outranks the matrix: agents/identity.py::force_review, set for the
duration of a flock member's turn. It forces the proposal path whatever the
level says and whatever SKEIN_AGENT_REVIEW is, and forbidden still outranks
IT. Write paths that skip this gate by design carry their own guard
(refuse_when_consultative); tests/test_gate_coverage.py::UNGATED_WRITERS is the list."""

import json

from .. import db, ratelimit
from ..agents import receipts
from ..agents.identity import agent_identity, requester_identity
from ..extensions.policy import (
    PolicyEffect,
    PolicyInput,
    PolicyResource,
    PolicySubject,
    approval_fingerprint,
    current_policy_engine,
    current_policy_subject,
    policy_input_data,
)
from ..services import lexicon, review
from ..services.delegation import authority_level

# irreversible verbs ALWAYS go through the review inbox, even with
# SKEIN_AGENT_REVIEW off — a prompt-injected agent must never hard-delete
# the knowledge base or its own steering evidence without a human verdict
# (edits stay reversible + old->new logged, so they follow the normal flag)
ALWAYS_REVIEW = {"note_delete", "memory_forget", "event_cancel", "absence"}

# The matrix is keyed on the literal entity a tool passes, and the registry
# splits families (note / note_edit / note_delete). Forbidding the base entity
# therefore left every mutator open: an agent forbidden on `note` still
# rewrote an existing note's content, which is strictly worse than the
# creation the operator blocked. A grant may still be fine-grained; a
# FORBIDDEN is absolute, so authority resolves over the family and the
# strictest level wins.
# Every registry entity named <root>_<verb> belongs to <root>'s family.
# test_authority pins this against the registry so a new mutator cannot be
# added without one.
_FAMILY = {
    "note_edit": "note",
    "note_delete": "note",
    "blocker_edit": "blocker",
    "promise_edit": "promise",
    "promise_settle": "promise",
    "intake_edit": "intake",
    "memory_forget": "memory",
    "question_assign": "question",
    "event_cancel": "event",
}

# Registry entities that LOOK like <root>_<verb> but are not gate families.
# task_completion is filed by delegation.submit_completion, which never routes
# through gated_write — it is the sponsor's acceptance proposal, and the
# delegation trio already honors the `task` kill switch via
# delegation._check_not_forbidden. Listing it here is a decision on the
# record, which is what the parity test asks for.
_NOT_A_FAMILY = {"task_completion"}


def effective_level(actor: str, entity: str) -> str:
    """The entity's own level, unless its family root is explicitly forbidden.

    ONLY forbidden propagates. authority_level returns the default "review"
    when no row exists, so taking the strictest of the two made an ABSENT
    parent override an explicit child grant — granting note_edit=autonomous
    resolved to review, and the fine-grained grant the matrix exists to allow
    became a no-op. A kill switch is absolute; a grant stays per-entity."""
    root = _FAMILY.get(entity)
    if root and authority_level(actor, root) == "forbidden":
        return "forbidden"
    return authority_level(actor, entity)


def gated_write(
    entity: str,
    action: str,
    payload: dict,
    direct,
    entity_id: int = 0,
    summary: str = "",
    actor: str = "",
) -> str:
    # Serialize authoritative context resolution, the workplace decision, and
    # the resulting local mutation. Nested service transactions join this
    # transaction. This prevents a concurrent relink from changing the policy
    # domain after the decision but before the write.
    with db.transaction():
        return _gated_write_locked(
            entity,
            action,
            payload,
            direct,
            entity_id,
            summary,
            actor,
        )


def _gated_write_locked(
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
    # an empty update proposal would sail to a reviewer and only fail at
    # apply ("nothing to update") — bounce it on the agent instead. The
    # destructive ALWAYS_REVIEW verbs legitimately carry empty payloads.
    if action == "update" and not payload and entity not in ALWAYS_REVIEW:
        return json.dumps({"error": "nothing to change — pass at least one field"})
    # Same 30/min budget the REST creates use, and keyed on the SAME subject:
    # the human who asked. Keying on the actor alone made it one team-wide
    # bucket, because the default chat identity is the single name "agent" —
    # person B's write was refused because person A was mid-turn, under a
    # message claiming the cap was per person. Keying on the PAIR fixed that
    # and broke the arithmetic instead: agent identity is per persona, so one
    # person addressing a 5-member flock held 5 buckets of 30 against a cap
    # labelled 30 per person, on the path where every write becomes a
    # proposal. The requester is the subject the label already names.
    #
    # Falls back to the actor for MCP and the scheduler, where the agent IS
    # the caller and no human stands behind the write. No separator, no join:
    # a composite key collided the moment a teammate picked the name on the
    # other side of it.
    try:
        ratelimit.check("write", requester_identity() or actor)
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    from ..services import policy_context as domain_policy

    attributes = domain_policy.for_change(entity, entity_id, payload)
    project_type = str(attributes.get("project_type") or "")
    classification = str(attributes.get("classification") or "")
    subject = current_policy_subject()
    resolved_subject = requester_identity() or actor
    if subject.name == "agent" and resolved_subject != "agent":
        subject = PolicySubject(
            resolved_subject,
            kind="human" if requester_identity() else "agent",
        )
    policy_input = PolicyInput(
        subject=subject,
        action=f"{entity}.{action}",
        resource=PolicyResource(
            entity,
            str(entity_id or ""),
            project_type,
            classification,
            attributes,
        ),
        origin="agent",
        agent=actor,
        tool=f"skein.{entity}.{action}",
        tool_effect="write",
        tool_risk="high" if entity in ALWAYS_REVIEW else "medium",
    )
    decision = current_policy_engine().decide(policy_input)
    if decision.effect == PolicyEffect.DENY:
        # actor is passed even though the detail already names it: the live
        # chip composes its own sentence ("forbidden for ...") and needs the
        # name as data, not parsed back out of prose
        receipts.record("refused", entity, f"{actor} is forbidden on {entity}", actor=actor)
        return json.dumps(
            {"error": f"writes to {entity} are forbidden for '{actor}' by the authority matrix"}
        )
    # force_review outranks the matrix and the SKEIN_AGENT_REVIEW flag, and is
    # outranked by forbidden above (a kill switch never softens into a
    # proposal). Without it a flock member that earned `autonomous` writes
    # directly during a fan-out, so ONE consultative human message becomes N
    # unreviewed writes — see docs/FLOCKS.md and agents/identity.py.
    if decision.effect == PolicyEffect.PERMIT:
        try:
            result = direct()
        except ValueError as exc:
            receipts.record("failed", entity, str(exc), actor=actor)
            return json.dumps({"error": str(exc)})
        receipts.record(
            "wrote",
            entity,
            summary or lexicon.phrase(entity, action),
            int(result.get("id") or 0),
            actor=actor,
        )
        if "notify-team" in decision.obligations:
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
            policy_obligations=decision.obligations,
            approver_groups=decision.approver_groups,
            approver_capabilities=decision.approver_capabilities,
            policy_context={
                "input": policy_input_data(policy_input),
                "contract": {
                    "entity": entity,
                    "action": action,
                    "entity_id": entity_id,
                    "payload": payload,
                },
                "approval_fingerprint": approval_fingerprint(
                    policy_input,
                    decision,
                    {
                        "entity": entity,
                        "action": action,
                        "entity_id": entity_id,
                        "payload": payload,
                    },
                ),
            },
        )
    except ValueError as exc:
        receipts.record("failed", entity, str(exc), actor=actor)
        return json.dumps({"error": str(exc)})
    receipts.record(
        "queued",
        entity,
        summary or lexicon.phrase(entity, action),
        int(result.get("id") or 0),
        actor=actor,
    )
    return json.dumps({**result, "note": "queued for human review"})
