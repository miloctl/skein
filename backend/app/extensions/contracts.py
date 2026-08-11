"""Versioned, typed contracts used by the application composition root.

These contracts are deliberately narrow. A route, a scheduled job, and a
lifecycle callback have different security and runtime properties. They do
not share a universal plugin base class.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

from .. import config
from .policy import IdentityMapper, PolicyEngine, PolicyResource, PolicyRule, PolicySubject

if TYPE_CHECKING:
    from ..public.events import DomainEvent
    from ..public.work import WorkItems


try:
    SKEIN_CORE_VERSION = package_version("skein")
except PackageNotFoundError:
    SKEIN_CORE_VERSION = "0.2.0"
EXTENSION_API_VERSION = "1.0"


@dataclass(frozen=True)
class AppSettings:
    """An immutable snapshot of settings used during app composition.

    Existing modules still read ``app.config`` for compatibility. The factory
    uses this snapshot for startup, middleware, docs, and contributed jobs so
    one app cannot be changed by mutating a module registry after creation.
    """

    auth_mode: str
    auth_error: str
    api_token: str
    cors_origins: tuple[str, ...]
    timezone: str
    scheduler_enabled: bool
    mcp_user: str
    thread_pool: int
    tool_threads: int
    docs_enabled: bool

    @classmethod
    def from_config(cls) -> AppSettings:
        return cls(
            auth_mode=config.AUTH_MODE,
            auth_error=config.AUTH_ERROR,
            api_token=config.API_TOKEN,
            cors_origins=tuple(config.CORS_ORIGINS),
            timezone=config.TZ_NAME,
            scheduler_enabled=config.SCHEDULER_ENABLED,
            mcp_user=os.getenv("SKEIN_MCP_USER", "mcp-agent"),
            thread_pool=config.THREAD_POOL,
            tool_threads=config.TOOL_THREADS,
            docs_enabled=config.AUTH_MODE == "trusted-header",
        )


@dataclass(frozen=True)
class RouteContribution:
    """One router owned by one module.

    Private modules must use ``/api/extensions/{module namespace}``. Core
    routes use the same contract but are marked as core by the registry.
    """

    name: str
    router: APIRouter


@dataclass(frozen=True)
class JobExecutionContext:
    """Composed services and policy supplied to one scheduled job."""

    settings: AppSettings
    policy: PolicyEngine
    work_items: WorkItems
    subject: PolicySubject
    run_id: str


@dataclass(frozen=True)
class JobContribution:
    """A scheduled job and the cadence used by health reporting."""

    name: str
    handler: Callable[[JobExecutionContext], Any]
    service_identity: str
    policy_action: str
    effect: str
    risk: str
    trigger: Mapping[str, Any] = field(default_factory=dict)
    period_hours: float = 24
    catch_up: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "trigger", MappingProxyType(dict(self.trigger)))


@dataclass(frozen=True)
class LifecycleContext:
    """The limited startup context supplied to a trusted module."""

    app: FastAPI
    settings: AppSettings


LifecycleHandler = Callable[[LifecycleContext], Awaitable[None] | None]


@dataclass(frozen=True)
class LifecycleContribution:
    """A paired startup and optional shutdown callback."""

    name: str
    startup: LifecycleHandler
    shutdown: LifecycleHandler | None = None


@dataclass(frozen=True)
class PolicyContribution:
    """One policy rule. A rule can narrow a decision but cannot bypass core."""

    name: str
    rule: PolicyRule
    priority: int = 100


@dataclass(frozen=True)
class IdentityContribution:
    """Map verified identity groups to workplace policy attributes."""

    name: str
    mapper: IdentityMapper
    resolver: Callable[[str], Mapping[str, Any] | None] | None = None


@dataclass(frozen=True)
class ContextContribution:
    """A named context source available to contributed specialists."""

    name: str
    provider: Callable[[str], str]


@dataclass(frozen=True)
class ToolHandlerContext:
    """Composed services supplied after a tool call passes policy."""

    subject: PolicySubject
    policy: PolicyEngine
    work_items: WorkItems


@dataclass(frozen=True)
class ToolContribution:
    """A governed agent tool with explicit effects and error behavior."""

    name: str
    version: str
    model_name: str
    description: str
    handler: Callable[[ToolHandlerContext, BaseModel], Any]
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    effect: str
    risk: str
    policy_action: str
    allowed_agents: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    timeout_seconds: float = 30
    error_codes: tuple[str, ...] = ()
    receipt: str = "required"
    provenance: str = "service"
    resource: Callable[[BaseModel], PolicyResource] | None = None
    review_preview: Callable[[BaseModel], Mapping[str, Any]] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_agents", tuple(self.allowed_agents))
        object.__setattr__(self, "required_capabilities", tuple(self.required_capabilities))
        object.__setattr__(self, "error_codes", tuple(self.error_codes))


@dataclass(frozen=True)
class SpecialistContribution:
    """A specialist definition composed without a private core import."""

    name: str
    version: str
    display_name: str
    description: str
    system_prompt: str
    tools: tuple[str, ...] = ()
    context_sources: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "tools", tuple(self.tools))
        object.__setattr__(self, "context_sources", tuple(self.context_sources))
        object.__setattr__(self, "required_capabilities", tuple(self.required_capabilities))


@dataclass(frozen=True)
class EventExecutionContext:
    """Composed services and policy supplied to one event subscriber."""

    policy: PolicyEngine
    work_items: WorkItems
    subject_resolver: Callable[[str], PolicySubject]
    subject: PolicySubject | None = None
    delivery_id: str = ""


@dataclass(frozen=True)
class EventContribution:
    """A durable subscriber for selected versions and visibility tiers."""

    name: str
    version: str
    handler: Callable[[DomainEvent, EventExecutionContext], None]
    event_types: tuple[str, ...]
    service_identity: str
    policy_action: str
    effect: str
    risk: str
    schema_versions: tuple[int, ...] = (1,)
    visibilities: tuple[str, ...] = ("workspace",)
    max_attempts: int = 5
    timeout_seconds: float = 30

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_types", tuple(self.event_types))
        object.__setattr__(self, "schema_versions", tuple(self.schema_versions))
        object.__setattr__(self, "visibilities", tuple(self.visibilities))


@dataclass(frozen=True)
class ExtensionMigration:
    """One append-only migration in an extension-owned store."""

    version: int
    name: str
    statements: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "statements", tuple(self.statements))


class MigrationStore(Protocol):
    def migrate(self, migrations: tuple[ExtensionMigration, ...]) -> None: ...


@dataclass(frozen=True)
class MigrationContribution:
    """An isolated migration stream supplied by one trusted module."""

    name: str
    store: MigrationStore
    migrations: tuple[ExtensionMigration, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "migrations", tuple(self.migrations))


@dataclass(frozen=True)
class WorkflowActionContext:
    """Composed services supplied after a workflow action passes policy."""

    subject: PolicySubject
    policy: PolicyEngine
    work_items: WorkItems


@dataclass(frozen=True)
class WorkflowActionContribution:
    """A governed action that a declarative workflow can invoke."""

    name: str
    version: str
    handler: Callable[[WorkflowActionContext, BaseModel], Any]
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    effect: str
    risk: str
    policy_action: str
    timeout_seconds: float = 30
    error_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "error_codes", tuple(self.error_codes))


@dataclass(frozen=True)
class SkeinModule:
    """A signed-off module manifest passed explicitly to ``create_app``.

    This is a composition manifest, not a base plugin class. The contributed
    objects keep their separate types and lifecycle rules.
    """

    module_id: str
    version: str
    extension_api: str
    minimum_core: str
    maximum_core_exclusive: str
    requires: tuple[str, ...] = ()
    routes: tuple[RouteContribution, ...] = ()
    jobs: tuple[JobContribution, ...] = ()
    lifecycle: tuple[LifecycleContribution, ...] = ()
    policies: tuple[PolicyContribution, ...] = ()
    identities: tuple[IdentityContribution, ...] = ()
    contexts: tuple[ContextContribution, ...] = ()
    tools: tuple[ToolContribution, ...] = ()
    specialists: tuple[SpecialistContribution, ...] = ()
    events: tuple[EventContribution, ...] = ()
    migrations: tuple[MigrationContribution, ...] = ()
    workflow_actions: tuple[WorkflowActionContribution, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "requires", tuple(self.requires))
        object.__setattr__(self, "routes", tuple(self.routes))
        object.__setattr__(self, "jobs", tuple(self.jobs))
        object.__setattr__(self, "lifecycle", tuple(self.lifecycle))
        object.__setattr__(self, "policies", tuple(self.policies))
        object.__setattr__(self, "identities", tuple(self.identities))
        object.__setattr__(self, "contexts", tuple(self.contexts))
        object.__setattr__(self, "tools", tuple(self.tools))
        object.__setattr__(self, "specialists", tuple(self.specialists))
        object.__setattr__(self, "events", tuple(self.events))
        object.__setattr__(self, "migrations", tuple(self.migrations))
        object.__setattr__(self, "workflow_actions", tuple(self.workflow_actions))
