"""Strands adapters for governed tool and specialist contributions."""

from __future__ import annotations

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from inspect import isawaitable
from typing import Any

from .contracts import ContextContribution, ToolContribution
from .policy import (
    PolicyEffect,
    PolicyEngine,
    PolicyInput,
    PolicyResource,
    PolicySubject,
    current_policy_engine,
    current_policy_subject,
)
from .registry import ExtensionRegistry
from .tools import ToolCallContext, execute_tool


def missing_specialist_capabilities(
    registry: ExtensionRegistry,
    specialist: str,
    subject: PolicySubject,
) -> tuple[str, ...]:
    """Return missing capabilities for one contributed specialist."""
    try:
        contribution = registry.specialist(specialist)
    except ValueError:
        return ()
    return tuple(sorted(set(contribution.required_capabilities) - set(subject.capabilities)))


def resolve_context(
    contribution: ContextContribution,
    query: str,
    subject: PolicySubject,
    agent: str,
    policy: PolicyEngine,
) -> str:
    """Authorize and bound one specialist context retrieval."""
    missing = sorted(set(contribution.required_capabilities) - set(subject.capabilities))
    if missing:
        _record_context(contribution, agent, "refused", "capability_required")
        raise PermissionError("this context source needs a workplace capability")
    decision = policy.decide(
        PolicyInput(
            subject,
            contribution.policy_action,
            PolicyResource("context", contribution.name),
            "agent_context",
            agent=agent,
            tool=contribution.name,
            tool_effect="read",
            tool_risk=contribution.risk,
        )
    )
    if decision.effect != PolicyEffect.PERMIT:
        code = "review_unsupported" if decision.effect == PolicyEffect.REVIEW else "policy_denied"
        _record_context(contribution, agent, "refused", code)
        raise PermissionError("the workplace policy denied this context source")
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="skein-extension-context")
    future = executor.submit(contribution.provider, query)
    try:
        value = future.result(timeout=contribution.timeout_seconds)
        if isawaitable(value) or not isinstance(value, str):
            raise ValueError("the context provider returned an invalid value")
        if len(value) > contribution.max_output_chars:
            raise ValueError("the context provider exceeded its output limit")
    except FutureTimeout as exc:
        future.cancel()
        _record_context(contribution, agent, "failed", "deadline_exceeded")
        raise RuntimeError("the context provider exceeded its deadline") from exc
    except Exception:
        _record_context(contribution, agent, "failed", "context_error")
        raise
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    _record_context(contribution, agent, "completed")
    return value


def _record_context(
    contribution: ContextContribution,
    agent: str,
    status: str,
    error_code: str = "",
) -> None:
    from ..services.tool_audit import record_tool_execution

    record_tool_execution(
        actor=agent,
        tool=contribution.name,
        status=status,
        error_code=error_code,
    )


def strands_tools(
    registry: ExtensionRegistry,
    agent: str,
    selected: Iterable[str] | None = None,
) -> tuple:
    """Build model-facing wrappers without giving tools an ungoverned path."""
    from strands import tool

    allowed = set(selected) if selected is not None else None
    wrapped = []
    for contribution in registry.tools:
        if allowed is not None and contribution.name not in allowed:
            continue

        def wrap(current: ToolContribution) -> Any:
            async def invoke(**arguments: Any) -> str:
                from ..agents import receipts

                result = await execute_tool(
                    current,
                    arguments,
                    ToolCallContext(current_policy_subject(), agent),
                    current_policy_engine(),
                )
                if current.effect == "write":
                    kinds = {
                        "completed": "wrote",
                        "review_required": "queued",
                        "refused": "refused",
                        "completion_unknown": "failed",
                        "failed": "failed",
                    }
                    kind = kinds.get(result.status, "failed")
                    detail = result.status.replace("_", " ")
                    if result.status == "completion_unknown":
                        detail = "completion unknown"
                    receipts.record(
                        kind,
                        current.name,
                        detail,
                        result.review_id if result.status == "review_required" else 0,
                        actor=agent,
                    )
                return result.model_dump_json()

            return tool(
                name=current.model_name,
                description=current.description,
                inputSchema=current.input_schema.model_json_schema(),
            )(invoke)

        wrapped.append(wrap(contribution))
    return tuple(wrapped)
