"""Validation and immutable indexing for explicit Skein modules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from inspect import iscoroutinefunction

from ..identity_names import CORE_MACHINE_SUBJECTS
from .contracts import (
    EXTENSION_API_VERSION,
    SKEIN_CORE_VERSION,
    ContextContribution,
    EventContribution,
    IdentityContribution,
    JobContribution,
    LifecycleContribution,
    MigrationContribution,
    PolicyContribution,
    RouteContribution,
    RouteOperationContribution,
    ServiceIdentityContribution,
    SkeinModule,
    SpecialistContribution,
    ToolContribution,
    WorkflowActionContribution,
)
from .policy import PolicyEngine, PolicySubject

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")
_MODEL_TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_RESERVED_CORE_SUBJECTS = CORE_MACHINE_SUBJECTS


class ExtensionValidationError(ValueError):
    """A module cannot be composed safely."""


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set)):
        raise ExtensionValidationError(f"identity {label} must be a list or tuple")
    return tuple(str(item) for item in value)


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
    service_identities: tuple[ServiceIdentityContribution, ...]
    contexts: tuple[ContextContribution, ...]
    tools: tuple[ToolContribution, ...]
    specialists: tuple[SpecialistContribution, ...]
    events: tuple[EventContribution, ...]
    migrations: tuple[MigrationContribution, ...]
    workflow_actions: tuple[WorkflowActionContribution, ...]

    @property
    def policy_engine(self) -> PolicyEngine:
        ordered = sorted(self.policies, key=lambda contribution: contribution.priority)
        return PolicyEngine(tuple(contribution.rule for contribution in ordered))

    def identity_attributes(
        self, name: str, groups: tuple[str, ...], strong: bool
    ) -> dict[str, object]:
        attributes: dict[str, object] = {}
        roles: list[str] = []
        capabilities: list[str] = []
        for contribution in self.identities:
            contributed = dict(contribution.mapper(name, groups, strong))
            roles.extend(_string_tuple(contributed.pop("roles", ()), "roles"))
            capabilities.extend(_string_tuple(contributed.pop("capabilities", ()), "capabilities"))
            overlap = set(attributes) & set(contributed)
            if overlap:
                raise ExtensionValidationError(
                    f"identity attribute collision: {', '.join(sorted(overlap))}"
                )
            attributes.update(contributed)
        attributes["roles"] = tuple(dict.fromkeys(roles))
        attributes["capabilities"] = tuple(dict.fromkeys(capabilities))
        return attributes

    def service_subject(self, name: str) -> PolicySubject:
        """Resolve one registered service identity without human mapping."""
        try:
            identity = next(item for item in self.service_identities if item.subject == name)
        except StopIteration as exc:
            raise ExtensionValidationError(f"unknown service identity {name!r}") from exc
        return PolicySubject(
            name,
            kind="service",
            roles=identity.roles,
            capabilities=identity.capabilities,
            attributes=identity.attributes,
            strong=True,
            source="service",
        )

    def refresh_subject(self, subject: PolicySubject) -> PolicySubject:
        """Refresh a saved requester through configured directory resolvers."""
        from ..services.users import is_active

        if subject.kind == "service":
            return self.service_subject(subject.name)
        if not is_active(subject.name):
            raise PermissionError("The requester identity is no longer active.")
        groups = tuple(subject.groups)
        active = True
        groups_resolved = False
        explicit_group_resolver = next(
            (
                contribution
                for contribution in self.identities
                if contribution.resolver is not None and contribution.resolves_groups is True
            ),
            None,
        )
        legacy_group_results: list[tuple[str, ...]] = []
        legacy_resolvers_available = True
        for contribution in self.identities:
            if contribution.resolver is None:
                continue
            value = contribution.resolver(subject.name)
            if value is None:
                if explicit_group_resolver is contribution or (
                    explicit_group_resolver is None and contribution.resolves_groups is None
                ):
                    legacy_resolvers_available = False
                continue
            active = active and bool(value.get("active", True))
            if explicit_group_resolver is contribution and "groups" in value:
                groups_resolved = True
                groups = tuple(str(item) for item in value.get("groups") or ())
            elif explicit_group_resolver is contribution:
                legacy_resolvers_available = False
            elif contribution.resolves_groups is False and "groups" in value:
                raise PermissionError(
                    f"Identity profile resolver {contribution.name!r} returned groups."
                )
            elif explicit_group_resolver is not None and "groups" in value:
                raise PermissionError(
                    f"Legacy identity resolver {contribution.name!r} returned groups beside"
                    " the explicit group resolver."
                )
            elif "groups" in value:
                legacy_group_results.append(tuple(str(item) for item in value.get("groups") or ()))
        if explicit_group_resolver is None and legacy_resolvers_available:
            distinct = set(legacy_group_results)
            if len(distinct) == 1:
                groups = legacy_group_results[0]
                groups_resolved = True
            elif len(distinct) > 1:
                raise PermissionError(
                    "Legacy identity resolvers returned different group results."
                    " Declare one resolver with resolves_groups=True."
                )
        if (subject.refresh_required or subject.groups) and not groups_resolved:
            raise PermissionError("The requester directory identity could not be refreshed.")
        if not active:
            raise PermissionError("The requester identity is no longer active.")
        # A stored weak identity must never become strong during review resume.
        # The mapper receives the assurance that was proved on the original
        # request. Directory refresh can remove access, but it cannot add a
        # stronger proof than the requester supplied.
        attributes = self.identity_attributes(subject.name, groups, subject.strong)
        roles = _string_tuple(attributes.pop("roles", ()), "roles")
        capabilities = _string_tuple(attributes.pop("capabilities", ()), "capabilities")
        return PolicySubject(
            subject.name,
            kind=subject.kind,
            roles=roles,
            groups=groups,
            capabilities=capabilities,
            attributes=attributes,
            strong=subject.strong,
            source=subject.source,
            refresh_required=subject.refresh_required,
        )

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
        service_identities: list[ServiceIdentityContribution] = []
        contexts: list[ContextContribution] = []
        tools: list[ToolContribution] = []
        specialists: list[SpecialistContribution] = []
        events: list[EventContribution] = []
        migrations: list[MigrationContribution] = []
        workflow_actions: list[WorkflowActionContribution] = []
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
                ("service-identity", module.service_identities),
                ("context", module.contexts),
                ("tool", module.tools),
                ("specialist", module.specialists),
                ("event", module.events),
                ("migration", module.migrations),
                ("workflow-action", module.workflow_actions),
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
            service_identities.extend(module.service_identities)
            contexts.extend(module.contexts)
            tools.extend(module.tools)
            specialists.extend(module.specialists)
            events.extend(module.events)
            migrations.extend(module.migrations)
            workflow_actions.extend(module.workflow_actions)

        tool_names = {contribution.name for contribution in tools}
        model_tool_names = [contribution.model_name for contribution in tools]
        if len(model_tool_names) != len(set(model_tool_names)):
            raise ExtensionValidationError("duplicate model-facing tool name")
        context_names = {contribution.name for contribution in contexts}
        service_subjects = [contribution.subject for contribution in service_identities]
        if len(service_subjects) != len(set(service_subjects)):
            raise ExtensionValidationError("duplicate service identity subject")
        specialist_subjects = {contribution.name for contribution in specialists}
        machine_collisions = sorted(specialist_subjects.intersection(service_subjects))
        if machine_collisions:
            raise ExtensionValidationError(
                "machine identity is both a specialist and a service: "
                + ", ".join(machine_collisions)
            )
        group_resolvers = [
            contribution.name
            for contribution in identities
            if contribution.resolver is not None and contribution.resolves_groups is True
        ]
        if len(group_resolvers) > 1:
            raise ExtensionValidationError(
                "only one identity contribution can resolve groups: " + ", ".join(group_resolvers)
            )
        missing_service_identities = sorted(
            {
                contribution.service_identity
                for contribution in jobs
                if contribution.service_identity not in service_subjects
            }
            | {
                contribution.service_identity
                for contribution in events
                if contribution.service_identity not in service_subjects
            }
        )
        if missing_service_identities:
            raise ExtensionValidationError(
                "unregistered service identities: " + ", ".join(missing_service_identities)
            )
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
            tuple(service_identities),
            tuple(contexts),
            tuple(tools),
            tuple(specialists),
            tuple(events),
            tuple(migrations),
            tuple(workflow_actions),
        )


def validate_machine_identity_ownership(
    registry: ExtensionRegistry,
    additional: tuple[tuple[str, str], ...] = (),
) -> None:
    """Keep one owner for each composed machine identity.

    Persona and flock overlays are deployment content. Validate them after
    composition and before a process reserves any machine user.
    """
    from ..services import flocks, personas
    from ..services.users import fold

    content_owners = {
        **{fold(slug): f"persona {slug!r}" for slug in personas.bench_slugs()},
        **{fold(item["slug"]): f"flock {item['slug']!r}" for item in flocks.list_flocks()},
    }
    claims = [
        *(("service", identity.subject) for identity in registry.service_identities),
        *(("specialist", specialist.name) for specialist in registry.specialists),
        *additional,
    ]
    machine_owners: dict[str, str] = {}
    core_owners = {fold(name): f"core actor {name!r}" for name in _RESERVED_CORE_SUBJECTS}
    collisions: list[str] = []
    for folded, configured_content_owner in content_owners.items():
        if core_owner := core_owners.get(folded):
            collisions.append(f"{configured_content_owner} conflicts with {core_owner}")
    for kind, name in claims:
        owner = f"{kind} {name!r}"
        folded = fold(name)
        content_owner = content_owners.get(folded)
        if content_owner:
            collisions.append(f"{owner} conflicts with {content_owner}")
        if (kind, name) in additional and (core_owner := core_owners.get(folded)):
            collisions.append(f"{owner} conflicts with {core_owner}")
        machine_owner = machine_owners.get(folded)
        if machine_owner:
            collisions.append(f"{owner} conflicts with {machine_owner}")
        else:
            machine_owners[folded] = owner
    if collisions:
        raise RuntimeError("machine identity ownership conflict: " + "; ".join(sorted(collisions)))


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
    _version(module.version, f"module {module.module_id} version")
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
        for operation in route_contribution.operations:
            _validate_route_operation(route_contribution, operation)
    for job_contribution in module.jobs:
        if not _IDENTIFIER.fullmatch(job_contribution.name):
            raise ExtensionValidationError(
                f"module {module.module_id!r} has invalid contribution name "
                f"{job_contribution.name!r}"
            )
        if not job_contribution.service_identity or not job_contribution.policy_action:
            raise ExtensionValidationError(
                f"job {job_contribution.name!r} needs a service identity and policy action"
            )
        if job_contribution.effect not in ("none", "read", "write", "unknown"):
            raise ExtensionValidationError(f"job {job_contribution.name!r} has invalid effect")
        if job_contribution.risk not in ("low", "medium", "high", "critical"):
            raise ExtensionValidationError(f"job {job_contribution.name!r} has invalid risk")
        if job_contribution.timeout_seconds <= 0:
            raise ExtensionValidationError(
                f"job {job_contribution.name!r} needs a positive timeout"
            )
        if iscoroutinefunction(job_contribution.handler):
            raise ExtensionValidationError(
                f"job {job_contribution.name!r} must use a synchronous handler"
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
    for service_identity in module.service_identities:
        _validate_contribution_name(module, service_identity.name)
        if not service_identity.subject or len(service_identity.subject) > 64:
            raise ExtensionValidationError(
                f"service identity {service_identity.name!r} needs a valid subject"
            )
    for context_contribution in module.contexts:
        _validate_contribution_name(module, context_contribution.name)
        _version(context_contribution.version, f"context {context_contribution.name} version")
        if not context_contribution.policy_action:
            raise ExtensionValidationError(
                f"context {context_contribution.name!r} needs a policy action"
            )
        if context_contribution.risk not in ("low", "medium", "high", "critical"):
            raise ExtensionValidationError(
                f"context {context_contribution.name!r} has invalid risk"
            )
        if context_contribution.timeout_seconds <= 0:
            raise ExtensionValidationError(
                f"context {context_contribution.name!r} needs a positive timeout"
            )
        if context_contribution.max_output_chars <= 0:
            raise ExtensionValidationError(
                f"context {context_contribution.name!r} needs a positive output limit"
            )
        if iscoroutinefunction(context_contribution.provider):
            raise ExtensionValidationError(
                f"context {context_contribution.name!r} must use a synchronous provider"
            )
    for tool_contribution in module.tools:
        _validate_contribution_name(module, tool_contribution.name)
        _version(tool_contribution.version, f"tool {tool_contribution.name} version")
        if not _MODEL_TOOL_NAME.fullmatch(tool_contribution.model_name):
            raise ExtensionValidationError(
                f"tool {tool_contribution.name!r} has invalid model_name"
            )
        if not tool_contribution.policy_action:
            raise ExtensionValidationError(f"tool {tool_contribution.name!r} needs a policy action")
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
        if len(specialist_contribution.name) > 64:
            raise ExtensionValidationError(
                f"specialist {specialist_contribution.name!r} exceeds the identity limit"
            )
    for event_contribution in module.events:
        _validate_contribution_name(module, event_contribution.name)
        _version(event_contribution.version, f"event {event_contribution.name} version")
        if not event_contribution.service_identity or not event_contribution.policy_action:
            raise ExtensionValidationError(
                f"event {event_contribution.name!r} needs a service identity and policy action"
            )
        if event_contribution.effect not in ("none", "read", "write", "unknown"):
            raise ExtensionValidationError(f"event {event_contribution.name!r} has invalid effect")
        if event_contribution.risk not in ("low", "medium", "high", "critical"):
            raise ExtensionValidationError(f"event {event_contribution.name!r} has invalid risk")
        if event_contribution.timeout_seconds <= 0:
            raise ExtensionValidationError(
                f"event {event_contribution.name!r} needs a positive timeout"
            )
        if not event_contribution.event_types:
            raise ExtensionValidationError(
                f"event {event_contribution.name!r} must select an event type"
            )
        if any(version < 1 for version in event_contribution.schema_versions):
            raise ExtensionValidationError(
                f"event {event_contribution.name!r} has an invalid schema version"
            )
        if not 1 <= event_contribution.max_attempts <= 100:
            raise ExtensionValidationError(
                f"event {event_contribution.name!r} has invalid max_attempts"
            )
        if iscoroutinefunction(event_contribution.handler):
            raise ExtensionValidationError(
                f"event {event_contribution.name!r} must use a synchronous handler"
            )
    for migration_contribution in module.migrations:
        _validate_contribution_name(module, migration_contribution.name)
        versions = [migration.version for migration in migration_contribution.migrations]
        if versions != sorted(set(versions)) or any(version < 1 for version in versions):
            raise ExtensionValidationError(
                f"migration {migration_contribution.name!r} needs unique ascending versions"
            )
        if any(
            not migration.name or not migration.statements
            for migration in migration_contribution.migrations
        ):
            raise ExtensionValidationError(
                f"migration {migration_contribution.name!r} has an empty migration"
            )
    for action_contribution in module.workflow_actions:
        _validate_contribution_name(module, action_contribution.name)
        _version(action_contribution.version, f"workflow action {action_contribution.name} version")
        if not action_contribution.policy_action:
            raise ExtensionValidationError(
                f"workflow action {action_contribution.name!r} needs a policy action"
            )
        if action_contribution.effect not in ("none", "read", "write", "unknown"):
            raise ExtensionValidationError(
                f"workflow action {action_contribution.name!r} has invalid effect"
            )
        if action_contribution.risk not in ("low", "medium", "high", "critical"):
            raise ExtensionValidationError(
                f"workflow action {action_contribution.name!r} has invalid risk"
            )
        if action_contribution.timeout_seconds <= 0:
            raise ExtensionValidationError(
                f"workflow action {action_contribution.name!r} needs a positive timeout"
            )
        if iscoroutinefunction(action_contribution.handler):
            raise ExtensionValidationError(
                f"workflow action {action_contribution.name!r} must use a synchronous handler"
            )


def _validate_contribution_name(module: SkeinModule, name: str) -> None:
    if not _IDENTIFIER.fullmatch(name):
        raise ExtensionValidationError(
            f"module {module.module_id!r} has invalid contribution name {name!r}"
        )


def _validate_route_operation(
    contribution: RouteContribution,
    operation: RouteOperationContribution,
) -> None:
    if operation.method not in ("DELETE", "GET", "PATCH", "POST", "PUT"):
        raise ExtensionValidationError(
            f"route {contribution.name!r} has an invalid operation method"
        )
    if not operation.path.startswith("/") or not operation.policy_action.strip():
        raise ExtensionValidationError(
            f"route {contribution.name!r} has an invalid operation policy"
        )
    if operation.effect not in ("none", "read", "write", "unknown"):
        raise ExtensionValidationError(
            f"route {contribution.name!r} has an invalid operation effect"
        )
    if operation.risk not in ("low", "medium", "high", "critical"):
        raise ExtensionValidationError(f"route {contribution.name!r} has an invalid operation risk")


def _validate_namespace(module: SkeinModule) -> None:
    if module.module_id == "skein.core":
        return
    # These actors identify core-owned activity and policy principals. A
    # private module must not reuse them for a service or specialist.
    claimed_reserved = sorted(
        {
            contribution.subject
            for contribution in module.service_identities
            if contribution.subject.casefold() in _RESERVED_CORE_SUBJECTS
        }
        | {
            contribution.name
            for contribution in module.specialists
            if contribution.name.casefold() in _RESERVED_CORE_SUBJECTS
        }
    )
    if claimed_reserved:
        raise ExtensionValidationError(
            "private module claims a reserved core subject: " + ", ".join(claimed_reserved)
        )
    prefix = f"/api/extensions/{module.module_id}/"
    bare_prefix = prefix.removesuffix("/")
    for route_contribution in module.routes:
        _validate_owned(module, route_contribution.name)
        for route in route_contribution.router.routes:
            path = getattr(route, "path", "")
            if path != bare_prefix and not path.startswith(prefix):
                raise ExtensionValidationError(
                    f"route {path!r} from {module.module_id!r} must be under {bare_prefix}"
                )
        actual = {
            (method, str(getattr(route, "path", "")))
            for route in route_contribution.router.routes
            for method in getattr(route, "methods", ())
            if method not in ("HEAD", "OPTIONS")
        }
        declared = {
            (operation.method, operation.path) for operation in route_contribution.operations
        }
        if len(declared) != len(route_contribution.operations):
            raise ExtensionValidationError(
                f"route {route_contribution.name!r} has duplicate operation policies"
            )
        if actual != declared:
            missing = sorted(f"{method} {path}" for method, path in actual - declared)
            unknown = sorted(f"{method} {path}" for method, path in declared - actual)
            detail = []
            if missing:
                detail.append("missing policy for " + ", ".join(missing))
            if unknown:
                detail.append("unknown operation " + ", ".join(unknown))
            raise ExtensionValidationError(
                f"route {route_contribution.name!r} operation contract is invalid: "
                + "; ".join(detail)
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
    for service_identity in module.service_identities:
        _validate_owned(module, service_identity.name)
    for context_contribution in module.contexts:
        _validate_owned(module, context_contribution.name)
    for tool_contribution in module.tools:
        _validate_owned(module, tool_contribution.name)
    for specialist_contribution in module.specialists:
        _validate_owned(module, specialist_contribution.name)
    for event_contribution in module.events:
        _validate_owned(module, event_contribution.name)
    for migration_contribution in module.migrations:
        _validate_owned(module, migration_contribution.name)
    for action_contribution in module.workflow_actions:
        _validate_owned(module, action_contribution.name)


def _validate_owned(module: SkeinModule, name: str) -> None:
    if not name.startswith(f"{module.module_id}."):
        raise ExtensionValidationError(
            f"contribution {name!r} must start with {module.module_id!r}"
        )


def validate_core_tool_names(registry: ExtensionRegistry, names: set[str]) -> None:
    """Refuse model-facing names already owned by a built-in tool."""
    overlap = sorted(names & {item.model_name for item in registry.tools})
    if overlap:
        raise ExtensionValidationError(
            f"contributed model tool collides with core: {', '.join(overlap)}"
        )
