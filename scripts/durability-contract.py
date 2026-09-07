#!/usr/bin/env python3
"""Real two-serving-backend faults. Only uniquely named, labeled test resources.

Run: python3 scripts/durability-contract.py [--keep] [--backend-image TAG]
Evidence is private local JSON/text. --keep retains this run's owned stack.
Cleanup: python3 scripts/durability-contract.py --cleanup /path/to/stack.json
The deterministic provider and upstream live only in the derived test image.
A reused backend image must be trusted. Clearing configuration does not make image code safe.
"""

import argparse
import base64
import concurrent.futures
import contextlib
import hashlib
import hmac
import http.client
import json
import secrets
import socket
import subprocess
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

# This is an executable test, not a production input surface. SQL identifiers
# and values below come from its own fixed fixture and server-generated IDs.
# ruff: noqa: S101, S108, S310, S603, S607, S608

ROOT = Path(__file__).resolve().parent.parent
POSTGRES = (
    "postgres:17-alpine@sha256:d4bb0a8c1b7bb2e29f976d099e7bfb9a5d8858cffe9e46b35cd302cd1f1f8168"
)
LABEL = "skein.durability"
DRILLS = (
    "fresh_concurrent_boot",
    "job_exclusion",
    "shared_session_revocation",
    "oauth_other_pod",
    "cross_pod_artifact_digest",
    "signed_delivery_dedupe",
    "commit_before_ack",
    "before_commit_retry",
    "healthy_worker",
    "paused_stale_worker",
    "sigkill_worker",
    "selective_database_loss",
    "global_database_restart",
    "storage_failure",
    "rolling_replacement",
    "deployment_rate_cap",
)


def clean_environment(inherited, overrides):
    # A reused trusted image can carry deployment credentials and routing. Empty
    # every inherited value before applying the fixture's explicit configuration.
    env = {item.partition("=")[0]: "" for item in inherited}
    env.update(
        {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/tmp",
            "LANG": "C.UTF-8",
            "PYTHONPATH": "/fixtures:/app",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "PYTHON_DOTENV_DISABLED": "1",
            "SKEIN_DATABASE_URL": "",
            "SKEIN_MCP_SERVERS": "",
            "SKEIN_MCP_SERVERS_FILE": "",
            "SKEIN_OTEL_ENDPOINT": "",
            "OTEL_SDK_DISABLED": "true",
            "OTEL_TRACES_EXPORTER": "none",
            "OTEL_METRICS_EXPORTER": "none",
            "OTEL_LOGS_EXPORTER": "none",
            "HTTP_PROXY": "",
            "HTTPS_PROXY": "",
            "ALL_PROXY": "",
            "NO_PROXY": "*",
            "http_proxy": "",
            "https_proxy": "",
            "all_proxy": "",
            "no_proxy": "*",
        }
    )
    env.update(overrides)
    return env


def docker(*args, input=None, timeout=120):
    result = subprocess.run(
        ["docker", *args],
        input=input,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        # Arguments can contain credentials. Only local protected evidence gets
        # command output, never an argv rendered into a public test exception.
        raise RuntimeError(f"docker {args[0]} failed: {result.stderr[-2000:]}")
    return (result.stdout + (result.stderr if args[0] in {"logs", "build"} else "")).strip()


def request(base, path, *, method="GET", payload=None, headers=None, timeout=8):
    headers = dict(headers or {})
    if isinstance(payload, (dict, list)):
        payload = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=payload, headers=headers, method=method)
    try:
        response = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            req, timeout=timeout
        )
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        body = response.read()
        try:
            value = json.loads(body)
        except ValueError:
            value = body
        return response.status, value, response.headers


def wait(check, *, seconds=45, description="condition"):
    deadline = time.monotonic() + seconds
    last = "not ready"
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except (OSError, ValueError, RuntimeError) as exc:
            last = type(exc).__name__
        time.sleep(0.25)
    raise AssertionError(f"Deadline for {description}: {last}")


def private_json(path, value):
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")
    path.chmod(0o600)


def cleanup(stack):
    for kind in ("container", "volume", "network", "image"):
        for name in reversed(stack.get("resources", {}).get(kind, [])):
            try:
                found = json.loads(docker(kind, "inspect", name))[0]
            except RuntimeError:
                continue
            labels = (
                found.get("Config", {}).get("Labels", {})
                if kind in {"container", "image"}
                else found.get("Labels", {})
            )
            if (labels or {}).get(LABEL) != stack["prefix"]:
                raise RuntimeError(f"Refused cleanup of an unowned {kind}.")
            if kind == "container" and found.get("State", {}).get("Paused"):
                docker("unpause", name)
            flags = ["-f", "-v"] if kind == "container" else ["-f"] if kind == "image" else []
            docker(kind, "rm", *flags, name)


