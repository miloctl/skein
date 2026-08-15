"""Pending-changes review flow: agents (and cautious humans) propose, humans
approve. Approval applies the payload through the same service registry the
rest of the platform uses, stamped origin='agent_verified'."""

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from .. import config, db
from ..public.errors import PublicError
from . import lexicon, scope


@dataclass(frozen=True)
class _CurrentExtensionReview:
    request: Any
    contract: dict[str, Any] | None = None
    decision: Any | None = None
    fingerprint: str = ""


@dataclass(frozen=True)
class _ApprovalGrant:
    fingerprint: str
    decision: Any


@dataclass(frozen=True)
class _ApprovalFailure:
    error: Exception


def _registry() -> dict:
    from . import (
        absences,
        blockers,
        collab,
        delegation,
        engagements,
        intake,
        memory,
        playbooks,
        promises,
        schedule,
        weekly,
        work,
    )

    return {
        "milestone": {"create": work.create_milestone, "update": work.update_milestone},
        "task": {"create": work.create_task, "update": work.update_task},
        "question": {"create": collab.ask_question, "update": collab.answer_question},
        "question_assign": {"update": collab.assign_question},
        "decision": {"create": collab.record_decision, "update": collab.supersede_decision},
        "standup": {"create": collab.post_standup},
        "note": {"create": collab.save_note},
        "note_edit": {"update": collab.update_note},
        "note_delete": {"update": collab.delete_note},
        "event": {"create": schedule.schedule_event},
        "event_cancel": {"update": schedule.cancel_event},
        "blocker": {"create": blockers.raise_blocker, "update": blockers.resolve_blocker},
        "blocker_edit": {"update": blockers.edit_blocker},
        "engagement": {
            "create": engagements.create_engagement,
            "update": engagements.update_engagement,
        },
        "intake": {"create": intake.submit_request},
        "intake_edit": {"update": intake.edit_request},
        "lesson": {"create": engagements.record_lesson},
        "playbook": {"create": playbooks.instantiate},
        "weekly_plan": {"create": weekly.apply_plan},
        "promise": {
            "create": promises.add_promise,
            "update": promises.update_promise,
        },
        "promise_edit": {"update": promises.edit_promise},
        "promise_settle": {"update": promises.update_promise},
        "memory": {"create": memory.remember},
        "memory_forget": {"update": memory.forget},
        "delegation": {"create": delegation.delegate_task},
        "task_completion": {"update": delegation.accept_completion},
        "authority": {"create": delegation.set_authority},
        "absence": {"create": absences.add_absence},
    }


def unappliable(entity: str, payload: dict) -> str:
    """Why the service would refuse this payload at apply time, or "".

    A proposal is stored now and applied LATER, through the same service the
    REST door uses. A payload the service will refuse becomes a row that can
    only ever be rejected, and the reviewer is told why at the verdict — long
    after whoever wrote it could fix it. Worse, the row is already in the queue
    when the bound ships, so a deploy strands it.

    Only the free-text bounds are checked, which are the ones a pasted line or
    a model can exceed. Every other refusal a service makes (a missing
    milestone, a bad date) is about state that can change between the proposal
    and the verdict, and guessing at it here would drop proposals that WOULD
    have applied.
    """
    from .intake import DETAIL_LEN
    from .work import DESCRIPTION_LEN, TITLE_LEN

    caps = {
        "task": (("title", TITLE_LEN), ("description", DESCRIPTION_LEN)),
        "milestone": (("title", TITLE_LEN), ("description", DESCRIPTION_LEN)),
        "intake": (("detail", DETAIL_LEN),),
    }
    for field, cap in caps.get(entity, ()):
        if len(str(payload.get(field) or "")) > cap:
            return f"{entity} {field} must be {cap} characters or fewer"
    return ""


def propose_change(
    entity: str,
    action: str,
    payload: dict,
    summary: str = "",
    entity_id: int = 0,
    *,
    actor: str = "agent",
    origin: str = "agent",
    notify_team: bool = True,
    requested_by: str = "",
    policy_obligations: tuple[str, ...] = (),
    approver_groups: tuple[str, ...] = (),
    approver_capabilities: tuple[str, ...] = (),
    review_visibility: str = scope.WORKSPACE,
    review_crew_id: int = 0,
    review_owner: str = "",
    policy_context: dict | None = None,
) -> dict:
    """Store a proposal and its notice in one transaction."""
    with db.transaction():
        return _propose_change_locked(
            entity,
            action,
            payload,
            summary,
            entity_id,
            actor=actor,
            origin=origin,
            notify_team=notify_team,
            requested_by=requested_by,
            policy_obligations=policy_obligations,
            approver_groups=approver_groups,
            approver_capabilities=approver_capabilities,
            review_visibility=review_visibility,
            review_crew_id=review_crew_id,
            review_owner=review_owner,
            policy_context=policy_context,
        )


def _propose_change_locked(
    entity: str,
    action: str,
    payload: dict,
    summary: str = "",
    entity_id: int = 0,
    *,
    actor: str = "agent",
    origin: str = "agent",
    notify_team: bool = True,
    requested_by: str = "",
    policy_obligations: tuple[str, ...] = (),
    approver_groups: tuple[str, ...] = (),
    approver_capabilities: tuple[str, ...] = (),
    review_visibility: str = scope.WORKSPACE,
    review_crew_id: int = 0,
    review_owner: str = "",
    policy_context: dict | None = None,
) -> dict:
    reg = _registry()
    extension_entity = (entity, action) in lexicon.REVIEW_ONLY
    if entity not in reg and not extension_entity:
        raise ValueError(f"unknown entity — one of {sorted(reg)}")
    supported = action == "create" if extension_entity else action in reg[entity]
    if action not in ("create", "update") or not supported:
        raise ValueError(f"unsupported action for {entity} — create or update")
    if action == "update" and not entity_id:
        raise ValueError("entity_id required for updates")
    # a proposal a reviewer must read is bounded like any other write —
    # oversized payloads would also fail at apply and wedge in the queue
    if len(json.dumps(payload)) > 20_000:
        raise ValueError("proposal payload too large — keep it under 20k characters")
    if review_visibility not in scope.TIERS:
        raise ValueError("review visibility must be private, crew, or workspace")
    if review_visibility == scope.CREW and not review_crew_id:
        raise ValueError("a crew review must name its crew")
    encoded_policy = json.dumps(policy_context or {})
    if len(encoded_policy) > 20_000:
        raise ValueError("reviewed policy context must be under 20k characters")
    # HERE, not in one producer: the agent gate (tools/_gate.py) and the notes
    # ingester both file proposals, and a guard in either one leaves the other
    # storing rows that can never be approved.
    refusal = unappliable(entity, payload)
    if refusal:
        raise ValueError(refusal)
    pid = db.execute(
        "INSERT INTO pending_changes (entity, entity_id, action, payload, summary,"
        " proposed_by, origin, created_at, requested_by, policy_obligations,"
        " approver_groups, approver_capabilities, review_visibility, review_crew_id,"
        " review_owner, policy_context, review_contract_version)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " RETURNING id",
        (
            entity,
            entity_id or None,
            action,
            json.dumps(payload),
            summary or f"{action} {entity}",
            actor,
            origin,
            db.now(),
            requested_by or None,
            json.dumps(tuple(dict.fromkeys(policy_obligations))),
            json.dumps(tuple(dict.fromkeys(approver_groups))),
            json.dumps(tuple(dict.fromkeys(approver_capabilities))),
            review_visibility,
            review_crew_id or None,
            review_owner,
            encoded_policy,
            1,
        ),
    )
    db.log_activity(
        actor,
        "propose_change",
        f"#{pid} {action} {entity}" + (f" (asked by {requested_by})" if requested_by else ""),
    )
    if notify_team:  # bulk producers (ingestion) send ONE summary instead
        from .notifications import notify

        notify(
            "team",
            (
                lambda source: (
                    f"Review needed: #{pid} {action} {entity} #{source['id']}"
                    if entity_id
                    else f"Review needed: #{pid} {summary or f'{action} {entity}'}"
                )
            ),
            tier="digest",
            link="/review",
            source_entity=entity,
            source_id=entity_id,
            pending_change_id=pid,
        )
    return {"id": pid, "status": "pending"}


