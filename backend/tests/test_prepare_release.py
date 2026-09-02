import base64
import hashlib
import importlib.util
import json
import shutil
import subprocess
from datetime import date
from pathlib import Path
from typing import NamedTuple

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "prepare_release", ROOT / "scripts/prepare-release.py"
)
assert SPEC and SPEC.loader
prepare_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare_release)

RELEASE_FILES = (
    ".github/release-version",
    "CHANGELOG.md",
    "RELEASING.md",
    "backend/app/extensions/contracts.py",
    "backend/pyproject.toml",
    "cli/pyproject.toml",
    "docs/EXTENSIONS.md",
    "docs/SETUP.md",
    "deploy/k8s/base/kustomization.yaml",
    "deploy/k8s/overlays/example-dev/kustomization.yaml",
    "deploy/k8s/overlays/example-prod/kustomization.yaml",
    "examples/workplace-extension/README.md",
    "examples/workplace-extension/deployment/Dockerfile",
    "examples/workplace-extension/deployment/Frontend.Dockerfile",
    "examples/workplace-extension/deployment/README.md",
    "examples/workplace-extension/deployment/skein.yaml",
    "examples/workplace-extension/package-lock.json",
    "examples/workplace-extension/package.json",
    "examples/workplace-extension/pyproject.toml",
    "examples/workplace-extension/requirements-test.in",
    "examples/workplace-extension/requirements-test.lock",
    "examples/workplace-extension/requirements.in",
    "examples/workplace-extension/requirements.lock",
    "frontend/README.md",
    "frontend/package-lock.json",
    "frontend/package.json",
    "scripts/reference-deployment-contract.sh",
    "scripts/reference-frontend-contract.sh",
    "scripts/reference-images-contract.sh",
    "scripts/publish-images.sh",
    "examples/workplace-extension/scripts/local-contract.sh",
)


class ReleaseFixture(NamedTuple):
    root: Path
    old: str
    target: str


@pytest.fixture()
def release_tree(tmp_path):
    for relative in RELEASE_FILES:
        source = ROOT / relative
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    dist = tmp_path / "examples/workplace-extension/dist"
    dist.mkdir(parents=True)
    (dist / ".gitignore").write_text("*\n!.gitignore\n")
    old = prepare_release._project_version(tmp_path / "backend/pyproject.toml")
    major, minor, patch = prepare_release.validate_version(old)
    target = f"{major}.{minor}.{patch + 1}"
    changelog = tmp_path / "CHANGELOG.md"
    text = changelog.read_text()
    filled = (
        "## Unreleased\n\n"
        "### Contracts\n\n- No contract change.\n\n"
        "### Behavior\n\n- One behavior fix.\n\n"
        "### Operations\n\n- One operations fix.\n\n"
    )
    start = text.index("## Unreleased\n")
    end = text.index("\n## ", start + 1)
    current = text[start:end]
    for heading in ("### Contracts", "### Behavior", "### Operations"):
        assert current.count(heading) == 1
    changelog.write_text(text[:start] + filled + text[end + 1 :])
    lock_path = tmp_path / "examples/workplace-extension/package-lock.json"
    lock = json.loads(lock_path.read_text())
    api_filename = "miloctl-skein-extension-api-1.0.0.tgz"
    api_digest = base64.b64encode(hashlib.sha512(api_filename.encode()).digest()).decode()
    lock["packages"]["node_modules/@miloctl/skein-extension-api"]["integrity"] = (
        f"sha512-{api_digest}"
    )
    lock_path.write_text(f"{json.dumps(lock, indent=2)}\n")
    return ReleaseFixture(tmp_path, old, target)


