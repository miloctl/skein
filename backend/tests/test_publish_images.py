import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "publish-images.sh"
GIT = shutil.which("git")
assert GIT


def _git(cwd: Path, *args: str) -> None:
    subprocess.run((GIT, *args), cwd=cwd, check=True)  # noqa: S603 -- resolved git


def _run(*args: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 -- fixed test commands
        args,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _release_tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "repo"
    (root / "backend").mkdir(parents=True)
    (root / "frontend").mkdir()
    (root / "scripts").mkdir()
    (root / "backend" / "pyproject.toml").write_text('[project]\nversion = "0.3.2"\n')
    script = root / "scripts" / "publish-images.sh"
    shutil.copy2(SCRIPT, script)

    tool = tmp_path / "container-tool"
    tool.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >>"$FAKE_LOG"\n'
        'if [ "$1" = manifest ] && [ "$2" = inspect ]; then\n'
        '  case "$FAKE_MODE" in\n'
        "    exists) exit 0 ;;\n"
        '    missing) echo "no such manifest: $3" >&2; exit 1 ;;\n'
        '    error) echo "registry unavailable" >&2; exit 1 ;;\n'
        "  esac\n"
        "fi\n"
        "exit 0\n"
    )
    tool.chmod(0o755)

    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Contract")
    _git(root, "config", "user.email", "contract@example.invalid")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "release")
    _git(root, "tag", "-a", "v0.3.2", "-m", "v0.3.2")
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", "-q", str(remote))
    _git(root, "remote", "add", "github", str(remote))
    _git(root, "push", "-q", "github", "HEAD:refs/heads/main", "refs/tags/v0.3.2")
    return root, script, tool


def _environment(tmp_path: Path, tool: Path, mode: str) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "CONTAINER_TOOL": str(tool),
            "FAKE_LOG": str(tmp_path / "container.log"),
            "FAKE_MODE": mode,
            "SKEIN_REGISTRY": "registry.example.invalid/team",
        }
    )
    return environment


def test_image_publication_requires_a_clean_release_tree(tmp_path):
    root, script, tool = _release_tree(tmp_path)
    (root / "untracked").write_text("different bytes\n")

    result = _run(
        str(script),
        "0.3.2",
        "prod=https://api.example.invalid,https://example.invalid",
        cwd=root,
        env=_environment(tmp_path, tool, "missing"),
    )

    assert result.returncode != 0
    assert "the release tree has changes" in result.stderr
    assert not (tmp_path / "container.log").exists()


@pytest.mark.parametrize(
    "specs",
    (
        ("prod=https://api.example.invalid",),
        ("prod=,https://example.invalid",),
        ("prod=https://api.example.invalid,",),
        ("prod=https://api.example.invalid,https://example.invalid,extra",),
        (
            "prod=https://api.example.invalid,https://example.invalid",
            "prod=https://other-api.example.invalid,https://other.example.invalid",
        ),
    ),
)
def test_image_publication_refuses_invalid_or_duplicate_environments(tmp_path, specs):
    root, script, tool = _release_tree(tmp_path)
    result = _run(
        str(script),
        "0.3.2",
        *specs,
        cwd=root,
        env=_environment(tmp_path, tool, "missing"),
    )

    assert result.returncode != 0
    assert not (tmp_path / "container.log").exists()


def test_image_publication_uses_the_trusted_remote_without_a_local_tag(tmp_path):
    root, script, tool = _release_tree(tmp_path)
    _git(root, "tag", "-d", "v0.3.2")

    result = _run(
        str(script),
        "0.3.2",
        "prod=https://api.example.invalid,https://example.invalid",
        cwd=root,
        env=_environment(tmp_path, tool, "missing"),
    )

    assert result.returncode == 0, result.stderr


def test_image_publication_refuses_a_lightweight_trusted_remote_tag(tmp_path):
    root, script, tool = _release_tree(tmp_path)
    _git(root, "push", "-q", "github", ":refs/tags/v0.3.2")
    _git(root, "tag", "-d", "v0.3.2")
    _git(root, "tag", "v0.3.2")
    _git(root, "push", "-q", "github", "refs/tags/v0.3.2")

    result = _run(
        str(script),
        "0.3.2",
        "prod=https://api.example.invalid,https://example.invalid",
        cwd=root,
        env=_environment(tmp_path, tool, "missing"),
    )

    assert result.returncode != 0
    assert "trusted remote tag" in result.stderr
    assert not (tmp_path / "container.log").exists()


def test_image_publication_refuses_head_past_the_trusted_remote(tmp_path):
    root, script, tool = _release_tree(tmp_path)
    (root / "later").write_text("later commit\n")
    _git(root, "add", "later")
    _git(root, "commit", "-qm", "later")

    result = _run(
        str(script),
        "0.3.2",
        "prod=https://api.example.invalid,https://example.invalid",
        cwd=root,
        env=_environment(tmp_path, tool, "missing"),
    )

    assert result.returncode != 0
    assert "trusted remote tag" in result.stderr
    assert not (tmp_path / "container.log").exists()


def test_image_publication_refuses_existing_or_unreadable_remote_tags(tmp_path):
    for mode, expected in (
        ("exists", "already exists"),
        ("error", "did not report whether"),
    ):
        case = tmp_path / mode
        case.mkdir()
        root, script, tool = _release_tree(case)
        result = _run(
            str(script),
            "0.3.2",
            "prod=https://api.example.invalid,https://example.invalid",
            cwd=root,
            env=_environment(case, tool, mode),
        )

        assert result.returncode != 0
        assert expected in result.stderr
        calls = (case / "container.log").read_text().splitlines()
        assert calls == ["manifest inspect registry.example.invalid/team/skein:0.3.2"]


def test_image_publication_checks_all_tags_before_building(tmp_path):
    root, script, tool = _release_tree(tmp_path)
    result = _run(
        str(script),
        "0.3.2",
        "prod=https://api.example.invalid,https://example.invalid",
        "dev=https://api-dev.example.invalid,https://dev.example.invalid",
        cwd=root,
        env=_environment(tmp_path, tool, "missing"),
    )

    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "container.log").read_text().splitlines()
    assert calls[:3] == [
        "manifest inspect registry.example.invalid/team/skein:0.3.2",
        "manifest inspect registry.example.invalid/team/skein-frontend:0.3.2-prod",
        "manifest inspect registry.example.invalid/team/skein-frontend:0.3.2-dev",
    ]
    assert calls[3].startswith("build -t registry.example.invalid/team/skein:0.3.2 ")
