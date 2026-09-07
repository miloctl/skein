"""The Docker driver's cleanup cannot cross its run's ownership label."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/durability-contract.py"
spec = importlib.util.spec_from_file_location("durability_contract", SCRIPT)
assert spec and spec.loader
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


def test_optimized_python_cannot_skip_drill_assertions():
    result = subprocess.run(  # noqa: S603 — current interpreter and the checked-in test driver
        [sys.executable, "-O", str(SCRIPT), "--help"], capture_output=True, text=True, check=False
    )
    assert result.returncode != 0
    assert "without -O" in result.stderr


@pytest.mark.parametrize("kind", ["container", "volume", "network", "image"])
def test_cleanup_refuses_an_unowned_resource(monkeypatch, kind):
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        return json.dumps([{"Config": {"Labels": {}}, "Labels": {}}])

    monkeypatch.setattr(harness, "docker", docker)
    with pytest.raises(RuntimeError, match="unowned"):
        harness.cleanup({"prefix": "skein-durability-proof", "resources": {kind: ["other"]}})
    assert calls == [(kind, "inspect", "other")]


def test_inherited_image_settings_cannot_select_external_services(tmp_path):
    inherited = [
        "PATH=/foreign/bin",
        "SKEIN_DATABASE_URL=postgresql://unused.invalid:1/not-owned",
        "SKEIN_GITHUB_RECOVERY=1",
        "SKEIN_GITHUB_TOKEN=fixture-only-token",
        "SKEIN_GITHUB_API_URL=https://unused.invalid",
        'SKEIN_MCP_SERVERS=[{"url":"https://unused.invalid"}]',
        "SLACK_WEBHOOK_URL=https://unused.invalid",
        "OTEL_EXPORTER_OTLP_ENDPOINT=https://unused.invalid",
        "AWS_SECRET_ACCESS_KEY=fixture-only-key",
        "HTTPS_PROXY=http://unused.invalid:1",
    ]
    env = harness.clean_environment(
        inherited,
        {
            "SKEIN_DATA_DIR": str(tmp_path),
            "SKEIN_DB_HOST": "owned-db",
            "SKEIN_DB_PORT": "5432",
            "SKEIN_DB_USER": "owned-role",
            "SKEIN_DB_PASSWORD": "owned-fixture-password",
            "SKEIN_DB_NAME": "owned-database",
            "SKEIN_MODEL_PROVIDER": "mock",
        },
    )
    assert env["SKEIN_DATABASE_URL"] == ""
    assert env["SKEIN_GITHUB_RECOVERY"] == "0"
    assert all(
        env[key] == ""
        for key in (
            "SKEIN_GITHUB_TOKEN",
            "SKEIN_MCP_SERVERS",
            "SLACK_WEBHOOK_URL",
            "AWS_SECRET_ACCESS_KEY",
            "HTTPS_PROXY",
        )
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json,socket; socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('No network in configuration check')); from app import config; from psycopg.conninfo import conninfo_to_dict; p=conninfo_to_dict(config.DATABASE_URL); print(json.dumps({k:p[k] for k in ('host','port','user','dbname')}))",
        ],
        cwd=SCRIPT.parent.parent / "backend",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "host": "owned-db",
        "port": "5432",
        "user": "owned-role",
        "dbname": "owned-database",
    }


def test_storage_oracle_requires_actual_filesystem_failure(monkeypatch):
    driver = harness.Harness.__new__(harness.Harness)
    driver.pods = {"a": {"name": "owned"}}
    responses = iter(
        [
            ((200, {"id": 1}, {}), b"baseline"),
            (
                (
                    500,
                    {
                        "detail": "Skein cannot access its storage. Ask whoever runs the server to check storage permissions."
                    },
                    {},
                ),
                b"",
            ),
            ((200, {"id": 2}, {}), b"restored"),
        ]
    )
    monkeypatch.setattr(driver, "upload", lambda *args: next(responses))
    monkeypatch.setattr(driver, "sql", lambda *args: [{"n": 1}])
    monkeypatch.setattr(driver, "events", lambda *args: [])
    commands = []
    monkeypatch.setattr(harness, "docker", lambda *args: commands.append(args))
    with pytest.raises(AssertionError):
        driver.storage_failure()
    assert commands[-1][-2:] == ("0770", "/data/artifacts/uploads")


def test_cleanup_only_removes_the_explicit_owned_list(monkeypatch):
    calls = []
    prefix = "skein-durability-proof"

    def docker(*args, **kwargs):
        calls.append(args)
        return json.dumps([{"Config": {"Labels": {harness.LABEL: prefix}}}])

    monkeypatch.setattr(harness, "docker", docker)
    harness.cleanup({"prefix": prefix, "resources": {"container": [prefix + "-a"]}})
    assert calls == [
        ("container", "inspect", prefix + "-a"),
        ("container", "rm", "-f", "-v", prefix + "-a"),
    ]
