"""Typed work commands and queries for trusted extensions."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Annotated, Any
from weakref import ReferenceType, WeakKeyDictionary, ref

from pydantic import BaseModel, ConfigDict, Field

from .. import db
from ..extensions.policy import (
    PolicyDecision,
    PolicyEffect,
    PolicyEngine,
    PolicyInput,
    PolicyResource,
    PolicySubject,
)
from ..services import blockers, promises, scope, work
from .errors import PublicError


@dataclass(frozen=True)
class CommandContext:
    subject: PolicySubject
    origin: str
    namespace: str = ""
    receipt_namespace: str = ""
    correlation_id: str = ""
    project_type: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    actor: str = ""
    actor_kind: str = ""
    # What the contribution declared about the operation performing this write.
    # Without these the domain decision always read "none"/"low", so a
    # workplace rule keyed on risk never fired on the write it meant to gate.
    effect: str = "none"
    risk: str = "low"

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))

    @property
    def execution_actor(self) -> str:
        return self.actor or self.subject.name

    @property
    def execution_actor_kind(self) -> str:
        return self.actor_kind or self.subject.kind


@dataclass(frozen=True)
class _ExecutionGrant:
    subject: PolicySubject
    namespace: str
    receipt_namespace: str
    correlation_id: str
    actor: str
    actor_kind: str
    read_name: str
    read_strong: bool
    effect: str = "none"
    risk: str = "low"


@dataclass(frozen=True)
class _IssuedCommand:
    signature: tuple[Any, ...]
    read_name: str
    read_strong: bool


_RegistryPayload = _ExecutionGrant | _IssuedCommand | tuple[Any, ...]
_IdentityRegistry = dict[int, tuple[ReferenceType[Any], _RegistryPayload]]
_BOUND_EXECUTIONS: WeakKeyDictionary[object, _IdentityRegistry] = WeakKeyDictionary()
_ISSUED_COMMANDS: WeakKeyDictionary[object, _IdentityRegistry] = WeakKeyDictionary()


def _remember_identity(
    registry: _IdentityRegistry, value: object, payload: _RegistryPayload
) -> None:
    """Retain authority by object identity without retaining the request object."""
    identity = id(value)

    def forget(reference: ReferenceType[Any]) -> None:
        current = registry.get(identity)
        if current is not None and current[0] is reference:
            registry.pop(identity, None)

    reference = ref(value, forget)
    registry[identity] = (reference, payload)


def _identity_payload(registry: _IdentityRegistry, value: object) -> _RegistryPayload | None:
    current = registry.get(id(value))
    if current is None or current[0]() is not value:
        return None
    return current[1]


def _command_signature(context: CommandContext) -> tuple[Any, ...]:
    return (
        context.subject,
        context.origin,
        context.namespace,
        context.receipt_namespace,
        context.correlation_id,
        context.project_type,
        deepcopy(dict(context.attributes)),
        context.actor,
        context.actor_kind,
        context.effect,
        context.risk,
    )


def _bind_execution_context[ExecutionContextT](
    work_items: WorkItems,
    context: ExecutionContextT,
    *,
    subject: PolicySubject,
    namespace: str,
    receipt_namespace: str,
    correlation_id: str,
    actor: str = "",
    actor_kind: str = "",
    read_name: str = "",
    read_strong: bool = False,
    effect: str = "none",
    risk: str = "low",
) -> ExecutionContextT:
    """Bind a core-created adapter object to one immutable provenance grant.

    This helper is an internal composition function. It is not a method on the
    public facade supplied to extension handlers.
    """
    if not namespace.strip():
        raise ValueError("A command namespace is required.")
    if not receipt_namespace.strip():
        raise ValueError("A command receipt namespace is required.")
    grant = _ExecutionGrant(
        subject,
        namespace,
        receipt_namespace,
        correlation_id,
        actor or subject.name,
        actor_kind or subject.kind,
        read_name,
        read_strong,
        effect,
        risk,
    )
    registry = _BOUND_EXECUTIONS.setdefault(work_items, {})
    _remember_identity(registry, context, grant)
    return context


class _HeldForReview(Exception):
    """Carry a held command out of its write transaction before queueing it.

    The proposal cannot be written inside the transaction it is refusing: that
    transaction rolls back on the refusal and takes the proposal with it, so
    the caller is told to seek approval for a review that no longer exists.
    """

    def __init__(
        self,
        context: CommandContext,
        request: PolicyInput,
        decision: PolicyDecision,
        command: dict[str, Any],
        obligations: tuple[str, ...],
    ) -> None:
        super().__init__("held for review")
        self.context = context
        self.request = request
        self.decision = decision
        self.command = command
        self.obligations = obligations


@dataclass(frozen=True)
class _ResumedCommand:
    """The execution boundary a verdict issues for one approved command."""

    review_id: int


def _execute_reviewed_command(invocation: dict[str, Any], registry: Any) -> dict[str, Any]:
    """Run one approved public command under a fresh, core-issued grant.

    The grant the caller held is gone: a route closes its grant with the
    response, and a job closes its own at the deadline. This mints a new one
    from the stored provenance so the write keeps the integration as its
    author, with the reviewer recorded separately by the review service.
    """
    from ..extensions.policy import policy_subject_from_data

    name = str(invocation.get("command") or "")
    if name not in (
        "create_task",
        "update_task",
        "create_blocker",
        "update_blocker",
        "create_promise",
        "update_promise",
    ):
        raise ValueError("the reviewed command is not supported")
    saved_subject = invocation.get("subject")
    if not isinstance(saved_subject, dict):
        raise ValueError("the reviewed command identity is invalid")
    subject = registry.refresh_subject(policy_subject_from_data(saved_subject))
    work_items = WorkItems(registry.policy_engine)
    execution = _ResumedCommand(int(invocation.get("review_id") or 0))
    _bind_execution_context(
        work_items,
        execution,
        subject=subject,
        namespace=str(invocation.get("namespace") or ""),
        receipt_namespace=str(invocation.get("receipt_namespace") or ""),
        correlation_id=str(invocation.get("correlation_id") or ""),
        # From the REFRESHED subject, not the stored string: the stored name is
        # whatever the invocation row says, and provenance must not be one
        # column edit away from naming somebody who never acted.
        actor=subject.name,
        actor_kind=subject.kind,
        effect=str(invocation.get("effect") or "none"),
        risk=str(invocation.get("risk") or "low"),
    )
    context = work_items._issue_context(
        execution,
        project_type=str(invocation.get("project_type") or ""),
        attributes=dict(invocation.get("attributes") or {}),
    )
    reviewed_action = str(invocation.get("action") or "")
    arguments = dict(invocation.get("arguments") or {})
    try:
        return _run_reviewed(work_items, name, arguments, context, reviewed_action)
    except PublicError as exc:
        if exc.status_code == 404:
            # The target vanished between the hold and the verdict. Re-approving
            # can never succeed, so hand review.approve_change the exception its
            # auto-reject branch already settles instead of the generic one that
            # resets to pending and boomerangs in the queue forever.
            raise db.NotFound(str(exc.detail)) from None
        raise
    except _HeldForReview as held:
        # The reviewer answered one gate and this command meets another. Refuse
        # rather than run: nobody has answered the second rule, and its
        # approvers were never computed, let alone checked.
        raise PermissionError(
            f"this command also needs approval for {held.request.action}"
        ) from None


def _run_reviewed(
    work_items: WorkItems,
    name: str,
    arguments: dict[str, Any],
    context: CommandContext,
    reviewed_action: str,
) -> dict[str, Any]:
    resumed: TaskView | BlockerView | PromiseView
    if name == "create_task":
        resumed = work_items._create_task_locked(
            CreateTaskCommand(**arguments), context, approved=reviewed_action
        )
    elif name == "update_task":
        resumed = work_items._update_task_locked(
            UpdateTaskCommand(**arguments), context, approved=reviewed_action
        )
    elif name == "create_promise":
        resumed = work_items._create_promise_locked(
            CreatePromiseCommand(**arguments), context, approved=reviewed_action
        )
    elif name == "update_promise":
        resumed = work_items._update_promise_locked(
            UpdatePromiseCommand(**arguments), context, approved=reviewed_action
        )
    elif name == "create_blocker":
        resumed = work_items._create_blocker_locked(
            CreateBlockerCommand(**arguments), context, approved=reviewed_action
        )
    else:
        resumed = work_items._update_blocker_locked(
            UpdateBlockerCommand(**arguments), context, approved=reviewed_action
        )
    return resumed.model_dump(mode="json")


class CreateTaskCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: Annotated[str, Field(max_length=work.TITLE_LEN)]
    description: Annotated[str, Field(max_length=work.DESCRIPTION_LEN)] = ""
    milestone_id: int = 0
    engagement_id: int = 0
    assignee: Annotated[str, Field(max_length=64)] = ""
    priority: Annotated[str, Field(max_length=10)] = "medium"
    due_date: Annotated[str, Field(max_length=10)] = ""
    visibility: str = scope.WORKSPACE
    crew_id: int = 0
    status: str = "todo"
    idempotency_key: Annotated[str, Field(max_length=200)] = ""


class UpdateTaskCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: int
    status: str | None = None
    assignee: str | None = None
    priority: str | None = None
    due_date: str | None = None
    description: Annotated[str | None, Field(max_length=work.DESCRIPTION_LEN)] = None
    title: Annotated[str | None, Field(max_length=work.TITLE_LEN)] = None
    committed_week: str | None = None
    waiting_on: str | None = None
    milestone_id: int | None = None
    engagement_id: int | None = None
    forge_url: str | None = None


class CreateBlockerCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: Annotated[str, Field(max_length=work.TITLE_LEN)]
    detail: Annotated[str, Field(max_length=work.DESCRIPTION_LEN)] = ""
    owner: Annotated[str, Field(max_length=64)] = ""
    impact: Annotated[str, Field(max_length=10)] = "medium"
    task_id: int = 0
    source: Annotated[str, Field(max_length=200)] = ""
    escalate_after_hours: int = 0
    visibility: str = scope.WORKSPACE
    crew_id: int = 0
    idempotency_key: Annotated[str, Field(max_length=200)] = ""


class UpdateBlockerCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    blocker_id: int
    # "resolved" is the only status a caller sets. Escalation is the scheduled
    # sweep's decision, not an integration's (services/blockers.py).
    status: Annotated[str | None, Field(max_length=10)] = None
    resolution: Annotated[str | None, Field(max_length=work.DESCRIPTION_LEN)] = None
    title: Annotated[str | None, Field(max_length=work.TITLE_LEN)] = None
    detail: Annotated[str | None, Field(max_length=work.DESCRIPTION_LEN)] = None
    owner: Annotated[str | None, Field(max_length=64)] = None


class BlockerView(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    title: str
    detail: str
    owner: str
    impact: str
    status: str
    task_id: int | None
    visibility: str
    crew_id: int | None
    origin: str
    created_by: str
    created_at: str
    updated_at: str


class CreatePromiseCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    promise: Annotated[str, Field(max_length=work.DESCRIPTION_LEN)]
    to_whom: Annotated[str, Field(max_length=120)] = ""
    due_date: Annotated[str, Field(max_length=10)] = ""
    engagement_id: int = 0
    audience: Annotated[str, Field(max_length=10)] = "external"
    direction: Annotated[str, Field(max_length=10)] = "given"
    visibility: str = scope.WORKSPACE
    crew_id: int = 0
    idempotency_key: Annotated[str, Field(max_length=200)] = ""


class UpdatePromiseCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    promise_id: int
    status: Annotated[str, Field(max_length=10)]


class PromiseView(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    promise: str
    to_whom: str
    due_date: str | None
    engagement_id: int | None
    audience: str
    direction: str
    status: str
    visibility: str
    crew_id: int | None
    origin: str
    created_by: str
    created_at: str
    updated_at: str


class TaskView(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    title: str
    description: str
    status: str
    assignee: str
    priority: str
    due_date: str | None
    milestone_id: int | None
    engagement_id: int | None
    visibility: str
    crew_id: int | None
    origin: str
    created_by: str
    created_at: str
    updated_at: str


_PREVIEW_FIELDS = (
    "title",
    "promise",
    "status",
    "impact",
    "assignee",
    "owner",
    "to_whom",
    "priority",
    "due_date",
    "visibility",
    "resolution",
    "task_id",
    "blocker_id",
    "promise_id",
    "engagement_id",
    "milestone_id",
)
_PREVIEW_CHARS = 200


def _command_preview(command: dict[str, Any]) -> dict[str, Any]:
    """A bounded, readable summary of the arguments a reviewer is approving.

    The full arguments stay out of the queue on purpose (migration 013), so
    this names the fields that decide whether the write is acceptable and
    truncates every one of them.
    """
    arguments = command.get("arguments")
    if not isinstance(arguments, dict):
        return {}
    preview: dict[str, Any] = {}
    for name in _PREVIEW_FIELDS:
        value = arguments.get(name)
        if value in (None, "", 0):
            continue
        preview[name] = value if isinstance(value, int) else str(value)[:_PREVIEW_CHARS]
    return preview


def _revoked_error() -> PublicError:
    return PublicError(
        "EXECUTION_CONTEXT_CLOSED",
        "The request finished before this work request could run.",
        status_code=403,
    )


def _close_execution(work_items: WorkItems) -> None:
    """Revoke every grant and issued command bound to one facade.

    A route has no deadline, so nothing else ends its authority: a thread the
    handler spawned would keep writing core rows under the route's provenance
    after the response, and after shutdown. Dropping the registries is what
    revokes the authority; the flag only names the cause in the error.
    """
    work_items._closed = True
    _BOUND_EXECUTIONS.pop(work_items, None)
    _ISSUED_COMMANDS.pop(work_items, None)


class WorkItems:
    """The public facade for task commands and queries."""

    def __init__(self, policy: PolicyEngine) -> None:
        self._policy = policy
        self._closed = False

    def _issue_context(
        self,
        execution_context: object,
        *,
        project_type: str = "",
        attributes: dict[str, Any] | None = None,
    ) -> CommandContext:
        """Create the provenance context used by one composed execution boundary."""
        if self._closed:
            raise _revoked_error()
        grant = _identity_payload(_BOUND_EXECUTIONS.get(self, {}), execution_context)
        if not isinstance(grant, _ExecutionGrant):
            raise PublicError(
                "COMMAND_CONTEXT_REQUIRED",
                "Use the command context from the composed execution boundary.",
                status_code=403,
            )
        context = CommandContext(
            subject=grant.subject,
            origin=f"extension:{grant.namespace}",
            namespace=grant.namespace,
            receipt_namespace=grant.receipt_namespace,
            correlation_id=grant.correlation_id,
            project_type=project_type,
            attributes=attributes or {},
            actor=grant.actor,
            actor_kind=grant.actor_kind,
            effect=grant.effect,
            risk=grant.risk,
        )
        issued = _ISSUED_COMMANDS.setdefault(self, {})
        _remember_identity(
            issued,
            context,
            _IssuedCommand(_command_signature(context), grant.read_name, grant.read_strong),
        )
        return context

    def _require_issued_context(self, context: CommandContext) -> None:
        # A context minted before the close is still a live object the handler
        # holds, so the write path re-checks the facade and not just the grant.
        if self._closed:
            raise _revoked_error()
        signature = _identity_payload(_ISSUED_COMMANDS.get(self, {}), context)
        if not isinstance(signature, _IssuedCommand) or signature.signature != _command_signature(
            context
        ):
            raise PublicError(
                "COMMAND_CONTEXT_REQUIRED",
                "Use the command context from the composed execution boundary.",
                status_code=403,
            )

    def _authorize(
        self,
        context: CommandContext,
        action: str,
        resource: PolicyResource,
        *,
        command: dict[str, Any] | None = None,
        approved: str = "",
    ) -> None:
        request = PolicyInput(
            subject=context.subject,
            action=action,
            resource=resource,
            origin=context.origin,
            context=context.attributes,
            tool_effect=context.effect,
            tool_risk=context.risk,
        )
        decision = self._policy.decide(request)
        if decision.effect == PolicyEffect.DENY:
            raise PublicError(
                "POLICY_DENIED",
                "The policy denied this action.",
                status_code=403,
                obligations=decision.obligations,
            )
        if decision.effect == PolicyEffect.REVIEW:
            if approved == action:
                # review.approve_change refreshed the requester, evaluated the
                # current policy, and checked the reviewer immediately before
                # this call. A second decision here could name approvers the
                # review service never checked, so run the one verdict that
                # already qualified. A deny above still refuses.
                #
                # Matched against the ACTION, never a blanket flag: one command
                # can meet two independent rules ("creates go to intake,
                # state changes go to the release board"), and a boolean let
                # the first approval satisfy both without anyone seeing the
                # second.
                return
            obligations = (
                *decision.obligations,
                *(f"approver-group:{group}" for group in decision.approver_groups),
                *(
                    f"approver-capability:{capability}"
                    for capability in decision.approver_capabilities
                ),
            )
            if command is not None:
                raise _HeldForReview(context, request, decision, command, obligations)
            raise PublicError(
                "REVIEW_REQUIRED",
                "A policy review is required before this action.",
                status_code=409,
                obligations=obligations,
            )

    @staticmethod
    def _prior_result(context: CommandContext, key: str, result_type: str) -> int:
        """Return the id a previous identical command produced, or 0.

        The receipt records result_type and nothing read it, so a key reused
        across two command types replayed one entity as another: the caller
        received a promise row it never wrote, at any tier, and no write
        happened. The type is part of the identity of the receipt.
        """
        if not key:
            return 0
        # Serialize the whole check-then-insert for THIS key. Two commands
        # carrying the same idempotency key both read "no receipt" otherwise,
        # both write, and the loser dies on the receipts primary key — which
        # is a 500 on a request whose entire purpose was to be safe to repeat.
        # Keyed on the receipt, so unrelated commands never wait on each other.
        db.name_lock(db.LOCK_RECEIPT, f"{context.receipt_namespace}|{key}")
        prior = db.query_one(
            "SELECT result_type, result_id FROM extension_command_receipts"
            " WHERE namespace = ? AND idempotency_key = ?",
            (context.receipt_namespace, key),
        )
        if not prior:
            return 0
        if str(prior["result_type"]) != result_type:
            raise PublicError(
                "IDEMPOTENCY_KEY_REUSED",
                "That idempotency key already names a different kind of record.",
                status_code=409,
            )
        return int(prior["result_id"])

    def _held_error(self, held: _HeldForReview) -> PublicError:
        review_id = self._queue_command(held.context, held.request, held.decision, held.command)
        return PublicError(
            "REVIEW_REQUIRED",
            "A policy review is required before this action.",
            status_code=409,
            obligations=held.obligations,
            review_id=review_id,
        )

    def _queue_command(
        self,
        context: CommandContext,
        request: PolicyInput,
        decision: PolicyDecision,
        command: dict[str, Any],
    ) -> int:
        """Store one reviewed command so a human can approve and run it.

        Without this a route answers 409 and a job answers
        POLICY_REVIEW_UNSUPPORTED, and neither leaves anything for a reviewer:
        an unattended integration could be stopped but never asked.
        """
        from ..extensions.policy import policy_subject_data
        from ..services import review as review_service

        invocation = {
            **command,
            # Which gate the reviewer answered. Resume disarms this action and
            # no other, so a command that meets a second independent rule is
            # held again rather than riding the first approval through.
            "action": request.action,
            "subject": policy_subject_data(context.subject),
            "namespace": context.namespace,
            "receipt_namespace": context.receipt_namespace,
            "correlation_id": context.correlation_id,
            "actor": context.actor,
            "actor_kind": context.actor_kind,
            "project_type": context.project_type,
            "attributes": dict(context.attributes),
            "effect": context.effect,
            "risk": context.risk,
        }
        proposal = review_service.propose_extension_invocation(
            "public_command",
            {
                "command": command["command"],
                "namespace": context.namespace,
                "action": request.action,
                "resource_type": request.resource.type,
                "resource_id": request.resource.id,
                # What the reviewer is answering. Without it the queue showed
                # the command name alone, so a status change bundled into a
                # create was invisible on the approve screen. Governed tools
                # carry review_preview for the same reason.
                "preview": _command_preview(command),
            },
            invocation,
            summary=f"Run {command['command']} from {context.namespace}",
            actor=context.execution_actor,
            requested_by=context.subject.name if context.subject.kind == "human" else "",
            policy_obligations=decision.obligations,
            approver_groups=decision.approver_groups,
            approver_capabilities=decision.approver_capabilities,
            review_visibility=(
                request.resource.classification
                if request.resource.classification in scope.TIERS
                else scope.WORKSPACE
            ),
            review_crew_id=int(request.resource.attributes.get("crew_id") or 0),
            policy_input=request,
        )
        return int(proposal["id"])

    def _permits(
        self,
        context: CommandContext,
        action: str,
        resource_type: str,
        resource_id: int,
        attributes: dict[str, str],
    ) -> bool:
        return (
            self._policy.decide(
                PolicyInput(
                    subject=context.subject,
                    action=action,
                    resource=PolicyResource(
                        resource_type,
                        str(resource_id),
                        project_type=str(attributes.get("project_type") or ""),
                        classification=str(attributes.get("classification") or ""),
                        attributes=attributes,
                    ),
                    origin=context.origin,
                    context=context.attributes,
                )
            ).effect
            == PolicyEffect.PERMIT
        )

    def _viewer(self, context: CommandContext) -> scope.Viewer:
        """Return only the read authority granted by the composition boundary."""
        issued = _identity_payload(_ISSUED_COMMANDS.get(self, {}), context)
        if not isinstance(issued, _IssuedCommand):
            return scope.NOBODY
        return scope.Viewer(issued.read_name, issued.read_strong)

    @staticmethod
    def _visible_engagement(engagement_id: int, viewer: scope.Viewer) -> dict | None:
        visible, params = scope.visible_filter(viewer, "engagements", "engagement")
        return db.query_one(
            f"SELECT engagement.id, engagement.project_class,"  # noqa: S608 -- scope emits only bound marks
            " engagement.visibility, engagement.crew_id"
            " FROM engagements engagement"
            f" WHERE engagement.id = ? AND {visible}",
            (engagement_id, *params),
        )

    @staticmethod
    def _visible_milestone(milestone_id: int, viewer: scope.Viewer) -> dict | None:
        milestone_visible, milestone_params = scope.visible_filter(
            viewer, "milestones", "milestone"
        )
        engagement_visible, engagement_params = scope.visible_filter(
            viewer, "engagements", "engagement"
        )
        row = db.query_one(
            f"SELECT milestone.id, milestone.engagement_id,"  # noqa: S608 -- scope emits only bound marks
            " milestone.visibility, milestone.crew_id,"
            " engagement.id AS visible_engagement_id, engagement.project_class,"
            " engagement.visibility AS engagement_visibility,"
            " engagement.crew_id AS engagement_crew_id"
            " FROM milestones milestone LEFT JOIN engagements engagement"
            " ON engagement.id = milestone.engagement_id"
            f" AND {engagement_visible}"
            f" WHERE milestone.id = ? AND {milestone_visible}",
            (*engagement_params, milestone_id, *milestone_params),
        )
        if not row or (row["engagement_id"] and not row["visible_engagement_id"]):
            return None
        return row

    def _relationship_context(
        self,
        engagement_id: int,
        milestone_id: int,
        viewer: scope.Viewer,
        *,
        fallback_project_type: str = "",
        error_code: str,
        conceal_as_task: int = 0,
        child_visibility: str = scope.WORKSPACE,
        child_crew_id: int | None = None,
    ) -> dict[str, Any]:
        """Resolve all task links without exposing an unreadable parent."""
        engagement = None
        milestone = None
        if engagement_id:
            engagement = self._visible_engagement(engagement_id, viewer)
            if engagement is None:
                table, row_id = (
                    ("tasks", conceal_as_task)
                    if conceal_as_task
                    else ("engagements", engagement_id)
                )
                raise PublicError(
                    error_code,
                    scope.missing_text(table, row_id),
                    status_code=404 if conceal_as_task else 400,
                )
        if milestone_id:
            milestone = self._visible_milestone(milestone_id, viewer)
            if milestone is None:
                table, row_id = (
                    ("tasks", conceal_as_task) if conceal_as_task else ("milestones", milestone_id)
                )
                raise PublicError(
                    error_code,
                    scope.missing_text(table, row_id),
                    status_code=404 if conceal_as_task else 400,
                )

        def require_containment(parent: dict, *, milestone_parent: bool = False) -> None:
            parent_visibility = (
                parent.get("engagement_visibility")
                if milestone_parent
                else parent.get("visibility")
            )
            parent_crew_id = (
                parent.get("engagement_crew_id") if milestone_parent else parent.get("crew_id")
            )
            if parent_visibility and not scope.relationship_contains(
                str(parent_visibility),
                parent_crew_id,
                child_visibility,
                child_crew_id,
            ):
                if conceal_as_task:
                    raise PublicError(
                        error_code,
                        scope.missing_text("tasks", conceal_as_task),
                        status_code=404,
                    )
                raise PublicError(
                    error_code,
                    "A task cannot be visible to more people than its linked work."
                    " Use the same or a narrower visibility.",
                )

        if engagement is not None:
            require_containment(engagement)
        if milestone is not None:
            require_containment(milestone)
            if milestone.get("visible_engagement_id"):
                require_containment(milestone, milestone_parent=True)
                if engagement is not None and int(engagement["id"]) != int(
                    milestone["visible_engagement_id"]
                ):
                    if conceal_as_task:
                        raise PublicError(
                            error_code,
                            scope.missing_text("tasks", conceal_as_task),
                            status_code=404,
                        )
                    raise PublicError(
                        error_code,
                        "A task's milestone and engagement must belong to the same engagement.",
                    )

        effective_engagement = engagement_id or int((milestone or {}).get("engagement_id") or 0)
        project_type = fallback_project_type
        if engagement is not None:
            project_type = str(engagement["project_class"] or "")
        elif milestone is not None and milestone["project_class"]:
            project_type = str(milestone["project_class"])
        attributes: dict[str, Any] = {"project_type": project_type}
        if effective_engagement:
            attributes["engagement_id"] = effective_engagement
        if milestone_id:
            attributes["milestone_id"] = milestone_id
        return attributes

    def _task_state(
        self, task_id: int, context: CommandContext
    ) -> tuple[dict, scope.Viewer, dict[str, Any]]:
        viewer = self._viewer(context)
        visible, params = scope.visible_filter(viewer, "tasks", "task")
        row = db.query_one(
            f"SELECT task.* FROM tasks task WHERE task.id = ? AND {visible}",  # noqa: S608 -- scope emits only bound marks
            (task_id, *params),
        )
        if row is None:
            raise PublicError(
                "TASK_NOT_FOUND",
                scope.missing_text("tasks", task_id),
                status_code=404,
            )
        attributes = self._relationship_context(
            int(row["engagement_id"] or 0),
            int(row["milestone_id"] or 0),
            viewer,
            error_code="TASK_NOT_FOUND",
            conceal_as_task=task_id,
            child_visibility=str(row["visibility"]),
            child_crew_id=row["crew_id"],
        )
        attributes.update(
            classification=str(row["visibility"] or ""),
            crew_id=str(row["crew_id"] or ""),
        )
        return row, viewer, attributes

    def get_task(self, task_id: int, context: CommandContext) -> TaskView:
        self._require_issued_context(context)
        # One snapshot binds relationship visibility, policy, and the view
        # returned to the extension. A concurrent relink cannot change the
        # project after policy evaluates it.
        with db.read_transaction():
            _row, viewer, attributes = self._task_state(task_id, context)
            self._authorize(
                context,
                "work.task.read",
                PolicyResource(
                    "task",
                    str(task_id),
                    project_type=attributes["project_type"],
                    classification=attributes["classification"],
                    attributes=attributes,
                ),
            )
            return self._task_view(task_id, context, viewer=viewer)

    def create_task(self, command: CreateTaskCommand, context: CommandContext) -> TaskView:
        # Keep linked-project resolution, policy, idempotency, and creation in
        # one serialized write boundary.
        try:
            with db.transaction():
                return self._create_task_locked(command, context)
        except _HeldForReview as held:
            raise self._held_error(held) from None

    def _create_task_locked(
        self,
        command: CreateTaskCommand,
        context: CommandContext,
        *,
        approved: str = "",
    ) -> TaskView:
        self._require_issued_context(context)
        viewer = self._viewer(context)
        link_attributes = self._relationship_context(
            command.engagement_id,
            command.milestone_id,
            viewer,
            fallback_project_type=context.project_type,
            error_code="TASK_CREATE_REJECTED",
            child_visibility=command.visibility,
            child_crew_id=command.crew_id or None,
        )
        requested_attributes = {
            **command.model_dump(exclude={"idempotency_key"}, mode="json"),
            **link_attributes,
            "classification": command.visibility,
            "crew_id": command.crew_id,
        }
        resumable = {"command": "create_task", "arguments": command.model_dump(mode="json")}
        self._authorize(
            context,
            "work.task.create",
            PolicyResource(
                "task",
                project_type=link_attributes["project_type"],
                classification=command.visibility,
                attributes=requested_attributes,
            ),
            command=resumable,
            approved=approved,
        )
        if command.status != "todo":
            self._authorize(
                context,
                "work.task.update",
                PolicyResource(
                    "task",
                    project_type=link_attributes["project_type"],
                    classification=command.visibility,
                    attributes=requested_attributes,
                ),
                command=resumable,
                approved=approved,
            )
        try:
            with db.transaction():
                prior_id = self._prior_result(context, command.idempotency_key, "task")
                if prior_id:
                    return self.get_task(prior_id, context)
                values = command.model_dump(exclude={"status", "idempotency_key"})
                result = work.create_task(
                    **values,
                    actor=context.execution_actor,
                    origin=context.origin,
                    correlation_id=context.correlation_id,
                    event_actor_kind=context.execution_actor_kind,
                )
                if command.status != "todo":
                    work.update_task(
                        result["id"],
                        status=command.status,
                        actor=context.execution_actor,
                        origin=context.origin,
                        note=f" through {context.origin}",
                        correlation_id=context.correlation_id,
                        event_actor_kind=context.execution_actor_kind,
                    )
                if command.idempotency_key:
                    db.execute(
                        "INSERT INTO extension_command_receipts"
                        " (namespace, idempotency_key, result_type, result_id, created_at)"
                        " VALUES (?, ?, 'task', ?, ?)",
                        (
                            context.receipt_namespace,
                            command.idempotency_key,
                            result["id"],
                            db.now(),
                        ),
                    )
                # This is the result of the caller's own write, not a query.
                # Do not manufacture a read-capable Viewer for a machine or a
                # weak identity merely to populate the command result.
                return self._written_task_view(result["id"])
        except PublicError:
            raise
        except (ValueError, PermissionError) as exc:
            raise PublicError("TASK_CREATE_REJECTED", str(exc)) from exc

    def get_promise(self, promise_id: int, context: CommandContext) -> PromiseView:
        self._require_issued_context(context)
        with db.read_transaction():
            row, attributes = self._promise_state(promise_id, context)
            self._authorize(
                context,
                "work.promise.read",
                PolicyResource(
                    "promise",
                    str(promise_id),
                    project_type=attributes["project_type"],
                    classification=attributes["classification"],
                    attributes=attributes,
                ),
            )
            return PromiseView.model_validate(row)

    def create_promise(self, command: CreatePromiseCommand, context: CommandContext) -> PromiseView:
        try:
            with db.transaction():
                return self._create_promise_locked(command, context)
        except _HeldForReview as held:
            raise self._held_error(held) from None

    def _create_promise_locked(
        self,
        command: CreatePromiseCommand,
        context: CommandContext,
        *,
        approved: str = "",
    ) -> PromiseView:
        self._require_issued_context(context)
        # The linked engagement owns the project class. Reading it from the
        # caller's context let a rule keyed on it govern task writes into a
        # regulated engagement and skip a promise written into the same one.
        project_type = self._linked_project_type(
            engagement_id=command.engagement_id, fallback=context.project_type
        )
        attributes = {
            **command.model_dump(exclude={"idempotency_key"}, mode="json"),
            "project_type": project_type,
            "classification": command.visibility,
            "crew_id": command.crew_id,
        }
        self._authorize(
            context,
            "work.promise.create",
            PolicyResource(
                "promise",
                project_type=project_type,
                classification=command.visibility,
                attributes=attributes,
            ),
            command={"command": "create_promise", "arguments": command.model_dump(mode="json")},
            approved=approved,
        )
        try:
            prior_id = self._prior_result(context, command.idempotency_key, "promise")
            if prior_id:
                return self.get_promise(prior_id, context)
            result = promises.add_promise(
                **command.model_dump(exclude={"idempotency_key"}),
                actor=context.execution_actor,
                origin=context.origin,
            )
            if command.idempotency_key:
                db.execute(
                    "INSERT INTO extension_command_receipts"
                    " (namespace, idempotency_key, result_type, result_id, created_at)"
                    " VALUES (?, ?, 'promise', ?, ?)",
                    (
                        context.receipt_namespace,
                        command.idempotency_key,
                        result["id"],
                        db.now(),
                    ),
                )
            return self._written_promise_view(int(result["id"]))
        except PublicError:
            raise
        except (ValueError, PermissionError) as exc:
            raise PublicError("PROMISE_CREATE_REJECTED", str(exc)) from exc

    def update_promise(self, command: UpdatePromiseCommand, context: CommandContext) -> PromiseView:
        try:
            with db.transaction():
                return self._update_promise_locked(command, context)
        except _HeldForReview as held:
            raise self._held_error(held) from None

    def _update_promise_locked(
        self,
        command: UpdatePromiseCommand,
        context: CommandContext,
        *,
        approved: str = "",
    ) -> PromiseView:
        self._require_issued_context(context)
        _row, attributes = self._promise_state(command.promise_id, context)
        self._authorize(
            context,
            "work.promise.update",
            PolicyResource(
                "promise",
                str(command.promise_id),
                project_type=attributes["project_type"],
                classification=attributes["classification"],
                attributes={**attributes, "status": command.status},
            ),
            command={"command": "update_promise", "arguments": command.model_dump(mode="json")},
            approved=approved,
        )
        try:
            promises.update_promise(
                command.promise_id,
                command.status,
                actor=context.execution_actor,
                origin=context.origin,
            )
            return self._written_promise_view(command.promise_id)
        except PublicError:
            raise
        except db.NotFound as exc:
            raise PublicError("PROMISE_NOT_FOUND", str(exc), status_code=404) from exc
        except PermissionError as exc:
            raise PublicError("PROMISE_UPDATE_FORBIDDEN", str(exc), status_code=403) from exc
        except ValueError as exc:
            raise PublicError("PROMISE_UPDATE_REJECTED", str(exc)) from exc

    def _promise_state(
        self, promise_id: int, context: CommandContext
    ) -> tuple[dict[str, Any], dict[str, str]]:
        row = self._visible_promise_row(promise_id, context)
        attributes = {
            # HIGH-5: the engagement owns the project class, not the caller. A
            # rule keyed on it governed task writes and silently skipped these.
            "project_type": self._promise_project_type(row, context),
            "classification": str(row.get("visibility") or scope.WORKSPACE),
            # A crew review must name its crew, and _queue_command reads it
            # from here. Without it a crew-tier hold raised a bare 400.
            "crew_id": str(row.get("crew_id") or ""),
            "audience": str(row.get("audience") or ""),
            "direction": str(row.get("direction") or ""),
            "status": str(row.get("status") or ""),
        }
        return row, attributes

    @staticmethod
    def _linked_project_type(
        *, engagement_id: int = 0, task_id: int = 0, fallback: str = ""
    ) -> str:
        """Resolve the project class from the row this write attaches to."""
        if engagement_id:
            row = db.query_one(
                "SELECT project_class FROM engagements WHERE id = ?", (engagement_id,)
            )
            if row:
                return str(row["project_class"])
        if task_id:
            row = db.query_one(
                "SELECT e.project_class FROM tasks t"
                " JOIN engagements e ON e.id = t.engagement_id WHERE t.id = ?",
                (task_id,),
            )
            if row:
                return str(row["project_class"])
        return fallback

    @staticmethod
    def _promise_project_type(row: dict[str, Any], context: CommandContext) -> str:
        """The linked engagement's class, falling back to what the caller said."""
        engagement_id = int(row.get("engagement_id") or 0)
        if not engagement_id:
            return context.project_type
        linked = db.query_one(
            "SELECT project_class FROM engagements WHERE id = ?", (engagement_id,)
        )
        return str(linked["project_class"]) if linked else context.project_type

    def _visible_promise_row(self, promise_id: int, context: CommandContext) -> dict[str, Any]:
        """Read one promise through the caller's own filter.

        An unfiltered SELECT here returned private rows to any caller: the
        policy engine permits a non-agent origin by default, so this filter is
        the only thing between an extension route and a teammate's private
        promise. _task_state does the same for tasks.
        """
        viewer = self._viewer(context)
        visible, params = scope.visible_filter(viewer, "promises")
        row = db.query_one(
            f"SELECT * FROM promises WHERE id = ? AND {visible}",  # noqa: S608 -- scope emits only bound marks
            (promise_id, *params),
        )
        if not row:
            raise PublicError(
                "PROMISE_NOT_FOUND",
                scope.missing_text("promises", promise_id),
                status_code=404,
            )
        return dict(row)

    @staticmethod
    def _promise_row(promise_id: int) -> dict[str, Any]:
        """The caller's own write receipt. Never used to serve a read."""
        row = db.query_one("SELECT * FROM promises WHERE id = ?", (promise_id,))
        if not row:
            raise PublicError(
                "PROMISE_NOT_FOUND",
                f"promise #{promise_id} not found",
                status_code=404,
            )
        return dict(row)

    @staticmethod
    def _written_promise_view(promise_id: int) -> PromiseView:
        return PromiseView.model_validate(WorkItems._promise_row(promise_id))

    def get_blocker(self, blocker_id: int, context: CommandContext) -> BlockerView:
        self._require_issued_context(context)
        with db.read_transaction():
            row, attributes = self._blocker_state(blocker_id, context)
            self._authorize(
                context,
                "work.blocker.read",
                PolicyResource(
                    "blocker",
                    str(blocker_id),
                    project_type=attributes["project_type"],
                    classification=attributes["classification"],
                    attributes=attributes,
                ),
            )
            return BlockerView.model_validate(dict(row))

    def create_blocker(self, command: CreateBlockerCommand, context: CommandContext) -> BlockerView:
        try:
            with db.transaction():
                return self._create_blocker_locked(command, context)
        except _HeldForReview as held:
            raise self._held_error(held) from None

    def _create_blocker_locked(
        self,
        command: CreateBlockerCommand,
        context: CommandContext,
        *,
        approved: str = "",
    ) -> BlockerView:
        self._require_issued_context(context)
        # raise_blocker flips the linked task to blocked, so the task's project
        # class governs this write even though the caller never names it.
        project_type = self._linked_project_type(
            task_id=command.task_id, fallback=context.project_type
        )
        attributes = {
            **command.model_dump(exclude={"idempotency_key"}, mode="json"),
            "project_type": project_type,
            "classification": command.visibility,
            "crew_id": command.crew_id,
        }
        self._authorize(
            context,
            "work.blocker.create",
            PolicyResource(
                "blocker",
                project_type=project_type,
                classification=command.visibility,
                attributes=attributes,
            ),
            command={"command": "create_blocker", "arguments": command.model_dump(mode="json")},
            approved=approved,
        )
        try:
            prior_id = self._prior_result(context, command.idempotency_key, "blocker")
            if prior_id:
                return self.get_blocker(prior_id, context)
            result = blockers.raise_blocker(
                **command.model_dump(exclude={"idempotency_key"}),
                actor=context.execution_actor,
                origin=context.origin,
            )
            if command.idempotency_key:
                db.execute(
                    "INSERT INTO extension_command_receipts"
                    " (namespace, idempotency_key, result_type, result_id, created_at)"
                    " VALUES (?, ?, 'blocker', ?, ?)",
                    (
                        context.receipt_namespace,
                        command.idempotency_key,
                        result["id"],
                        db.now(),
                    ),
                )
            return self._written_blocker_view(int(result["id"]))
        except PublicError:
            raise
        except (ValueError, PermissionError) as exc:
            raise PublicError("BLOCKER_CREATE_REJECTED", str(exc)) from exc

    def update_blocker(self, command: UpdateBlockerCommand, context: CommandContext) -> BlockerView:
        try:
            with db.transaction():
                return self._update_blocker_locked(command, context)
        except _HeldForReview as held:
            raise self._held_error(held) from None

    def _update_blocker_locked(
        self,
        command: UpdateBlockerCommand,
        context: CommandContext,
        *,
        approved: str = "",
    ) -> BlockerView:
        self._require_issued_context(context)
        _row, attributes = self._blocker_state(command.blocker_id, context)
        changes = command.model_dump(exclude={"blocker_id"}, exclude_none=True)
        if not changes:
            raise PublicError("EMPTY_COMMAND", "The command does not contain a change.")
        if command.status is not None and command.status != "resolved":
            # Escalation belongs to the scheduled sweep in services/blockers.py:
            # a caller that could set it would move the escalation clock.
            raise PublicError("BLOCKER_UPDATE_REJECTED", "A blocker update can only resolve it.")
        self._authorize(
            context,
            "work.blocker.update",
            PolicyResource(
                "blocker",
                str(command.blocker_id),
                project_type=attributes["project_type"],
                classification=attributes["classification"],
                attributes={**attributes, **changes},
            ),
            command={"command": "update_blocker", "arguments": command.model_dump(mode="json")},
            approved=approved,
        )
        try:
            edits = {
                key: value
                for key, value in changes.items()
                if key in ("title", "detail", "owner") and value is not None
            }
            if edits:
                blockers.edit_blocker(
                    command.blocker_id,
                    actor=context.execution_actor,
                    origin=context.origin,
                    **edits,
                )
            if command.status == "resolved":
                blockers.resolve_blocker(
                    command.blocker_id,
                    command.resolution or "",
                    actor=context.execution_actor,
                    origin=context.origin,
                )
            return self._written_blocker_view(command.blocker_id)
        except PublicError:
            raise
        except db.NotFound as exc:
            raise PublicError("BLOCKER_NOT_FOUND", str(exc), status_code=404) from exc
        except PermissionError as exc:
            raise PublicError("BLOCKER_UPDATE_FORBIDDEN", str(exc), status_code=403) from exc
        except ValueError as exc:
            raise PublicError("BLOCKER_UPDATE_REJECTED", str(exc)) from exc

    def _blocker_state(
        self, blocker_id: int, context: CommandContext
    ) -> tuple[dict[str, Any], dict[str, str]]:
        row = db.query_one("SELECT * FROM blockers WHERE id = ?", (blocker_id,))
        if not row:
            raise PublicError(
                "BLOCKER_NOT_FOUND",
                f"blocker #{blocker_id} not found",
                status_code=404,
            )
        attributes = blockers.existing_policy_context(blocker_id, actor=context.execution_actor)
        return dict(row), attributes

    @staticmethod
    def _written_blocker_view(blocker_id: int) -> BlockerView:
        row = db.query_one("SELECT * FROM blockers WHERE id = ?", (blocker_id,))
        if not row:
            raise PublicError(
                "BLOCKER_NOT_FOUND",
                f"blocker #{blocker_id} not found",
                status_code=404,
            )
        return BlockerView.model_validate(dict(row))

    def update_task(self, command: UpdateTaskCommand, context: CommandContext) -> TaskView:
        try:
            return self._update_task_locked(command, context)
        except _HeldForReview as held:
            raise self._held_error(held) from None

    def _update_task_locked(
        self,
        command: UpdateTaskCommand,
        context: CommandContext,
        *,
        approved: str = "",
    ) -> TaskView:
        self._require_issued_context(context)
        try:
            # One transaction over the authoritative target lookup, the
            # policy decision, and the mutation — with the target row HELD
            # inside it (policy_context.hold_resource), because a read alone
            # locks nothing. A concurrent relink cannot then move the task
            # under a stricter policy between the check and the write.
            with db.transaction():
                from ..services import policy_context

                policy_context.hold_resource("task", command.task_id)
                current_row, viewer, current_attributes = self._task_state(command.task_id, context)
                self._authorize(
                    context,
                    "work.task.read",
                    PolicyResource(
                        "task",
                        str(command.task_id),
                        project_type=current_attributes["project_type"],
                        classification=current_attributes["classification"],
                        attributes=current_attributes,
                    ),
                )
                changes = command.model_dump(exclude={"task_id"}, exclude_none=True)
                if not changes:
                    raise PublicError("EMPTY_COMMAND", "The command does not contain a change.")
                engagement_id = int(current_row["engagement_id"] or 0)
                milestone_id = int(current_row["milestone_id"] or 0)
                if changes.get("engagement_id"):
                    engagement_id = max(int(changes["engagement_id"]), 0)
                if changes.get("milestone_id"):
                    milestone_id = max(int(changes["milestone_id"]), 0)
                attributes = self._relationship_context(
                    engagement_id,
                    milestone_id,
                    viewer,
                    error_code="TASK_UPDATE_REJECTED",
                    child_visibility=str(current_row["visibility"]),
                    child_crew_id=current_row["crew_id"],
                )
                attributes.update(
                    classification=str(current_row["visibility"] or ""),
                    crew_id=str(current_row["crew_id"] or ""),
                )
                requested_attributes = {**changes, **attributes}
                self._authorize(
                    context,
                    "work.task.update",
                    PolicyResource(
                        "task",
                        str(command.task_id),
                        project_type=attributes["project_type"],
                        classification=attributes["classification"],
                        attributes=requested_attributes,
                    ),
                    command={
                        "command": "update_task",
                        "arguments": command.model_dump(mode="json"),
                    },
                    approved=approved,
                )
                work.update_task(
                    command.task_id,
                    **changes,
                    actor=context.execution_actor,
                    origin=context.origin,
                    note=f" through {context.origin}",
                    correlation_id=context.correlation_id,
                    event_actor_kind=context.execution_actor_kind,
                )
                result = self._task_view(command.task_id, context, viewer=viewer)
        except PublicError:
            raise
        except db.NotFound as exc:
            raise PublicError("TASK_NOT_FOUND", str(exc), status_code=404) from exc
        except PermissionError as exc:
            raise PublicError("TASK_UPDATE_FORBIDDEN", str(exc), status_code=403) from exc
        except ValueError as exc:
            raise PublicError("TASK_UPDATE_REJECTED", str(exc)) from exc
        return result

    def _task_view(
        self,
        task_id: int,
        context: CommandContext,
        *,
        viewer: scope.Viewer = scope.NOBODY,
    ) -> TaskView:
        try:
            task = work.get_task(task_id, viewer)
            task = work.redact_task_relationships(
                [task],
                viewer,
                lambda entity, entity_id, linked_attributes: self._permits(
                    context,
                    "work.task.read",
                    entity,
                    entity_id,
                    linked_attributes,
                ),
            )[0]
            return TaskView.model_validate(task)
        except db.NotFound as exc:
            raise PublicError("TASK_NOT_FOUND", str(exc), status_code=404) from exc

    @staticmethod
    def _written_task_view(task_id: int) -> TaskView:
        """Return the exact row created by this command as its write receipt."""
        row = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if row is None:
            raise PublicError(
                "TASK_NOT_FOUND",
                scope.missing_text("tasks", task_id),
                status_code=404,
            )
        return TaskView.model_validate(row)
