"""The Docker build must carry every source directory used as wheel data.

The installed wheel resolves these files from ``skein_stock``. A Docker build
cannot create that wheel if one source directory is missing from its context.
"""

import os
import subprocess
import sys
import tomllib
from pathlib import Path

from conftest import authored_repo_root

BACKEND = Path(__file__).resolve().parent.parent
ROOT = authored_repo_root(Path(__file__))
DOCKERFILE = BACKEND / "Dockerfile"
CONTENT_DIRS = {"fieldguide", "flocks", "personas", "playbooks", "schemas"}


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
        (".gitea/workflows/ci.yml", 2),
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


def test_workplace_login_finishes_before_removing_interception():
    body = (ROOT / "frontend/e2e-oidc/workplace-runtime.spec.ts").read_text()
    helper = body.split("async function signedInPage", 1)[1].split(
        "\nfunction watchSignedInPage", 1
    )[0]
    ready = 'await expect(page.getByRole("button", { name: new RegExp(user, "i") }))'
    assert (
        helper.index(ready)
        < helper.index("await page.unroute(")
        < helper.index("return { context, page,")
    )


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
