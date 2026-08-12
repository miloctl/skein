"""FastAPI adapters for the public identity and policy contracts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Annotated, Any
from uuid import uuid4

from fastapi import Depends, Header, Request
from fastapi.routing import APIRoute

from ..public.errors import PublicError
from ..routes.deps import CurrentUser
from .policy import (
    PolicyDecision,
    PolicyEffect,
    PolicyEngine,
    PolicyInput,
    PolicyResource,
    PolicySubject,
)

if TYPE_CHECKING:
    from ..public.work import CommandContext, WorkItems
    from .contracts import RouteContribution


def subject_for(request: Request, user: str) -> PolicySubject:
    groups = tuple(getattr(request.state, "auth_groups", ()))
    strong = bool(getattr(request.state, "strong_auth", False))
    source = str(getattr(request.state, "auth_source", "trusted-header"))
    registry = request.app.state.skein_registry
    attributes = registry.identity_attributes(user, groups, strong)
    roles = tuple(str(value) for value in attributes.pop("roles", ()))
    capabilities = tuple(str(value) for value in attributes.pop("capabilities", ()))
    return PolicySubject(
        name=user,
        roles=roles,
        groups=groups,
        capabilities=capabilities,
        attributes=attributes,
        strong=strong,
        source=source,
        refresh_required=source == "oidc",
    )


def policy_subject(request: Request, user: CurrentUser) -> PolicySubject:
    return subject_for(request, user)


PolicySubjectDep = Annotated[PolicySubject, Depends(policy_subject)]


@dataclass(frozen=True)
class ExtensionRouteServices:
    """Public composed services for one trusted extension route."""

    subject: PolicySubject
    policy: PolicyEngine
    work_items: WorkItems
    namespace: str = ""
    correlation_id: str = ""

    def command_context(
        self,
        *,
        project_type: str = "",
        attributes: dict[str, Any] | None = None,
    ) -> CommandContext:
        """Return the command context bound to this route contribution."""
        return self.work_items._issue_context(
            self,
            project_type=project_type,
            attributes=attributes,
        )


def extension_route_services(request: Request, subject: PolicySubjectDep) -> ExtensionRouteServices:
    from ..public.work import WorkItems, _bind_execution_context

    policy = request.app.state.skein_registry.policy_engine
    action, _resource_type, _resource_id = _route_policy_action(request)
    namespace = str(getattr(request.state, "skein_extension_namespace", action))
    work_items = WorkItems(policy)
    correlation_id = uuid4().hex
    context = ExtensionRouteServices(subject, policy, work_items, namespace, correlation_id)
    return _bind_execution_context(
        work_items,
        context,
        subject=subject,
        namespace=namespace,
        receipt_namespace=f"route:{namespace}",
        correlation_id=correlation_id,
        read_name=subject.name if subject.kind == "human" else "",
        read_strong=subject.strong if subject.kind == "human" else False,
    )


ExtensionRouteServicesDep = Annotated[
    ExtensionRouteServices,
    Depends(extension_route_services),
]


def contributed_route_policy(contribution: RouteContribution):
    """Create the domain-policy dependency for one trusted router."""

    async def enforce(request: Request, subject: PolicySubjectDep) -> None:
        route = request.scope.get("route")
        path = str(getattr(route, "path", request.url.path))
        operation = next(
            item
            for item in contribution.operations
            if item.method == request.method and item.path == path
        )
        request.state.skein_extension_namespace = contribution.name
        resource = operation.resource
        if operation.resource_id_param:
            resource = replace(
                resource,
                id=str(request.path_params.get(operation.resource_id_param) or ""),
            )
        decision = request.app.state.skein_registry.policy_engine.decide(
            PolicyInput(
                subject,
                operation.policy_action,
                resource,
                "human",
                tool=contribution.name,
                tool_effect=operation.effect,
                tool_risk=operation.risk,
            )
        )
        enforce_decision(decision)

    return enforce


def decide(
    request: Request,
    subject: PolicySubject,
    action: str,
    resource_type: str,
    *,
    resource_id: str = "",
    project_type: str = "",
    classification: str = "",
    origin: str = "human",
    attributes: dict[str, Any] | None = None,
) -> PolicyDecision:
    engine = request.app.state.skein_registry.policy_engine
    return engine.decide(
        _policy_input(
            subject,
            action,
            resource_type,
            resource_id=resource_id,
            project_type=project_type,
            classification=classification,
            origin=origin,
            attributes=attributes,
        )
    )


def _policy_input(
    subject: PolicySubject,
    action: str,
    resource_type: str,
    *,
    resource_id: str = "",
    project_type: str = "",
    classification: str = "",
    origin: str = "human",
    attributes: dict[str, Any] | None = None,
) -> PolicyInput:
    return PolicyInput(
        subject=subject,
        action=action,
        resource=PolicyResource(
            resource_type,
            resource_id,
            project_type,
            classification,
            attributes or {},
        ),
        origin=origin,
    )


def _route_policy_action(request: Request) -> tuple[str, str, str]:
    """Return a stable action, resource type, and resource identifier."""
    route = request.scope.get("route")
    template = str(getattr(route, "path", request.url.path))
    literals = [
        segment
        for segment in template.strip("/").split("/")
        if segment and segment != "api" and not segment.startswith("{")
    ]
    resource_type = literals[0] if literals else "api"
    if request.method == "POST" and template == "/api/playbooks/instantiate":
        # Use the same domain action as deterministic chat and agent tools.
        # The resource context below supplies the definition's project class.
        action = "playbook.create"
    else:
        action = ".".join(("skein", "rest", request.method.lower(), *literals))
    resource_id = next(
        (str(value) for value in request.path_params.values() if value is not None),
        "",
    )
    return action, resource_type, resource_id


async def enforce_mutation_policy(
    request: Request,
    x_user: Annotated[str, Header()] = "",
    authorization: Annotated[str, Header()] = "",
) -> None:
    """Apply workplace policy before one authenticated REST operation."""

    # The calendar feed has its own query-token gate because calendar clients
    # cannot send API headers. It is not an authenticated REST operation.
    if request.url.path == "/api/calendar.ics":
        return

    # Use the existing identity swap point. Do not record adoption here:
    # the route's normal user dependency owns that one record and still
    # enforces its stronger identity requirement.
    from ..routes.deps import _resolve, authentication_source

    user, strong, groups = _resolve(x_user, authorization, request.method, request)
    registry = request.app.state.skein_registry
    attributes = registry.identity_attributes(user, tuple(groups), strong)
    roles = tuple(str(value) for value in attributes.pop("roles", ()))
    capabilities = tuple(str(value) for value in attributes.pop("capabilities", ()))
    source = authentication_source(request, authorization, strong)
    subject = PolicySubject(
        user,
        roles=roles,
        groups=tuple(groups),
        capabilities=capabilities,
        attributes=attributes,
        strong=strong,
        source=source,
        refresh_required=source == "oidc",
    )
    action, resource_type, resource_id = _route_policy_action(request)
    payload: dict[str, Any] = {}
    if request.headers.get("content-type", "").split(";", 1)[0] == "application/json":
        try:
            candidate = await request.json()
            if isinstance(candidate, dict):
                payload = candidate
        except ValueError:
            # FastAPI owns the public request-validation error. Policy still
            # applies to the route and current resource without trusting a
            # malformed body.
            pass
    from ..services.policy_context import for_route

    # Task routes perform an actor-visible, transaction-bound relationship
    # decision in their handler. The generic early gate must not inspect a
    # hidden target id first, because a project-specific DENY would become a
    # project-class oracle before the service returns the stable missing-id
    # refusal.
    if resource_type == "tasks" and (
        request.method in {"POST", "PATCH"} or (request.method == "GET" and resource_id)
    ):
        domain = {}
    else:
        domain = for_route(resource_type, resource_id, payload)
    if action == "playbook.create":
        request.state.skein_playbook_policy_context = dict(domain)
    policy_input = _policy_input(
        subject,
        action,
        resource_type,
        resource_id=resource_id,
        project_type=domain.get("project_type", ""),
        classification=domain.get("classification", ""),
    )
    decision = request.app.state.skein_registry.policy_engine.decide(policy_input)
    if action == "playbook.create" and decision.effect == PolicyEffect.REVIEW:
        # The playbook route has a durable, exact-input review adapter. Other
        # direct REST mutations remain fail-closed because they cannot resume
        # a reviewed call safely.
        request.state.skein_playbook_policy_review = True
        request.state.skein_playbook_policy_input = policy_input
        request.state.skein_playbook_policy_decision = decision
        return
    enforce_decision(decision)


def enforce_decision(decision: PolicyDecision) -> None:
    """Convert one public policy result into the stable HTTP error contract."""
    if decision.effect.value == "permit":
        return
    obligations = tuple(decision.obligations)
    obligations += tuple(f"approver-group:{value}" for value in decision.approver_groups)
    obligations += tuple(f"approver-capability:{value}" for value in decision.approver_capabilities)
    if decision.effect.value == "review":
        raise PublicError(
            "POLICY_REVIEW_UNSUPPORTED",
            "This direct route cannot resume a reviewed action. Use a governed tool or workflow.",
            status_code=403,
            obligations=tuple(dict.fromkeys(obligations)),
        )
    raise PublicError(
        "POLICY_DENIED",
        "The policy denied this action.",
        status_code=403,
        obligations=tuple(dict.fromkeys(obligations)),
    )


class PolicyAPIRoute(APIRoute):
    """Add the central policy dependency to authenticated routes."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        methods = {str(value).upper() for value in kwargs.get("methods", ())}
        if methods:
            dependencies = list(kwargs.get("dependencies") or ())
            dependencies.insert(0, Depends(enforce_mutation_policy))
            kwargs["dependencies"] = dependencies
        super().__init__(*args, **kwargs)
