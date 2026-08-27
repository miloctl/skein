"""Release metadata must not drift across the core and reference packages."""

import json
import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from app.extensions import EXTENSION_API_VERSION, SKEIN_CORE_VERSION

ROOT = Path(__file__).resolve().parents[2]


def _toml(path: str) -> dict:
    return tomllib.loads((ROOT / path).read_text())


def _json(path: str) -> dict:
    return json.loads((ROOT / path).read_text())


def test_core_release_and_extension_api_versions_are_synchronized():
    backend = _toml("backend/pyproject.toml")["project"]
    frontend = _json("frontend/package.json")
    frontend_lock = _json("frontend/package-lock.json")
    frontend_api = _json("frontend/packages/extension-api/package.json")

    assert backend["name"] == "skein-agents"
    assert backend["version"] == frontend["version"] == SKEIN_CORE_VERSION
    assert frontend["name"] == "@miloctl/skein-frontend-host"
    assert frontend["private"] is False
    assert frontend_lock["name"] == frontend["name"]
    assert frontend_lock["version"] == frontend["version"]
    assert frontend_lock["packages"][""]["name"] == frontend["name"]
    assert frontend_lock["packages"][""]["version"] == frontend["version"]
    assert frontend_api["name"] == "@miloctl/skein-extension-api"
    assert frontend["peerDependencies"]["@miloctl/skein-extension-api"] == frontend_api["version"]
    assert frontend["engines"]["node"] == "22.x"
    for package, directory in (
        (frontend, "frontend"),
        (frontend_api, "frontend/packages/extension-api"),
    ):
        assert package["repository"] == {
            "type": "git",
            "url": "git+https://github.com/miloctl/skein.git",
            "directory": directory,
        }
        assert package["publishConfig"] == {"registry": "https://npm.pkg.github.com"}
    assert frontend_api["license"] == "Apache-2.0"
    assert {"LICENSE", "NOTICE"} <= set(frontend["files"])
    assert {"LICENSE", "NOTICE"} <= set(frontend_api["files"])
    assert frontend_api["version"].removesuffix(".0") == EXTENSION_API_VERSION

    locked = {
        canonicalize_name(line.split("==", 1)[0])
        for line in (ROOT / "backend/requirements.lock").read_text().splitlines()
        if line and not line[0].isspace() and "==" in line
    }
    direct = {canonicalize_name(Requirement(value).name) for value in backend["dependencies"]}
    assert direct <= locked


def test_the_source_fallback_version_matches_the_packaged_version():
    """A tree with no installed distribution reports this literal as its own
    version, and every module compatibility range is checked against it. A
    stale literal refuses a valid private package with no other symptom, so
    the release bump moves every packaged version together."""
    from app.extensions.contracts import FALLBACK_CORE_VERSION

    core = _toml("backend/pyproject.toml")["project"]["version"]
    assert core == FALLBACK_CORE_VERSION
    # skein-cli is its own distributable with its own build-system. Calling the
    # bump "a trio" is what let it drift a release behind unnoticed.
    assert _toml("cli/pyproject.toml")["project"]["version"] == core


