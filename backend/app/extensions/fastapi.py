"""FastAPI adapters for the public identity and policy contracts."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Header, Request
from fastapi.routing import APIRoute

from ..public.errors import PublicError
from ..routes.deps import CurrentUser
from .policy import PolicyDecision, PolicyInput, PolicyResource, PolicySubject

_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def subject_for(request: Request, user: str) -> PolicySubject:
    groups = tuple(getattr(request.state, "auth_groups", ()))
    strong = bool(getattr(request.state, "strong_auth", False))
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
    )


def policy_subject(request: Request, user: CurrentUser) -> PolicySubject:
    return subject_for(request, user)


PolicySubjectDep = Annotated[PolicySubject, Depends(policy_subject)]


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
        PolicyInput(
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
    action = ".".join(("skein", "rest", request.method.lower(), *literals))
    resource_id = next(
        (str(value) for value in request.path_params.values() if value is not None),
        "",
    )
    return action, resource_type, resource_id


def enforce_mutation_policy(
    request: Request,
    x_user: Annotated[str, Header()] = "",
    authorization: Annotated[str, Header()] = "",
) -> None:
    """Apply workplace policy before one authenticated REST mutation."""
    if request.method in _READ_METHODS:
        return

    # Use the existing identity swap point. Do not record adoption here:
    # the route's normal user dependency owns that one record and still
    # enforces its stronger identity requirement.
    from ..routes.deps import _resolve

    user, strong, groups = _resolve(x_user, authorization, request.method, request)
    registry = request.app.state.skein_registry
    attributes = registry.identity_attributes(user, tuple(groups), strong)
    roles = tuple(str(value) for value in attributes.pop("roles", ()))
    capabilities = tuple(str(value) for value in attributes.pop("capabilities", ()))
    subject = PolicySubject(
        user,
        roles=roles,
        groups=tuple(groups),
        capabilities=capabilities,
        attributes=attributes,
    )
    action, resource_type, resource_id = _route_policy_action(request)
    decision = decide(
        request,
        subject,
        action,
        resource_type,
        resource_id=resource_id,
    )
    if decision.effect.value == "permit":
        return

    obligations = tuple(decision.obligations)
    obligations += tuple(f"approver-group:{value}" for value in decision.approver_groups)
    obligations += tuple(f"approver-capability:{value}" for value in decision.approver_capabilities)
    if decision.effect.value == "review":
        raise PublicError(
            "POLICY_REVIEW_REQUIRED",
            "This action needs review before it can run.",
            status_code=409,
            obligations=tuple(dict.fromkeys(obligations)),
        )
    raise PublicError(
        "POLICY_DENIED",
        "The policy denied this action.",
        status_code=403,
        obligations=tuple(dict.fromkeys(obligations)),
    )


class PolicyAPIRoute(APIRoute):
    """Add the central policy dependency to mutation routes."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        methods = {str(value).upper() for value in kwargs.get("methods", ())}
        if methods and not methods.issubset(_READ_METHODS):
            dependencies = list(kwargs.get("dependencies") or ())
            dependencies.insert(0, Depends(enforce_mutation_policy))
            kwargs["dependencies"] = dependencies
        super().__init__(*args, **kwargs)