def propose_extension_invocation(
    kind: str,
    public_payload: dict,
    invocation: dict,
    *,
    summary: str,
    actor: str,
    requested_by: str,
    policy_obligations: tuple[str, ...] = (),
    approver_groups: tuple[str, ...] = (),
    approver_capabilities: tuple[str, ...] = (),
    review_visibility: str = scope.WORKSPACE,
    review_crew_id: int = 0,
    review_owner: str = "",
    policy_input=None,
) -> dict:
    """Create one review and keep its executable arguments out of the queue."""
    if kind not in ("tool", "workflow", "mcp_tool", "core_tool", "public_command"):
        raise ValueError("extension review kind is not supported")
    stored_invocation = {**invocation, "kind": kind}
    encoded = json.dumps(stored_invocation)
    if len(encoded) > 20_000:
        raise ValueError("reviewed invocation must be under 20k characters")
    policy_context = None
    if policy_input is not None:
        from ..extensions.policy import policy_input_data

        policy_context = {
            "kind": "extension",
            "input": policy_input_data(policy_input),
        }
    with db.transaction():
        proposal = propose_change(
            f"extension_{kind}",
            "create",
            public_payload,
            summary,
            actor=actor,
            origin=(
                "agent" if kind in ("tool", "mcp_tool", "core_tool", "public_command") else "human"
            ),
            requested_by=requested_by,
            policy_obligations=policy_obligations,
            approver_groups=approver_groups,
            approver_capabilities=approver_capabilities,
            review_visibility=review_visibility,
            review_crew_id=review_crew_id,
            review_owner=review_owner,
            policy_context=policy_context,
        )
        db.execute(
            "INSERT INTO extension_review_invocations"
            " (change_id, kind, invocation) VALUES (?, ?, ?)",
            (proposal["id"], kind, encoded),
        )
    return proposal


def _check_reviewer(actor: str) -> None:
    """Verdicts are human work. No tool exposes approve/reject, but the REST
    path resolves any X-User — an agent identity must be refused here too."""
    from .users import is_agent

    if is_agent(actor):
        raise ValueError(f"'{actor}' is an agent identity — proposals are judged by humans")


def _check_separation(change: dict, actor: str) -> None:
    """Refuse an approver who is the reason the proposal exists.

    Approval only. Rejection stays open to everyone qualified, because a rule
    that traps a proposal in the queue is worse than one person declining it.
    Names are folded the way identity_names folds them, so a different case or
    Unicode form is the same person here too.
    """
    if not config.REVIEW_SEPARATION:
        return
    from ..identity_names import fold_identity

    folded = fold_identity(actor)
    originators = {
        fold_identity(str(change.get(column) or "")) for column in ("requested_by", "proposed_by")
    }
    if folded and folded in originators:
        raise PermissionError(
            "This proposal came from you. Separated review duties are on,"
            " so a different qualified person must approve it."
        )


def _check_policy_approver(
    change: dict,
    groups: tuple[str, ...],
    capabilities: tuple[str, ...],
) -> dict[str, list[str]]:
    required_groups = set(json.loads(change.get("approver_groups") or "[]"))
    required_capabilities = set(json.loads(change.get("approver_capabilities") or "[]"))
    missing_groups = required_groups - set(groups)
    missing_capabilities = required_capabilities - set(capabilities)
    if missing_groups or missing_capabilities:
        raise PermissionError("This approval requires the configured workplace approver.")
    return {
        "matched_groups": sorted(required_groups & set(groups)),
        "matched_capabilities": sorted(required_capabilities & set(capabilities)),
    }


def _sponsor_of(change: dict) -> str:
    """The task's CURRENT sponsor for a task_completion proposal ('' for
    everything else) — looked up at verdict time, so a re-delegation moves
    the verdict to the new sponsor."""
    if change["entity"] != "task_completion" or not change["entity_id"]:
        return ""
    task = db.query_one("SELECT sponsor FROM tasks WHERE id = ?", (change["entity_id"],))
    return (task["sponsor"] or "") if task else ""


def _sponsor_override(change: dict, actor: str, note: str) -> str:
    """Acceptance verdicts belong to the sponsor — they judged the work, so
    their verdict is the trust label. Anyone else may still act, but only
    with a reason on record (the sponsor is away, gone, or asked them to),
    and the verdict is marked an override so it never feeds a streak.
    Returns a label for the activity log when this verdict is an override,
    else ''."""
    if change["entity"] != "task_completion":
        return ""
    sponsor = _sponsor_of(change)
    if sponsor and actor == sponsor:
        return ""
    if not sponsor:
        # reassignment cleared the delegation: nobody sponsors this proposal
        # anymore, so NO verdict on it is a trust signal — reason required
        if not note.strip():
            raise ValueError(
                f"task #{change['entity_id']}'s delegation was cleared —"
                " judging this orphaned acceptance needs a note saying why"
            )
        return "orphaned delegation"
    if not note.strip():
        raise ValueError(
            f"task #{change['entity_id']} is sponsored by {sponsor} — acting"
            " for them needs a note saying why (it goes on the record)"
        )
    return sponsor


def _claim(
    change_id: int,
    new_status: str,
    note: str,
    actor: str,
    strong: bool = False,
    override: bool = False,
) -> None:
    """Compare-and-swap the pending -> reviewed transition so concurrent
    approve/reject calls can't both act on the same change."""
    claimed = db.execute_rowcount(
        "UPDATE pending_changes SET status = ?, reviewed_by = ?, review_note = ?,"
        " reviewed_at = ?, reviewed_strong = ?, reviewed_override = ?"
        " WHERE id = ? AND status = 'pending'",
        (new_status, actor, note, db.now(), int(strong), int(override), change_id),
    )
    if not claimed:
        change = db.query_one("SELECT status FROM pending_changes WHERE id = ?", (change_id,))
        if not change:
            raise db.NotFound(f"pending change #{change_id} not found")
        raise ValueError(f"change #{change_id} already {change['status']}")


def _assert_judgeable(change: dict, viewer: scope.Viewer) -> None:
    """A verdict on a row you cannot read is refused, in the same words as a
    proposal that does not exist.

    `_readable` hides these from the queue and `change_diff` returns no diff,
    but the VERDICT endpoints took a bare id — so a caller in no crew could
    walk ids and approve a proposal whose payload then overwrote a crew note's
    content, or applied a note_delete / memory_forget / event_cancel. The
    apply runs as `change["proposed_by"]`, an agent slug, and scope.is_machine
    lets a machine work a crew row on purpose, so assert_editable inside the
    handler never refuses it. This is the only place that can.

    NotFound with the same sentence as an absent proposal: any other wording
    tells the caller that #12 exists and is scoped (scope.missing).
    """
    tier = _governing_tier(change)
    # a VANISHED target is not refused here, unlike _readable: there is no row
    # left to protect, and approve_change has its own auto-reject path for it
    # ("target vanished") that a 404 would hide behind the wrong sentence.
    # _readable still drops it from the LIST, where the summary would show.
    if not isinstance(tier, tuple):  # None (unscoped) or "gone" (deleted)
        return
    if not scope.can_read(tier[0], tier[1], viewer, tier[2]):
        raise db.NotFound(f"pending change #{change['id']} not found")


