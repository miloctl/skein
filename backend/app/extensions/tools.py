"""Governed execution for contributed agent and external tools."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from inspect import isawaitable
from typing import Any

from pydantic import BaseModel, ValidationError

from .contracts import ToolContribution
from .policy import PolicyEffect, PolicyEngine, PolicyInput, PolicyResource, PolicySubject


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


@dataclass(frozen=True)
class ToolCallContext:
    subject: PolicySubject
    agent: str
    origin: str = "agent_tool"
    resource: PolicyResource = field(default_factory=lambda: PolicyResource(type="tool"))


async def execute_tool(
    contribution: ToolContribution,
    arguments: dict[str, Any],
    context: ToolCallContext,
    policy: PolicyEngine,
) -> ToolExecution:
    """Validate, authorize, execute, and validate one contributed tool."""
    if contribution.allowed_agents and context.agent not in contribution.allowed_agents:
        return _refused(contribution, "deny", "agent_not_allowed")
    missing = sorted(set(contribution.required_capabilities) - set(context.subject.capabilities))
    if missing:
        return _refused(contribution, "deny", "capability_required")
    try:
        validated = contribution.input_schema.model_validate(arguments)
    except ValidationError:
        return _refused(contribution, "deny", "invalid_input")

    decision = policy.decide(
        PolicyInput(
            subject=context.subject,
            action=contribution.policy_action,
            resource=context.resource,
            origin=context.origin,
            agent=context.agent,
            tool=contribution.name,
            tool_effect=contribution.effect,
            tool_risk=contribution.risk,
        )
    )
    common: dict[str, Any] = {
        "tool": contribution.name,
        "version": contribution.version,
        "policy_effect": decision.effect.value,
        "obligations": list(decision.obligations),
        "approver_groups": list(decision.approver_groups),
        "approver_capabilities": list(decision.approver_capabilities),
    }
    if decision.effect == PolicyEffect.DENY:
        return ToolExecution(status="refused", error_code="policy_denied", **common)
    if decision.effect == PolicyEffect.REVIEW:
        return ToolExecution(status="review_required", error_code="review_required", **common)

    async def invoke() -> Any:
        result = await asyncio.to_thread(contribution.handler, **validated.model_dump())
        if isawaitable(result):
            return await result
        return result

    try:
        raw = await asyncio.wait_for(invoke(), timeout=contribution.timeout_seconds)
        output = contribution.output_schema.model_validate(raw)
    except TimeoutError:
        return ToolExecution(status="failed", error_code="timeout", **common)
    except ExtensionToolError as exc:
        code = exc.code if exc.code in contribution.error_codes else "tool_error"
        return ToolExecution(status="failed", error_code=code, detail=exc.detail, **common)
    except (TypeError, ValueError, ValidationError):
        return ToolExecution(status="failed", error_code="invalid_output", **common)
    except Exception:
        return ToolExecution(status="failed", error_code="internal_error", **common)
    return ToolExecution(status="completed", output=output.model_dump(mode="json"), **common)


def _refused(contribution: ToolContribution, effect: str, code: str) -> ToolExecution:
    return ToolExecution(
        status="refused",
        tool=contribution.name,
        version=contribution.version,
        error_code=code,
        policy_effect=effect,
    )