class Harness:
    def __init__(self, args):
        self.args = args
        self.lock = threading.RLock()
        self.prefix = "skein-durability-" + secrets.token_hex(6)
        self.evidence = Path(tempfile.mkdtemp(prefix=self.prefix + "-", dir=args.evidence_parent))
        self.evidence.chmod(0o700)
        self.stack = {
            "prefix": self.prefix,
            "evidence": str(self.evidence),
            "resources": {key: [] for key in ("container", "volume", "network", "image")},
        }
        self.results = {name: {"status": "not_run"} for name in DRILLS}
        self.pods = {}
        self.secret = secrets.token_hex(32)
        self.password = secrets.token_hex(24)
        self.app_password = secrets.token_hex(24)
        self.credential_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=8)
        self.save()

    def save(self):
        with self.lock:
            self.stack["pods"] = self.pods
            private_json(self.evidence / "stack.json", self.stack)
            private_json(self.evidence / "results.json", self.results)

    def own(self, kind, name):
        with self.lock:
            self.stack["resources"][kind].append(name)
            self.save()
        return name

    def run_container(self, suffix, image, *args):
        name = self.prefix + "-" + suffix
        docker("run", "-d", "--name", name, "--label", f"{LABEL}={self.prefix}", *args, image)
        return self.own("container", name)

    def port(self, container, port):
        row = json.loads(docker("container", "inspect", container))[0]
        return int(row["NetworkSettings"]["Ports"][f"{port}/tcp"][0]["HostPort"])

    def code(self, code, pod="a"):
        text = docker("exec", "-i", self.pods[pod]["name"], "python", "-", input=code)
        return json.loads(text.splitlines()[-1]) if text else None

    def sql(self, sql, params=()):
        # Read-only observations use the administrative connection in the owned
        # database container. They still work when one application's link is down.
        if not sql.lstrip().upper().startswith("SELECT"):
            raise ValueError("Drill observations must be SELECT statements.")
        text = docker(
            "exec",
            self.db,
            "psql",
            "-U",
            "skein_bootstrap",
            "-d",
            "skein",
            "-qtAX",
            "-c",
            "SELECT coalesce(json_agg(probe), '[]'::json) FROM (" + sql + ") probe",
        )
        assert not params
        return json.loads(text)

    def control(self, **changes):
        assert request(self.fixture, "/control", method="POST", payload=changes)[0] == 200

    def events(self, kind):
        status, state, _ = request(self.fixture, "/state")
        assert status == 200
        return [event["value"] for event in state["events"] if event["kind"] == kind]

    def api(self, pod, path, *, method="GET", payload=None, headers=None, expected=200):
        base = self.fixture if pod == "proxy" else self.pods[pod]["url"]
        response = request(
            base,
            path,
            method=method,
            payload=payload,
            headers={"Authorization": "Bearer " + self.key, **(headers or {})},
        )
        assert response[0] == expected, (path, response[0], response[1])
        return response

    def ready(self, pod):
        return request(self.pods[pod]["url"], "/ready", timeout=3)[0] == 200

    def start_pod(self, name, link="a"):
        env = {
            "PYTHON_DOTENV_DISABLED": "1",
            "SKEIN_DATA_DIR": "/data",
            "SKEIN_BACKUP_MIRROR": "/mirror",
            "SKEIN_DB_HOST": "fixture",
            "SKEIN_DB_PORT": "15432" if link == "a" else "15433",
            "SKEIN_DB_USER": "skein_app",
            "SKEIN_DB_PASSWORD": self.app_password,
            "SKEIN_DB_NAME": "skein",
            "SKEIN_AUTH_MODE": "api-key",
            "SKEIN_ADMINS": "durability-admin",
            "SKEIN_MODEL_PROVIDER": "mock",
            "SKEIN_SCHEDULER": "1",
            "SKEIN_AGENT_RUN_SECONDS": "300",
            "SKEIN_CREDENTIAL_KEY": self.credential_key,
            "SKEIN_FORGE_WEBHOOK_SECRET": self.secret,
            "SKEIN_CORS_ORIGINS": "http://localhost:4311",
            "SKEIN_EMBEDDINGS": "0",
            "DURABILITY_UPSTREAM": "http://fixture:8080",
            "DURABILITY_POD": name,
        }
        env = clean_environment(self.image_environment, env)
        flags = [item for key, val in env.items() for item in ("-e", key + "=" + val)]
        # Docker reallocates an implicit host port on restart. An explicit free
        # port keeps the direct HTTP endpoint valid across the SIGKILL drill.
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
        container = self.run_container(
            name,
            self.image,
            "--network",
            self.network,
            "--user",
            "1000710000:0",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--mount",
            f"type=volume,source={self.data},target=/data",
            "--mount",
            f"type=volume,source={self.mirror},target=/mirror",
            "-p",
            f"127.0.0.1:{port}:8000",
            *flags,
        )
        published_port = self.port(container, 8000)
        with self.lock:
            self.pods[name] = {
                "name": container,
                "url": f"http://localhost:{published_port}",
                "link": link,
            }
            self.save()

    def setup(self):
        backend = self.args.backend_image or self.prefix + "-backend:local"
        if not self.args.backend_image:
            log = docker(
                "build",
                "--label",
                f"{LABEL}={self.prefix}",
                "-t",
                backend,
                str(ROOT / "backend"),
                timeout=600,
            )
            (self.evidence / "build-backend.txt").write_text(log)
            self.own("image", backend)
        self.image = self.prefix + "-fixture:local"
        log = docker(
            "build",
            "--label",
            f"{LABEL}={self.prefix}",
            "--build-arg",
            "BACKEND_IMAGE=" + backend,
            "-f",
            str(ROOT / "scripts/fixtures/Durability.Dockerfile"),
            "-t",
            self.image,
            str(ROOT),
            timeout=600,
        )
        (self.evidence / "build-fixture.txt").write_text(log)
        self.own("image", self.image)
        self.stack["backend_image"] = backend
        self.stack["backend_image_id"] = docker("image", "inspect", "--format", "{{.Id}}", backend)
        self.stack["fixture_image_id"] = docker(
            "image", "inspect", "--format", "{{.Id}}", self.image
        )
        self.image_environment = json.loads(
            docker("image", "inspect", "--format", "{{json .Config.Env}}", self.image)
        )
        self.network = self.prefix + "-network"
        docker("network", "create", "--label", f"{LABEL}={self.prefix}", self.network)
        self.own("network", self.network)
        for suffix in ("data", "mirror", "postgres"):
            volume = self.prefix + "-" + suffix
            docker("volume", "create", "--label", f"{LABEL}={self.prefix}", volume)
            self.own("volume", volume)
            setattr(self, suffix, volume)
        self.db = self.run_container(
            "db",
            POSTGRES,
            "--network",
            self.network,
            "--mount",
            f"type=volume,source={self.postgres},target=/var/lib/postgresql/data",
            "-e",
            "POSTGRES_USER=skein_bootstrap",
            "-e",
            "POSTGRES_PASSWORD=" + self.password,
            "-e",
            "POSTGRES_DB=skein",
        )
        wait(
            lambda: (
                docker("exec", self.db, "pg_isready", "-U", "skein_bootstrap", "-d", "skein")
                and self.sql("SELECT 1")
            ),
            description="PostgreSQL startup",
        )
        # pg_isready can see initdb's temporary server. TCP excludes that server.
        wait(
            lambda: docker(
                "exec", self.db, "pg_isready", "-h", "127.0.0.1", "-U", "skein_bootstrap"
            ),
            description="PostgreSQL TCP startup",
        )
        docker(
            "cp", str(ROOT / "deploy/postgres-init/10-app-role.sh"), self.db + ":/tmp/app-role.sh"
        )
        docker(
            "exec",
            "-e",
            "SKEIN_APP_USER=skein_app",
            "-e",
            "SKEIN_APP_PASSWORD=" + self.app_password,
            self.db,
            "bash",
            "/tmp/app-role.sh",
        )
        assert self.sql("SELECT rolsuper, rolcreatedb FROM pg_roles WHERE rolname='skein_app'") == [
            {"rolsuper": False, "rolcreatedb": False}
        ]
        fixture_name = self.prefix + "-fixture"
        fixture_env = clean_environment(
            self.image_environment, {"DURABILITY_DB": self.db, "SKEIN_DATA_DIR": "/tmp/data"}
        )
        fixture_flags = [
            item for key, val in fixture_env.items() for item in ("-e", key + "=" + val)
        ]
        docker(
            "run",
            "-d",
            "--name",
            fixture_name,
            "--label",
            f"{LABEL}={self.prefix}",
            "--network",
            self.network,
            "--network-alias",
            "fixture",
            "--no-healthcheck",
            "--user",
            "1000710000:0",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=32m",
            # The inherited VOLUME /data otherwise allocates an anonymous volume.
            "--tmpfs",
            "/data:rw,noexec,nosuid,size=1m",
            "-p",
            "127.0.0.1::8080",
            *fixture_flags,
            self.image,
            "python",
            "/fixtures/durability_upstream.py",
        )
        self.own("container", fixture_name)
        self.fixture = f"http://localhost:{self.port(fixture_name, 8080)}"
        wait(lambda: request(self.fixture, "/state")[0] == 200, description="fixture startup")
        self.stack["proxy_url"] = self.fixture
        assert self.sql("SELECT to_regclass('public.schema_version') AS schema") == [
            {"schema": None}
        ]
        boots = [self.pool.submit(self.start_pod, name, name) for name in ("a", "b")]
        for boot in boots:
            boot.result(timeout=120)
        for name in ("a", "b"):
            wait(
                lambda name=name: self.ready(name),
                seconds=120,
                description=f"concurrent boot {name}",
            )
        self.control(routes=[self.pods[name]["name"] for name in ("a", "b")])
        assert self.sql("SELECT COUNT(*) AS n FROM schema_version")[0]["n"] > 20
        for name in ("a", "b"):
            assert docker("exec", self.pods[name]["name"], "id", "-u") == "1000710000"
            selected = self.code(
                "import json,os\nfrom app import config\nfrom psycopg.conninfo import conninfo_to_dict\np=conninfo_to_dict(config.DATABASE_URL)\nprint(json.dumps({'host':p['host'],'port':p['port'],'dbname':p['dbname'],'user':p['user'],'clean':os.getenv('SKEIN_DATABASE_URL') == '' and not any(os.getenv(k) for k in ('SKEIN_MODEL_API_KEY','SKEIN_MODEL_BASE_URL','SKEIN_MCP_SERVERS','SLACK_WEBHOOK_URL','OTEL_EXPORTER_OTLP_ENDPOINT','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY')),'otel_disabled':os.getenv('OTEL_SDK_DISABLED')}))",
                name,
            )
            assert selected == {
                "host": "fixture",
                "port": "15432" if name == "a" else "15433",
                "dbname": "skein",
                "user": "skein_app",
                "clean": True,
                "otel_disabled": "true",
            }, selected
        self.key = self.code(
            "import json\nfrom app.services import users, api_keys\nusers.ensure_user('durability-admin')\nprint(json.dumps(api_keys.create_key('durability-admin', 'isolated harness')['key']))"
        )
        private_json(
            self.evidence / "credentials.json",
            {
                "api_key": self.key,
                "cors_origin": "http://localhost:4311",
                "proxy_url": self.fixture,
                "pods": self.pods,
            },
        )
        self.results["fresh_concurrent_boot"] = {
            "status": "pass",
            "non_superuser": True,
            "arbitrary_uid": 1000710000,
            "read_only_root": True,
            "hostile_image_env_cleared": True,
        }
        self.save()

    def task(self, title, pod="a"):
        return self.api(pod, "/api/tasks", method="POST", payload={"title": title})[1]["id"]

    def upload(self, pod, filename="proof.txt"):
        content = b"shared artifact bytes\n" + bytes(range(128))
        boundary = "durability-" + secrets.token_hex(12)
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: text/plain\r\n\r\n'.encode()
            + content
            + f"\r\n--{boundary}--\r\n".encode()
        )
        response = request(
            self.pods[pod]["url"],
            "/api/files",
            method="POST",
            payload=body,
            headers={
                "Authorization": "Bearer " + self.key,
                "Content-Type": "multipart/form-data; boundary=" + boundary,
            },
        )
        return response, content

    def delivery(self, task, suffix="", delivery=None):
        body = json.dumps(
            {
                "ref": f"refs/heads/task/{task}-{suffix or 'proof'}",
                "deleted": False,
                "repository": {
                    "full_name": "durability/proof",
                    "html_url": "https://gitea.example/durability/proof",
                },
                "sender": {"login": "durability-admin"},
                "pusher": {"login": "durability-admin"},
            }
        ).encode()
        delivery = delivery or str(uuid4())
        signature = hmac.new(self.secret.encode(), body, hashlib.sha256).hexdigest()
        headers = {
            "X-Gitea-Event": "push",
            "X-Gitea-Delivery": delivery,
            "X-Gitea-Signature": signature,
            # Gitea also emits these matching compatibility aliases.
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": "sha256=" + signature,
            "Content-Type": "application/json",
        }
        return body, headers

    def send_delivery(self, pod, packet):
        base = self.fixture if pod == "proxy" else self.pods[pod]["url"]
        return request(
            base,
            "/api/webhooks/forge",
            method="POST",
            payload=packet[0],
            headers=packet[1],
            timeout=20,
        )

    def assert_delivery(self, task, packet):
        assert self.api("b", f"/api/tasks/{task}")[1]["status"] == "in_progress"
        rows = self.sql(
            f"SELECT action, detail FROM activity WHERE detail LIKE '#{task} %' AND action='update_task' AND actor='forge'"
        )
        assert len(rows) == 1, rows
        receipts = self.sql(
            f"SELECT provider, delivery_id, payload_sha256 FROM forge_receipts WHERE task_id={task}"
        )
        assert receipts == [
            {
                "provider": "gitea",
                "delivery_id": packet[1]["X-Gitea-Delivery"],
                "payload_sha256": hashlib.sha256(packet[0]).hexdigest(),
            }
        ], receipts

    def worker(self, name, pod="a"):
        task = self.task("durability worker " + name, pod)
        self.api(
            pod,
            f"/api/tasks/{task}/delegate",
            method="POST",
            payload={
                "agent": "durability-" + name,
                "acceptance_criteria": "Record one bounded progress result.",
            },
        )
        gate = wait(
            lambda: next((row for row in self.events("gate") if row["task"] == str(task)), None),
            seconds=45,
            description="worker reaches real upstream",
        )
        assert len(gate["receipts"]) == 2 and all(
            row["kind"] == "wrote" for row in gate["receipts"]
        )
        row = self.sql(f"SELECT * FROM agent_wakeups WHERE trigger_task_id={task}")[0]
        assert row["status"] == "running" and gate["pod"] == pod, (row, gate)
        return task, gate["thread"], pod

    def release(self, task):
        state = request(self.fixture, "/state")[1]
        self.control(released=[*state["released"], str(task)])

    def unknown(self, task):
        row = self.sql(f"SELECT status, reason FROM agent_wakeups WHERE trigger_task_id={task}")[0]
        return row if row["status"] == "completion_unknown" else None

    def run_drill(self, name, fn):
        started = time.monotonic()
        try:
            evidence = fn() or {}
            self.results[name] = {
                "status": "pass",
                "seconds": round(time.monotonic() - started, 3),
                **evidence,
            }
        except Exception as exc:
            (self.evidence / (name + "-failure.txt")).write_text(traceback.format_exc())
            self.results[name] = {
                "status": "fail",
                "seconds": round(time.monotonic() - started, 3),
                "error": str(exc),
                "exception": type(exc).__name__,
            }
        self.save()
        print(json.dumps({"drill": name, **self.results[name]}), flush=True)

    def job_exclusion(self):
        wait(lambda: self.events("effect/job"), seconds=20, description="scheduled job effect")
        count = len(self.events("effect/job"))
        assert count == 1
        # Observe more than two configured firing intervals, not just boot's
        # first winner. Both actual APScheduler instances are serving throughout.
        time.sleep(11)
        assert len(self.events("effect/job")) == 1
        rows = self.sql("SELECT job, status FROM job_outcomes")
        assert len(rows) == 1 and rows[0]["status"] == "ok", rows
        return {"upstream_effects": 1, "outcomes": rows}

    def shared_session_revocation(self):
        base = self.pods["a"]["url"]
        status, meta, headers = request(
            base,
            "/api/auth/session/key",
            method="POST",
            payload={"key": self.key},
            headers={"Origin": base},
        )
        assert status == 200, meta
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        binding = {
            "Cookie": cookie,
            "Origin": self.pods["b"]["url"],
            "X-Skein-CSRF": meta["csrf_token"],
        }
        assert (
            request(self.pods["b"]["url"], "/api/auth/session", headers=binding)[1]["authenticated"]
            is True
        )
        assert request(self.pods["b"]["url"], "/api/tasks", headers=binding)[0] == 200
        assert (
            request(self.pods["b"]["url"], "/api/auth/session", method="DELETE", headers=binding)[0]
            == 204
        )
        assert (
            request(
                base, "/api/tasks", headers={"Cookie": cookie, "X-Skein-CSRF": meta["csrf_token"]}
            )[0]
            == 401
        )
        assert self.sql("SELECT COUNT(*) AS n FROM browser_sessions")[0]["n"] == 0
        return {"created_on": "a", "used_and_revoked_on": "b"}

    def oauth_other_pod(self):
        row = self.api(
            "a",
            "/api/mcp/servers",
            method="POST",
            payload={"name": "durability-oauth", "url": "http://fixture:8080/mcp", "auth": "oauth"},
        )[1]
        url = self.api("a", f"/api/mcp/servers/{row['id']}/sign-in", method="POST")[1][
            "authorization_url"
        ]
        state = parse_qs(urlsplit(url).query)["state"][0]
        assert (
            request(
                self.pods["b"]["url"],
                "/api/mcp/oauth/callback?state=" + state + "&code=durability-code",
            )[0]
            == 200
        )
        wait(
            lambda: self.api("b", "/api/mcp/servers")[1]["personal"][0]["signed_in"],
            description="OAuth token sealed by initiating pod",
        )
        assert len(self.events("oauth_token")) == 1
        wait(
            lambda: any(row["method"] == "tools/list" for row in self.events("mcp")),
            description="authenticated MCP discovery after token storage",
        )
        assert (
            request(
                self.pods["a"]["url"],
                "/api/mcp/oauth/callback?state=" + state + "&code=durability-code",
            )[0]
            == 404
        )
        return {"token_exchanges": 1, "real_mcp_tools_list": True}

    def cross_pod_artifact_digest(self):
        response, content = self.upload("a")
        assert response[0] == 200, response
        aid = response[1]["id"]
        downloaded = self.api("b", f"/api/files/{aid}/download")[1]
        assert downloaded == content
        digest = hashlib.sha256(content).hexdigest()
        assert (
            self.sql(f"SELECT content_sha256 FROM artifacts WHERE id={aid}")[0]["content_sha256"]
            == digest
        )
        self.artifact = aid
        return {"artifact_id": aid, "sha256": digest, "bytes": len(content)}

    def signed_delivery_dedupe(self):
        task = self.task("simultaneous signed delivery")
        packet = self.delivery(task)
        attempts = [self.pool.submit(self.send_delivery, pod, packet) for pod in ("a", "b")]
        responses = [future.result(timeout=25) for future in attempts]
        assert all(response[0] == 200 for response in responses), responses
        self.assert_delivery(task, packet)
        return {"responses": [response[1] for response in responses], "task_id": task}

    def commit_before_ack(self):
        task = self.task("committed before lost acknowledgement")
        packet = self.delivery(task)
        self.control(drop_ack=True)
        try:
            self.send_delivery("proxy", packet)
        except (OSError, http.client.HTTPException):
            pass
        else:
            raise AssertionError("The proxy did not drop the actual HTTP acknowledgement.")
        assert self.events("dropped_ack")[-1]["status"] == 200
        assert self.send_delivery("b", packet)[0] == 200
        self.assert_delivery(task, packet)
        return {"task_id": task, "lost_acknowledgements": 1}

    def before_commit_retry(self):
        task = self.task("before commit transaction crash")
        packet = self.delivery(task, "before-commit")
        self.control(before_commit=True)
        pending = self.pool.submit(self.send_delivery, "a", packet)
        try:
            wait(
                lambda: self.events("before_commit"),
                seconds=15,
                description="real transaction reaches precommit gate",
            )
            docker("kill", "--signal", "KILL", self.pods["a"]["name"])
            assert self.api("b", f"/api/tasks/{task}")[1]["status"] == "todo"
        finally:
            self.control(before_commit=False)
            docker("start", self.pods["a"]["name"])
            wait(lambda: self.ready("a"), seconds=90, description="killed pod restart")
            with contextlib.suppress(OSError, http.client.HTTPException):
                pending.result(timeout=25)
        assert self.send_delivery("b", packet)[0] == 200
        self.assert_delivery(task, packet)
        return {"task_id": task, "uncommitted_write_rolled_back": True}

    def healthy_worker(self):
        task, thread, _ = self.worker("healthy")
        self.release(task)
        wait(
            lambda: (
                self.sql(f"SELECT status FROM agent_wakeups WHERE trigger_task_id={task}")[0][
                    "status"
                ]
                == "completed"
            ),
            description="healthy wake completion",
        )
        assert (
            self.sql(f"SELECT COUNT(*) AS n FROM session_messages WHERE session_id='{thread}'")[0][
                "n"
            ]
            == 4
        )
        observed = self.events(f"observed/{task}")[0]
        assert len(observed["receipts"]) == 1 and observed["receipts"][0]["kind"] == "wrote", (
            observed
        )
        return {"task_id": task, "session_messages": 4, "tool_receipts": 3}

    def paused_stale_worker(self):
        task, thread, _ = self.worker("paused")
        started = time.monotonic()
        docker("pause", self.pods["a"]["name"])
        try:
            unknown = wait(
                lambda: self.unknown(task),
                seconds=135,
                description="real 90-second lease recovery by other serving pod",
            )
            elapsed = time.monotonic() - started
            time.sleep(max(0, 91 - elapsed))
            elapsed = time.monotonic() - started
            assert elapsed >= 90, elapsed
            assert unknown["reason"] == "lease_expired"
        finally:
            docker("unpause", self.pods["a"]["name"])
        self.release(task)
        observed = wait(
            lambda: self.events(f"observed/{task}"), seconds=40, description="stale worker resumes"
        )[0]
        assert observed["results"] == {"tool": "LeaseLost", "session": "LeaseLost"}, observed
        assert observed["receipts"] == []
        assert (
            self.sql(f"SELECT COUNT(*) AS n FROM session_messages WHERE session_id='{thread}'")[0][
                "n"
            ]
            == 2
        )
        assert self.unknown(task)
        return {
            "task_id": task,
            "real_elapsed_seconds": round(elapsed, 2),
            "stale_writes_refused": ["tool", "session"],
            "outcome": unknown,
        }

    def sigkill_worker(self):
        task, thread, _ = self.worker("killed")
        started = time.monotonic()
        docker("kill", "--signal", "KILL", self.pods["a"]["name"])
        try:
            unknown = wait(
                lambda: self.unknown(task),
                seconds=135,
                description="SIGKILL worker's real lease expiry",
            )
            assert unknown["reason"] == "lease_expired"
            assert len([row for row in self.events("gate") if row["task"] == str(task)]) == 1
            assert (
                self.sql(f"SELECT COUNT(*) AS n FROM session_messages WHERE session_id='{thread}'")[
                    0
                ]["n"]
                == 2
            )
        finally:
            docker("start", self.pods["a"]["name"])
            wait(lambda: self.ready("a"), seconds=90, description="restart after SIGKILL")
            self.release(task)
        assert self.unknown(task)
        assert not self.events(f"observed/{task}")
        return {
            "task_id": task,
            "real_elapsed_seconds": round(time.monotonic() - started, 2),
            "upstream_invocations": 1,
            "outcome": unknown,
        }

    def selective_database_loss(self):
        worker_task, thread, _ = self.worker("partitioned")
        self.control(db_a=False)
        try:
            started = time.monotonic()
            wait(
                lambda: request(self.pods["a"]["url"], "/ready", timeout=3)[0] == 503,
                seconds=5,
                description="selective DB readiness failure",
            )
            assert time.monotonic() - started < 5
            assert request(self.pods["a"]["url"], "/health")[0] == 200
            assert self.ready("b")
            task = self.task("healthy pod during peer DB loss", "b")
            unknown = wait(
                lambda: self.unknown(worker_task),
                seconds=135,
                description="partitioned worker's real lease recovery",
            )
            assert request(self.pods["a"]["url"], "/health")[0] == 200
        finally:
            self.control(db_a=True)
            # The real pool backs off connection retries during an outage.
            # Each probe stays bounded, but recovery can wait for that backoff.
            wait(lambda: self.ready("a"), seconds=75, description="selective database recovery")
            self.release(worker_task)
        assert self.api("a", f"/api/tasks/{task}")[1]["title"] == "healthy pod during peer DB loss"
        observed = wait(
            lambda: self.events(f"observed/{worker_task}"),
            seconds=40,
            description="partitioned worker resumes",
        )[0]
        assert observed["results"] == {"tool": "LeaseLost", "session": "LeaseLost"}, observed
        assert observed["receipts"] == []
        assert (
            self.sql(f"SELECT COUNT(*) AS n FROM session_messages WHERE session_id='{thread}'")[0][
                "n"
            ]
            == 2
        )
        return {
            "unready": "a",
            "healthy_write_pod": "b",
            "task_id": task,
            "worker_task_id": worker_task,
            "outcome": unknown,
            "stale_writes_refused": ["tool", "session"],
        }

    def global_database_restart(self):
        task = self.task("database restart preserves committed work")
        docker("stop", "--time", "5", self.db)
        try:
            for pod in ("a", "b"):
                response = request(self.pods[pod]["url"], "/ready", timeout=3)
                assert response[0] == 503 and response[1] == {
                    "ok": False,
                    "auth_mode": "api-key",
                    "auth_error": "",
                }
                assert request(self.pods[pod]["url"], "/health")[0] == 200
        finally:
            docker("start", self.db)
            for pod in ("a", "b"):
                wait(
                    lambda pod=pod: self.ready(pod),
                    seconds=45,
                    description="common DB restart recovery",
                )
        retries = []

        def recovered(pod):
            status, body, headers = request(
                self.pods[pod]["url"],
                f"/api/tasks/{task}",
                headers={"Authorization": "Bearer " + self.key},
            )
            if status == 503:
                assert body == {
                    "detail": "The database is unavailable. Check the operation's outcome before trying again."
                }, body
                assert headers["Retry-After"] == "5"
                retries.append({"pod": pod, "status": status, "retry_after": 5})
                time.sleep(5)
                return False
            assert status == 200, (status, body)
            assert body["title"] == "database restart preserves committed work"
            return True

        for pod in ("a", "b"):
            wait(
                lambda pod=pod: recovered(pod), seconds=75, description="application pool recovers"
            )
        return {
            "task_id": task,
            "both_liveness_probes_up": True,
            "explicit_retryable_responses": retries,
        }

    def storage_failure(self):
        baseline, _ = self.upload("b", "storage-baseline.txt")
        assert baseline[0] == 200, baseline
        before = self.sql("SELECT COUNT(*) AS n FROM artifacts")[0]["n"]
        observed_before = len(self.events("observed/storage"))
        docker(
            "exec",
            "--user",
            "1000710000:0",
            self.pods["a"]["name"],
            "chmod",
            "0550",
            "/data/artifacts/uploads",
        )
        try:
            response, _ = self.upload("b", "storage-failed.txt")
            assert response[0] == 500, response
            assert response[1] == {
                "detail": "Skein cannot access its storage. Ask whoever runs the server to check storage permissions."
            }
            faults = self.events("observed/storage")[observed_before:]
            assert faults == [{"errno": 13, "directory": "/data/artifacts/uploads", "pod": "b"}], (
                faults
            )
            assert self.sql("SELECT COUNT(*) AS n FROM artifacts")[0]["n"] == before
        finally:
            docker(
                "exec",
                "--user",
                "1000710000:0",
                self.pods["a"]["name"],
                "chmod",
                "0770",
                "/data/artifacts/uploads",
            )
        assert self.upload("b", "storage-restored.txt")[0][0] == 200
        return {
            "failed_publication_rolled_back": True,
            "actual_publication_errno": 13,
            "healthy_baseline_artifact_id": baseline[1]["id"],
        }

    def rolling_replacement(self):
        self.start_pod("c", "b")
        wait(lambda: self.ready("c"), seconds=90, description="overlapping replacement readiness")
        self.control(routes=[self.pods[name]["name"] for name in ("a", "b", "c")])
        ids = [self.task("overlap write " + str(i), "proxy") for i in range(3)]
        self.control(routes=[self.pods[name]["name"] for name in ("b", "c")])
        docker("stop", "--time", "15", self.pods["a"]["name"])
        try:
            for task in ids:
                assert self.api("proxy", f"/api/tasks/{task}")[1]["id"] == task
            assert self.api("c", f"/api/files/{self.artifact}/download")[0] == 200
            assert len(self.events("effect/job")) == 1
        finally:
            docker("start", self.pods["a"]["name"])
            wait(
                lambda: self.ready("a"), seconds=90, description="restored direct browser endpoint"
            )
        return {
            "overlapping_backends": 3,
            "new_pod_reads_existing_artifact": True,
            "committed_task_ids": ids,
        }

    def deployment_rate_cap(self):
        # A fresh address through the fixture proxy isolates this fixed window
        # from earlier direct sign-ins without deleting or editing rate rows.
        remaining = 60 - time.time() % 60
        if remaining < 5:
            time.sleep(remaining + 0.1)
        responses = []
        for _ in range(11):
            response = request(
                self.fixture,
                "/api/auth/session/key",
                method="POST",
                payload={"key": "invalid-contract-key"},
                headers={"Origin": "http://localhost:4311"},
            )
            responses.append(response)
        assert [response[0] for response in responses] == [401] * 10 + [429], [
            response[0] for response in responses
        ]
        assert int(responses[-1][2]["Retry-After"]) > 0
        assert self.sql("SELECT COUNT(*) AS n FROM rate_hits WHERE surface='signin'")[0]["n"] >= 1
        return {
            "alternating_pod_requests": 11,
            "refused_request": 11,
            "retry_after": responses[-1][2]["Retry-After"],
        }

    def finish(self):
        for container in self.stack["resources"]["container"]:
            with contextlib.suppress(RuntimeError):
                (self.evidence / (container + ".log")).write_text(docker("logs", container))
        self.save()
        self.pool.shutdown(wait=False, cancel_futures=True)


def main():
    if not __debug__:
        raise SystemExit("Run the Docker contract without -O so its assertions execute.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true")
    parser.add_argument(
        "--backend-image",
        help="Reuse a trusted real backend image without owning or removing it",
    )
    parser.add_argument("--evidence-parent", default="/tmp")
    parser.add_argument("--cleanup", type=Path)
    args = parser.parse_args()
    if args.cleanup:
        cleanup(json.loads(args.cleanup.read_text()))
        return 0
    harness = Harness(args)
    print("Private evidence: " + str(harness.evidence), flush=True)
    try:
        harness.setup()
        for name in DRILLS[1:]:
            harness.run_drill(name, getattr(harness, name))
    except Exception as exc:
        harness.results["fresh_concurrent_boot"] = {
            "status": "fail",
            "exception": type(exc).__name__,
            "error": str(exc),
        }
        print("Setup failed. Read the private evidence.", flush=True)
    finally:
        harness.finish()
        if not args.keep:
            cleanup(harness.stack)
    return 0 if all(result["status"] == "pass" for result in harness.results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