def approve_change(
    change_id: int,
    note: str = "",
    *,
    actor: str = "system",
    strong: bool = False,
    viewer: scope.Viewer = scope.NOBODY,
    reviewer_groups: tuple[str, ...] = (),
    reviewer_capabilities: tuple[str, ...] = (),
    extension_executor: Callable[[dict, int], dict] | None = None,
    policy_registry=None,
) -> dict:
    # Serialize current-state policy resolution, the verdict claim, and the
    # resulting write, so a project relink or a policy-relevant state change
    # cannot race a durable approval. Nested service transactions join this
    # one. The boundary is the transaction plus the target-row hold that
    # _approve_change_locked takes once it knows what the proposal is about
    # (services/policy_context.py::hold_resource) — a read alone locks
    # nothing.
    with db.transaction():
        result = _approve_change_locked(
            change_id,
            note,
            actor=actor,
            strong=strong,
            viewer=viewer,
            reviewer_groups=reviewer_groups,
            reviewer_capabilities=reviewer_capabilities,
            extension_executor=extension_executor,
            policy_registry=policy_registry,
        )
    if isinstance(result, _ApprovalFailure):
        raise result.error
    return result


def _approve_change_locked(
    change_id: int,
    note: str = "",
    *,
    actor: str = "system",
    strong: bool = False,
    viewer: scope.Viewer = scope.NOBODY,
    reviewer_groups: tuple[str, ...] = (),
    reviewer_capabilities: tuple[str, ...] = (),
    extension_executor: Callable[[dict, int], dict] | None = None,
    policy_registry=None,
) -> dict | _ApprovalFailure:
    _check_reviewer(actor)
    change = db.query_one("SELECT * FROM pending_changes WHERE id = ?", (change_id,))
    if not change:
        raise db.NotFound(f"pending change #{change_id} not found")
    # Hold the row the verdict is ABOUT, now that the proposal says which one.
    # The policy is re-evaluated below against that row's current state, and a
    # relink landing between the two would settle the proposal under a rule
    # chosen for the project it used to belong to.
    from . import policy_context

    if change.get("source_id"):
        policy_context.hold_resource(str(change["source_entity"]), int(change["source_id"]))
    _assert_judgeable(change, viewer)
    # settle the already-reviewed case before any gating, so a non-sponsor
    # isn't told to fetch a note for a verdict that already happened
    if change["status"] != "pending":
        raise ValueError(f"change #{change_id} already {change['status']}")
    approval_grant = _revalidate_policy(
        change,
        policy_registry,
        reviewer_groups,
        reviewer_capabilities,
    )
    _check_separation(change, actor)
    qualifications: dict[str, Any] = _check_policy_approver(
        change,
        reviewer_groups,
        reviewer_capabilities,
    )
    # the direct authority endpoint requires a personal key; the proposal
    # path must not be the weaker door to the same lever
    if change["entity"] == "authority" and not strong:
        raise ValueError(
            "authority changes need a strong identity — approve with your personal API key"
        )

    # resolve the handler BEFORE claiming — a stale entity/action must not
    # leave the row marked approved with nothing applied
    is_extension = (change["entity"], change["action"]) in lexicon.REVIEW_ONLY
    executor = extension_executor
    if is_extension:
        if executor is None:
            raise ValueError("this extension review needs the composed application executor")
        stored = db.query_one(
            "SELECT * FROM extension_review_invocations WHERE change_id = ?",
            (change_id,),
        )
        if stored is None:
            raise ValueError("the reviewed extension invocation is missing")
        invocation = json.loads(stored["invocation"])
        if approval_grant is not None:
            from ..extensions.policy import policy_decision_data

            invocation["_approval_grant"] = approval_grant.fingerprint
            invocation["_approval_decision"] = policy_decision_data(approval_grant.decision)
        fn = None
    else:
        try:
            fn = _registry()[change["entity"]][change["action"]]
        except KeyError as exc:
            raise ValueError(f"no handler for {change['entity']}.{change['action']}") from exc
    payload = json.loads(change["payload"])
    sponsor = _sponsor_override(change, actor, note)
    _claim(change_id, "approved", note, actor, strong, override=bool(sponsor))
    db.execute(
        "UPDATE pending_changes SET reviewer_qualifications = ? WHERE id = ?",
        (json.dumps(qualifications), change_id),
    )
    try:
        # The verdict claim belongs to the outer transaction. The apply gets a
        # savepoint of its own. If it fails, roll back its partial domain writes
        # before the handler records a durable pending or rejected settlement.
        with db.savepoint():
            if is_extension:
                if executor is None:
                    raise ValueError("the extension review executor is missing")
                result = executor(invocation, change_id)
                db.execute(
                    "UPDATE extension_review_invocations SET status = 'approved', result = ?,"
                    " error_code = ?, executed_at = ? WHERE change_id = ?",
                    (
                        json.dumps(result),
                        str(result.get("error_code") or ""),
                        db.now(),
                        change_id,
                    ),
                )
            else:
                # Compound applies (playbook, weekly_plan) land atomically or
                # not at all. A failed apply returns safely to the review queue.
                if fn is None:
                    raise ValueError("the core review handler is missing")
                # authorship stays with the proposer: created_by must say who
                # wrote it, not who clicked approve (the verdict is recorded on
                # the pending_changes row + activity)
                author = change["proposed_by"] or actor
                if change["action"] == "update":
                    result = fn(
                        change["entity_id"], **payload, actor=author, origin="agent_verified"
                    )
                else:
                    result = fn(**payload, actor=author, origin="agent_verified")
    except db.NotFound as exc:
        # the proposal's own target vanished (event cancelled via REST, row
        # hard-deleted): re-approving can never succeed, so a pending reset
        # would boomerang forever — settle it as rejected, on the record
        # reviewed_strong cleared for the same reason as the terminal branch
        # below: nobody judged this work, so it must not reach the streak.
        db.execute(
            "UPDATE pending_changes SET status = 'rejected', reviewed_strong = 0,"
            " review_note = ? WHERE id = ?",
            (f"auto-rejected — target vanished: {exc}", change_id),
        )
        db.log_activity(actor, "reject_change", f"#{change_id} (target vanished)")
        _clear_review_ping(change_id)
        return _ApprovalFailure(
            ValueError(
                f"could not apply {change['entity']}.{change['action']}: {exc}"
                " — proposal auto-rejected (its target no longer exists)"
            )
        )
    except db.TerminalReject as exc:
        # a permanent policy block (an agent's own delegated-done proposal):
        # re-approving can never succeed, so settle it rejected like a vanished
        # target instead of resetting to pending, where it would clutter the
        # queue until a human rejected it by hand
        # reviewed_strong is cleared with it. `_claim` stamped the reviewer's
        # own strength a moment ago, and the demotion streak counts consecutive
        # strong non-override rejections (services/delegation.py::trust_scores)
        # — so an automatic settle of a moot proposal would read as a human
        # judging the agent's work badly, and walk it down a rung for work
        # nobody rejected.
        db.execute(
            "UPDATE pending_changes SET status = 'rejected', reviewed_strong = 0,"
            " review_note = ? WHERE id = ?",
            (f"auto-rejected — {exc}", change_id),
        )
        db.log_activity(actor, "reject_change", f"#{change_id} (not applicable)")
        _clear_review_ping(change_id)
        return _ApprovalFailure(ValueError(f"could not apply and auto-rejected: {exc}"))
    except Exception as exc:
        # ANY OTHER failure (IntegrityError, lock timeout, stale state)
        # resets the claim — an approved-but-never-applied proposal would
        # vanish from the queue. The reviewer's note survives the reset.
        db.execute(
            "UPDATE pending_changes SET status = 'pending', reviewed_by = NULL,"
            " reviewed_at = NULL, reviewed_strong = 0, reviewed_override = 0,"
            " reviewer_qualifications = '{}', review_note = ? WHERE id = ?",
            (f"apply failed: {exc}" + (f" (reviewer note: {note})" if note else ""), change_id),
        )
        if is_extension:
            db.execute(
                "UPDATE extension_review_invocations SET status = 'pending',"
                " error_code = 'EXECUTION_FAILED' WHERE change_id = ?",
                (change_id,),
            )
        return _ApprovalFailure(
            ValueError(f"could not apply {change['entity']}.{change['action']}: {exc}")
        )

    db.execute(
        "UPDATE pending_changes SET result_id = ? WHERE id = ?", (result.get("id"), change_id)
    )
    applied = f"#{result['id']}" if result.get("id") is not None else "applied"
    db.log_activity(
        actor,
        "approve_change",
        f"#{change_id} -> {change['entity']} {applied}"
        + (f" (accepted for {sponsor})" if sponsor else ""),
    )
    _clear_review_ping(change_id)
    return {"id": change_id, "status": "approved", "result": result}


