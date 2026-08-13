"""One policy decision contract for people, agents, tools, and workflows."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol


class PolicyEffect(StrEnum):
    PERMIT = "permit"
    DENY = "deny"
    REVIEW = "review"


@dataclass(frozen=True)
class PolicySubject:
    name: str
    kind: str = "human"
    roles: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    attributes: Mapping[str, Any] = field(default_factory=dict)
    strong: bool = False
    source: str = "local"
    refresh_required: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "roles", tuple(self.roles))
        object.__setattr__(self, "groups", tuple(self.groups))
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))


@dataclass(frozen=True)
class PolicyResource:
    type: str
    id: str = ""
    project_type: str = ""
    classification: str = ""
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))


@dataclass(frozen=True)
class PolicyInput:
    subject: PolicySubject
    action: str
    resource: PolicyResource
    origin: str
    agent: str = ""
    tool: str = ""
    tool_effect: str = "none"
    tool_risk: str = "low"
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "context", MappingProxyType(dict(self.context)))


@dataclass(frozen=True)
class PolicyDecision:
    effect: PolicyEffect
    reasons: tuple[str, ...] = ()
    obligations: tuple[str, ...] = ()
    approver_groups: tuple[str, ...] = ()
    approver_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "reasons", tuple(self.reasons))
        object.__setattr__(self, "obligations", tuple(self.obligations))
        object.__setattr__(self, "approver_groups", tuple(self.approver_groups))
        object.__setattr__(self, "approver_capabilities", tuple(self.approver_capabilities))


class PolicyRule(Protocol):
    def __call__(self, request: PolicyInput) -> PolicyDecision | None: ...


@dataclass(frozen=True)
class ScopedPolicyRule:
    """Limit one contributed rule to its declared stable actions."""

    rule: PolicyRule
    actions: tuple[str, ...] = ()

    def __call__(self, request: PolicyInput) -> PolicyDecision | None:
        if self.actions and request.action not in self.actions:
            return None
        return self.rule(request)


class CorePolicy:
    """Safe defaults after every workplace rule has had a chance to narrow."""

    def __call__(self, request: PolicyInput) -> PolicyDecision:
        if request.resource.attributes.get("relationship_conflict"):
            return PolicyDecision(
                PolicyEffect.DENY,
                ("The resource has conflicting project relationships.",),
            )
        if request.tool and request.tool_effect == "unknown":
            return PolicyDecision(
                PolicyEffect.DENY,
                ("The tool does not declare whether it writes.",),
            )
        if request.tool and request.tool_effect in ("none", "read"):
            return PolicyDecision(PolicyEffect.PERMIT)
        if request.context.get("core_governance") == "specialized":
            return PolicyDecision(PolicyEffect.PERMIT)
        if request.origin not in ("agent", "agent_tool", "mcp"):
            return PolicyDecision(PolicyEffect.PERMIT)

        from .. import config
        from ..agents.identity import force_review
        from ..tools._gate import ALWAYS_REVIEW, effective_level

        level = effective_level(request.agent or request.subject.name, request.resource.type)
        if level == "forbidden":
            return PolicyDecision(
                PolicyEffect.DENY,
                ("The authority matrix forbids this action.",),
            )
        if (
            request.resource.type in ALWAYS_REVIEW
            or force_review()
            or (level == "review" and config.AGENT_REVIEW)
        ):
            return PolicyDecision(
                PolicyEffect.REVIEW,
                ("A human review is required.",),
            )
        obligations = ("notify-team",) if level == "notify" else ()
        return PolicyDecision(PolicyEffect.PERMIT, obligations=obligations)


class PolicyEngine:
    """Combine independent rules without allowing one permit to erase a deny."""

    def __init__(self, rules: Sequence[PolicyRule] = ()) -> None:
        self._workplace_rules = tuple(rules)
        self._rules = (*self._workplace_rules, CorePolicy())

    def has_workplace_rules_for(self, action: str) -> bool:
        """Return true when a contributed rule can inspect this action."""
        return any(
            not isinstance(rule, ScopedPolicyRule) or not rule.actions or action in rule.actions
            for rule in self._workplace_rules
        )

    def decide(self, request: PolicyInput) -> PolicyDecision:
        decisions = [decision for rule in self._rules if (decision := rule(request)) is not None]
        strongest = max(
            decisions,
            key=lambda item: {
                PolicyEffect.PERMIT: 0,
                PolicyEffect.REVIEW: 1,
                PolicyEffect.DENY: 2,
            }[item.effect],
        )
        same = [item for item in decisions if item.effect == strongest.effect]
        return PolicyDecision(
            strongest.effect,
            tuple(reason for item in same for reason in item.reasons),
            tuple(dict.fromkeys(value for item in same for value in item.obligations)),
            tuple(dict.fromkeys(value for item in same for value in item.approver_groups)),
            tuple(dict.fromkeys(value for item in same for value in item.approver_capabilities)),
        )


def permits_resource(
    engine: PolicyEngine,
    subject: PolicySubject,
    action: str,
    resource_type: str,
    resource_id: int | str,
    attributes: dict[str, str],
    origin: str,
    *,
    agent: str = "",
    tool: str = "",
) -> bool:
    """Apply one composed read decision to one authoritative resource row."""
    return (
        engine.decide(
            PolicyInput(
                subject,
                action,
                PolicyResource(
                    resource_type,
                    str(resource_id),
                    str(attributes.get("project_type") or ""),
                    str(attributes.get("classification") or ""),
                    attributes,
                ),
                origin,
                agent=agent,
                tool=tool,
                tool_effect="read" if tool else "none",
                tool_risk="low" if tool else "none",
            )
        ).effect
        == PolicyEffect.PERMIT
    )


def approval_fingerprint(
    request: PolicyInput,
    decision: PolicyDecision,
    contract: Mapping[str, Any],
) -> str:
    """Bind one verdict to the exact policy input and executable contract."""
    value = {
        "subject": {
            "name": request.subject.name,
            "kind": request.subject.kind,
            "roles": request.subject.roles,
            "groups": request.subject.groups,
            "capabilities": request.subject.capabilities,
            "attributes": request.subject.attributes,
            "strong": request.subject.strong,
            "source": request.subject.source,
            "refresh_required": request.subject.refresh_required,
        },
        "action": request.action,
        "resource": {
            "type": request.resource.type,
            "id": request.resource.id,
            "project_type": request.resource.project_type,
            "classification": request.resource.classification,
            "attributes": request.resource.attributes,
        },
        "origin": request.origin,
        "agent": request.agent,
        "tool": request.tool,
        "tool_effect": request.tool_effect,
        "tool_risk": request.tool_risk,
        "context": request.context,
        "decision": {
            "effect": decision.effect.value,
            "obligations": decision.obligations,
            "approver_groups": decision.approver_groups,
            "approver_capabilities": decision.approver_capabilities,
        },
        "contract": contract,
    }
    encoded = json.dumps(_plain(value), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def policy_input_data(request: PolicyInput) -> dict[str, Any]:
    """Return the stable JSON form stored with a reviewed core write."""
    return _plain(
        {
            "subject": {
                "name": request.subject.name,
                "kind": request.subject.kind,
                "roles": request.subject.roles,
                "groups": request.subject.groups,
                "capabilities": request.subject.capabilities,
                "attributes": request.subject.attributes,
                "strong": request.subject.strong,
                "source": request.subject.source,
                "refresh_required": request.subject.refresh_required,
            },
            "action": request.action,
            "resource": {
                "type": request.resource.type,
                "id": request.resource.id,
                "project_type": request.resource.project_type,
                "classification": request.resource.classification,
                "attributes": request.resource.attributes,
            },
            "origin": request.origin,
            "agent": request.agent,
            "tool": request.tool,
            "tool_effect": request.tool_effect,
            "tool_risk": request.tool_risk,
            "context": request.context,
        }
    )


def policy_decision_data(decision: PolicyDecision) -> dict[str, Any]:
    """Return the stable JSON form of one verdict-time policy decision."""
    return _plain(
        {
            "effect": decision.effect,
            "reasons": decision.reasons,
            "obligations": decision.obligations,
            "approver_groups": decision.approver_groups,
            "approver_capabilities": decision.approver_capabilities,
        }
    )


def policy_decision_from_data(value: Mapping[str, Any]) -> PolicyDecision:
    """Rebuild the exact policy decision qualified by a reviewer."""
    try:
        effect = PolicyEffect(str(value.get("effect") or ""))
    except ValueError as exc:
        raise ValueError("the reviewed policy decision is invalid") from exc

    def values(name: str) -> tuple[str, ...]:
        raw = value.get(name, ())
        if not isinstance(raw, (list, tuple)):
            raise ValueError("the reviewed policy decision is invalid")
        return tuple(str(item) for item in raw)

    return PolicyDecision(
        effect,
        reasons=values("reasons"),
        obligations=values("obligations"),
        approver_groups=values("approver_groups"),
        approver_capabilities=values("approver_capabilities"),
    )


def policy_subject_data(subject: PolicySubject) -> dict[str, Any]:
    """Return the stable JSON form of one authenticated policy subject."""
    return _plain(
        {
            "name": subject.name,
            "kind": subject.kind,
            "roles": subject.roles,
            "groups": subject.groups,
            "capabilities": subject.capabilities,
            "attributes": subject.attributes,
            "strong": subject.strong,
            "source": subject.source,
            "refresh_required": subject.refresh_required,
        }
    )


def policy_subject_from_data(value: Mapping[str, Any]) -> PolicySubject:
    """Rebuild one saved subject without increasing its assurance."""
    attributes = value.get("attributes")
    if not isinstance(attributes, Mapping):
        raise ValueError("the reviewed requester attributes are invalid")
    return PolicySubject(
        name=str(value.get("name") or ""),
        kind=str(value.get("kind") or "human"),
        roles=tuple(str(item) for item in value.get("roles") or ()),
        groups=tuple(str(item) for item in value.get("groups") or ()),
        capabilities=tuple(str(item) for item in value.get("capabilities") or ()),
        attributes=dict(attributes),
        strong=bool(value.get("strong", False)),
        source=str(value.get("source") or "local"),
        refresh_required=bool(value.get("refresh_required", False)),
    )


def policy_input_from_data(value: Mapping[str, Any], subject: PolicySubject) -> PolicyInput:
    """Rebuild a saved policy input with an authoritatively refreshed subject."""
    resource = value.get("resource")
    if not isinstance(resource, Mapping):
        raise ValueError("the reviewed policy resource is invalid")
    context = value.get("context")
    if not isinstance(context, Mapping):
        raise ValueError("the reviewed policy context is invalid")
    attributes = resource.get("attributes")
    if not isinstance(attributes, Mapping):
        raise ValueError("the reviewed policy resource attributes are invalid")
    return PolicyInput(
        subject=subject,
        action=str(value.get("action") or ""),
        resource=PolicyResource(
            type=str(resource.get("type") or ""),
            id=str(resource.get("id") or ""),
            project_type=str(resource.get("project_type") or ""),
            classification=str(resource.get("classification") or ""),
            attributes=dict(attributes),
        ),
        origin=str(value.get("origin") or "agent"),
        agent=str(value.get("agent") or ""),
        tool=str(value.get("tool") or ""),
        tool_effect=str(value.get("tool_effect") or "none"),
        tool_risk=str(value.get("tool_risk") or "low"),
        context=dict(context),
    )


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_plain(item) for item in value]
    if isinstance(value, PolicyEffect):
        return value.value
    return value


_current_engine: ContextVar[PolicyEngine | None] = ContextVar("skein_policy_engine", default=None)
_current_subject: ContextVar[PolicySubject | None] = ContextVar(
    "skein_policy_subject", default=None
)


def current_policy_engine() -> PolicyEngine:
    # A core-rules-only fallback here would silently drop every workplace
    # rule on an entry point that forgot set_policy_engine. Fail closed.
    engine = _current_engine.get()
    if engine is None:
        raise RuntimeError(
            "No policy engine is installed in this execution context."
            " Install the composed engine with set_policy_engine before"
            " dispatching chat, tool, MCP, or agent work."
        )
    return engine


def set_policy_engine(engine: PolicyEngine) -> Token:
    return _current_engine.set(engine)


def reset_policy_engine(token: Token) -> None:
    _current_engine.reset(token)


def current_policy_subject() -> PolicySubject:
    return _current_subject.get() or PolicySubject(
        "agent", kind="agent", strong=True, source="agent"
    )


def set_policy_subject(subject: PolicySubject) -> Token:
    return _current_subject.set(subject)


def reset_policy_subject(token: Token) -> None:
    _current_subject.reset(token)


IdentityMapper = Callable[[str, tuple[str, ...], bool], Mapping[str, Any]]
