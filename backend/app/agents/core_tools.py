"""Policy wrappers for stock Strands tools that do not use the write gate."""

from __future__ import annotations

import contextlib
from collections.abc import Iterable
from typing import Any, cast

from strands.types.tools import AgentTool, ToolUse

from .. import db
from ..extensions.policy import (
    PolicyDecision,
    PolicyEffect,
    PolicyInput,
    PolicyResource,
    approval_fingerprint,
    current_policy_engine,
    current_policy_subject,
    policy_subject_data,
)
from ..services import policy_context, scope

# These stock writers have specialized sponsor, receipt, or artifact rules.
# The wrapper adds workplace policy without replacing those rules.
SPECIALIZED_WRITE_TOOLS = frozenset(
    {
        "claim_delegated_task",
        "report_progress",
        "submit_for_acceptance",
        "generate_handoff",
    }
)


def _name(tool: Any) -> str:
    return str(getattr(tool, "tool_name", getattr(tool, "__name__", "")))


def _resource(arguments: dict[str, Any]) -> PolicyResource:
    for key, value in arguments.items():
        # A model sends "42" as often as 42, and Strands coerces the string
        # to int with pydantic AFTER this policy check runs. int() here must
        # be as wide as that coercion — isdecimal() let ' 42', '+42' and
        # '4_2' pass as a generic `tool` resource while the delegate wrote
        # to the real task, skipping its task-scoped rules.
        if isinstance(value, str):
            with contextlib.suppress(ValueError):
                value = int(value)
        if not key.endswith("_id") or not isinstance(value, int) or value <= 0:
            continue
        entity = key.removesuffix("_id")
        if entity == "project":
            entity = "engagement"
        attributes = (
            policy_context.existing("task", value)
            if entity == "task"
            else policy_context.existing_scoped(entity, value, scope.NOBODY)
        )
        return PolicyResource(
            entity,
            str(value),
            str(attributes.get("project_type") or ""),
            str(attributes.get("classification") or ""),
            attributes,
        )
    return PolicyResource("tool")


def _error(tool_use: dict[str, Any], detail: str, status: str = "failed") -> dict[str, Any]:
    return {
        "toolUseId": tool_use.get("toolUseId", "unknown"),
        "status": "error",
        "completionStatus": status,
        "content": [{"text": detail}],
    }


class GovernedCoreTool(AgentTool):
    """Apply the composed policy before one stock read or specialized write."""

    def __init__(self, delegate: AgentTool, *, effect: str = "read", risk: str = "low") -> None:
        super().__init__()
        self._delegate = delegate
        self.effect = effect
        self.risk = risk

    @property
    def tool_name(self) -> str:
        return _name(self._delegate)

    @property
    def tool_spec(self):
        return self._delegate.tool_spec

    @property
    def tool_type(self) -> str:
        return "skein-core-governed"

    @property
    def supports_hot_reload(self) -> bool:
        return False

    async def stream(self, tool_use, invocation_state: dict[str, Any], **kwargs: Any):
        from .identity import agent_identity

        async for event in self._stream(
            tool_use,
            invocation_state,
            current_policy_subject(),
            agent_identity(),
            "",
            None,
            **kwargs,
        ):
            yield event

    async def _stream(
        self,
        tool_use: dict[str, Any],
        invocation_state: dict[str, Any],
        subject,
        actor: str,
        approved_fingerprint: str,
        approved_decision: PolicyDecision | None = None,
        **kwargs: Any,
    ):
        if self.effect == "write":
            with db.transaction():
                async for event in self._run(
                    tool_use,
                    invocation_state,
                    subject,
                    actor,
                    approved_fingerprint,
                    approved_decision,
                    **kwargs,
                ):
                    yield event
            return
        async for event in self._run(
            tool_use,
            invocation_state,
            subject,
            actor,
            approved_fingerprint,
            approved_decision,
            **kwargs,
        ):
            yield event

    async def _run(
        self,
        tool_use: dict[str, Any],
        invocation_state: dict[str, Any],
        subject,
        actor: str,
        approved_fingerprint: str,
        approved_decision: PolicyDecision | None = None,
        **kwargs: Any,
    ):
        from . import receipts

        arguments = tool_use.get("input") or {}
        if not isinstance(arguments, dict):
            yield _error(tool_use, "The stock tool input must be an object.")
            return
        request = PolicyInput(
            subject,
            f"skein.tool.{self.tool_name}",
            _resource(arguments),
            "agent_tool",
            agent=actor,
            tool=self.tool_name,
            tool_effect=self.effect,
            tool_risk=self.risk,
            context={"core_governance": "specialized"} if self.effect == "write" else {},
        )
        decision = approved_decision or current_policy_engine().decide(request)
        fingerprint = approval_fingerprint(
            request,
            decision,
            {"tool": self.tool_name, "input": arguments},
        )
        if approved_decision is not None and approved_fingerprint != fingerprint:
            yield _error(
                tool_use,
                "The reviewed stock tool approval is stale.",
                "approval_stale",
            )
            return
        if decision.effect == PolicyEffect.REVIEW and approved_fingerprint != fingerprint:
            from ..services import review
            from ..services import scope as visibility_scope

            proposal = review.propose_extension_invocation(
                "core_tool",
                {"tool": self.tool_name, "agent": actor},
                {
                    "tool": self.tool_name,
                    "tool_use": tool_use,
                    "invocation_state": invocation_state,
                    "subject": policy_subject_data(subject),
                    "agent": actor,
                    "approval_fingerprint": fingerprint,
                },
                summary=f"Run governed stock tool {self.tool_name}",
                actor=actor,
                requested_by=subject.name,
                policy_obligations=decision.obligations,
                approver_groups=decision.approver_groups,
                approver_capabilities=decision.approver_capabilities,
                review_visibility=(
                    request.resource.classification
                    if request.resource.classification in visibility_scope.TIERS
                    else visibility_scope.WORKSPACE
                ),
                review_crew_id=int(request.resource.attributes.get("crew_id") or 0),
                review_owner=subject.name,
                policy_input=request,
            )
            receipts.record("queued", self.tool_name, "review required", proposal["id"], actor)
            yield _error(
                tool_use,
                f"Skein review #{proposal['id']} is required for this tool.",
                "review_required",
            )
            return
        if decision.effect == PolicyEffect.DENY:
            if self.effect == "write":
                receipts.record("refused", self.tool_name, "policy denied", actor=actor)
            yield _error(tool_use, "Skein policy denied this tool.", "denied")
            return
        async for event in self._delegate.stream(
            cast(ToolUse, tool_use), invocation_state, **kwargs
        ):
            yield event


