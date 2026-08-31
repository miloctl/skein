"""Release metadata must not drift across the core and reference packages."""

import json
import os
import re
import tomllib
from pathlib import Path

import yaml
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from app.extensions import EXTENSION_API_VERSION, SKEIN_CORE_VERSION

ROOT = Path(__file__).resolve().parents[2]


def _toml(path: str) -> dict:
    return tomllib.loads((ROOT / path).read_text())


def _json(path: str) -> dict:
    return json.loads((ROOT / path).read_text())


def _lock_versions(path: str) -> dict[str, str]:
    versions = {}
    for line in (ROOT / path).read_text().splitlines():
        if line and not line[0].isspace() and "==" in line:
            name, version = line.split("==", 1)
            versions[canonicalize_name(name)] = version.split()[0]
    return versions


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
    assert workplace_package["engines"] == {"node": "22.x"}
    assert workplace_lock["packages"][""]["engines"] == {"node": "22.x"}

    test_dependencies = {
        canonicalize_name(Requirement(value).name)
        for value in backend_package["optional-dependencies"]["test"]
    }
    assert {"pytest", "httpx2", "mypy", "pip-audit"} <= test_dependencies
    test_lock = _lock_versions("examples/workplace-extension/requirements-test.lock")
    production_lock = _lock_versions("examples/workplace-extension/requirements.lock")
    assert test_dependencies <= test_lock.keys()
    assert {"skein-agents", "atlas-skein-extension"}.isdisjoint(test_lock)
    shared = test_lock.keys() & production_lock.keys()
    assert {name: test_lock[name] for name in shared} == {
        name: production_lock[name] for name in shared
    }

    test_input = (ROOT / "examples/workplace-extension/requirements-test.in").read_text()
    core_version = _toml("backend/pyproject.toml")["project"]["version"]
    assert test_input.splitlines() == [
        "-c requirements.lock",
        f"./dist/skein_agents-{core_version}-py3-none-any.whl",
        f"./dist/atlas_skein_extension-{manifest['version']}-py3-none-any.whl[test]",
    ]

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
    publish = (ROOT / ".github/workflows/publish-release.yml").read_text()
    finalize = (ROOT / ".github/workflows/finalize-release.yml").read_text()
    releasing = (ROOT / "RELEASING.md").read_text()
    for workflow in (gitea, github):
        assert "actions/upload-artifact@" in workflow
        assert "actions/download-artifact@" in workflow
        assert "SKEIN_RELEASE_DIST:" in workflow
    for action in re.findall(r"uses:\s+(\S+)", github + publish):
        assert re.search(r"@[0-9a-f]{40}$", action), action

    assert 'tags: ["v*"]' not in github + gitea
    marker = (
        os.environ.get("SKEIN_RELEASE_MARKER_OVERRIDE")
        or (ROOT / ".github/release-version").read_text().strip()
    )
    declared = _toml("backend/pyproject.toml")["project"]["version"]
    # equal, not "unreleased or equal": the marker is the credential that makes
    # a commit publishable, and prepare-release.py::validate_version refuses a
    # non-X.Y.Z marker as its previous version, so a resting sentinel would
    # brick the one script authorized to write this file.
    assert marker == declared
    assert "\n  publish:" not in gitea
    assert "PACKAGE_TOKEN" not in gitea + github
    # THE regression pin for 2026-08-30: no publication decision may read
    # push-range state. The marker-diff trigger could not publish a release
    # whose gates failed, because the commit that fixed them did not touch the
    # marker. ci.yml publishes nothing at all now.
    assert "github.event.before" not in github + publish + finalize
    # both dispatch paths judge a run through the same validator, and both
    # name it the same way — RELEASE_RUN_ID, the only variable it now reads
    assert finalize.count("scripts/validate_release_run.py") == 1
    assert "RELEASE_RUN_ID: ${{ inputs.release_run_id }}" in finalize
    assert "RELEASE_RUN_ID: ${{ inputs.release_run_id }}" in publish
    assert "RETRY_RUN_ID" not in publish + finalize
    assert "environment: release-finalization" in finalize
    assert "publish-pypi:" not in github
    assert "publish-npm:" not in github
    assert "release-guard:" not in github
    assert "retention-days: 90" in github

    # publication names a green run, and binds it to a reviewed declaration
    assert "workflow_dispatch:" in publish
    assert "release_run_id:" in publish
    assert "version:" in publish
    assert "scripts/validate_release_run.py" in publish
    assert 'git show "$RELEASE_SHA:.github/release-version"' in publish
    assert 'git show "$RELEASE_SHA:backend/pyproject.toml"' in publish
    assert 'git merge-base --is-ancestor "$RELEASE_SHA" origin/main' in publish
    # a tag forbids, it never permits
    assert 'git ls-remote --exit-code --tags origin "refs/tags/v$VERSION"' in publish
    assert "already finalized" in publish
    # inputs reach the shell through env, never interpolated into a run: body
    assert "${{ inputs.version }}" not in re.sub(r"(?m)^\s*VERSION: .*$", "", publish)
    # one version at a time, whichever run is named
    assert publish.count("group: release-publish") == 2
    assert publish.count("cancel-in-progress: false") == 2
    assert "artifact_run_id:" in publish
    assert "artifact_id:" in publish
    assert publish.count("needs: verify") == 2

    assert "publish-pypi:" in publish
    assert "environment: pypi" in publish
    assert "id-token: write" in publish
    assert 'version: "0.11.11"' in publish
    assert "pypi-dist" in publish
    # every artifact download names the validated run, and by artifact ID —
    # the name fallback went with the guard that could leave the ID empty
    assert publish.count("run-id: ${{ needs.verify.outputs.artifact_run_id }}") == 2
    assert publish.count("artifact-ids: ${{ needs.verify.outputs.artifact_id }}") == 2
    assert "name: release-packages" not in publish
    assert publish.count("actions: read") == 3
    assert "uv publish --trusted-publishing always --check-url https://pypi.org/simple/" in publish

    assert "publish-npm:" in publish
    assert "environment: npm" in publish
    assert "packages: write" in publish
    assert "https://npm.pkg.github.com" in publish
    assert 'scope: "@miloctl"' in publish
    assert "secrets.GITHUB_TOKEN" in publish
    assert "dist.integrity" in publish
    assert "E404|404 Not Found" in publish
    assert "for attempt in 1 2 3" in publish
    assert "publish_package ./dist/miloctl-skein-extension-api-*.tgz" in publish
    assert "publish_package ./dist/miloctl-skein-frontend-host-*.tgz" in publish
    assert publish.index("miloctl-skein-extension-api-*.tgz") < publish.index(
        "miloctl-skein-frontend-host-*.tgz"
    )

    # The PyPI Trusted Publisher names ONE workflow file. Moving the publisher
    # without re-registering it breaks the OIDC exchange on the next release,
    # and nothing else in the suite compares the two.
    assert "id-token: write" in publish and "id-token: write" not in github
    workflow_line = re.search(r"^Workflow: (\S+)$", releasing, re.M)
    assert workflow_line and workflow_line.group(1) == "publish-release.yml"
    assert "ghcr.io" not in github
    assert "reviewed release pull request" in releasing
    assert "python3.12 scripts/prepare-release.py X.Y.Z" in releasing
    assert "printf '" not in releasing
    assert "pypi` and `npm` environments" in releasing
    assert "`finalize-release` workflow" in releasing
    assert "`release-finalization` environment" in releasing
    assert "original release run ID" in releasing
    assert "original artifact" in releasing
    assert "./scripts/audit-deps.sh workplace" in (ROOT / ".gitea/workflows/weekly.yml").read_text()


