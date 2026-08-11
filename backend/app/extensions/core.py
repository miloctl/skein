"""The built-in module manifest used by the default composition root."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any

from ..routes import api, auth, chat, private, slack, webhooks
from ..services.jobs import JOBS
from .contracts import (
    EXTENSION_API_VERSION,
    SKEIN_CORE_VERSION,
    JobContribution,
    JobExecutionContext,
    RouteContribution,
    ServiceIdentityContribution,
    SkeinModule,
)


def _run_core_job(_context: JobExecutionContext, *, fn: Callable[[], Any]) -> Any:
    return fn()


def core_module() -> SkeinModule:
    """Return core behavior in the same contribution shapes as extensions."""
    return SkeinModule(
        module_id="skein.core",
        version=SKEIN_CORE_VERSION,
        extension_api=EXTENSION_API_VERSION,
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        routes=(
            RouteContribution("skein.core.api", api.router),
            RouteContribution("skein.core.auth", auth.router),
            RouteContribution("skein.core.chat", chat.router),
            RouteContribution("skein.core.private", private.router),
            RouteContribution("skein.core.slack", slack.router),
            RouteContribution("skein.core.webhooks", webhooks.router),
        ),
        jobs=tuple(
            JobContribution(
                name=f"skein.core.{spec.name}",
                handler=partial(_run_core_job, fn=spec.fn),
                service_identity="skein.scheduler",
                policy_action=f"skein.job.{spec.name}",
                effect="write",
                risk="medium",
                trigger=dict(spec.trigger),
                period_hours=spec.period_hours,
                catch_up=spec.catch_up,
            )
            for spec in JOBS
        ),
        service_identities=(
            ServiceIdentityContribution(
                name="skein.core.scheduler-identity",
                subject="skein.scheduler",
                roles=("scheduler",),
            ),
            ServiceIdentityContribution(
                name="skein.core.forge-identity",
                subject="skein.forge",
                roles=("integration",),
            ),
        ),
    )