def _revalidate_policy(
    change: dict,
    registry,
    reviewer_groups: tuple[str, ...],
    reviewer_capabilities: tuple[str, ...],
    *,
    approving: bool = True,
) -> _ApprovalGrant | None:
    """Refresh and re-evaluate the policy that created a core proposal."""
    saved = json.loads(change.get("policy_context") or "{}")
    if not saved:
        if approving and _legacy_review_needs_binding(change, registry):
            raise PermissionError(
                "The reviewed action has no policy binding. Request a new review."
            )
        return None
    if registry is None:
        raise PermissionError("The reviewed policy cannot be refreshed.")
    policy_data = saved.get("input")
    if not isinstance(policy_data, dict):
        raise PermissionError("The reviewed policy context is invalid.")
    if saved.get("kind") == "extension":
        subject_data = policy_data.get("subject")
        if not isinstance(subject_data, dict):
            raise PermissionError("The reviewed requester identity is invalid.")
        from ..extensions.policy import (
            PolicyEffect,
            policy_input_from_data,
            policy_subject_from_data,
        )

        saved_subject = policy_subject_from_data(subject_data)
        # Rejection never executes the invocation. Resolve the executable
        # contract before refreshing its requester so removal of an entire
        # workplace module (including its directory resolver) cannot strand a
        # durable proposal forever. Approval still requires a current
        # directory identity and remains closed.
        if not approving:
            saved_current = policy_input_from_data(policy_data, saved_subject)
            try:
                _current_extension_review(
                    change,
                    registry,
                    saved_current,
                    approving=False,
                )
            except (KeyError, TypeError, ValueError, PermissionError, PublicError):
                change["approver_groups"] = "[]"
                change["approver_capabilities"] = "[]"
                change["_stale_contract"] = True
                return None
        subject = registry.refresh_subject(saved_subject)
        current = policy_input_from_data(policy_data, subject)
        try:
            state = _current_extension_review(
                change,
                registry,
                current,
                approving=approving,
            )
        except PermissionError:
            if approving:
                raise
            change["approver_groups"] = "[]"
            change["approver_capabilities"] = "[]"
            change["_stale_contract"] = True
            return None
        except (KeyError, TypeError, ValueError, PublicError) as exc:
            if approving:
                raise PermissionError(
                    "The reviewed extension contract cannot be refreshed. Request a new review."
                ) from exc
            change["approver_groups"] = "[]"
            change["approver_capabilities"] = "[]"
            change["_stale_contract"] = True
            return None
        decision = state.decision or registry.policy_engine.decide(state.request)
        if decision.effect == PolicyEffect.DENY and approving:
            raise PermissionError("The current workplace policy denies this reviewed action.")
        # The next qualification check must use current requirements. Reusing
        # the proposal-time group would let a removed group reject work or
        # require a reviewer to hold both the old and new grants.
        change["approver_groups"] = json.dumps(
            decision.approver_groups if decision.effect == PolicyEffect.REVIEW else ()
        )
        change["approver_capabilities"] = json.dumps(
            decision.approver_capabilities if decision.effect == PolicyEffect.REVIEW else ()
        )
        if not approving:
            return None
        if state.fingerprint:
            return _ApprovalGrant(state.fingerprint, decision)
        if state.contract is None:
            return None
        from ..extensions.policy import approval_fingerprint

        return _ApprovalGrant(
            approval_fingerprint(state.request, decision, state.contract),
            decision,
        )
    contract = saved.get("contract")
    if not isinstance(contract, dict):
        raise PermissionError("The reviewed policy context is invalid.")
    expected = {
        "entity": change["entity"],
        "action": change["action"],
        "entity_id": int(change.get("entity_id") or 0),
        "payload": json.loads(change["payload"]),
    }
    if contract != expected:
        raise PermissionError("The reviewed action no longer matches its policy context.")
    subject_data = policy_data.get("subject")
    if not isinstance(subject_data, dict):
        raise PermissionError("The reviewed requester identity is invalid.")
    from ..extensions.policy import (
        PolicyEffect,
        policy_input_from_data,
        policy_subject_from_data,
    )

    saved_subject = policy_subject_from_data(subject_data)
    subject = registry.refresh_subject(saved_subject)
    current = policy_input_from_data(policy_data, subject)
    from ..extensions.policy import PolicyResource
    from . import policy_context as domain_policy

    actual = domain_policy.for_change(
        change["entity"],
        int(change.get("entity_id") or 0),
        expected["payload"],
        actor=str(current.agent or subject.name),
    )
    if change["entity"] == "playbook":
        from . import playbooks

        expected_digest = str(expected["payload"].get("expected_definition_digest") or "")
        if not expected_digest and approving:
            raise PermissionError(
                "The reviewed playbook has no content digest. Request a new review."
            )
        if expected_digest and approving:
            slug = str(expected["payload"].get("slug") or "")
            try:
                definition = playbooks.get_playbook(slug)
            except ValueError as exc:
                raise PermissionError(
                    "The reviewed playbook is no longer available. Request a new review."
                ) from exc
            if not playbooks.definition_digest_matches(expected_digest, definition):
                raise PermissionError("The reviewed playbook changed. Request a new review.")
    current = replace(
        current,
        resource=PolicyResource(
            current.resource.type,
            current.resource.id,
            str(actual.get("project_type") or ""),
            str(actual.get("classification") or ""),
            actual,
        ),
    )
    decision = registry.policy_engine.decide(current)
    if decision.effect == PolicyEffect.DENY and approving:
        raise PermissionError("The current workplace policy denies this reviewed action.")
    change["approver_groups"] = json.dumps(
        decision.approver_groups if decision.effect == PolicyEffect.REVIEW else ()
    )
    change["approver_capabilities"] = json.dumps(
        decision.approver_capabilities if decision.effect == PolicyEffect.REVIEW else ()
    )
    return None


def _legacy_review_needs_binding(change: dict, registry) -> bool:
    """Return true when an old agent review reaches a composed policy boundary."""
    if registry is None:
        return False
    if int(change.get("review_contract_version") or 0) >= 1:
        return False
    return (change["entity"], change["action"]) in lexicon.REVIEW_ONLY or change.get(
        "origin"
    ) == "agent"


