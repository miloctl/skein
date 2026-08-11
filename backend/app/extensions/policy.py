"""One policy decision contract for people, agents, tools, and workflows."""

from __future__ import annotations

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


class CorePolicy:
    """Safe defaults after every workplace rule has had a chance to narrow."""

    def __call__(self, request: PolicyInput) -> PolicyDecision:
        if request.tool and request.tool_effect == "unknown":
            return PolicyDecision(
                PolicyEffect.DENY,
                ("The tool does not declare whether it writes.",),
            )
        if request.tool and request.tool_effect in ("none", "read"):
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
                approver_capabilities=("skein.review",),
            )
        obligations = ("notify-team",) if level == "notify" else ()
        return PolicyDecision(PolicyEffect.PERMIT, obligations=obligations)


class PolicyEngine:
    """Combine independent rules without allowing one permit to erase a deny."""

    def __init__(self, rules: Sequence[PolicyRule] = ()) -> None:
        self._rules = (*tuple(rules), CorePolicy())

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


_DEFAULT_ENGINE = PolicyEngine()
_current_engine: ContextVar[PolicyEngine] = ContextVar(
    "skein_policy_engine", default=_DEFAULT_ENGINE
)
_current_subject: ContextVar[PolicySubject | None] = ContextVar(
    "skein_policy_subject", default=None
)


def current_policy_engine() -> PolicyEngine:
    return _current_engine.get()


def set_policy_engine(engine: PolicyEngine) -> Token:
    return _current_engine.set(engine)


def reset_policy_engine(token: Token) -> None:
    _current_engine.reset(token)


def current_policy_subject() -> PolicySubject:
    return _current_subject.get() or PolicySubject("agent", kind="agent")


def set_policy_subject(subject: PolicySubject) -> Token:
    return _current_subject.set(subject)


def reset_policy_subject(token: Token) -> None:
    _current_subject.reset(token)


IdentityMapper = Callable[[str, tuple[str, ...], bool], Mapping[str, Any]]
