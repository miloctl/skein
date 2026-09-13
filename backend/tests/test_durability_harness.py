"""The Docker driver's cleanup cannot cross its run's ownership label."""

import hashlib
import hmac
import importlib.util
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from conftest import authored_repo_root

BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = authored_repo_root(Path(__file__)) / "scripts/durability-contract.py"
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
        "SKEIN_MODEL_API_KEY=fixture-only-key",
        "SKEIN_MODEL_BASE_URL=https://unused.invalid",
        'SKEIN_MCP_SERVERS=[{"url":"https://unused.invalid"}]',
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
    assert all(
        env[key] == ""
        for key in (
            "SKEIN_MODEL_API_KEY",
            "SKEIN_MODEL_BASE_URL",
            "SKEIN_MCP_SERVERS",
            "AWS_SECRET_ACCESS_KEY",
            "HTTPS_PROXY",
        )
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json,socket; socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('No network in configuration check')); from app import config; from psycopg.conninfo import conninfo_to_dict; p=conninfo_to_dict(config.DATABASE_URL); print(json.dumps({**{k:p[k] for k in ('host','port','user','dbname')}, 'source':config.__file__}))",
        ],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "source": str(BACKEND / "app/config.py"),
        "host": "owned-db",
        "port": "5432",
        "user": "owned-role",
        "dbname": "owned-database",
    }


def test_fixture_image_replaces_runtime_and_stock():
    dockerfile = (SCRIPT.parent / "fixtures/Durability.Dockerfile").read_text()
    assert dockerfile.index("RUN rm -rf /app/app /app/skein_stock") < dockerfile.index(
        "COPY backend/app /app/app"
    )
    for directory in ("playbooks", "personas", "flocks", "fieldguide", "schemas"):
        assert f"COPY backend/{directory} /app/skein_stock/{directory}" in dockerfile


@pytest.mark.parametrize("compatibility_aliases", [False, True])
def test_gitea_drill_packet_commits_one_generic_receipt(
    client, fresh_db, monkeypatch, compatibility_aliases
):
    from app import config

    driver = harness.Harness.__new__(harness.Harness)
    driver.secret = "isolated-gitea-signature"
    monkeypatch.setattr(config, "FORGE_WEBHOOK_SECRET", driver.secret)
    created = client.post("/api/tasks", json={"title": "Signed Gitea durability"})
    assert created.status_code == 200
    task = created.json()["id"]
    body, headers = driver.delivery(task)
    payload = json.loads(body)
    assert payload["repository"]["html_url"] == "https://gitea.example/durability/proof"
    assert payload["pusher"]["login"] == "durability-admin"
    assert "X-GitHub-Hook-ID" not in headers
    assert (
        headers["X-Gitea-Signature"]
        == hmac.new(driver.secret.encode(), body, hashlib.sha256).hexdigest()
    )
    if not compatibility_aliases:
        headers = {
            key: value
            for key, value in headers.items()
            if not key.startswith(("X-GitHub-", "X-Hub-"))
        }
    for _ in range(2):
        response = client.post("/api/webhooks/forge", content=body, headers=headers)
        assert response.status_code == 200, response.text
    assert fresh_db.query_row("SELECT status, forge_url FROM tasks WHERE id = ?", (task,)) == {
        "status": "in_progress",
        "forge_url": f"https://gitea.example/durability/proof/src/branch/task/{task}-proof",
    }
    assert fresh_db.query("SELECT provider, delivery_id, payload_sha256 FROM forge_receipts") == [
        {
            "provider": "gitea",
            "delivery_id": headers["X-Gitea-Delivery"],
            "payload_sha256": hashlib.sha256(body).hexdigest(),
        }
    ]
    assert (
        fresh_db.query_row(
            "SELECT COUNT(*) AS n FROM activity WHERE actor = 'forge' AND action = 'update_task'"
        )["n"]
        == 1
    )


def test_signed_delivery_drill_preserves_human_edit_on_native_redelivery(
    client, fresh_db, monkeypatch
):
    from app import config

    driver = harness.Harness.__new__(harness.Harness)
    driver.secret = "isolated-gitea-signature"
    monkeypatch.setattr(config, "FORGE_WEBHOOK_SECRET", driver.secret)

    def api(pod, path, *, method="GET", payload=None):
        response = client.request(method, path, json=payload)
        assert response.status_code == 200, response.text
        return response.status_code, response.json(), response.headers

    def send_delivery(pod, packet):
        response = client.post("/api/webhooks/forge", content=packet[0], headers=packet[1])
        return response.status_code, response.json(), response.headers

    monkeypatch.setattr(driver, "api", api)
    monkeypatch.setattr(driver, "send_delivery", send_delivery)
    monkeypatch.setattr(driver, "sql", fresh_db.query)
    with ThreadPoolExecutor(max_workers=2) as driver.pool:
        result = driver.signed_delivery_dedupe()
    assert result["replay_receipts"] == 2
    assert result["forge_updates_after_replay"] == 1
    assert result["forge_updates_after_changed_push"] == 2
    assert result["human_edit_preserved"] is True
    assert sum("status" in response for response in result["responses"]) == 1
    assert sum("ignored" in response for response in result["responses"]) == 1
    assert len(harness.DRILLS) == 16


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