def _current_extension_review(
    change: dict,
    registry,
    current,
    *,
    approving: bool,
) -> _CurrentExtensionReview:
    """Resolve mutable extension resources again before either verdict."""
    stored = db.query_one(
        "SELECT kind, invocation FROM extension_review_invocations WHERE change_id = ?",
        (change["id"],),
    )
    if stored is None:
        raise PermissionError("The reviewed extension invocation is missing.")
    try:
        invocation = json.loads(stored["invocation"])
    except (TypeError, ValueError) as exc:
        raise PermissionError("The reviewed extension invocation is invalid.") from exc
    kind = str(stored["kind"] or "")
    if kind == "core_tool":
        from ..agents.core_tools import reviewed_policy_input

        request = reviewed_policy_input(invocation, current.subject)
        tool_use = invocation.get("tool_use")
        if not isinstance(tool_use, dict):
            raise ValueError("the reviewed stock tool call is invalid")
        return _CurrentExtensionReview(
            request,
            {
                "tool": str(invocation.get("tool") or ""),
                "input": tool_use.get("input") or {},
            },
        )
    if kind == "tool":
        tool = registry.tool(str(invocation.get("tool") or ""))
        if invocation.get("version") != tool.version and approving:
            raise PermissionError("The reviewed tool contract changed.")
        arguments = invocation.get("arguments")
        if not isinstance(arguments, dict):
            raise PermissionError("The reviewed tool arguments are invalid.")
        try:
            validated = tool.input_schema.model_validate(arguments)
            resource = tool.resource(validated) if tool.resource else current.resource
        except (TypeError, ValueError) as exc:
            raise PermissionError("The reviewed tool resource cannot be refreshed.") from exc
        request = replace(
            current,
            action=tool.policy_action,
            resource=resource,
            tool=tool.name,
            tool_effect=tool.effect,
            tool_risk=tool.risk,
        )
        return _CurrentExtensionReview(
            request,
            {
                "tool": tool.name,
                "version": tool.version,
                "arguments": validated.model_dump(mode="json"),
            },
        )
    if kind == "public_command":
        # The saved policy input already names the command, its resource, and
        # the project the write lands in. Re-decide against the CURRENT
        # subject so a group the reviewer lost since the proposal counts.
        return _CurrentExtensionReview(current)
    if kind == "mcp_tool":
        from ..agents.mcp_tools import reviewed_policy_contract

        request, contract, version_matches = reviewed_policy_contract(
            invocation,
            current.subject,
        )
        if not version_matches and approving:
            raise PermissionError("The reviewed remote tool contract changed.")
        return _CurrentExtensionReview(request, contract)
    if kind == "workflow":
        from . import playbooks

        slug = str(invocation.get("playbook") or "")
        expected = str(invocation.get("definition_digest") or "")
        if not expected and approving:
            raise PermissionError(
                "The reviewed playbook has no content digest. Request a new review."
            )
        definition = playbooks.get_playbook(slug)
        if expected and not playbooks.definition_digest_matches(expected, definition) and approving:
            raise PermissionError("The reviewed playbook changed. Request a new review.")
        project_type = str(definition.get("project_class") or slug)
        resource = replace(current.resource, project_type=project_type)
        request = replace(current, resource=resource)
        if invocation.get("workflow_kind") == "playbook_policy":
            return _CurrentExtensionReview(request)
        raw_workflow = definition.get("workflow")
        if raw_workflow is None:
            raise ValueError("the reviewed playbook no longer has a workflow")
        from ..public.workflow import WorkflowEngine, _issue_workflow_context

        engine = WorkflowEngine(registry.workflow_actions, registry.policy_engine)
        steps = engine.prepare(raw_workflow)
        context = _issue_workflow_context(
            engine,
            current.subject,
            str(invocation.get("origin") or "human"),
            project_type=project_type,
            resource_id=str(invocation.get("resource_id") or ""),
            run_id=str(invocation.get("run_id") or ""),
            values={
                **dict(invocation.get("values") or {}),
                "project_type": project_type,
            },
            approval_grants=dict(invocation.get("approval_grants") or {}),
        )
        review_key = str(invocation.get("reviewed_key") or "")
        workflow_request, decision, fingerprint = engine.current_review(
            steps,
            context,
            review_key,
        )
        return _CurrentExtensionReview(
            workflow_request,
            decision=decision,
            fingerprint=fingerprint,
        )
    return _CurrentExtensionReview(current)


def _clear_review_ping(change_id: int) -> None:
    """The review is handled — its "Review needed" ping must not keep
    nagging. Called AFTER the apply succeeds (a failed apply resets the
    proposal to pending and must keep its notification unread)."""
    from .notifications import mark_pending_change_read, mark_read_matching

    if mark_pending_change_read(change_id) == 0:
        # Rows from before 002_link_review_notifications.sql have no typed
        # link. Without this fallback, resolving one leaves its notification
        # unread forever.
        mark_read_matching(f"Review needed: #{change_id} ")


def reject_change(
    change_id: int,
    note: str = "",
    *,
    actor: str = "system",
    strong: bool = False,
    viewer: scope.Viewer = scope.NOBODY,
    reviewer_groups: tuple[str, ...] = (),
    reviewer_capabilities: tuple[str, ...] = (),
    policy_registry=None,
) -> dict:
    # Rejection is a durable policy verdict, settled by the same CAS approval
    # uses (_claim, UPDATE ... WHERE status = 'pending'), so two reviewers
    # cannot both settle one change.
    #
    # It does NOT hold the target row the way approve_change does: rejection
    # writes nothing to the target, so a relink landing mid-decision can only
    # record the refusal against a stale policy domain, never mutate the
    # wrong row. Add hold_resource here if a rejection ever gains a target
    # write.
    with db.transaction():
        return _reject_change_locked(
            change_id,
            note,
            actor=actor,
            strong=strong,
            viewer=viewer,
            reviewer_groups=reviewer_groups,
            reviewer_capabilities=reviewer_capabilities,
            policy_registry=policy_registry,
        )


def _reject_change_locked(
    change_id: int,
    note: str = "",
    *,
    actor: str = "system",
    strong: bool = False,
    viewer: scope.Viewer = scope.NOBODY,
    reviewer_groups: tuple[str, ...] = (),
    reviewer_capabilities: tuple[str, ...] = (),
    policy_registry=None,
) -> dict:
    _check_reviewer(actor)
    change = db.query_one("SELECT * FROM pending_changes WHERE id = ?", (change_id,))
    if not change:
        raise db.NotFound(f"pending change #{change_id} not found")
    # a reject is a verdict too: it feeds rejection streaks and demotion, and
    # it settles a proposal against a row this caller cannot read
    _assert_judgeable(change, viewer)
    if change["status"] != "pending":
        raise ValueError(f"change #{change_id} already {change['status']}")
    _revalidate_policy(
        change,
        policy_registry,
        reviewer_groups,
        reviewer_capabilities,
        approving=False,
    )
    qualifications: dict[str, Any] = _check_policy_approver(
        change,
        reviewer_groups,
        reviewer_capabilities,
    )
    if change.get("_stale_contract"):
        qualifications["stale_contract"] = True
    # symmetric with approve: a non-sponsor reject feeds rejection streaks
    # (demotion input), so it needs the same reason-on-record
    sponsor = _sponsor_override(change, actor, note)
    _claim(change_id, "rejected", note, actor, strong, override=bool(sponsor))
    db.execute(
        "UPDATE pending_changes SET reviewer_qualifications = ? WHERE id = ?",
        (json.dumps(qualifications), change_id),
    )
    if any(change["entity"] == entity for entity, _action in lexicon.REVIEW_ONLY):
        db.execute(
            "UPDATE extension_review_invocations SET status = 'rejected', executed_at = ?"
            " WHERE change_id = ?",
            (db.now(), change_id),
        )
    db.log_activity(
        actor,
        "reject_change",
        f"#{change_id}" + (f" (rejected for {sponsor})" if sponsor else ""),
    )
    _clear_review_ping(change_id)
    return {"id": change_id, "status": "rejected"}


