"""Strands adapters for governed tool and specialist contributions."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .contracts import ToolContribution
from .policy import current_policy_engine, current_policy_subject
from .registry import ExtensionRegistry
from .tools import ToolCallContext, execute_tool


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
                    kind = "wrote" if result.status == "completed" else "refused"
                    receipts.record(
                        kind,
                        current.name,
                        result.status.replace("_", " "),
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
