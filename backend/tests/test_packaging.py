"""The Docker build must carry every source directory used as wheel data.

The installed wheel resolves these files from ``skein_stock``. A Docker build
cannot create that wheel if one source directory is missing from its context.
"""

import os
import re
import subprocess
import sys
import tomllib
from importlib.metadata import requires
from pathlib import Path

import pytest
import yaml
from conftest import authored_repo_root
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

BACKEND = Path(__file__).resolve().parent.parent
ROOT = authored_repo_root(Path(__file__))
DOCKERFILE = BACKEND / "Dockerfile"
CONTENT_DIRS = {"fieldguide", "flocks", "personas", "playbooks", "schemas"}


@pytest.mark.parametrize("source", ["pyproject", "installed-metadata"])
def test_runtime_dependencies_declare_the_supported_mcp_sdk(source):
    if source == "pyproject":
        values = tomllib.loads((BACKEND / "pyproject.toml").read_text())["project"]["dependencies"]
    else:
        values = requires("skein-agents")
        assert values is not None
    requirements = {
        canonicalize_name(requirement.name): requirement
        for requirement in map(Requirement, values)
        if requirement.marker is None or requirement.marker.evaluate({"extra": ""})
    }
    mcp = requirements["mcp"]
    assert mcp.marker is None
    for version in ("2.1.1", "2.1.2", "2.1.99"):
        assert version in mcp.specifier
    for version in ("1.26.0", "2.0.0", "2.1.0", "2.2.0", "3.0.0"):
        assert version not in mcp.specifier
    strands = requirements["strands-agents"]
    assert "1.55.1" in strands.specifier
    assert "1.55.0" not in strands.specifier
    httpx2 = requirements["httpx2"]
    assert httpx2.marker is None
    assert "2.9.0" in httpx2.specifier
    assert "2.8.0" not in httpx2.specifier
    httpx = requirements["httpx"]
    assert httpx.marker is None
    assert "0.28.1" in httpx.specifier
    assert "0.28.0" not in httpx.specifier
    assert "1.0.0" not in httpx.specifier


def content_dirs() -> set[str]:
    return CONTENT_DIRS


def test_content_dirs_exist():
    assert content_dirs(), "the root-relative idiom changed — this test now pins nothing"
    for name in content_dirs():
        assert (BACKEND / name).is_dir(), f"app reads {name}/, which is not in the repo"


def test_dockerfile_copies_every_content_dir():
    body = DOCKERFILE.read_text()
    for name in content_dirs():
        assert f"COPY {name} ./{name}" in body, (
            f"Dockerfile does not COPY {name}/ — the image ships without it"
        )


def test_runtime_images_are_pinned_by_digest():
    for relative in (
        "backend/Dockerfile",
        "frontend/Dockerfile",
        "examples/workplace-extension/deployment/Dockerfile",
        "examples/workplace-extension/deployment/Frontend.Dockerfile",
    ):
        aliases: set[str] = set()
        for line in (ROOT / relative).read_text().splitlines():
            if not line.startswith("FROM "):
                continue
            parts = line.split()
            image = parts[1]
            if image not in aliases:
                assert "@sha256:" in image, f"{relative} uses a mutable base image"
            if len(parts) >= 4 and parts[-2] == "AS":
                aliases.add(parts[-1])
    for relative in (
        "docker-compose.yml",
        "deploy/k8s/base/postgres.yaml",
        "scripts/skein.sh",
        "scripts/reference-images-contract.sh",
    ):
        text = (ROOT / relative).read_text()
        assert "postgres:17-alpine@sha256:" in text, f"{relative} uses a mutable database image"
    assert "node:22-alpine@sha256:" in (ROOT / "scripts/reference-images-contract.sh").read_text()
    for relative, count in (
        (".github/workflows/ci.yml", 3),
        (".gitea/workflows/ci.yml", 3),
        (".gitea/workflows/weekly.yml", 1),
    ):
        text = (ROOT / relative).read_text()
        assert text.count("image: postgres:17-alpine@sha256:") == count, (
            f"{relative} uses a mutable or unexpected database service image"
        )


def test_browser_contracts_clear_the_ambient_public_api_token():
    for relative in ("frontend/playwright.config.ts", "frontend/playwright.oidc.config.ts"):
        assert (ROOT / relative).read_text().count("NEXT_PUBLIC_API_TOKEN=") >= 2
    frontend_contract = (ROOT / "scripts/reference-frontend-contract.sh").read_text()
    assert frontend_contract.count("NEXT_PUBLIC_API_TOKEN=") >= 3
    assert "node:22-bookworm@sha256:" in frontend_contract


