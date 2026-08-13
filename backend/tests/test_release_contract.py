"""Release metadata must not drift across the core and reference packages."""

import json
import tomllib
from pathlib import Path

from app.extensions import EXTENSION_API_VERSION, SKEIN_CORE_VERSION

ROOT = Path(__file__).resolve().parents[2]


def _toml(path: str) -> dict:
    return tomllib.loads((ROOT / path).read_text())


def _json(path: str) -> dict:
    return json.loads((ROOT / path).read_text())


def test_core_release_and_extension_api_versions_are_synchronized():
    backend_version = _toml("backend/pyproject.toml")["project"]["version"]
    frontend_version = _json("frontend/package.json")["version"]
    frontend_api = _json("frontend/packages/extension-api/package.json")["version"]

    assert backend_version == frontend_version == SKEIN_CORE_VERSION
    assert frontend_api.removesuffix(".0") == EXTENSION_API_VERSION


def test_reference_extension_metadata_uses_owned_compatibility_literals():
    manifest = _toml("examples/workplace-extension/extension.toml")["extension"]
    backend_package = _toml("examples/workplace-extension/pyproject.toml")["project"]
    frontend_package = _json("examples/workplace-extension/frontend/package.json")
    source = (ROOT / "examples/workplace-extension/backend/src/atlas_skein/module.py").read_text()
    frontend_source = (ROOT / "examples/workplace-extension/frontend/index.tsx").read_text()

    assert f"skein>={manifest['minimum_core']},<{manifest['maximum_core_exclusive']}" in tuple(
        backend_package["dependencies"]
    )
    assert frontend_package["version"] == manifest["version"]
    for literal in (
        manifest["extension_api"],
        manifest["minimum_core"],
        manifest["maximum_core_exclusive"],
    ):
        assert f'"{literal}"' in source
        assert f'"{literal}"' in frontend_source


def test_extension_api_one_exports_exactly_the_documented_surface():
    """The 1.0 surface is frozen at release. A removal breaks installed
    packages; an addition is a new compatibility commitment. Both need a
    deliberate edit here and in docs/EXTENSIONS.md, not a drive-by export."""
    import app.extensions as extensions
    import app.public as public

    assert set(public.__all__) == {
        "CommandContext",
        "CreateTaskCommand",
        "DomainEvent",
        "EventActor",
        "PublicError",
        "ResourceReference",
        "TaskView",
        "UpdateTaskCommand",
        "WorkItems",
    }
    assert set(extensions.__all__) == {
        "EXTENSION_API_VERSION",
        "SKEIN_CORE_VERSION",
        "AppSettings",
        "ContextContribution",
        "EventContribution",
        "EventExecutionContext",
        "ExtensionMigration",
        "ExtensionRegistry",
        "ExtensionRouteServices",
        "ExtensionRouteServicesDep",
        "ExtensionStore",
        "ExtensionValidationError",
        "IdentityContribution",
        "JobContribution",
        "JobExecutionContext",
        "MigrationContribution",
        "PolicyContribution",
        "PolicyDecision",
        "PolicyEffect",
        "PolicyEngine",
        "PolicyInput",
        "PolicyResource",
        "PolicySubject",
        "RouteContribution",
        "RouteOperationContribution",
        "ServiceIdentityContribution",
        "SkeinModule",
        "SpecialistContribution",
        "ToolContribution",
        "ToolHandlerContext",
        "WorkflowActionContext",
        "WorkflowActionContribution",
    }
    for package in (public, extensions):
        assert all(getattr(package, name, None) is not None for name in package.__all__)
