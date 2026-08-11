"""FastAPI adapters for the public identity and policy contracts."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Request

from ..routes.deps import CurrentUser
from .policy import PolicyDecision, PolicyInput, PolicyResource, PolicySubject


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