def govern_core_tools(tools: Iterable[Any]) -> list[Any]:
    """Wrap every stock read and only the specialized stock write paths."""
    governed: list[Any] = []
    from ..tools import CORE_WRITE_TOOLS

    for tool in tools:
        name = _name(tool)
        if name in SPECIALIZED_WRITE_TOOLS:
            governed.append(GovernedCoreTool(tool, effect="write", risk="high"))
        elif name not in CORE_WRITE_TOOLS:
            governed.append(GovernedCoreTool(tool))
        else:
            governed.append(tool)
    return governed


def reviewed_policy_input(invocation: dict[str, Any], subject) -> PolicyInput:
    """Rebuild the current policy input for one pending stock-tool verdict."""
    name = str(invocation.get("tool") or "")
    if name not in SPECIALIZED_WRITE_TOOLS:
        raise ValueError("the reviewed stock tool is not resumable")
    tool_use = invocation.get("tool_use")
    if not isinstance(tool_use, dict):
        raise ValueError("the reviewed stock tool call is invalid")
    arguments = tool_use.get("input") or {}
    if not isinstance(arguments, dict):
        raise ValueError("the reviewed stock tool input is invalid")
    actor = str(invocation.get("agent") or "agent")
    return PolicyInput(
        subject,
        f"skein.tool.{name}",
        _resource(arguments),
        "agent_tool",
        agent=actor,
        tool=name,
        tool_effect="write",
        tool_risk="high",
        context={"core_governance": "specialized"},
    )


async def execute_reviewed_core(invocation: dict[str, Any], registry) -> dict[str, Any]:
    """Resume one exact stock tool call through current workplace policy."""
    from ..extensions.policy import (
        policy_decision_from_data,
        policy_subject_from_data,
        reset_policy_engine,
        set_policy_engine,
    )
    from ..tools import ALL_TOOLS
    from .identity import (
        reset_agent_identity,
        reset_requester_identity,
        set_agent_identity,
        set_requester_identity,
    )

    name = str(invocation.get("tool") or "")
    delegate = next((item for item in ALL_TOOLS if _name(item) == name), None)
    if delegate is None or name not in SPECIALIZED_WRITE_TOOLS:
        raise ValueError("the reviewed stock tool is not resumable")
    subject_data = invocation.get("subject")
    if not isinstance(subject_data, dict):
        raise ValueError("the reviewed stock tool identity is invalid")
    subject = registry.refresh_subject(policy_subject_from_data(subject_data))
    decision_data = invocation.get("_approval_decision")
    approved_decision = (
        policy_decision_from_data(decision_data) if isinstance(decision_data, dict) else None
    )
    wrapper = GovernedCoreTool(cast(AgentTool, delegate), effect="write", risk="high")
    policy_token = set_policy_engine(registry.policy_engine)
    agent_token = set_agent_identity(str(invocation.get("agent") or "agent"))
    requester_token = set_requester_identity("" if subject.kind == "agent" else subject.name)
    try:
        events = [
            event
            async for event in wrapper._stream(
                dict(invocation.get("tool_use") or {}),
                dict(invocation.get("invocation_state") or {}),
                subject,
                str(invocation.get("agent") or "agent"),
                str(
                    invocation.get("_approval_grant")
                    or invocation.get("approval_fingerprint")
                    or ""
                ),
                approved_decision,
            )
        ]
    finally:
        reset_requester_identity(requester_token)
        reset_agent_identity(agent_token)
        reset_policy_engine(policy_token)
    last = events[-1] if events else {}
    status = (
        "completed"
        if last.get("status") == "success"
        else str(last.get("completionStatus") or "failed")
    )
    return {"status": status, "events": events}
