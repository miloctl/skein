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


def _next_minor(version: str) -> str:
    """The first version this core line does NOT cover.

    Derived, never a literal. `_validate_module` checks
    `minimum_core <= SKEIN_CORE_VERSION < maximum_core_exclusive` for EVERY
    module including this one, so a hardcoded ceiling becomes false the day
    the core reaches it. This module carried a literal ceiling of one minor
    ahead until the 0.3.0 release caught up with it, at which point the core
    would have failed its own validation and refused to compose at startup.
    Extensions state a real ceiling because theirs is a promise about code
    they do not own; the core owns itself.
    """
    major, minor = (int(part) for part in version.split(".")[:2])
    return f"{major}.{minor + 1}.0"


def core_module() -> SkeinModule:
    """Return core behavior in the same contribution shapes as extensions."""
    return SkeinModule(
        module_id="skein.core",
        version=SKEIN_CORE_VERSION,
        extension_api=EXTENSION_API_VERSION,
        minimum_core="0.2.0",
        maximum_core_exclusive=_next_minor(SKEIN_CORE_VERSION),
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
                service_identity="scheduler",
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
                subject="scheduler",
                roles=("scheduler",),
            ),
            ServiceIdentityContribution(
                name="skein.core.forge-identity",
                subject="forge",
                roles=("integration",),
            ),
        ),
    )
