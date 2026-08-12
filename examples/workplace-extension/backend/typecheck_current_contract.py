"""Typed contribution checks for the additive core 0.2.1 surface."""

from typing import assert_type

from pydantic import BaseModel

from app.extensions import (
    ToolContribution,
    ToolHandlerContext,
    WorkflowActionContext,
    WorkflowActionContribution,
)


class _Input(BaseModel):
    value: str


class _Output(BaseModel):
    accepted: bool


def _tool_handler(_context: ToolHandlerContext, _request: _Input) -> _Output:
    return _Output(accepted=True)


def _workflow_handler(_context: WorkflowActionContext, _request: _Input) -> _Output:
    return _Output(accepted=True)


assert_type(
    ToolContribution(
        name="acme.workplace.tool",
        version="1.0.0",
        model_name="acme_tool",
        description="Typed tool",
        handler=_tool_handler,
        input_schema=_Input,
        output_schema=_Output,
        effect="read",
        risk="low",
        policy_action="acme.tool.read",
    ),
    ToolContribution[_Input, _Output],
)
assert_type(
    WorkflowActionContribution(
        name="acme.workplace.action",
        version="1.0.0",
        handler=_workflow_handler,
        input_schema=_Input,
        output_schema=_Output,
        effect="write",
        risk="medium",
        policy_action="acme.action.run",
    ),
    WorkflowActionContribution[_Input, _Output],
)