class FakeRun:
    def __init__(
        self,
        root: Path,
        version: str,
        *,
        fail_build: bool = False,
        fail_test: bool = False,
        changed_api: bool = False,
        changed_api_source: bool = False,
        untracked_api_source: bool = False,
        trusted_tag: str = "annotated",
    ):
        self.root = root
        self.version = version
        self.old = (root / ".github/release-version").read_text().strip()
        self.fail_build = fail_build
        self.fail_test = fail_test
        self.changed_api = changed_api
        self.changed_api_source = changed_api_source
        self.untracked_api_source = untracked_api_source
        self.trusted_tag = trusted_tag
        self.calls: list[tuple[list[str], str]] = []

    def __call__(self, args, *, cwd, **_kwargs):
        args = list(args)
        marker = (self.root / ".github/release-version").read_text().strip()
        self.calls.append((args, marker))
        if args == ["node", "--version"]:
            return subprocess.CompletedProcess(args, 0, stdout="v22.23.2\n", stderr="")
        if args[:3] == ["git", "ls-remote", "--exit-code"]:
            if self.trusted_tag == "unavailable":
                raise subprocess.CalledProcessError(2, args)
            tag = f"refs/tags/v{self.old}"
            lines = [f"{'a' * 40}\t{tag}"]
            if self.trusted_tag != "lightweight":
                lines.append(f"{'b' * 40}\t{tag}^{{}}")
            return subprocess.CompletedProcess(args, 0, stdout="\n".join(lines) + "\n", stderr="")
        if args[:3] == ["git", "cat-file", "-e"] and self.trusted_tag == "missing-object":
            raise subprocess.CalledProcessError(1, args)
        if args[:3] == ["git", "diff", "--quiet"] and self.changed_api_source:
            raise subprocess.CalledProcessError(1, args)
        if args[:2] == ["git", "ls-files"]:
            stdout = "README.md\n" if self.untracked_api_source else ""
            return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")
        if args[:2] == ["npm", "version"]:
            for name in ("package.json", "package-lock.json"):
                path = Path(cwd) / name
                data = json.loads(path.read_text())
                data["version"] = self.version
                if name == "package-lock.json":
                    data["packages"][""]["version"] = self.version
                path.write_text(f"{json.dumps(data, indent=2)}\n")
        elif args[:2] == ["uv", "build"]:
            if self.fail_build:
                raise subprocess.CalledProcessError(1, args)
            dist = self.root / "examples/workplace-extension/dist"
            filename = (
                f"skein_agents-{self.version}-py3-none-any.whl"
                if args[-1] == "backend"
                else "atlas_skein_extension-2.0.0-py3-none-any.whl"
            )
            (dist / filename).write_bytes(filename.encode())
        elif args[:2] == ["npm", "pack"]:
            dist = self.root / "examples/workplace-extension/dist"
            filename = (
                "miloctl-skein-extension-api-1.0.0.tgz"
                if args[-1].endswith("extension-api")
                else f"miloctl-skein-frontend-host-{self.version}.tgz"
            )
            content = filename.encode()
            if self.changed_api and args[-1].endswith("extension-api"):
                content += b"changed"
            (dist / filename).write_bytes(content)
        elif args[:2] == ["npm", "update"]:
            workplace = self.root / "examples/workplace-extension"
            path = workplace / "package-lock.json"
            lock = json.loads(path.read_text())
            for package, package_version, filename in (
                (
                    "@miloctl/skein-extension-api",
                    "1.0.0",
                    "miloctl-skein-extension-api-1.0.0.tgz",
                ),
                (
                    "@miloctl/skein-frontend-host",
                    self.version,
                    f"miloctl-skein-frontend-host-{self.version}.tgz",
                ),
            ):
                archive = workplace / "dist" / filename
                integrity = base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode()
                lock["packages"][""]["dependencies"][package] = f"file:dist/{filename}"
                lock["packages"][f"node_modules/{package}"].update(
                    version=package_version,
                    resolved=f"file:dist/{filename}",
                    integrity=f"sha512-{integrity}",
                )
            path.write_text(f"{json.dumps(lock, indent=2)}\n")
        elif len(args) > 2 and args[1:3] == ["-m", "pytest"] and self.fail_test:
            raise subprocess.CalledProcessError(1, args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")


@pytest.mark.parametrize(
    "value",
    ("1.2", "1.2.3.4", "01.2.3", "1.02.3", "1.2.03", "v1.2.3", "1.2.x"),
)
def test_release_version_must_be_canonical(value):
    with pytest.raises(prepare_release.ReleaseError, match=r"X\.Y\.Z"):
        prepare_release.validate_version(value)


def test_release_version_orders_numerically():
    assert prepare_release.validate_version("1.12.3") > prepare_release.validate_version("1.9.9")


def test_expected_replacement_refuses_drift(tmp_path):
    path = tmp_path / "version.txt"
    path.write_text("version=0.3.0\nversion=0.3.0\n")
    with pytest.raises(prepare_release.ReleaseError, match=r"version\.txt"):
        prepare_release.replace_expected(tmp_path, "version.txt", "version=0.3.0", "version=0.3.2")


def test_expected_replacement_can_resume(tmp_path):
    path = tmp_path / "version.txt"
    path.write_text("version=0.3.2\n")
    prepare_release.replace_expected(tmp_path, "version.txt", "version=0.3.0", "version=0.3.2")
    assert path.read_text() == "version=0.3.2\n"


def test_unreleased_notes_promote_and_leave_the_next_slot(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(
        "# Changelog\n\n"
        "## Unreleased\n\n"
        "### Contracts\n\n- No contract change.\n\n"
        "### Behavior\n\n- One behavior fix.\n\n"
        "### Operations\n\n- One operations fix.\n\n"
        "## 0.3.0 — 2026-08-27\n\nOld notes.\n"
    )
    prepare_release.promote_changelog(tmp_path, "0.3.2", date(2026, 8, 28))
    text = path.read_text()
    assert text.startswith(
        "# Changelog\n\n"
        "## Unreleased\n\n"
        "### Contracts\n\n"
        "### Behavior\n\n"
        "### Operations\n\n"
        "## 0.3.2 — 2026-08-28\n"
    )
    assert "- One behavior fix." in text
    assert "- One operations fix.\n\n## 0.3.0 — 2026-08-27" in text
    assert "## 0.3.1" not in text


def test_changelog_promotion_resumes_across_a_date_boundary(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(
        "# Changelog\n\n"
        "## Unreleased\n\n"
        "### Contracts\n\n- No contract change.\n\n"
        "### Behavior\n\n- One behavior fix.\n\n"
        "### Operations\n\n- One operations fix.\n\n"
        "## 0.3.0 — 2026-08-27\n\nOld notes.\n"
    )
    prepare_release.promote_changelog(tmp_path, "0.3.2", date(2026, 8, 28))
    first = path.read_text()
    prepare_release.promote_changelog(tmp_path, "0.3.2", date(2026, 8, 29))
    assert path.read_text() == first


def test_changelog_resume_refuses_new_unreleased_items(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n"
        "## Unreleased\n\n"
        "### Contracts\n\n- A late contract change.\n\n"
        "### Behavior\n\n"
        "### Operations\n\n"
        "## 0.3.2 — 2026-08-28\n\nPrepared notes.\n"
    )
    with pytest.raises(prepare_release.ReleaseError, match="canonical resumed"):
        prepare_release.promote_changelog(tmp_path, "0.3.2", date(2026, 8, 29))


def test_changelog_resume_requires_target_as_the_first_release(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n"
        "## Unreleased\n\n"
        "### Contracts\n\n"
        "### Behavior\n\n"
        "### Operations\n\n"
        "## 0.3.3 — 2026-08-29\n\nOther release.\n\n"
        "## 0.3.2 — 2026-08-28\n\nStale target.\n"
    )
    with pytest.raises(prepare_release.ReleaseError, match="canonical resumed"):
        prepare_release.promote_changelog(tmp_path, "0.3.2", date(2026, 8, 29))


def test_changelog_resume_requires_populated_target_sections(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n"
        "## Unreleased\n\n"
        "### Contracts\n\n"
        "### Behavior\n\n"
        "### Operations\n\n"
        "## 0.3.2 — 2026-08-28\n\n"
        "### Contracts\n\n"
        "### Behavior\n\n"
        "### Operations\n\n"
        "## 0.3.0 — 2026-08-27\n\nOld notes.\n"
    )
    with pytest.raises(prepare_release.ReleaseError, match="canonical resumed"):
        prepare_release.promote_changelog(tmp_path, "0.3.2", date(2026, 8, 29))


def test_unreleased_notes_require_all_sections(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## Unreleased\n\n### Contracts\n\n- No change.\n"
    )
    with pytest.raises(prepare_release.ReleaseError, match="Unreleased"):
        prepare_release.promote_changelog(tmp_path, "0.3.2", date(2026, 8, 28))


def test_prepare_builds_artifacts_before_locks_and_writes_marker_last(release_tree):
    root, old, target = release_tree
    runner = FakeRun(root, target)
    prepare_release.prepare(
        root,
        target,
        runner=runner,
        today=date(2026, 8, 28),
    )
    names = [args[:2] for args, _marker in runner.calls]
    build_indexes = [
        i for i, name in enumerate(names) if name in (["uv", "build"], ["npm", "pack"])
    ]
    lock_indexes = [i for i, name in enumerate(names) if name in (["npm", "update"], ["uv", "pip"])]
    assert max(build_indexes) < min(lock_indexes)
    assert all(marker == old for _args, marker in runner.calls)
    assert runner.calls[-2][0][:3] == ["uv", "pip", "install"]
    assert runner.calls[-1][0][1:3] == ["-m", "pytest"]
    assert (root / ".github/release-version").read_text() == f"{target}\n"
    assert f"## {target} — 2026-08-28" in (root / "CHANGELOG.md").read_text()
    assert ">=0.3.0,<0.6.0" in (root / "examples/workplace-extension/pyproject.toml").read_text()
    assert (
        f"newTag: {target}"
        in (root / "deploy/k8s/overlays/example-prod/kustomization.yaml").read_text()
    )
    assert (
        f"newTag: {target}-dev"
        in (root / "deploy/k8s/overlays/example-dev/kustomization.yaml").read_text()
    )
    assert f"publish-images.sh {target}" in (root / "scripts/publish-images.sh").read_text()
    extension_guide = (root / "docs/EXTENSIONS.md").read_text()
    assert "In the current source" not in extension_guide
    assert "Read the Contracts section" in extension_guide


@pytest.mark.parametrize(
    ("trusted_tag", "message"),
    (
        ("unavailable", "tag is unavailable"),
        ("lightweight", "must be one annotated tag"),
        ("missing-object", "is not in this clone"),
    ),
)
def test_prior_release_requires_the_trusted_annotated_tag(release_tree, trusted_tag, message):
    root, old, target = release_tree
    runner = FakeRun(root, target, trusted_tag=trusted_tag)

    with pytest.raises(prepare_release.ReleaseError, match=message):
        prepare_release.prepare(root, target, runner=runner, today=date(2026, 8, 28))

    assert (root / ".github/release-version").read_text() == f"{old}\n"
    assert f"## {target}" not in (root / "CHANGELOG.md").read_text()


def test_changed_extension_api_source_refuses_before_release_writes(release_tree):
    root, old, target = release_tree
    runner = FakeRun(root, target, changed_api_source=True)

    with pytest.raises(prepare_release.ReleaseError, match="extension API source changed"):
        prepare_release.prepare(root, target, runner=runner, today=date(2026, 8, 28))

    assert (root / ".github/release-version").read_text() == f"{old}\n"
    assert f"## {target}" not in (root / "CHANGELOG.md").read_text()
    assert not any(args[:2] == ["npm", "update"] for args, _marker in runner.calls)


def test_untracked_extension_api_source_refuses_before_release_writes(release_tree):
    root, old, target = release_tree
    runner = FakeRun(root, target, untracked_api_source=True)

    with pytest.raises(prepare_release.ReleaseError, match="untracked files"):
        prepare_release.prepare(root, target, runner=runner, today=date(2026, 8, 28))

    assert (root / ".github/release-version").read_text() == f"{old}\n"
    assert f"## {target}" not in (root / "CHANGELOG.md").read_text()


def test_changed_extension_api_refuses_before_lock_update(release_tree):
    root, old, target = release_tree
    runner = FakeRun(root, target, changed_api=True)

    with pytest.raises(prepare_release.ReleaseError, match="version change"):
        prepare_release.prepare(
            root,
            target,
            runner=runner,
            today=date(2026, 8, 28),
        )

    assert not any(args[:2] == ["npm", "update"] for args, _marker in runner.calls)
    assert (root / ".github/release-version").read_text() == f"{old}\n"


def test_workplace_lock_checks_extension_api_bytes(release_tree):
    root, _old, target = release_tree
    prepare_release.prepare(
        root,
        target,
        runner=FakeRun(root, target),
        today=date(2026, 8, 28),
    )
    archive = root / "examples/workplace-extension/dist/miloctl-skein-extension-api-1.0.0.tgz"
    archive.write_bytes(archive.read_bytes() + b"changed")

    with pytest.raises(prepare_release.ReleaseError, match="extension-api"):
        prepare_release._verify_workplace_lock(root, target)


def test_build_failure_never_advances_the_marker(release_tree):
    root, old, target = release_tree
    runner = FakeRun(root, target, fail_build=True)
    with pytest.raises(prepare_release.ReleaseError, match="Release command failed"):
        prepare_release.prepare(
            root,
            target,
            runner=runner,
            today=date(2026, 8, 28),
        )
    assert (root / ".github/release-version").read_text() == f"{old}\n"


def test_final_test_failure_keeps_marker_and_can_retry(release_tree):
    root, old, target = release_tree
    failing = FakeRun(root, target, fail_test=True)
    with pytest.raises(prepare_release.ReleaseError, match="Release command failed"):
        prepare_release.prepare(
            root,
            target,
            runner=failing,
            today=date(2026, 8, 28),
        )
    assert (root / ".github/release-version").read_text() == f"{old}\n"

    retry = FakeRun(root, target)
    prepare_release.prepare(
        root,
        target,
        runner=retry,
        today=date(2026, 8, 28),
    )
    assert (root / ".github/release-version").read_text() == f"{target}\n"


def test_equal_or_lower_release_fails_before_commands(release_tree):
    root, old, _target = release_tree
    for version in (old, "0.0.0"):
        runner = FakeRun(root, version)
        with pytest.raises(prepare_release.ReleaseError, match="greater"):
            prepare_release.prepare(root, version, runner=runner)
        assert runner.calls == []


def test_stale_check_is_not_fooled_by_a_version_prefix():
    """Releasing 0.3.20 after 0.3.2: every replaced file contains the new
    number, and the old number is a substring of it — a plain substring
    check makes the twentieth patch release unreachable."""
    token = "newTag: 0.3.2"
    assert prepare_release._names_prior_release("newTag: 0.3.2\n", token)
    assert prepare_release._names_prior_release("newTag: 0.3.2-prod\n", token)
    assert not prepare_release._names_prior_release("newTag: 0.3.20\n", token)
    assert not prepare_release._names_prior_release("newTag: 0.3.21-prod\n", token)
