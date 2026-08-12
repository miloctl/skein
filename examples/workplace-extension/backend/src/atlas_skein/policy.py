"""Fictional directory mapping and workplace policy."""

from dataclasses import dataclass

from app.extensions import PolicyDecision, PolicyEffect, PolicyInput


def atlas_identity(name: str, groups: tuple[str, ...], _strong: bool) -> dict[str, tuple[str, ...]]:
    capabilities: list[str] = []
    roles: list[str] = []
    if "atlas-delivery-managers" in groups:
        capabilities.extend(("atlas.dashboard", "atlas.approve", "atlas.specialist"))
        roles.append("delivery-manager")
    if "atlas-integrations" in groups:
        capabilities.append("atlas.integration")
    return {"roles": tuple(roles), "capabilities": tuple(capabilities)}


def atlas_directory(name: str) -> dict[str, object] | None:
    """Fictional directory refresh used by the executable example.

    A real private package calls its directory adapter here. Returning no
    record makes approval fail closed.
    """
    if name == "mira":
        return {"active": True, "groups": ("atlas-delivery-managers",)}
    if name == "ava":
        return {"active": True, "groups": ()}
    return None


def atlas_profile(_name: str) -> dict[str, object]:
    """Return active profile state without owning directory groups."""
    return {"active": True}


_ATLAS_POLICY_ACTIONS = (
    "atlas.dashboard.view",
    "atlas.integration.sync",
    "atlas.release.approve",
)


def _atlas_policy(request: PolicyInput) -> PolicyDecision | None:
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


@dataclass(frozen=True)
class _AtlasPolicy:
    """Carry action scope without requiring a newer PolicyContribution constructor."""

    actions: tuple[str, ...] = _ATLAS_POLICY_ACTIONS

    def __call__(self, request: PolicyInput) -> PolicyDecision | None:
        if request.action not in self.actions:
            return None
        return _atlas_policy(request)


atlas_policy = _AtlasPolicy()