_DIFF_TABLES = {
    "task": "tasks",
    "milestone": "milestones",
    "engagement": "engagements",
    "promise": "promises",
    "promise_edit": "promises",
    "promise_settle": "promises",
    "blocker": "blockers",
    "blocker_edit": "blockers",
    "question": "questions",
    "decision": "decisions",
    "note_edit": "notes",
    "note_delete": "notes",
    "event_cancel": "events",
    "intake_edit": "intake_requests",
    "memory_forget": "memories",
}

# What table an entity's proposal TARGETS. A superset of _DIFF_TABLES, which
# answers a narrower question (what can be rendered as a before/after) and so
# omits the two entities whose payload is not a column set: task_completion
# carries a free-text work summary, question_assign carries an assignee.
# _readable needs the TARGET, and reading it off _DIFF_TABLES let both of
# those bypass the tier check entirely — every reader saw a crew task's
# acceptance payload in the review queue, on the dashboard, and in the stats.
_TARGET_TABLE = {
    **_DIFF_TABLES,
    # updates _DIFF_TABLES cannot render (their payload is not a column set)
    "task_completion": "tasks",
    "question_assign": "questions",
    # every CREATE. These have no target row yet, so _DIFF_TABLES never needed
    # them — but the tier the row WOULD take is in the payload, and without an
    # entry here _readable had nothing to look up and kept the proposal, body
    # and all, for every reader.
    "task": "tasks",
    "milestone": "milestones",
    "question": "questions",
    "decision": "decisions",
    "standup": "standups",
    "note": "notes",
    "event": "events",
    "blocker": "blockers",
    "engagement": "engagements",
    "intake": "intake_requests",
    "lesson": "lessons",
    "promise": "promises",
    "memory": "memories",
    "absence": "absences",
    "delegation": "tasks",
}

# A CREATE whose subject is a row that ALREADY EXISTS names it in the payload.
# `delegation` is the case: delegate_task(task_id=...) changes a task that has
# its own tier, so there is nothing to declare in the payload and nothing in
# `entity_id`, which propose_change only requires for updates. Reading neither,
# such a proposal was shown to every reader AND judgeable by them — a
# non-member approved a delegation of a crew task they cannot see.
_CREATE_PARENT = {"delegation": ("tasks", "task_id")}

# Entities that address no scoped row at all, and why. Kept as an explicit
# list rather than an absence, so tests/test_review.py can prove _registry
# gained nothing that silently skips the tier check.
_UNTARGETED = {
    "playbook": "instantiates a whole engagement tree, no single target row",
    "weekly_plan": "commits a set of task ids, each already tier-checked on its own",
    "authority": "an agent's permission matrix, which carries no tier",
}

# columns worth showing a reviewer when a proposal would DESTROY the row —
# an empty payload must never mean an uninformed verdict
_DESTRUCTIVE_VIEW = {
    "note_delete": ("topic", "content"),
    "memory_forget": ("topic", "content", "user"),
    "event_cancel": ("title", "starts_at", "attendees"),
}


def change_diff(change_id: int, viewer: scope.Viewer = scope.NOBODY) -> dict:
    """Before/after view for update proposals: current row values for exactly
    the fields the payload would change.

    Filtered on the TARGET row, not on the proposal. `pending_changes` carries
    no tier of its own (scope.UNSCOPED) and this endpoint is CurrentUser, so
    an unfiltered read here handed any reader the current body of a private
    note or memory verbatim — _DESTRUCTIVE_VIEW renders `content` and `topic`
    for exactly the two entities whose deletion always files a proposal.
    A reader who cannot see the row gets no diff at all: the proposed half is
    the payload, which for an edit holds the new body.
    """
    change = db.query_one("SELECT * FROM pending_changes WHERE id = ?", (change_id,))
    if not change:
        raise db.NotFound(f"pending change #{change_id} not found")
    table = _DIFF_TABLES.get(change["entity"])
    if change["action"] != "update" or not table or not change["entity_id"]:
        return {"id": change_id, "diff": None}
    frag, fp = scope.visible_filter(viewer, table) if table in scope.CLASSIFIED else ("1 = 1", [])
    row = db.query_one(
        f"SELECT * FROM {table} WHERE id = ? AND {frag}",  # noqa: S608 — table from constant map, and scope.visible_filter emits only bound marks
        (change["entity_id"], *fp),
    )
    if not row:
        return {"id": change_id, "diff": None}
    payload = json.loads(change["payload"])
    doomed = _DESTRUCTIVE_VIEW.get(change["entity"])
    if doomed:
        # deletion diff: show what would be destroyed; proposed side is empty
        current = {k: (row.get(k) if row else None) for k in doomed}
        return {
            "id": change_id,
            "diff": {"current": current, "proposed": dict.fromkeys(doomed, "")},
        }
    current = {k: (row.get(k) if row else None) for k in payload}
    return {"id": change_id, "diff": {"current": current, "proposed": payload}}


def mark_seen(ids: list[int], *, actor: str = "system") -> dict:
    """The review UI calls this when a human loads pending proposals —
    first-seen (claim_at) starts the active-review clock, so review burden
    can be measured as seen→verdict instead of created→verdict (which is
    dominated by queue wait, not human effort)."""
    batch = ids[:200]
    if not batch:
        return {"seen": 0}
    marks = ", ".join("?" for _ in batch)
    n = db.execute_rowcount(
        f"UPDATE pending_changes SET claim_at = ?"  # noqa: S608 — placeholders built above
        f" WHERE id IN ({marks}) AND status = 'pending' AND claim_at IS NULL",
        (db.now(), *batch),
    )
    return {"seen": n}


def review_stats(viewer: scope.Viewer = scope.NOBODY) -> dict:
    """The review inbox as a flywheel: every verdict is a labeled example.
    These stats show which proposal types earn trust and which waste reviewer
    time — the input to authority-matrix decisions."""
    by_entity = db.query(
        "SELECT entity,"
        " COUNT(*) AS proposed,"
        " COUNT(*) FILTER (WHERE status = 'approved') AS approved,"
        " COUNT(*) FILTER (WHERE status = 'rejected') AS rejected,"
        " COUNT(*) FILTER (WHERE status = 'pending') AS pending,"
        " ROUND(AVG(CASE WHEN reviewed_at IS NOT NULL THEN"
        " (EXTRACT(epoch FROM reviewed_at::timestamptz - created_at::timestamptz) / 86400.0) * 24 END)::numeric, 1)"
        " AS avg_review_hours"
        " FROM pending_changes GROUP BY entity ORDER BY proposed DESC"
    )
    by_proposer = db.query(
        "SELECT proposed_by,"
        " COUNT(*) AS proposed,"
        " COUNT(*) FILTER (WHERE status = 'approved') AS approved,"
        " COUNT(*) FILTER (WHERE status = 'rejected') AS rejected"
        " FROM pending_changes GROUP BY proposed_by ORDER BY proposed DESC"
    )
    from . import users

    by_proposer = [row for row in by_proposer if users.is_agent(row["proposed_by"])]
    # the only list here that carries row TEXT. The aggregates above count
    # rows per entity and per proposer, which discloses nothing; `summary` is
    # built by the producer out of the target row's own title, so a rejected
    # proposal against a crew note republished it to the whole roster.
    rejection_reasons = _readable(
        db.query(
            "SELECT entity, entity_id, summary, review_note, reviewed_by,"
            " review_visibility, review_crew_id, review_owner FROM pending_changes"
            " WHERE status = 'rejected' AND review_note != '' ORDER BY id DESC LIMIT 20"
        ),
        viewer,
    )
    minutes = sorted(
        r["m"]
        for r in db.query(
            "SELECT (EXTRACT(epoch FROM reviewed_at::timestamptz - claim_at::timestamptz) / 86400.0) * 24 * 60 AS m"
            " FROM pending_changes WHERE reviewed_at IS NOT NULL AND claim_at IS NOT NULL"
        )
        if r["m"] is not None
    )
    # the shared primitive, not a third inline copy — services/stats.py exists
    # because two of these had already drifted apart
    from . import stats

    n = len(minutes)
    median = stats.median(minutes)
    return {
        "by_entity": by_entity,
        "by_proposer": by_proposer,
        "recent_rejections": rejection_reasons,
        # medians over means (docs/INSIGHTS.md contract)
        "active_review_minutes": {"median": median, "n": n},
    }


