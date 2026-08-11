"""Strands adapters for governed tool and specialist contributions."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .contracts import ToolContribution
from .policy import PolicySubject, current_policy_engine, current_policy_subject
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
