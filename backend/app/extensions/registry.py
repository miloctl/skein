"""Validation and immutable indexing for explicit Skein modules."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .contracts import (
    EXTENSION_API_VERSION,
    SKEIN_CORE_VERSION,
    ContextContribution,
    IdentityContribution,
    JobContribution,
    LifecycleContribution,
    PolicyContribution,
    RouteContribution,
    SkeinModule,
    SpecialistContribution,
    ToolContribution,
)
from .policy import PolicyEngine

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")
_MODEL_TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class ExtensionValidationError(ValueError):
    """A module cannot be composed safely."""


def _version(value: str, label: str) -> tuple[int, int, int]:
    parts = value.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ExtensionValidationError(f"{label} must be a three-part numeric version")
    return int(parts[0]), int(parts[1]), int(parts[2])


@dataclass(frozen=True)
class ExtensionRegistry:
    """The validated contributions for one application instance."""

    modules: tuple[SkeinModule, ...]
    routes: tuple[RouteContribution, ...]
    jobs: tuple[JobContribution, ...]
    lifecycle: tuple[LifecycleContribution, ...]
    policies: tuple[PolicyContribution, ...]
    identities: tuple[IdentityContribution, ...]
    contexts: tuple[ContextContribution, ...]
    tools: tuple[ToolContribution, ...]
    specialists: tuple[SpecialistContribution, ...]

    @property
    def policy_engine(self) -> PolicyEngine:
        ordered = sorted(self.policies, key=lambda contribution: contribution.priority)
        return PolicyEngine(tuple(contribution.rule for contribution in ordered))

    def identity_attributes(
        self, name: str, groups: tuple[str, ...], strong: bool
    ) -> dict[str, object]:
        attributes: dict[str, object] = {}
        for contribution in self.identities:
            contributed = dict(contribution.mapper(name, groups, strong))
            overlap = set(attributes) & set(contributed)
            if overlap:
                raise ExtensionValidationError(
                    f"identity attribute collision: {', '.join(sorted(overlap))}"
                )
            attributes.update(contributed)
        return attributes

    def tool(self, name: str) -> ToolContribution:
        try:
            return next(contribution for contribution in self.tools if contribution.name == name)
        except StopIteration as exc:
            raise ExtensionValidationError(f"unknown contributed tool {name!r}") from exc

    def specialist(self, name: str) -> SpecialistContribution:
        try:
            return next(
                contribution for contribution in self.specialists if contribution.name == name
            )
        except StopIteration as exc:
            raise ExtensionValidationError(f"unknown contributed specialist {name!r}") from exc

    @classmethod
    def build(cls, modules: tuple[SkeinModule, ...]) -> ExtensionRegistry:
        ordered = _order_modules(modules)
        routes: list[RouteContribution] = []
        jobs: list[JobContribution] = []
        lifecycle: list[LifecycleContribution] = []
        policies: list[PolicyContribution] = []
        identities: list[IdentityContribution] = []
        contexts: list[ContextContribution] = []
        tools: list[ToolContribution] = []
        specialists: list[SpecialistContribution] = []
        names: dict[str, str] = {}
        route_signatures: dict[tuple[str, str], str] = {}

        for module in ordered:
            _validate_module(module)
            for kind, contributions in (
                ("route", module.routes),
                ("job", module.jobs),
                ("lifecycle", module.lifecycle),
                ("policy", module.policies),
                ("identity", module.identities),
                ("context", module.contexts),
                ("tool", module.tools),
                ("specialist", module.specialists),
            ):
                for contribution in contributions:
                    key = f"{kind}:{contribution.name}"
                    if key in names:
                        raise ExtensionValidationError(
                            f"duplicate {kind} contribution {contribution.name!r}"
                        )
                    names[key] = module.module_id
            _validate_namespace(module)
            for route_contribution in module.routes:
                for route in route_contribution.router.routes:
                    path = getattr(route, "path", "")
                    for method in getattr(route, "methods", ()):
                        signature = method, path
                        if signature in route_signatures:
                            owner = route_signatures[signature]
                            raise ExtensionValidationError(
                                f"route collision for {method} {path}: {owner!r} and "
                                f"{module.module_id!r}"
                            )
                        route_signatures[signature] = module.module_id
            routes.extend(module.routes)
            jobs.extend(module.jobs)
            lifecycle.extend(module.lifecycle)
            policies.extend(module.policies)
            identities.extend(module.identities)
            contexts.extend(module.contexts)
            tools.extend(module.tools)
            specialists.extend(module.specialists)

        tool_names = {contribution.name for contribution in tools}
        model_tool_names = [contribution.model_name for contribution in tools]
        if len(model_tool_names) != len(set(model_tool_names)):
            raise ExtensionValidationError("duplicate model-facing tool name")
        context_names = {contribution.name for contribution in contexts}
        for specialist in specialists:
            missing = sorted(
                (set(specialist.tools) - tool_names)
                | (set(specialist.context_sources) - context_names)
            )
            if missing:
                raise ExtensionValidationError(
                    f"specialist {specialist.name!r} has unknown contributions: "
                    f"{', '.join(missing)}"
                )

        return cls(
            ordered,
            tuple(routes),
            tuple(jobs),
            tuple(lifecycle),
            tuple(policies),
            tuple(identities),
            tuple(contexts),
            tuple(tools),
            tuple(specialists),
        )


def _order_modules(modules: tuple[SkeinModule, ...]) -> tuple[SkeinModule, ...]:
    by_id: dict[str, SkeinModule] = {}
    for module in modules:
        if module.module_id in by_id:
            raise ExtensionValidationError(f"duplicate module id {module.module_id!r}")
        by_id[module.module_id] = module

    missing = sorted(
        {
            requirement
            for module in modules
            for requirement in module.requires
            if requirement not in by_id
        }
    )
    if missing:
        raise ExtensionValidationError(f"missing required modules: {', '.join(missing)}")

    result: list[SkeinModule] = []
    remaining = dict(by_id)
    while remaining:
        ready = sorted(
            (
                module_id
                for module_id, module in remaining.items()
                if all(requirement not in remaining for requirement in module.requires)
            ),
            key=lambda module_id: (module_id != "skein.core", module_id),
        )
        if not ready:
            raise ExtensionValidationError("module dependency cycle")
        for module_id in ready:
            result.append(remaining.pop(module_id))
    return tuple(result)


def _validate_module(module: SkeinModule) -> None:
    if not _IDENTIFIER.fullmatch(module.module_id):
        raise ExtensionValidationError(f"invalid module id {module.module_id!r}")
    if module.extension_api != EXTENSION_API_VERSION:
        raise ExtensionValidationError(
            f"module {module.module_id!r} needs extension API {module.extension_api}; "
            f"this core provides {EXTENSION_API_VERSION}"
        )
    core = _version(SKEIN_CORE_VERSION, "core version")
    minimum = _version(module.minimum_core, "minimum_core")
    maximum = _version(module.maximum_core_exclusive, "maximum_core_exclusive")
    if not minimum <= core < maximum:
        raise ExtensionValidationError(
            f"module {module.module_id!r} supports core versions from "
            f"{module.minimum_core} up to but not including {module.maximum_core_exclusive}"
        )
    for route_contribution in module.routes:
        if not _IDENTIFIER.fullmatch(route_contribution.name):
            raise ExtensionValidationError(
                f"module {module.module_id!r} has invalid contribution name "
                f"{route_contribution.name!r}"
            )
    for job_contribution in module.jobs:
        if not _IDENTIFIER.fullmatch(job_contribution.name):
            raise ExtensionValidationError(
                f"module {module.module_id!r} has invalid contribution name "
                f"{job_contribution.name!r}"
            )
    for lifecycle_contribution in module.lifecycle:
        if not _IDENTIFIER.fullmatch(lifecycle_contribution.name):
            raise ExtensionValidationError(
                f"module {module.module_id!r} has invalid contribution name "
                f"{lifecycle_contribution.name!r}"
            )
    for policy_contribution in module.policies:
        _validate_contribution_name(module, policy_contribution.name)
    for identity_contribution in module.identities:
        _validate_contribution_name(module, identity_contribution.name)
    for context_contribution in module.contexts:
        _validate_contribution_name(module, context_contribution.name)
    for tool_contribution in module.tools:
        _validate_contribution_name(module, tool_contribution.name)
        _version(tool_contribution.version, f"tool {tool_contribution.name} version")
        if not _MODEL_TOOL_NAME.fullmatch(tool_contribution.model_name):
            raise ExtensionValidationError(
                f"tool {tool_contribution.name!r} has invalid model_name"
            )
        if tool_contribution.effect not in ("none", "read", "write", "unknown"):
            raise ExtensionValidationError(f"tool {tool_contribution.name!r} has invalid effect")
        if tool_contribution.risk not in ("low", "medium", "high", "critical"):
            raise ExtensionValidationError(f"tool {tool_contribution.name!r} has invalid risk")
        if tool_contribution.timeout_seconds <= 0:
            raise ExtensionValidationError(
                f"tool {tool_contribution.name!r} needs a positive timeout"
            )
        if tool_contribution.receipt != "required" or tool_contribution.provenance != "service":
            raise ExtensionValidationError(
                f"tool {tool_contribution.name!r} must require receipts and service provenance"
            )
    for specialist_contribution in module.specialists:
        _validate_contribution_name(module, specialist_contribution.name)
        _version(
            specialist_contribution.version,
            f"specialist {specialist_contribution.name} version",
        )


def _validate_contribution_name(module: SkeinModule, name: str) -> None:
    if not _IDENTIFIER.fullmatch(name):
        raise ExtensionValidationError(
            f"module {module.module_id!r} has invalid contribution name {name!r}"
        )


def _validate_namespace(module: SkeinModule) -> None:
    if module.module_id == "skein.core":
        return
    prefix = f"/api/extensions/{module.module_id}/"
    bare_prefix = prefix.removesuffix("/")
    for route_contribution in module.routes:
        for route in route_contribution.router.routes:
            path = getattr(route, "path", "")
            if path != bare_prefix and not path.startswith(prefix):
                raise ExtensionValidationError(
                    f"route {path!r} from {module.module_id!r} must be under {bare_prefix}"
                )
    for job_contribution in module.jobs:
        if not job_contribution.name.startswith(f"{module.module_id}."):
            raise ExtensionValidationError(
                f"contribution {job_contribution.name!r} must start with {module.module_id!r}"
            )
    for lifecycle_contribution in module.lifecycle:
        if not lifecycle_contribution.name.startswith(f"{module.module_id}."):
            raise ExtensionValidationError(
                f"contribution {lifecycle_contribution.name!r} must start with {module.module_id!r}"
            )
    for policy_contribution in module.policies:
        _validate_owned(module, policy_contribution.name)
    for identity_contribution in module.identities:
        _validate_owned(module, identity_contribution.name)
    for context_contribution in module.contexts:
        _validate_owned(module, context_contribution.name)
    for tool_contribution in module.tools:
        _validate_owned(module, tool_contribution.name)
    for specialist_contribution in module.specialists:
        _validate_owned(module, specialist_contribution.name)


def _validate_owned(module: SkeinModule, name: str) -> None:
    if not name.startswith(f"{module.module_id}."):
        raise ExtensionValidationError(
            f"contribution {name!r} must start with {module.module_id!r}"
        )