def _governing_tier(change: dict) -> tuple[str, int | None, str] | str | None:
    """The tier that decides who may see or judge one proposal.

    Returns `(visibility, crew_id, author)`, the string "gone" when the row it
    addresses has been deleted, or None when it addresses no scoped row at all
    (weekly_plan, authority, playbook).

    ONE resolver, because `_readable` and `_assert_judgeable` each grew their
    own and they disagreed: the list hid a create while the verdict endpoints
    applied it. A proposal that is invisible must not be approvable, and the
    only way to keep that true is for both to ask the same question.

    Three sources, in order: the row `entity_id` names (updates), the row the
    payload names (_CREATE_PARENT), then the tier the payload declares.
    """
    if any(change["entity"] == entity for entity, _action in lexicon.REVIEW_ONLY):
        return (
            str(change.get("review_visibility") or scope.WORKSPACE),
            change.get("review_crew_id"),
            str(change.get("review_owner") or change.get("requested_by") or ""),
        )
    table = _TARGET_TABLE.get(change["entity"])
    if table not in scope.CLASSIFIED:
        return None
    try:
        payload = json.loads(change["payload"]) if change.get("payload") else {}
    except (TypeError, ValueError):
        payload = {}

    row_id = change["entity_id"]
    if not row_id and change["entity"] in _CREATE_PARENT:
        table, key = _CREATE_PARENT[change["entity"]]
        row_id = payload.get(key)
    if row_id:
        author = scope.CLASSIFIED[table]
        row = db.query_one(
            f"SELECT visibility, crew_id, {author} AS author FROM {table} WHERE id = ?",  # noqa: S608 — table and column from constant maps
            (row_id,),
        )
        return (row["visibility"], row["crew_id"], row["author"] or "") if row else "gone"

    # a create with no parent row: the tier it WOULD land at is declared here.
    # Absent because the caller chose none, and absent because the caller never
    # selected the payload column — both mean workspace, and neither can
    # disclose a body the reader is not already being shown.
    crew = payload.get("crew_id")
    return (
        str(payload.get("visibility") or scope.WORKSPACE),
        crew if isinstance(crew, int) else None,
        str(payload.get("author") or ""),
    )


def _readable(rows: list[dict], viewer: scope.Viewer) -> list[dict]:
    """Drop proposals whose target row the viewer may not read.

    `pending_changes` carries no tier of its own (scope.UNSCOPED) and this
    list returns the whole row — `payload` parsed, and a `summary` that four
    producers build out of the row's own text (delegation.submit_completion,
    tools/collab.py::delete_note, tools/memory.py::forget_memory,
    tools/schedule.py::cancel_event). Served unfiltered to every CurrentUser,
    the review queue is a full-text mirror of every scoped row somebody
    proposed a change to.

    Dropped, not blanked: a reviewer who cannot read the row cannot judge the
    change, so a redacted entry is a verdict taken blind. The crew keeps
    seeing its own.

    An entity with no tier at all (weekly_plan, authority, playbook) stays —
    those carry no row to be scoped by, and defaulting them out would empty
    the queue the whole review flow runs on.

    Eight readers quote `summary` or `payload` out of this table and each has
    to call this explicitly — GET /api/review, briefing.my_day,
    delegation.agent_inbox, review_stats, the handoff, the week-close ritual,
    and the two insights rules that write into findings.receipt. They do not
    arrive here on their own.
    """
    out = []
    for r in rows:
        tier = _governing_tier(r)
        if tier is None:
            # no scoped row to be judged by (weekly_plan, authority, playbook).
            # Keeping these is what makes the queue usable at all.
            out.append(r)
        elif not isinstance(tier, tuple):
            # "gone": the target row is DELETED. We cannot prove the viewer
            # could read it and the summary still quotes it, so this fails
            # closed — anything else makes deleting the row the way to
            # publish it.
            continue
        elif scope.can_read(tier[0], tier[1], viewer, tier[2]):
            out.append(r)
    return out


def filter_policy_resources(
    rows: list[dict],
    resource_filter,
    *,
    allow_unclassified: bool,
    viewer: scope.Viewer | None,
) -> list[dict]:
    """Filter static review text by saved and current target policy context."""
    from . import policy_context as domain_policy

    resources = [(str(row.get("entity") or ""), int(row.get("entity_id") or 0)) for row in rows]
    saved_resources: list[tuple[str, int, dict[str, str]] | None] = []
    for row in rows:
        try:
            saved = json.loads(str(row.get("policy_context") or "{}"))
        except (TypeError, ValueError):
            saved = {}
        policy_input = saved.get("input") if isinstance(saved, dict) else None
        resource = policy_input.get("resource") if isinstance(policy_input, dict) else None
        if not isinstance(resource, dict) or not str(resource.get("type") or ""):
            saved_resources.append(None)
            continue
        saved_entity = str(resource["type"])
        try:
            saved_id = int(resource.get("id") or 0)
        except (TypeError, ValueError):
            saved_id = 0
        raw_attributes = resource.get("attributes")
        attributes = dict(raw_attributes) if isinstance(raw_attributes, dict) else {}
        attributes.update(
            project_type=str(resource.get("project_type") or ""),
            classification=str(resource.get("classification") or ""),
        )
        saved_resources.append((saved_entity, saved_id, attributes))

    supported = {
        resource
        for resource in resources
        if resource[1] > 0 and domain_policy.supports_resource(resource[0])
    }
    supported.update(
        (entity, entity_id)
        for saved_resource in saved_resources
        if saved_resource is not None
        for entity, entity_id, _attributes in (saved_resource,)
        if entity_id > 0 and domain_policy.supports_resource(entity)
    )
    current = (
        {resource: domain_policy.existing(resource[0], resource[1]) for resource in supported}
        if viewer is None
        else domain_policy.resource_contexts(list(supported), viewer)
    )
    result: list[dict] = []
    for row, (entity, entity_id), saved_resource in zip(
        rows, resources, saved_resources, strict=True
    ):
        if entity_id > 0 and domain_policy.supports_resource(entity):
            current_attributes = current.get((entity, entity_id))
            if not current_attributes or not resource_filter(entity, entity_id, current_attributes):
                continue

        if saved_resource is not None:
            saved_entity, saved_id, saved_attributes = saved_resource
            if saved_id > 0 and domain_policy.supports_resource(saved_entity):
                current_saved = current.get((saved_entity, saved_id))
                if not current_saved:
                    continue
                if str(current_saved.get("relationship_conflict") or "").lower() == "true":
                    continue
                if not resource_filter(saved_entity, saved_id, current_saved):
                    continue
            if not resource_filter(saved_entity, saved_id, saved_attributes):
                continue
        elif not allow_unclassified:
            continue
        result.append({key: value for key, value in row.items() if key != "policy_context"})
    return result


