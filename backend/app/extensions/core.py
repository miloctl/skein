"""The built-in module manifest used by the default composition root."""

from __future__ import annotations

from ..routes import api, auth, chat, private, slack, webhooks
from ..services.jobs import JOBS
from .contracts import JobContribution, RouteContribution, SkeinModule


def core_module() -> SkeinModule:
    """Return core behavior in the same contribution shapes as extensions."""
    return SkeinModule(
        module_id="skein.core",
        version="0.1.0",
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
                handler=spec.fn,
                trigger=dict(spec.trigger),
                period_hours=spec.period_hours,
                catch_up=spec.catch_up,
            )
            for spec in JOBS
        ),
    )
