"""Explicit Atlas composition manifest."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.extensions import (
    ContextContribution,
    EventContribution,
    ExtensionMigration,
    ExtensionStore,
    IdentityContribution,
    JobContribution,
    MigrationContribution,
    PolicyContribution,
    PolicyEffect,
    PolicyEngine,
    PolicyInput,
    PolicyResource,
    PolicySubjectDep,
    RouteContribution,
    SkeinModule,
    SpecialistContribution,
    ToolContribution,
    WorkflowActionContribution,
)
from app.public import PublicError, WorkItems

from .integration import AtlasClient, AtlasIntegration, MemoryAtlasClient
from .policy import atlas_identity, atlas_policy


@dataclass(frozen=True)
class AtlasSettings:
    store_path: Path


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
    policy = PolicyEngine((atlas_policy,))
    integration = AtlasIntegration(client or MemoryAtlasClient(), store, WorkItems(policy))
    router = APIRouter(prefix="/api/extensions/atlas.workplace")

    def require(request: Request, subject, action: str) -> None:
        decision = request.app.state.skein_registry.policy_engine.decide(
            PolicyInput(
                subject=subject,
                action=action,
                resource=PolicyResource("atlas"),
                origin="human",
            )
        )
        if decision.effect != PolicyEffect.PERMIT:
            raise PublicError(
                "POLICY_DENIED",
                "The policy denied this Atlas action.",
                status_code=403,
            )

    @router.post("/sync", response_model=SyncOut)
    def sync(request: Request, subject: PolicySubjectDep):
        require(request, subject, "atlas.integration.sync")
        return integration.sync()

    @router.get("/metrics")
    def metrics(request: Request, subject: PolicySubjectDep):
        require(request, subject, "atlas.dashboard.view")
        return integration.metrics()

    def notify(channel: str, message: str):
        del channel, message
        return {"accepted": True}

    return SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        routes=(RouteContribution("atlas.workplace.routes", router),),
        jobs=(
            JobContribution(
                "atlas.workplace.sync",
                integration.sync,
                {"trigger": "interval", "minutes": 15},
                period_hours=0.25,
            ),
        ),
        policies=(PolicyContribution("atlas.workplace.policy", atlas_policy),),
        identities=(IdentityContribution("atlas.workplace.identity", atlas_identity),),
        contexts=(
            ContextContribution(
                "atlas.workplace.delivery-context",
                lambda _query: f"Atlas mappings: {integration.metrics()['linked_items']}",
            ),
        ),
        tools=(
            ToolContribution(
                name="atlas.workplace.sync-tool",
                version="1.0.0",
                model_name="atlas_sync",
                description="Synchronize work items with the fictional Atlas system.",
                handler=lambda full=False: integration.sync(),
                input_schema=SyncIn,
                output_schema=SyncOut,
                effect="write",
                risk="high",
                policy_action="atlas.integration.sync",
                allowed_agents=("atlas.workplace.delivery-specialist",),
                required_capabilities=("atlas.integration",),
                timeout_seconds=20,
                error_codes=("ATLAS_UNAVAILABLE",),
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
                "atlas.workplace.task-events",
                integration.deliver_task_event,
                ("skein.task.updated",),
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