def list_changes(status: str = "pending", viewer: scope.Viewer = scope.NOBODY) -> list[dict]:
    if status:
        rows = db.query(
            "SELECT * FROM pending_changes WHERE status = ? ORDER BY id DESC LIMIT 200",
            (status,),
        )
    else:
        rows = db.query("SELECT * FROM pending_changes ORDER BY id DESC LIMIT 100")
    rows = _readable(rows, viewer)
    # only the pairs on THIS page: trust_scores computes for every pair in
    # the settled history, and a queue of 200 rows from one proposer would
    # otherwise pay for every agent the deployment has ever had.
    record = _trust_by_pair({(r["proposed_by"], r["entity"]) for r in rows}) if rows else {}
    for r in rows:
        r["payload"] = json.loads(r["payload"])
        # The saved decision carries the requester's resolved roles,
        # capabilities and directory attributes, plus the resource attributes
        # the rule inspected. filter_policy_resources already strips it from
        # the rows it returns; this is the other reader of the same column.
        r.pop("policy_context", None)
        r["policy_obligations"] = json.loads(r.get("policy_obligations") or "[]")
        r["approver_groups"] = json.loads(r.get("approver_groups") or "[]")
        r["approver_capabilities"] = json.loads(r.get("approver_capabilities") or "[]")
        # what this proposal is CALLED, resolved here so the header, the
        # checkbox label and the notification cannot drift apart
        r["label"] = lexicon.phrase(r["entity"], r["action"])
        # the UI shows whose verdict this is — acceptance belongs to the sponsor
        if r["entity"] == "task_completion":
            r["sponsor"] = _sponsor_of(r)
            # the KEY is absent when there is no evidence, never an empty
            # object: `{}` is truthy in JavaScript, so the renderer's
            # `c.evidence ? <AcceptanceEvidence …>` guard passes and the
            # component reads `.length` of an absent worklog — one deleted or
            # unreadable task takes down the whole Approvals list, which is the
            # surface where that proposal gets cleaned up
            evidence = _acceptance_evidence(r, viewer)
            if evidence:
                r["evidence"] = evidence
        r["record"] = record.get((r["proposed_by"], r["entity"]))
    return rows


# How many worklog notes ride along with an acceptance proposal. The sponsor
# needs the shape of the work and its latest state, not the whole log — the
# panel behind the task link holds all of it (frontend/components/task-peek).
_EVIDENCE_NOTES = 5


def _acceptance_evidence(change: dict, viewer: scope.Viewer) -> dict:
    """The worklog and task state a sponsor judges an acceptance ON.

    Without it the verdict controls sit two navigations from the evidence: the
    only other web surface that shows a worklog is the task peek, so the
    sponsor leaves the decision screen, finds the task, reads the log, comes
    back and votes from memory. `fieldguide/knots.yaml` tells them to do
    exactly that walk.

    Read through `delegation.list_worklog`, which carries the delegation door —
    a sponsor may read the log of the task they sponsor whether or not a tier
    filter reaches it. Returns {} rather than raising when the task is gone or
    unreadable: a proposal whose task was deleted must still be rejectable, and
    the queue is the surface where that clean-up happens. The caller drops the
    key entirely on {} — see list_changes for why an empty object is not safe
    to send.
    """
    from .delegation import list_worklog

    task_id = change["entity_id"]
    if not task_id:
        return {}
    # viewer-filtered, like every other read of a scoped table. The proposal
    # row already passed `_readable`, but that tests the SUMMARY's target and
    # this query returns the task's own title and forge link — a second read
    # needs its own filter or it is a second door onto the same row.
    tfrag, tp = scope.visible_filter(viewer, "tasks")
    task = db.query_one(
        "SELECT id, title, status, delegated_agent, forge_url FROM tasks"  # noqa: S608 — scope.visible_filter emits only bound marks
        f" WHERE id = ? AND {tfrag}",
        (task_id, *tp),
    )
    if not task:
        return {}
    # Whether the person judging this is the person who was on the hook when
    # the work was submitted. Authority follows the CURRENT sponsor by design
    # (010_sponsor_at_submission.sql), so this is a receipt and not a refusal:
    # a reviewer must see the handover before they press Approve.
    was = str(change.get("sponsor_at_submission") or "")
    now = _sponsor_of(change)
    handover = was if was and was != now else ""
    try:
        # actor = the VIEWER's own name, never the proposer's. The proposer is
        # the delegated agent, and passing its name would open the delegation
        # door on behalf of whoever happened to load the queue — a crew
        # worklog served to a reviewer outside the crew. Passed this way the
        # door opens for exactly one reader, the sponsor, which is the same
        # pair report_progress lets write.
        notes = list_worklog(
            task_id,
            limit=_EVIDENCE_NOTES,
            viewer=viewer,
            actor=viewer.name,
        )
    except db.NotFound:
        # A SCOPE refusal only, never a bare Exception: the reviewer still has
        # to be able to REJECT a proposal whose task went private or vanished,
        # and this surface is where that clean-up happens — but a real database
        # fault swallowed here renders as "no notes were filed" beside live
        # Approve controls, which is a different sentence with a different
        # meaning.
        notes = []
    return {**task, "worklog": notes, "sponsor_was": handover}


def _trust_by_pair(wanted: set[tuple[str, str]]) -> dict[tuple[str, str], dict]:
    """This proposer's record on THIS entity, keyed for the queue.

    The reviewer judged every proposal blind: approval rate and streak were
    computed already and lived two pages away on /agents, so the one screen
    where the number decides something was the one screen without it. Read
    from `delegation.trust_scores` rather than recomputed — a second
    definition of "streak" that disagreed with the promotion job would be
    worse than none.

    Scoped to the pairs the page actually shows. `trust_scores` runs a
    per-pair lookup for every pair in the settled history, so an unfiltered
    call cost 123 queries to render a queue that needed one — the cost grew
    with the deployment's age rather than with the page.
    """
    from .delegation import (
        TRUST_STREAK,
        _authority_cutoff,
        _judged_pairs,
        promotion_blocked,
        trust_blocked,
        trust_scores,
    )
    from .users import is_agent

    # Why no streak CAN form, when none can — the same sentence Team → Agents
    # renders above its trust card. In trusted-header mode (the default) a
    # verdict is weak, so `recent_streak` is 0 for everyone: without this the
    # row read "8 of 8 approved (100%) · no run of approvals", which states a
    # perfect record and no run of approvals in one breath, and the promotion
    # line could never appear. An operator's fix, not a wait.
    blocked = trust_blocked()
    # one scan for the whole page: promotion_blocked below would otherwise
    # re-read every authority proposal per row (services/delegation.py)
    judged = _judged_pairs(_authority_cutoff())
    out: dict[tuple[str, str], dict] = {}
    for t in trust_scores(wanted):
        # AGENT proposers only. Ingest files proposals under the person who
        # pasted the notes (services/ingest.py, origin='human'), and /review is
        # team-visible — so keying this on the proposer alone would put one
        # teammate's approval history in front of the whole roster, which is
        # person-level data judging the PAST. The anti-surveillance rule is
        # enforced in the service layer, not by hoping a caller filters.
        # It is also the wrong question: the record exists to decide whether an
        # AGENT has earned more autonomy. Nobody scores a colleague's rate.
        if not is_agent(t["agent"]):
            continue
        out[(t["agent"], t["entity"])] = {
            "approved": t["approved"],
            "proposed": t["proposed"],
            "approval_rate": t["approval_rate"],
            "streak": t["recent_streak"],
            "streak_blocked": blocked,
            "level": t["current_level"],
            # said at the verdict, where the approval that earns it happens.
            # Only when this verdict is the one that closes the streak AND a
            # promotion is actually available from here — trust_scores makes
            # the same `review` check for its own suggestion.
            # asks delegation, never restates its rule: promotion_blocked
            # refuses the ALWAYS_REVIEW and NO_AUTHORITY entities and stays
            # silent for 28 days after a human declines. task_completion is
            # in NO_AUTHORITY and is also the entity a delegated agent
            # proposes on MOST, so a restatement here promised a promotion
            # that could never be filed, on the common case.
            "promotes_at": (
                TRUST_STREAK
                if t["recent_streak"] == TRUST_STREAK - 1
                and not promotion_blocked(t["agent"], t["entity"], t["current_level"], judged)
                else 0
            ),
        }
    return out
