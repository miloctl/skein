"""Stable composition contracts for trusted Skein extensions."""

from .contracts import (
    EXTENSION_API_VERSION,
    SKEIN_CORE_VERSION,
    AppSettings,
    JobContribution,
    LifecycleContribution,
    RouteContribution,
    SkeinModule,
)
from .registry import ExtensionRegistry, ExtensionValidationError

__all__ = [
    "EXTENSION_API_VERSION",
    "SKEIN_CORE_VERSION",
    "AppSettings",
    "ExtensionRegistry",
    "ExtensionValidationError",
    "JobContribution",
    "LifecycleContribution",
    "RouteContribution",
    "SkeinModule",
]
