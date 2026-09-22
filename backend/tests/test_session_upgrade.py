"""The cross-version drill must refuse unsafe targets and disabled checks."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import authored_repo_root

SCRIPT = authored_repo_root(Path(__file__)) / "scripts/check-session-upgrade.py"
_spec = importlib.util.spec_from_file_location("session_upgrade", SCRIPT)
upgrade = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(upgrade)


@pytest.mark.parametrize("owned", ["skein", "skein_test", "skein_sdk_upgrade_not-owned"])
def test_upgrade_worker_refuses_unowned_database_before_connecting(monkeypatch, tmp_path, owned):
    import psycopg

    def forbidden(*args, **kwargs):
        pytest.fail("An unsafe target reached PostgreSQL")

    monkeypatch.setattr(psycopg, "connect", forbidden)
    monkeypatch.setenv("SKEIN_DATABASE_URL", f"dbname={owned}")
    with pytest.raises(ValueError, match="ownership name"):
        upgrade.worker("write", owned, tmp_path / "checkpoint.json")


def test_upgrade_target_must_match_its_ownership_name():
    owned = "skein_sdk_upgrade_" + "a" * 32
    upgrade.guard_target(f"dbname={owned}", owned)
    with pytest.raises(ValueError, match="not owned"):
        upgrade.guard_target("dbname=skein", owned)


def test_upgrade_refuses_optimized_python():
    result = subprocess.run(  # noqa: S603 — this interpreter and the repository's test driver
        [sys.executable, "-O", str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "Optimized Python disables the checks" in result.stderr
