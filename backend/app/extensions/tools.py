"""Governed execution for contributed agent and external tools."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass, field
from inspect import isawaitable
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from ..public.errors import PublicError
from .contracts import ToolContribution, ToolHandlerContext
from .policy import (
    PolicyDecision,
    PolicyEffect,
    PolicyEngine,
    PolicyInput,
    PolicyResource,
    PolicySubject,
    approval_fingerprint,
    policy_subject_data,
)


class ExtensionToolError(ValueError):
    """A safe, declared tool failure."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class ToolExecution(BaseModel):
    status: str
    tool: str
    version: str
    output: dict[str, Any] | None = None
    error_code: str = ""
    detail: str = ""
    policy_effect: str
    obligations: list[str] = []
    approver_groups: list[str] = []
    approver_capabilities: list[str] = []
    review_id: int = 0


@dataclass(frozen=True)
class ToolCallContext:
    subject: PolicySubject
    agent: str
    origin: str = "agent_tool"
    resource: PolicyResource = field(default_factory=lambda: PolicyResource(type="tool"))
    correlation_id: str = field(default_factory=lambda: uuid4().hex)


async def _await_handler_result(result: Awaitable[Any]) -> Any:
    return await result


async def execute_tool(
    contribution: ToolContribution,
    arguments: dict[str, Any],
    context: ToolCallContext,
    policy: PolicyEngine,
    *,
    _approved_fingerprint: str = "",
    _approved_decision: PolicyDecision | None = None,
    _owner_dispatch: bool = False,
) -> ToolExecution:
    """Validate, authorize, execute, and validate one contributed tool."""
    if contribution.allowed_agents and context.agent not in contribution.allowed_agents:
        return _finish(contribution, context, _refused(contribution, "deny", "agent_not_allowed"))
    missing = sorted(set(contribution.required_capabilities) - set(context.subject.capabilities))
    if missing:
        return _finish(contribution, context, _refused(contribution, "deny", "capability_required"))
    try:
        validated = contribution.input_schema.model_validate(arguments)
    except ValidationError:
        return _finish(contribution, context, _refused(contribution, "deny", "invalid_input"))

    try:
        resource = contribution.resource(validated) if contribution.resource else context.resource
    except (TypeError, ValueError):
        return _finish(contribution, context, _refused(contribution, "deny", "invalid_resource"))

    policy_input = PolicyInput(
        subject=context.subject,
        action=contribution.policy_action,
        resource=resource,
        origin=context.origin,
        agent=context.agent,
        tool=contribution.name,
        tool_effect=contribution.effect,
        tool_risk=contribution.risk,
    )
    decision = _approved_decision or policy.decide(policy_input)
    fingerprint = approval_fingerprint(
        policy_input,
        decision,
        {
            "tool": contribution.name,
            "version": contribution.version,
            "arguments": validated.model_dump(mode="json"),
        },
    )
    common: dict[str, Any] = {
        "tool": contribution.name,
        "version": contribution.version,
        "policy_effect": decision.effect.value,
        "obligations": list(decision.obligations),
        "approver_groups": list(decision.approver_groups),
        "approver_capabilities": list(decision.approver_capabilities),
    }
    if _approved_decision is not None and _approved_fingerprint != fingerprint:
        return _finish(
            contribution,
            context,
            ToolExecution(status="refused", error_code="approval_stale", **common),
        )
    if decision.effect == PolicyEffect.DENY:
        return _finish(
            contribution,
            context,
            ToolExecution(status="refused", error_code="policy_denied", **common),
        )
    if decision.effect == PolicyEffect.REVIEW and _approved_fingerprint != fingerprint:
        from ..services import review
        from ..services import scope as visibility_scope

        invocation = _review_invocation(
            contribution,
            validated,
            context,
            resource,
            fingerprint,
        )
        preview = (
            dict(contribution.review_preview(validated))
            if contribution.review_preview is not None
            else {}
        )
        proposal = review.propose_extension_invocation(
            "tool",
            {
                "tool": contribution.name,
                "version": contribution.version,
                "resource_type": resource.type,
                "resource_id": resource.id,
                "agent": context.agent,
                "preview": preview,
            },
            invocation,
            summary=f"Run governed tool {contribution.name}",
            actor=context.agent or context.subject.name,
            requested_by=context.subject.name,
            policy_obligations=decision.obligations,
            approver_groups=decision.approver_groups,
            approver_capabilities=decision.approver_capabilities,
            review_visibility=(
                resource.classification
                if resource.classification in visibility_scope.TIERS
                else visibility_scope.WORKSPACE
            ),
            review_crew_id=int(resource.attributes.get("crew_id") or 0),
            review_owner=context.subject.name,
            policy_input=policy_input,
        )
        return _finish(
            contribution,
            context,
            ToolExecution(
                status="review_required",
                error_code="review_required",
                review_id=int(proposal["id"]),
                **common,
            ),
        )

    from ..public.work import WorkItems, _bind_execution_context

    def bind(work_items: WorkItems) -> ToolHandlerContext:
        handler_context = ToolHandlerContext(
            context.subject,
            policy,
            work_items,
            context.agent,
            context.correlation_id,
            contribution.name,
        )
        services = _bind_execution_context(
            work_items,
            handler_context,
            subject=context.subject,
            namespace=contribution.name,
            receipt_namespace=f"tool:{contribution.name}",
            correlation_id=context.correlation_id,
            actor=context.agent or context.subject.name,
            actor_kind="agent" if context.agent else context.subject.kind,
        )
        return services

    def invoke_owned(services: ToolHandlerContext, request: BaseModel) -> Any:
        result = contribution.handler(services, request)
        if isawaitable(result):
            return asyncio.run(_await_handler_result(result))
        return result

    from ..public._owner_work import run_bounded_work_handler

    try:
        if _owner_dispatch:
            execution = run_bounded_work_handler(
                policy,
                bind,
                invoke_owned,
                validated,
                contribution.timeout_seconds,
                thread_name="skein-reviewed-tool",
            )
        else:
            # The direct path must also revoke write authority at the
            # deadline: asyncio.wait_for stopped WAITING but the worker
            # thread kept a live WorkItems and wrote AFTER the terminal
            # completion_unknown receipt. The owner-dispatch facade closes
            # at the deadline, so a late command fails instead of landing.
            # to_thread keeps the dispatcher's blocking loop off the event
            # loop that carries every open chat stream.
            execution = await asyncio.to_thread(
                run_bounded_work_handler,
                policy,
                bind,
                invoke_owned,
                validated,
                contribution.timeout_seconds,
                thread_name="skein-extension-tool",
            )
        if execution.timed_out:
            raise TimeoutError
        raw = execution.value
        output = contribution.output_schema.model_validate(raw)
    except TimeoutError:
        status = "completion_unknown" if contribution.effect in ("write", "unknown") else "failed"
        return _finish(
            contribution,
            context,
            ToolExecution(status=status, error_code="deadline_exceeded", **common),
        )
    except (ExtensionToolError, PublicError) as exc:
        code = exc.code if exc.code in contribution.error_codes else "tool_error"
        status = "completion_unknown" if contribution.effect in ("write", "unknown") else "failed"
        return _finish(
            contribution,
            context,
            ToolExecution(status=status, error_code=code, detail=exc.detail, **common),
        )
    except (TypeError, ValueError, ValidationError):
        status = "completion_unknown" if contribution.effect in ("write", "unknown") else "failed"
        return _finish(
            contribution,
            context,
            ToolExecution(status=status, error_code="invalid_output", **common),
        )
    except Exception:
        status = "completion_unknown" if contribution.effect in ("write", "unknown") else "failed"
        return _finish(
            contribution,
            context,
            ToolExecution(status=status, error_code="internal_error", **common),
        )
    return _finish(
        contribution,
        context,
        ToolExecution(status="completed", output=output.model_dump(mode="json"), **common),
    )


