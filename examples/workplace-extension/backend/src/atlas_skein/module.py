"""Explicit Atlas composition manifest."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.extensions import (
    ContextContribution,
    EventContribution,
    ExtensionMigration,
    ExtensionRouteServicesDep,
    ExtensionStore,
    IdentityContribution,
    JobContribution,
    MigrationContribution,
    PolicyContribution,
    PolicyResource,
    RouteContribution,
    RouteOperationContribution,
    ServiceIdentityContribution,
    SkeinModule,
    SpecialistContribution,
    ToolContribution,
    WorkflowActionContext,
    WorkflowActionContribution,
)

from .integration import AtlasClient, AtlasHttpClient, AtlasIntegration, MemoryAtlasClient
from .policy import atlas_directory, atlas_identity, atlas_policy, atlas_profile


@dataclass(frozen=True)
class AtlasSettings:
    store_path: Path
    api_url: str = ""
    api_token: str = ""


class SyncIn(BaseModel):
    full: bool = False


class SyncOut(BaseModel):
    created: int
    updated: int


class NotifyIn(BaseModel):
    channel: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=500)


class NotifyOut(BaseModel):
    accepted: bool


def atlas_module(
    settings: AtlasSettings,
    client: AtlasClient | None = None,
) -> SkeinModule:
    store = ExtensionStore(settings.store_path)
    selected_client = client
    if selected_client is None and settings.api_url:
        if not settings.api_token:
            raise ValueError("ATLAS_API_TOKEN is required when ATLAS_API_URL is set")
        selected_client = AtlasHttpClient(settings.api_url, settings.api_token)
    selected_client = selected_client or MemoryAtlasClient()
    integration = AtlasIntegration(selected_client, store)
    router = APIRouter(prefix="/api/extensions/atlas.workplace")

    @router.post("/sync", response_model=SyncOut)
    def sync(services: ExtensionRouteServicesDep) -> dict[str, int]:
        return integration.sync(
            services.work_items,
            services.command_context(project_type="standard"),
        )

    @router.get("/metrics")
    def metrics(_services: ExtensionRouteServicesDep) -> dict[str, int]:
        return integration.metrics()

    def notify(context: WorkflowActionContext, request: NotifyIn) -> NotifyOut:
        selected_client.notify_manager(
            request.channel,
            request.message,
            f"{context.correlation_id}:manager-notification",
        )
        return NotifyOut(accepted=True)

    return SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        routes=(
            RouteContribution(
                "atlas.workplace.routes",
                router,
                (
                    RouteOperationContribution(
                        "POST",
                        "/api/extensions/atlas.workplace/sync",
                        "atlas.integration.sync",
                        PolicyResource("atlas"),
                        "write",
                        "high",
                    ),
                    RouteOperationContribution(
                        "GET",
                        "/api/extensions/atlas.workplace/metrics",
                        "atlas.dashboard.view",
                        PolicyResource("atlas-dashboard"),
                        "read",
                        "low",
                    ),
                ),
            ),
        ),
        jobs=(
            JobContribution(
                "atlas.workplace.sync",
                lambda context: integration.sync(
                    context.work_items,
                    context.command_context(project_type="standard"),
                ),
                service_identity="atlas-sync",
                policy_action="atlas.integration.sync",
                effect="write",
                risk="high",
                trigger={"trigger": "interval", "minutes": 15},
                period_hours=0.25,
            ),
        ),
        policies=(PolicyContribution("atlas.workplace.policy", atlas_policy),),
        identities=(
            IdentityContribution(
                "atlas.workplace.identity",
                atlas_identity,
                resolver=atlas_directory,
                resolves_groups=True,
            ),
            IdentityContribution(
                "atlas.workplace.profile",
                lambda *_args: {},
                resolver=atlas_profile,
                resolves_groups=False,
            ),
        ),
        service_identities=(
            ServiceIdentityContribution(
                "atlas.workplace.sync-identity",
                "atlas-sync",
                roles=("integration",),
                capabilities=("atlas.integration",),
            ),
            ServiceIdentityContribution(
                "atlas.workplace.event-identity",
                "atlas-events",
                roles=("integration",),
                capabilities=("atlas.integration",),
            ),
        ),
        contexts=(
            ContextContribution(
                "atlas.workplace.delivery-context",
                lambda _requester_name: f"Atlas mappings: {integration.metrics()['linked_items']}",
                policy_action="atlas.context.read",
                risk="low",
                required_capabilities=("atlas.specialist",),
                timeout_seconds=2,
                max_output_chars=2_000,
            ),
        ),
        tools=(
            ToolContribution(
                name="atlas.workplace.sync-tool",
                version="1.0.0",
                model_name="atlas_sync",
                description="Synchronize work items with the fictional Atlas system.",
                handler=lambda context, _request: integration.sync(
                    context.work_items,
                    context.command_context(project_type="standard"),
                ),
                input_schema=SyncIn,
                output_schema=SyncOut,
                effect="write",
                risk="high",
                policy_action="atlas.integration.sync",
                allowed_agents=("atlas.workplace.delivery-specialist",),
                required_capabilities=("atlas.integration",),
                timeout_seconds=20,
                error_codes=("ATLAS_UNAVAILABLE",),
                review_preview=lambda request: {"full_sync": request.full},
            ),
        ),
        specialists=(
            SpecialistContribution(
                name="atlas.workplace.delivery-specialist",
                version="1.0.0",
                display_name="Atlas Delivery Specialist",
                description="Coordinates delivery data with Atlas.",
                system_prompt="Use Atlas data only through governed Atlas tools.",
                tools=("atlas.workplace.sync-tool",),
                context_sources=("atlas.workplace.delivery-context",),
                required_capabilities=("atlas.specialist",),
            ),
        ),
        events=(
            EventContribution(
                name="atlas.workplace.task-events",
                version="1.0.0",
                handler=lambda event, context: integration.deliver_task_event(
                    event,
                    context,
                ),
                event_types=("skein.task.updated",),
                service_identity="atlas-events",
                policy_action="atlas.integration.deliver-task-event",
                effect="write",
                risk="high",
            ),
        ),
        migrations=(
            MigrationContribution(
                "atlas.workplace.data",
                store,
                (
                    ExtensionMigration(
                        1,
                        "create-work-links",
                        (
                            "CREATE TABLE work_links"
                            " (external_id TEXT PRIMARY KEY, skein_task_id INTEGER NOT NULL UNIQUE,"
                            " classification TEXT NOT NULL)",
                        ),
                    ),
                    ExtensionMigration(
                        2,
                        "create-sync-runs",
                        (
                            "CREATE TABLE sync_runs"
                            " (id INTEGER PRIMARY KEY, created_count INTEGER NOT NULL,"
                            " updated_count INTEGER NOT NULL, finished_at TEXT NOT NULL)",
                        ),
                    ),
                ),
            ),
        ),
        workflow_actions=(
            WorkflowActionContribution(
                name="atlas.workplace.notify-manager",
                version="1.0.0",
                handler=notify,
                input_schema=NotifyIn,
                output_schema=NotifyOut,
                effect="write",
                risk="medium",
                policy_action="atlas.notification.send",
                timeout_seconds=10,
                error_codes=("NOTIFICATION_UNAVAILABLE",),
            ),
        ),
    )