def test_reference_extension_metadata_uses_owned_compatibility_literals():
    manifest = _toml("examples/workplace-extension/extension.toml")["extension"]
    backend_package = _toml("examples/workplace-extension/pyproject.toml")["project"]
    frontend_package = _json("examples/workplace-extension/frontend/package.json")
    workplace_package = _json("examples/workplace-extension/package.json")
    workplace_lock = _json("examples/workplace-extension/package-lock.json")
    source = (ROOT / "examples/workplace-extension/backend/src/atlas_skein/module.py").read_text()
    frontend_source = (ROOT / "examples/workplace-extension/frontend/index.tsx").read_text()

    assert backend_package["version"] == manifest["version"]
    assert frontend_package["version"] == manifest["version"]
    assert workplace_package["version"] == manifest["version"]
    assert workplace_lock["version"] == manifest["version"]
    assert workplace_lock["packages"][""]["version"] == manifest["version"]
    host_package = _json("frontend/package.json")
    for name in ("next", "react", "react-dom"):
        assert workplace_package["dependencies"][name] == host_package["dependencies"][name]
    assert workplace_package["overrides"] == host_package["overrides"]
    for name in ("@miloctl/skein-extension-api", "@miloctl/skein-frontend-host"):
        locked = workplace_lock["packages"][f"node_modules/{name}"]
        assert locked["integrity"].startswith("sha512-")
        assert not locked.get("link", False)
    assert (
        f"skein-agents>={manifest['minimum_core']},<{manifest['maximum_core_exclusive']}"
        in tuple(backend_package["dependencies"])
    )
    assert frontend_package["version"] == manifest["version"]
    for literal in (
        manifest["version"],
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
    import app.main as main
    import app.public as public

    assert main.__all__ == ["create_app"]
    assert set(public.__all__) == {
        "BlockerView",
        "CommandContext",
        "CreateBlockerCommand",
        "CreatePromiseCommand",
        "CreateTaskCommand",
        "DomainEvent",
        "EventActor",
        "PublicError",
        "ResourceReference",
        "PromiseView",
        "TaskView",
        "UpdateBlockerCommand",
        "UpdatePromiseCommand",
        "UpdateTaskCommand",
        "WorkItems",
        "dispatch_events",
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
        "ToolCallContext",
        "ToolContribution",
        "ToolExecution",
        "ToolHandlerContext",
        "WorkflowActionContext",
        "WorkflowActionContribution",
        "assert_import_boundary",
        "execute_tool",
        "registry_for",
    }
    for package in (public, extensions):
        assert all(getattr(package, name, None) is not None for name in package.__all__)


def test_release_workflows_publish_the_tested_artifacts_and_audit_workplace():
    gitea = (ROOT / ".gitea/workflows/ci.yml").read_text()
    github = (ROOT / ".github/workflows/ci.yml").read_text()
    releasing = (ROOT / "RELEASING.md").read_text()
    for workflow in (gitea, github):
        assert "actions/upload-artifact@" in workflow
        assert "actions/download-artifact@" in workflow
        assert "SKEIN_RELEASE_DIST:" in workflow
    for action in re.findall(r"uses:\s+(\S+)", github):
        assert re.search(r"@[0-9a-f]{40}$", action), action

    assert 'tags: ["v*"]' not in github + gitea
    marker = (ROOT / ".github/release-version").read_text().strip()
    declared = _toml("backend/pyproject.toml")["project"]["version"]
    assert marker in ("unreleased", declared)
    assert "\n  publish:" not in gitea
    assert "PACKAGE_TOKEN" not in gitea + github
    assert "release-guard:" in github
    assert ".github/release-version" in github
    assert "github.event.before" in github
    assert "workflow_dispatch:" in github
    assert "retry_run_id:" in github
    assert "scripts/validate_release_run.py" in github
    assert "artifact_run_id:" in github
    assert "needs.release-guard.outputs.publish == 'true'" in github
    gated_publishers = (
        "needs: [packages, backend, frontend, extension-contracts, e2e, release-guard]"
    )
    assert github.count(gated_publishers) == 2

    assert "publish-pypi:" in github
    assert "environment: pypi" in github
    assert "id-token: write" in github
    assert 'version: "0.11.11"' in github
    assert "pypi-dist" in github
    assert github.count("run-id: ${{ needs.release-guard.outputs.artifact_run_id }}") == 2
    assert github.count("actions: read") == 3
    assert "uv publish --trusted-publishing always" in github

    assert "publish-npm:" in github
    assert "environment: npm" in github
    assert "packages: write" in github
    assert "https://npm.pkg.github.com" in github
    assert 'scope: "@miloctl"' in github
    assert "secrets.GITHUB_TOKEN" in github
    assert "dist.integrity" in github
    assert "E404|404 Not Found" in github
    assert "for attempt in 1 2 3" in github
    assert "publish_package ./dist/miloctl-skein-extension-api-*.tgz" in github
    assert "publish_package ./dist/miloctl-skein-frontend-host-*.tgz" in github
    assert github.index("miloctl-skein-extension-api-*.tgz") < github.index(
        "miloctl-skein-frontend-host-*.tgz"
    )
    assert "ghcr.io" not in github
    assert "reviewed release pull request" in releasing
    assert "pypi` and `npm` environments" in releasing
    assert "SKEIN_RELEASE_DIST=/tmp/skein-release" in releasing
    assert "original release run ID" in releasing
    assert "original artifact" in releasing
    assert "./scripts/audit-deps.sh workplace" in (ROOT / ".gitea/workflows/weekly.yml").read_text()
