"""Fictional directory mapping and workplace policy."""

from app.extensions import PolicyDecision, PolicyEffect, PolicyInput


def atlas_identity(name: str, groups: tuple[str, ...], _strong: bool):
    capabilities = []
    roles = []
    if "atlas-delivery-managers" in groups:
        capabilities.extend(("atlas.dashboard", "atlas.approve", "atlas.specialist"))
        roles.append("delivery-manager")
    if "atlas-integrations" in groups:
        capabilities.append("atlas.integration")
    if name in ("atlas-sync", "atlas-events"):
        capabilities.append("atlas.integration")
    return {"roles": tuple(roles), "capabilities": tuple(capabilities)}


def atlas_policy(request: PolicyInput):
    capabilities = set(request.subject.capabilities)
    if request.action == "atlas.dashboard.view" and "atlas.dashboard" not in capabilities:
        return PolicyDecision(PolicyEffect.DENY, ("The manager dashboard needs Atlas access.",))
    if request.action == "atlas.integration.sync" and "atlas.integration" not in capabilities:
        return PolicyDecision(PolicyEffect.DENY, ("The Atlas integration capability is required.",))
    if (
        request.action == "atlas.release.approve"
        and (
            request.resource.project_type == "regulated"
            or request.tool_risk in ("high", "critical")
        )
        and "atlas.approve" not in capabilities
    ):
        return PolicyDecision(
            PolicyEffect.REVIEW,
            ("A delivery manager must approve this regulated action.",),
            approver_groups=("atlas-delivery-managers",),
            approver_capabilities=("atlas.approve",),
        )
    return None