def test_workplace_browser_contract_uses_https_for_secure_sessions():
    body = (ROOT / "scripts/reference-frontend-contract.sh").read_text()
    for binding in (
        'NEXT_PUBLIC_API_URL="https://127.0.0.1:$api_port"',
        'NEXT_PUBLIC_SITE_URL="https://127.0.0.1:$app_port"',
        'SKEIN_CORS_ORIGINS="https://127.0.0.1:$app_port"',
        'SKEIN_OIDC_API_URL="https://127.0.0.1:$api_port"',
        'SKEIN_OIDC_APP_URL="https://127.0.0.1:$app_port"',
        "SKEIN_E2E_HTTPS=1",
    ):
        assert binding in body
    for port in ("api_port", "app_port"):
        assert f'wait_for_url "$proxy_pid" "https://127.0.0.1:${port}/' in body
    assert 'curl --fail --silent --cacert "$tmp/cert.pem" "$url"' in body
    assert "subjectAltName=IP:127.0.0.1" in body
    assert "https.createServer(" in body
    assert 'pids=("$proxy_pid" "$server_pid" "$backend_pid" "$idp_pid")' in body


def test_workplace_browser_specs_never_remove_a_route():
    """Removing the last Playwright route turns interception off, and a request
    the page starts during that switch stays pending forever. Waiting for sign-in
    before the removal only narrowed the window: CI still stranded a request.
    frontend/eslint.config.mjs refuses unroute in the source suites, and this
    covers the consumer copy that ESLint does not lint."""
    for spec in (
        "frontend/e2e-oidc/workplace-runtime.spec.ts",
        "examples/workplace-extension/e2e/workplace-runtime.spec.ts",
    ):
        assert not re.search(r"\.unroute(All)?\(", (ROOT / spec).read_text()), spec


def test_workplace_runtime_pid_belongs_to_the_server():
    body = (ROOT / "scripts/reference-frontend-contract.sh").read_text()
    helper = "db_helper() {" + body.split("db_helper() {", 1)[1].split("\n}", 1)[0] + "\n}"
    result = subprocess.run(  # noqa: S603 -- repo-owned helper, fixed command
        [
            "/bin/bash",
            "-c",
            helper
            + '\ndb_helper run-clean skein_contract_pid "$db_python"'
            + " -c 'import os; print(\"runtime\", os.getpid())' &"
            + '\nprintf "tracked %s\\n" "$!"; wait',
        ],
        env={
            "PATH": os.environ["PATH"],
            "root": str(ROOT),
            "db_python": sys.executable,
            "admin_database_url": "postgresql://skein@127.0.0.1/skein",
            "role_name": "skein_atlas_role_pid",
            "role_password": "a" * 48,
        },
        capture_output=True,
        text=True,
        check=True,
    )
    pids = dict(line.split() for line in result.stdout.splitlines())
    assert pids["tracked"] == pids["runtime"]


def test_public_python_contract_is_marked_as_typed():
    package_data = tomllib.loads((BACKEND / "pyproject.toml").read_text())["tool"]["setuptools"][
        "package-data"
    ]
    assert (BACKEND / "app" / "py.typed").is_file()
    assert "py.typed" in package_data["app"]


@pytest.mark.parametrize(
    "relative",
    [
        "backend/Dockerfile",
        "frontend/Dockerfile",
        "examples/workplace-extension/deployment/Dockerfile",
        "examples/workplace-extension/deployment/Frontend.Dockerfile",
    ],
)
def test_the_runtime_user_can_write_only_its_data_volume(relative):
    body = (ROOT / relative).read_text().replace("\\\n", " ")
    grants = re.findall(r"\b(?:chown|chgrp|chmod)\s+(?:-\S+\s+)*\S+((?:\s+/\S+)+)", body)
    # a group-0 grant on code lets OpenShift's arbitrary UID rewrite it
    assert {path for grant in grants for path in grant.split()} <= {"/data"}


def _containers(relative: str) -> dict[str, dict]:
    return {
        container["name"]: container
        for document in yaml.safe_load_all((ROOT / relative).read_text())
        if document and document.get("kind") == "Deployment"
        for container in document["spec"]["template"]["spec"]["containers"]
    }


def test_workplace_containers_carry_the_core_resource_budget():
    core = {
        **_containers("deploy/k8s/base/backend.yaml"),
        **_containers("deploy/k8s/base/frontend.yaml"),
    }
    workplace = _containers("examples/workplace-extension/deployment/skein.yaml")
    # with no memory limit, one leaking pod takes the node's memory
    assert workplace["skein"].get("resources") == core["backend"]["resources"]
    assert workplace["frontend"].get("resources") == core["frontend"]["resources"]