async def execute_reviewed_tool(
    contribution: ToolContribution,
    invocation: dict[str, Any],
    registry: Any,
) -> ToolExecution:
    """Execute the exact tool call stored with an approved review."""
    if (
        invocation.get("tool") != contribution.name
        or invocation.get("version") != contribution.version
    ):
        raise ValueError("the reviewed tool contract no longer matches the composed module")
    subject_data = invocation.get("subject")
    resource_data = invocation.get("resource")
    arguments = invocation.get("arguments")
    if not isinstance(subject_data, dict) or not isinstance(resource_data, dict):
        raise ValueError("the reviewed tool identity or resource is not valid")
    if not isinstance(arguments, dict):
        raise ValueError("the reviewed tool arguments are not valid")
    from .policy import policy_decision_from_data, policy_subject_from_data

    saved_subject = policy_subject_from_data(subject_data)
    subject = registry.refresh_subject(saved_subject)
    decision_data = invocation.get("_approval_decision")
    approved_decision = (
        policy_decision_from_data(decision_data) if isinstance(decision_data, dict) else None
    )
    resource = PolicyResource(
        type=str(resource_data.get("type") or "tool"),
        id=str(resource_data.get("id") or ""),
        project_type=str(resource_data.get("project_type") or ""),
        classification=str(resource_data.get("classification") or ""),
        attributes=dict(resource_data.get("attributes") or {}),
    )
    return await execute_tool(
        contribution,
        arguments,
        ToolCallContext(
            subject,
            str(invocation.get("agent") or ""),
            str(invocation.get("origin") or "agent_tool"),
            resource,
            str(invocation.get("correlation_id") or uuid4().hex),
        ),
        registry.policy_engine,
        _approved_fingerprint=str(
            invocation.get("_approval_grant") or invocation.get("approval_fingerprint") or ""
        ),
        _approved_decision=approved_decision,
        _owner_dispatch=True,
    )


def _review_invocation(
    contribution: ToolContribution,
    validated: BaseModel,
    context: ToolCallContext,
    resource: PolicyResource,
    fingerprint: str,
) -> dict[str, Any]:
    return {
        "tool": contribution.name,
        "version": contribution.version,
        "arguments": validated.model_dump(mode="json"),
        "subject": policy_subject_data(context.subject),
        "agent": context.agent,
        "origin": context.origin,
        "correlation_id": context.correlation_id,
        "approval_fingerprint": fingerprint,
        "resource": {
            "type": resource.type,
            "id": resource.id,
            "project_type": resource.project_type,
            "classification": resource.classification,
            "attributes": dict(resource.attributes),
        },
    }


def _refused(contribution: ToolContribution, effect: str, code: str) -> ToolExecution:
    return ToolExecution(
        status="refused",
        tool=contribution.name,
        version=contribution.version,
        error_code=code,
        policy_effect=effect,
    )


def _finish(
    contribution: ToolContribution,
    context: ToolCallContext,
    result: ToolExecution,
) -> ToolExecution:
    if contribution.effect in ("write", "unknown"):
        from ..services.tool_audit import record_tool_execution

        record_tool_execution(
            actor=context.agent or context.subject.name,
            tool=contribution.name,
            status=result.status,
            error_code=result.error_code,
            correlation_id=context.correlation_id,
        )
    return result
