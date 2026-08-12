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


def test_extension_api_one_keeps_its_original_public_import_surface():
    """Security hardening can narrow authority without breaking old imports."""
    import app.public as public

    original_api_one = {
        "CommandContext",
        "CreateTaskCommand",
        "DomainEvent",
        "EventActor",
        "PublicError",
        "ResourceReference",
        "TaskView",
        "UpdateTaskCommand",
        "WorkItems",
        "WorkflowContext",
        "WorkflowEngine",
        "WorkflowResult",
    }
    assert original_api_one <= set(public.__all__)
    assert all(getattr(public, name, None) is not None for name in original_api_one)
