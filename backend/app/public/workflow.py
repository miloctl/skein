"""A small workflow runner for conditions, approvals, actions, and checkpoints."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from .. import db
from ..extensions.contracts import WorkflowActionContribution
from ..extensions.policy import (
    PolicyEffect,
    PolicyEngine,
    PolicyInput,
    PolicyResource,
    PolicySubject,
)
from .errors import PublicError


class CheckpointStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["checkpoint"]
    name: str = Field(min_length=1, max_length=100)


class ApprovalStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["approval"]
    name: str = Field(min_length=1, max_length=100)
    action: str = Field(min_length=1, max_length=200)
    resource_type: str = Field(min_length=1, max_length=100)
    risk: Literal["low", "medium", "high", "critical"] = "medium"


class ActionStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["action"]
    name: str = Field(min_length=1, max_length=200)
    input: dict[str, Any] = Field(default_factory=dict)


class ConditionStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["condition"]
    field: str = Field(min_length=1, max_length=100)
    equals: str | int | bool
    then: list[dict[str, Any]] = Field(default_factory=list)
    otherwise: list[dict[str, Any]] = Field(default_factory=list)


WorkflowStep = Annotated[
    CheckpointStep | ApprovalStep | ActionStep | ConditionStep,
    Field(discriminator="type"),
]
_STEPS = TypeAdapter(list[WorkflowStep])


def validate_workflow_shape(raw: object) -> None:
    """Validate the declarative shape without requiring registered actions."""
    steps = tuple(_STEPS.validate_python(raw))
    for step in steps:
        if isinstance(step, ConditionStep):
            validate_workflow_shape(step.then)
            validate_workflow_shape(step.otherwise)


@dataclass(frozen=True)
class WorkflowContext:
    subject: PolicySubject
    origin: str
    project_type: str = ""
    resource_id: str = ""
    values: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))


class WorkflowResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["completed", "review_required", "denied", "failed"]
    completed: tuple[str, ...] = ()
    checkpoint: str = ""
    error_code: str = ""
    obligations: tuple[str, ...] = ()
    outputs: dict[str, dict[str, Any]] = Field(default_factory=dict)


class WorkflowEngine:
    """Execute validated steps against registered actions and one policy."""

    def __init__(
        self,
        actions: tuple[WorkflowActionContribution, ...],
        policy: PolicyEngine,
    ) -> None:
        self._actions = {action.name: action for action in actions}
        self._policy = policy

    def prepare(self, raw: object) -> tuple[WorkflowStep, ...]:
        try:
            steps = tuple(_STEPS.validate_python(raw))
            self._prepare_nested(steps)
        except ValidationError as exc:
            raise PublicError("INVALID_WORKFLOW", "The workflow step data is not valid.") from exc
        return steps

    def _prepare_nested(self, steps: tuple[WorkflowStep, ...]) -> None:
        for step in steps:
            if isinstance(step, ActionStep) and step.name not in self._actions:
                raise PublicError(
                    "UNKNOWN_WORKFLOW_ACTION",
                    f"The workflow action {step.name!r} is not registered.",
                )
            if isinstance(step, ConditionStep):
                for branch in (step.then, step.otherwise):
                    nested = tuple(_STEPS.validate_python(branch))
                    self._prepare_nested(nested)

    def run(self, steps: tuple[WorkflowStep, ...], context: WorkflowContext) -> WorkflowResult:
        state = _WorkflowState()
        stopped = self._run_steps(steps, context, state)
        if stopped is not None:
            return stopped
        return WorkflowResult(
            status="completed",
            completed=tuple(state.completed),
            outputs=state.outputs,
        )

    def _run_steps(
        self,
        steps: tuple[WorkflowStep, ...],
        context: WorkflowContext,
        state: _WorkflowState,
    ) -> WorkflowResult | None:
        for step in steps:
            if isinstance(step, CheckpointStep):
                state.completed.append(step.name)
                continue
            if isinstance(step, ConditionStep):
                raw_branch = (
                    step.then if context.values.get(step.field) == step.equals else step.otherwise
                )
                branch = tuple(_STEPS.validate_python(raw_branch))
                if stopped := self._run_steps(branch, context, state):
                    return stopped
                continue
            if isinstance(step, ApprovalStep):
                decision = self._policy.decide(
                    PolicyInput(
                        subject=context.subject,
                        action=step.action,
                        resource=PolicyResource(
                            step.resource_type,
                            context.resource_id,
                            project_type=context.project_type,
                        ),
                        origin=context.origin,
                        tool_effect="write",
                        tool_risk=step.risk,
                    )
                )
                obligations = _obligations(decision)
                if decision.effect == PolicyEffect.DENY:
                    return WorkflowResult(
                        status="denied",
                        completed=tuple(state.completed),
                        checkpoint=step.name,
                        error_code="POLICY_DENIED",
                        obligations=obligations,
                        outputs=state.outputs,
                    )
                if decision.effect == PolicyEffect.REVIEW:
                    return WorkflowResult(
                        status="review_required",
                        completed=tuple(state.completed),
                        checkpoint=step.name,
                        error_code="REVIEW_REQUIRED",
                        obligations=obligations,
                        outputs=state.outputs,
                    )
                state.completed.append(step.name)
                continue
            stopped = self._run_action(step, context, state)
            if stopped is not None:
                return stopped
        return None

    def _run_action(
        self,
        step: ActionStep,
        context: WorkflowContext,
        state: _WorkflowState,
    ) -> WorkflowResult | None:
        contribution = self._actions[step.name]
        decision = self._policy.decide(
            PolicyInput(
                subject=context.subject,
                action=contribution.policy_action,
                resource=PolicyResource(
                    "workflow",
                    context.resource_id,
                    project_type=context.project_type,
                ),
                origin=context.origin,
                tool=contribution.name,
                tool_effect=contribution.effect,
                tool_risk=contribution.risk,
            )
        )
        if decision.effect != PolicyEffect.PERMIT:
            return WorkflowResult(
                status=("review_required" if decision.effect == PolicyEffect.REVIEW else "denied"),
                completed=tuple(state.completed),
                checkpoint=step.name,
                error_code=(
                    "REVIEW_REQUIRED" if decision.effect == PolicyEffect.REVIEW else "POLICY_DENIED"
                ),
                obligations=_obligations(decision),
                outputs=state.outputs,
            )
        try:
            input_data = contribution.input_schema.model_validate(step.input)
        except ValidationError:
            return self._failed(state, step.name, "INVALID_ACTION_INPUT")
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="skein-workflow")
        future = executor.submit(contribution.handler, **input_data.model_dump())
        try:
            raw = future.result(timeout=contribution.timeout_seconds)
            output = contribution.output_schema.model_validate(raw)
        except FutureTimeout:
            future.cancel()
            return self._failed(state, step.name, "ACTION_TIMEOUT")
        except PublicError as exc:
            code = exc.code if exc.code in contribution.error_codes else "ACTION_ERROR"
            return self._failed(state, step.name, code)
        except (TypeError, ValueError, ValidationError):
            return self._failed(state, step.name, "INVALID_ACTION_OUTPUT")
        except Exception:
            return self._failed(state, step.name, "ACTION_ERROR")
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        state.outputs[step.name] = output.model_dump(mode="json")
        state.completed.append(step.name)
        db.log_activity(
            context.subject.name,
            "workflow_action",
            f"{step.name} ({context.origin})",
        )
        return None

    @staticmethod
    def _failed(state: _WorkflowState, name: str, code: str) -> WorkflowResult:
        return WorkflowResult(
            status="failed",
            completed=tuple(state.completed),
            checkpoint=name,
            error_code=code,
            outputs=state.outputs,
        )


@dataclass
class _WorkflowState:
    completed: list[str] = field(default_factory=list)
    outputs: dict[str, dict[str, Any]] = field(default_factory=dict)


def _obligations(decision: Any) -> tuple[str, ...]:
    return (
        *decision.obligations,
        *(f"approver-group:{group}" for group in decision.approver_groups),
        *(f"approver-capability:{capability}" for capability in decision.approver_capabilities),
    )
