"""Installed contract check for a private workplace package."""

from typing import assert_type

from pydantic import BaseModel

from app.extensions import (
    AppSettings,
    SkeinModule,
    ToolContribution,
    ToolHandlerContext,
    WorkflowActionContext,
    WorkflowActionContribution,
)
from app.public import (
    CreateTaskCommand,
    PublicError,
    UpdateTaskCommand,
    WorkflowContext,
    WorkflowEngine,
    WorkItems,
)


class _Input(BaseModel):
    value: str


class _Output(BaseModel):
    accepted: bool


def _tool_handler(_context: ToolHandlerContext, _request: _Input) -> _Output:
    return _Output(accepted=True)


def _workflow_handler(_context: WorkflowActionContext, _request: _Input) -> _Output:
    return _Output(accepted=True)


assert_type(AppSettings, type[AppSettings])
assert_type(SkeinModule, type[SkeinModule])
assert_type(CreateTaskCommand, type[CreateTaskCommand])
assert_type(WorkItems, type[WorkItems])
assert_type(WorkflowContext, type[WorkflowContext])
assert_type(WorkflowEngine, type[WorkflowEngine])
assert_type(CreateTaskCommand(title="Task"), CreateTaskCommand)
assert_type(PublicError("REMOTE_UNAVAILABLE", "The remote service is unavailable."), PublicError)
assert_type(UpdateTaskCommand(task_id=1, title="New"), UpdateTaskCommand)
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
