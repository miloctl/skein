"""Policy wrappers for stock Strands tools that do not use the write gate."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, cast

from strands.types.tools import AgentTool, ToolUse

from ..extensions.policy import (
    PolicyEffect,
    PolicyInput,
    PolicyResource,
    approval_fingerprint,
    current_policy_engine,
    current_policy_subject,
    policy_subject_data,
)
from ..services import policy_context

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
        if not key.endswith("_id") or not isinstance(value, int) or value <= 0:
            continue
        entity = key.removesuffix("_id")
        if entity == "project":
            entity = "engagement"
        attributes = policy_context.existing(entity, value)
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
        decision = current_policy_engine().decide(request)
        fingerprint = approval_fingerprint(
            request,
            decision,
            {"tool": self.tool_name, "input": arguments},
        )
        if decision.effect == PolicyEffect.REVIEW and approved_fingerprint != fingerprint:
            from ..services import review

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
                review_owner=subject.name,
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


async def execute_reviewed_core(invocation: dict[str, Any], registry) -> dict[str, Any]:
    """Resume one exact stock tool call through current workplace policy."""
    from ..extensions.policy import (
        policy_subject_from_data,
        reset_policy_engine,
        set_policy_engine,
    )
    from ..tools import ALL_TOOLS

    name = str(invocation.get("tool") or "")
    delegate = next((item for item in ALL_TOOLS if _name(item) == name), None)
    if delegate is None or name not in SPECIALIZED_WRITE_TOOLS:
        raise ValueError("the reviewed stock tool is not resumable")
    subject_data = invocation.get("subject")
    if not isinstance(subject_data, dict):
        raise ValueError("the reviewed stock tool identity is invalid")
    subject = registry.refresh_subject(policy_subject_from_data(subject_data))
    wrapper = GovernedCoreTool(cast(AgentTool, delegate), effect="write", risk="high")
    policy_token = set_policy_engine(registry.policy_engine)
    try:
        events = [
            event
            async for event in wrapper._stream(
                dict(invocation.get("tool_use") or {}),
                dict(invocation.get("invocation_state") or {}),
                subject,
                str(invocation.get("agent") or "agent"),
                str(invocation.get("approval_fingerprint") or ""),
            )
        ]
    finally:
        reset_policy_engine(policy_token)
    last = events[-1] if events else {}
    status = (
        "completed"
        if last.get("status") == "success"
        else str(last.get("completionStatus") or "failed")
    )
    return {"status": status, "events": events}