def test_ci_admin_database_url_is_scoped_to_database_contract_steps():
    workflows = {
        ".github/workflows/ci.yml": {"Backend package contract", "Frontend package contract"},
        ".gitea/workflows/ci.yml": {"Backend package contract", "Frontend package contract"},
    }
    for relative, expected in workflows.items():
        jobs = yaml.safe_load((ROOT / relative).read_text())["jobs"]
        extension = jobs["extension-contracts"]
        assert "SKEIN_DATABASE_URL" not in extension.get("env", {})
        actual = {
            step.get("name")
            for step in extension["steps"]
            if "SKEIN_DATABASE_URL" in step.get("env", {})
        }
        assert actual == expected

    e2e = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]["e2e"]
    assert "SKEIN_DATABASE_URL" not in e2e.get("env", {})
    assert {
        step.get("name") for step in e2e["steps"] if "SKEIN_DATABASE_URL" in step.get("env", {})
    } == set()
    for relative in ("frontend/playwright.config.ts", "frontend/playwright.oidc.config.ts"):
        text = (ROOT / relative).read_text()
        assert "env: BACKEND_ENV" in text
        assert "env: CLEAN_ENV" in text


def test_release_finalization_verifies_registry_bytes_before_tagging():
    workflow = (ROOT / ".github/workflows/finalize-release.yml").read_text()
    assert "workflow_dispatch:" in workflow
    assert "release_run_id:" in workflow
    assert "push:" not in workflow
    for action in re.findall(r"uses:\s+(\S+)", workflow):
        assert re.search(r"@[0-9a-f]{40}$", action), action
    assert "cancel-in-progress: false" in workflow
    assert "actions: read" in workflow
    assert "packages: read" in workflow
    assert workflow.count("contents: write") == 1
    assert "environment: release-finalization" in workflow
    assert "scripts/validate_release_run.py" in workflow
    assert "scripts/verify_release_packages.py inspect" in workflow
    assert "scripts/verify_release_packages.py compare" in workflow
    assert "artifact-ids:" in workflow
    assert "needs.verify.outputs.release_sha" in workflow
    assert "git tag -a" in workflow
    assert 'git push origin "refs/tags/$TAG"' in workflow
    assert "--force" not in workflow
    assert "uv build" not in workflow
