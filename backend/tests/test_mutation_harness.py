"""Authored-file checks cannot redirect copied application imports."""

import importlib
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import conftest as fixtures
import pytest
import test_durability_harness as durability


@pytest.mark.parametrize("layout", ["backend/tests", "backend/mutants/tests"])
def test_authored_root_skips_partial_nearer_roots(tmp_path, layout):
    root = tmp_path / "repo"
    (root / "backend").mkdir(parents=True)
    (root / "backend/pyproject.toml").touch()
    (root / "docs").mkdir()
    (root / "docs/FEATURES.md").touch()
    start = root / layout / "test_probe.py"
    start.parent.mkdir(parents=True, exist_ok=True)
    (start.parent / "backend").mkdir()
    (start.parent / "backend/pyproject.toml").touch()
    (start.parent.parent / "docs").mkdir()
    (start.parent.parent / "docs/FEATURES.md").touch()

    assert fixtures.authored_repo_root(start) == root


def test_authored_root_refuses_an_unrelated_tree(tmp_path):
    with pytest.raises(FileNotFoundError, match="authored repository"):
        fixtures.authored_repo_root(
            Path(tmp_path.anchor) / "skein-no-repository/tests/test_probe.py"
        )


def test_standalone_conftest_does_not_resolve_the_repository_at_import(tmp_path):
    copied = tmp_path / "conftest.py"
    shutil.copy2(fixtures.__file__, copied)
    result = subprocess.run(  # noqa: S603 -- current interpreter, copied test fixture
        [
            sys.executable,
            "-c",
            "import pathlib,runpy,sys\n"
            "original = pathlib.Path.is_file\n"
            "def is_file(path):\n"
            "    assert not path.as_posix().endswith(('/backend/pyproject.toml', '/docs/FEATURES.md'))\n"
            "    return original(path)\n"
            "pathlib.Path.is_file = is_file\n"
            "runpy.run_path(sys.argv[1])\n",
            str(copied),
        ],
        cwd=tmp_path,
        env={"PATH": os.environ["PATH"], "PYTHON_DOTENV_DISABLED": "1", "TMPDIR": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_config_reload_restores_database_settings_between_tests():
    import test_config_files

    from app import config

    url = "postgresql://config:unused@127.0.0.1:1/config"
    try:
        with pytest.MonkeyPatch.context() as baseline:
            baseline.setenv("SKEIN_DATABASE_URL", url)
            for key in test_config_files._FILE_KEYS:
                baseline.setenv(key, os.environ.get(key, ""))
            importlib.reload(config)
            with pytest.MonkeyPatch.context() as changed:
                restore = test_config_files._restore_config.__wrapped__(changed)
                next(restore)
                changed.delenv("SKEIN_DATABASE_URL")
                changed.delenv("SKEIN_DB_HOST", raising=False)
                assert config._database_url() == ""
                with pytest.raises(StopIteration):
                    next(restore)
                assert url == config.DATABASE_URL
                assert config.DATABASE_ERROR == ""
    finally:
        importlib.reload(config)


def test_copied_durability_check_keeps_the_copied_application(tmp_path):
    root = tmp_path / "repo"
    backend = root / "backend"
    copied_backend = backend / "mutants"
    tests = copied_backend / "tests"
    tests.mkdir(parents=True)
    for relative in (
        "backend/pyproject.toml",
        "docs/FEATURES.md",
        "scripts/durability-contract.py",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(durability.harness.ROOT / relative, target)
    app = copied_backend / "app"
    app.mkdir()
    for name in ("__init__.py", "config.py"):
        shutil.copy2(Path(fixtures.__file__).parents[1] / "app" / name, app / name)
    copied = tests / "test_durability_harness.py"
    shutil.copy2(durability.__file__, copied)

    check = runpy.run_path(str(copied))

    assert check["SCRIPT"] == root / "scripts/durability-contract.py"
    check["test_inherited_image_settings_cannot_select_external_services"](tmp_path)
