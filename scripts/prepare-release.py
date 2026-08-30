#!/usr/bin/env python3.12
"""Prepare one Skein release from one version input."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")

DOC_VERSION_COUNTS = {
    "docs/SETUP.md": 9,
    "docs/EXTENSIONS.md": 5,
    "frontend/README.md": 1,
    "examples/workplace-extension/README.md": 3,
    "examples/workplace-extension/deployment/README.md": 3,
    "scripts/publish-images.sh": 2,
}


class ReleaseError(ValueError):
    pass


def validate_version(value: str) -> tuple[int, int, int]:
    match = VERSION_RE.fullmatch(value)
    if not match:
        raise ReleaseError("The release version must use canonical X.Y.Z syntax.")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def replace_expected(
    root: Path,
    relative: str,
    old: str,
    new: str,
    count: int = 1,
) -> None:
    path = root / relative
    text = path.read_text()
    old_count = text.count(old)
    new_count = text.count(new)
    if old_count == count and new_count == 0:
        path.write_text(text.replace(old, new))
        return
    if old_count == 0 and new_count == count:
        return
    raise ReleaseError(
        f"{relative} does not contain the expected release text:"
        f" {old_count}x {old!r} (expected {count}), {new_count}x {new!r}"
        f" (expected 0). A pre-existing match on the new version is usually a"
        f" compatibility bound the release is crossing — see RELEASING.md,"
        f" 'When the release crosses a minor boundary'."
    )


def _section_has_item(body: str, heading: str, next_heading: str | None) -> bool:
    start = body.find(f"### {heading}")
    if start < 0:
        return False
    end = body.find(f"### {next_heading}", start) if next_heading else len(body)
    if end < 0:
        end = len(body)
    return "\n- " in body[start:end]


def promote_changelog(root: Path, version: str, today: date) -> None:
    path = root / "CHANGELOG.md"
    text = path.read_text()
    heading = "## Unreleased\n"
    start = text.find(heading)
    if start < 0:
        raise ReleaseError("CHANGELOG.md has no Unreleased section.")
    body_start = start + len(heading)
    next_release = text.find("\n## ", body_start)
    if next_release < 0:
        raise ReleaseError("CHANGELOG.md has no prior release after Unreleased.")
    body = text[body_start:next_release].strip()
    target_headings = list(
        re.finditer(
            rf"^## {re.escape(version)} — [0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$",
            text,
            re.MULTILINE,
        )
    )
    if target_headings:
        empty = "### Contracts\n\n### Behavior\n\n### Operations"
        target_start = target_headings[0].end()
        target_end = text.find("\n## ", target_start)
        target_body = text[target_start : target_end if target_end >= 0 else len(text)].strip()
        target_required = (
            _section_has_item(target_body, "Contracts", "Behavior"),
            _section_has_item(target_body, "Behavior", "Operations"),
            _section_has_item(target_body, "Operations", None),
        )
        if (
            len(target_headings) != 1
            or target_headings[0].start() != next_release + 1
            or body != empty
            or not all(target_required)
        ):
            raise ReleaseError("CHANGELOG.md is not in the canonical resumed release state.")
        return
    required = (
        _section_has_item(body, "Contracts", "Behavior"),
        _section_has_item(body, "Behavior", "Operations"),
        _section_has_item(body, "Operations", None),
    )
    if not all(required):
        raise ReleaseError("CHANGELOG.md Unreleased must contain all three sections and items.")
    next_slot = "## Unreleased\n\n### Contracts\n\n### Behavior\n\n### Operations\n\n"
    promoted = f"## {version} — {today.isoformat()}\n\n{body}\n\n"
    path.write_text(text[:start] + next_slot + promoted + text[next_release + 1 :])


def _project_version(path: Path) -> str:
    match = re.search(r'^version = "([^"]+)"$', path.read_text(), re.MULTILINE)
    if not match:
        raise ReleaseError(f"{path.name} has no project version.")
    return match.group(1)


def _fallback_version(path: Path) -> str:
    match = re.search(r'^FALLBACK_CORE_VERSION = "([^"]+)"$', path.read_text(), re.MULTILINE)
    if not match:
        raise ReleaseError("The fallback core version is absent.")
    return match.group(1)


def _json_version(path: Path) -> str:
    value = json.loads(path.read_text()).get("version")
    if not isinstance(value, str):
        raise ReleaseError(f"{path.name} has no package version.")
    return value


def _run(
    runner: Callable[..., subprocess.CompletedProcess[str]],
    args: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    try:
        return runner(
            list(args),
            cwd=cwd,
            env=env,
            check=True,
            text=True,
            capture_output=capture,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReleaseError(f"Release command failed: {' '.join(args)}") from exc


def _trusted_release_sha(
    root: Path,
    version: str,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> str:
    remote = os.environ.get("SKEIN_RELEASE_REMOTE", "github")
    tag = f"refs/tags/v{version}"
    try:
        refs = _run(
            runner,
            ["git", "ls-remote", "--exit-code", "--tags", remote, tag, f"{tag}^{{}}"],
            cwd=root,
            capture=True,
        ).stdout
    except ReleaseError as exc:
        raise ReleaseError(
            "The finalized prior release tag is unavailable. Check the trusted Git remote and access."
        ) from exc
    peeled = [
        line.split()[0]
        for line in refs.splitlines()
        if len(line.split()) == 2 and line.split()[1] == f"{tag}^{{}}"
    ]
    if len(peeled) != 1:
        raise ReleaseError("The trusted prior release tag must be one annotated tag.")
    try:
        _run(runner, ["git", "cat-file", "-e", f"{peeled[0]}^{{commit}}"], cwd=root)
    except ReleaseError as exc:
        raise ReleaseError(
            f"The finalized tag v{version} is not in this clone. Fetch it from {remote}."
        ) from exc
    return peeled[0]


def _preflight(
    root: Path,
    requested: str,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> str:
    requested_tuple = validate_version(requested)
    marker = (root / ".github/release-version").read_text().strip()
    validate_version(marker)
    current_tuple = validate_version(marker)
    if requested_tuple <= current_tuple:
        raise ReleaseError("The release version must be greater than the current version.")
    versions = {
        _project_version(root / "backend/pyproject.toml"),
        _project_version(root / "cli/pyproject.toml"),
        _json_version(root / "frontend/package.json"),
        _json_version(root / "frontend/package-lock.json"),
        _fallback_version(root / "backend/app/extensions/contracts.py"),
        marker,
    }
    if not versions <= {marker, requested}:
        raise ReleaseError("The synchronized release versions contain an unexpected value.")
    if sys.version_info[:2] != (3, 12):
        raise ReleaseError("Release preparation requires Python 3.12.")
    node = _run(runner, ["node", "--version"], cwd=root, capture=True).stdout.strip()
    if not re.fullmatch(r"v22(?:\.[0-9]+){2}", node):
        raise ReleaseError("Release preparation requires Node 22.")
    release_sha = _trusted_release_sha(root, marker, runner)
    try:
        _run(
            runner,
            [
                "git",
                "diff",
                "--quiet",
                release_sha,
                "--",
                "frontend/packages/extension-api",
            ],
            cwd=root,
        )
    except ReleaseError as exc:
        raise ReleaseError(
            "The extension API source changed without an extension API version change."
        ) from exc
    untracked = _run(
        runner,
        [
            "git",
            "ls-files",
            "--others",
            "--exclude-standard",
            "--",
            "frontend/packages/extension-api",
        ],
        cwd=root,
        capture=True,
    ).stdout
    if untracked.strip():
        raise ReleaseError(
            "The extension API package contains untracked files. Remove or commit them before release preparation."
        )
    return marker


def _replace_release_text(root: Path, old: str, new: str) -> None:
    replace_expected(
        root,
        "docs/EXTENSIONS.md",
        "In the current source, a retryable `PublicError` with status 503 adds\n"
        "`Retry-After: 60`. The scheduled job wrapper records the machine code and\n"
        "retryable value. It does not log the detail or the chained adapter error. PyPI\n"
        f"{old} does not include this behavior. If a package depends on it, wait for the\n"
        "next Skein release. Then set `minimum_core` to that released version.",
        "A retryable `PublicError` with status 503 adds `Retry-After: 60`. The\n"
        "scheduled job wrapper records the machine code and retryable value. It does\n"
        "not log the detail or the chained adapter error. Read the Contracts section\n"
        "of CHANGELOG.md for the minimum core version that includes this behavior.",
    )
    replacements = [
        ("backend/pyproject.toml", f'version = "{old}"', f'version = "{new}"', 1),
        (
            "backend/app/extensions/contracts.py",
            f'FALLBACK_CORE_VERSION = "{old}"',
            f'FALLBACK_CORE_VERSION = "{new}"',
            1,
        ),
        ("cli/pyproject.toml", f'version = "{old}"', f'version = "{new}"', 1),
        (
            "examples/workplace-extension/requirements.in",
            f"skein_agents-{old}-py3-none-any.whl",
            f"skein_agents-{new}-py3-none-any.whl",
            1,
        ),
        (
            "examples/workplace-extension/requirements-test.in",
            f"skein_agents-{old}-py3-none-any.whl",
            f"skein_agents-{new}-py3-none-any.whl",
            1,
        ),
        (
            "examples/workplace-extension/package.json",
            f"miloctl-skein-frontend-host-{old}.tgz",
            f"miloctl-skein-frontend-host-{new}.tgz",
            1,
        ),
        (
            "examples/workplace-extension/deployment/Dockerfile",
            f"skein_agents-{old}-py3-none-any.whl",
            f"skein_agents-{new}-py3-none-any.whl",
            3,
        ),
        (
            "examples/workplace-extension/deployment/Frontend.Dockerfile",
            f"miloctl-skein-frontend-host-{old}.tgz",
            f"miloctl-skein-frontend-host-{new}.tgz",
            1,
        ),
        (
            "examples/workplace-extension/deployment/skein.yaml",
            f"image: skein:{old}",
            f"image: skein:{new}",
            1,
        ),
        (
            "examples/workplace-extension/deployment/skein.yaml",
            f"image: skein-frontend:{old}",
            f"image: skein-frontend:{new}",
            1,
        ),
        (
            "deploy/k8s/overlays/example-prod/kustomization.yaml",
            f"    newTag: {old}-prod\n",
            f"    newTag: {new}-prod\n",
            1,
        ),
        (
            "deploy/k8s/overlays/example-prod/kustomization.yaml",
            f"    newTag: {old}\n",
            f"    newTag: {new}\n",
            1,
        ),
        (
            "deploy/k8s/overlays/example-dev/kustomization.yaml",
            f"    newTag: {old}-dev\n",
            f"    newTag: {new}-dev\n",
            1,
        ),
        (
            "deploy/k8s/overlays/example-dev/kustomization.yaml",
            f"    newTag: {old}\n",
            f"    newTag: {new}\n",
            1,
        ),
        (
            "deploy/k8s/base/kustomization.yaml",
            f"environment ({old}-prod)",
            f"environment ({new}-prod)",
            1,
        ),
        (
            "examples/workplace-extension/scripts/local-contract.sh",
            f"skein_agents-{old}-py3-none-any.whl",
            f"skein_agents-{new}-py3-none-any.whl",
            4,
        ),
        (
            "examples/workplace-extension/scripts/local-contract.sh",
            f"miloctl-skein-frontend-host-{old}.tgz",
            f"miloctl-skein-frontend-host-{new}.tgz",
            3,
        ),
        (
            "scripts/reference-deployment-contract.sh",
            f"skein_agents-{old}-py3-none-any.whl",
            f"skein_agents-{new}-py3-none-any.whl",
            1,
        ),
        (
            "scripts/reference-deployment-contract.sh",
            f"registry.example.com/skein/skein:{old}@sha256:",
            f"registry.example.com/skein/skein:{new}@sha256:",
            1,
        ),
        (
            "scripts/reference-deployment-contract.sh",
            f"registry.example.com/skein/skein-frontend:{old}-prod@sha256:",
            f"registry.example.com/skein/skein-frontend:{new}-prod@sha256:",
            1,
        ),
        (
            "scripts/reference-frontend-contract.sh",
            f"miloctl-skein-frontend-host-{old}.tgz",
            f"miloctl-skein-frontend-host-{new}.tgz",
            5,
        ),
        (
            "scripts/reference-images-contract.sh",
            f"skein_agents-{old}-py3-none-any.whl",
            f"skein_agents-{new}-py3-none-any.whl",
            1,
        ),
        (
            "scripts/reference-images-contract.sh",
            f"miloctl-skein-frontend-host-{old}.tgz",
            f"miloctl-skein-frontend-host-{new}.tgz",
            1,
        ),
        (
            "scripts/reference-images-contract.sh",
            f'assert version("skein-agents") == "{old}"',
            f'assert version("skein-agents") == "{new}"',
            1,
        ),
    ]
    for relative, source, target, count in replacements:
        replace_expected(root, relative, source, target, count)
    for relative, count in DOC_VERSION_COUNTS.items():
        replace_expected(root, relative, old, new, count)


def _clean_dist(dist: Path) -> None:
    dist.mkdir(parents=True, exist_ok=True)
    for path in dist.iterdir():
        if path.name != ".gitignore":
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()


def _verify_artifacts(dist: Path, version: str) -> None:
    expected = {
        f"skein_agents-{version}-py3-none-any.whl",
        "miloctl-skein-extension-api-1.0.0.tgz",
        f"miloctl-skein-frontend-host-{version}.tgz",
        "atlas_skein_extension-2.0.0-py3-none-any.whl",
    }
    actual = {path.name for path in dist.iterdir() if path.name != ".gitignore"}
    if actual != expected:
        raise ReleaseError("The release artifacts do not match the expected package set.")


def _verify_extension_api_unchanged(root: Path) -> None:
    workplace = root / "examples/workplace-extension"
    lock = json.loads((workplace / "package-lock.json").read_text())
    entry = lock.get("packages", {}).get("node_modules/@miloctl/skein-extension-api", {})
    filename = "miloctl-skein-extension-api-1.0.0.tgz"
    archive = workplace / "dist" / filename
    digest = base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode()
    if (
        entry.get("version") != "1.0.0"
        or entry.get("resolved") != f"file:dist/{filename}"
        or entry.get("integrity") != f"sha512-{digest}"
        or entry.get("link") is True
    ):
        raise ReleaseError(
            "The extension API bytes changed without an extension API version change."
        )


def _verify_workplace_lock(root: Path, version: str) -> None:
    workplace = root / "examples/workplace-extension"
    lock = json.loads((workplace / "package-lock.json").read_text())
    packages = lock.get("packages", {})
    expected = {
        "@miloctl/skein-extension-api": ("1.0.0", "miloctl-skein-extension-api-1.0.0.tgz"),
        "@miloctl/skein-frontend-host": (
            version,
            f"miloctl-skein-frontend-host-{version}.tgz",
        ),
    }
    for package, (package_version, filename) in expected.items():
        entry = packages.get(f"node_modules/{package}", {})
        archive = workplace / "dist" / filename
        digest = base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode()
        if (
            entry.get("version") != package_version
            or entry.get("resolved") != f"file:dist/{filename}"
            or entry.get("integrity") != f"sha512-{digest}"
            or entry.get("link") is True
        ):
            raise ReleaseError(f"The workplace npm lock does not match {package} bytes.")


def _assert_final_state(root: Path, old: str, new: str) -> None:
    versions = {
        _project_version(root / "backend/pyproject.toml"),
        _project_version(root / "cli/pyproject.toml"),
        _json_version(root / "frontend/package.json"),
        _json_version(root / "frontend/package-lock.json"),
        _fallback_version(root / "backend/app/extensions/contracts.py"),
    }
    if versions != {new}:
        raise ReleaseError("The prepared release versions do not agree.")
    stale_tokens = (
        f"skein_agents-{old}-py3-none-any.whl",
        f"miloctl-skein-frontend-host-{old}.tgz",
        f"skein-agents=={old}",
        f"@miloctl/skein-frontend-host@{old}",
        f"image: skein:{old}",
        f"image: skein-frontend:{old}",
        f"newTag: {old}",
    )
    active = [
        "RELEASING.md",
        "docs/SETUP.md",
        "docs/EXTENSIONS.md",
        "frontend/README.md",
        "examples/workplace-extension/README.md",
        "examples/workplace-extension/deployment/README.md",
        "examples/workplace-extension/package.json",
        "examples/workplace-extension/requirements.in",
        "examples/workplace-extension/requirements-test.in",
        "examples/workplace-extension/deployment/Dockerfile",
        "examples/workplace-extension/deployment/Frontend.Dockerfile",
        "examples/workplace-extension/deployment/skein.yaml",
        "examples/workplace-extension/scripts/local-contract.sh",
        "deploy/k8s/base/kustomization.yaml",
        "deploy/k8s/overlays/example-prod/kustomization.yaml",
        "deploy/k8s/overlays/example-dev/kustomization.yaml",
        "scripts/reference-deployment-contract.sh",
        "scripts/reference-frontend-contract.sh",
        "scripts/reference-images-contract.sh",
    ]
    for relative in active:
        text = (root / relative).read_text()
        if any(_names_prior_release(text, token) for token in stale_tokens):
            raise ReleaseError(f"{relative} still names the prior release artifact.")


def _names_prior_release(text: str, token: str) -> bool:
    """A stale token followed by a digit is the NEW number, not a leftover:
    0.3.2 is a prefix of 0.3.20, so a plain substring check refuses the
    twentieth patch release with its own replacements. A non-digit follower
    (newline, `-prod`, `.whl`) is a real leftover."""
    return re.search(re.escape(token) + r"(?!\d)", text) is not None


def prepare(
    root: Path,
    version: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    today: date | None = None,
) -> None:
    old = _preflight(root, version, runner)
    promote_changelog(root, version, today or date.today())
    _replace_release_text(root, old, version)

    frontend = root / "frontend"
    if _json_version(frontend / "package.json") == old:
        _run(
            runner,
            ["npm", "version", version, "--package-lock-only", "--no-git-tag-version"],
            cwd=frontend,
        )

    dist = root / "examples/workplace-extension/dist"
    _clean_dist(dist)
    env = os.environ.copy()
    env["UV_PYTHON"] = sys.executable
    commands = [
        (["uv", "build", "--quiet", "--wheel", "--out-dir", str(dist), "backend"], root),
        (
            [
                "uv",
                "build",
                "--quiet",
                "--wheel",
                "--out-dir",
                str(dist),
                "examples/workplace-extension",
            ],
            root,
        ),
        (
            [
                "npm",
                "pack",
                "--silent",
                "--pack-destination",
                str(dist),
                "./frontend/packages/extension-api",
            ],
            root,
        ),
        (
            [
                "npm",
                "pack",
                "--silent",
                "--pack-destination",
                str(dist),
                "./frontend",
            ],
            root,
        ),
    ]
    for args, cwd in commands:
        _run(runner, args, cwd=cwd, env=env)
    _verify_artifacts(dist, version)

    workplace = root / "examples/workplace-extension"
    _verify_extension_api_unchanged(root)
    _run(
        runner,
        [
            "npm",
            "update",
            "@miloctl/skein-frontend-host",
            "--package-lock-only",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
        ],
        cwd=workplace,
    )
    _verify_workplace_lock(root, version)
    compile_common = [
        "--python-version",
        "3.12",
        "--no-emit-package",
        "skein-agents",
        "--no-emit-package",
        "atlas-skein-extension",
        "--generate-hashes",
    ]
    _run(
        runner,
        [
            "uv",
            "pip",
            "compile",
            "requirements.in",
            *compile_common,
            "--output-file",
            "requirements.lock",
        ],
        cwd=workplace,
        env=env,
    )
    _run(
        runner,
        [
            "uv",
            "pip",
            "compile",
            "requirements-test.in",
            *compile_common,
            "--output-file",
            "requirements-test.lock",
        ],
        cwd=workplace,
        env=env,
    )

    _assert_final_state(root, old, version)
    _run(
        runner,
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(root / "backend/.venv/bin/python"),
            "--no-deps",
            "-e",
            str(root / "backend"),
        ],
        cwd=root,
        env=env,
    )
    test_env = env.copy()
    test_env["SKEIN_RELEASE_MARKER_OVERRIDE"] = version
    _run(
        runner,
        [
            str(root / "backend/.venv/bin/python"),
            "-m",
            "pytest",
            "-q",
            "-n0",
            "tests/test_release_contract.py",
        ],
        cwd=root / "backend",
        env=test_env,
    )
    replace_expected(
        root,
        ".github/release-version",
        f"{old}\n",
        f"{version}\n",
    )


def main() -> int:
    try:
        if len(sys.argv) != 2:
            raise ReleaseError("Usage: prepare-release.py X.Y.Z")
        prepare(ROOT, sys.argv[1])
        return 0
    except ReleaseError as exc:
        print(f"prepare-release: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
