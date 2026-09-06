"""Execute the operator's pre-boot fence against disposable recovery state."""

import re
import shlex
import shutil
import subprocess
import textwrap
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from psycopg.conninfo import conninfo_to_dict

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "deploy/k8s/README.md"
OPERATOR = ROOT / "deploy/k8s/OPERATOR.md"


def _tool(name):
    path = shutil.which(name)
    assert path, f"{name} is required for the recovery drill"
    return path


def _fence_script():
    restore = README.read_text().split("**Restore.**", 1)[1]
    step = restore.split("\n7. ", 1)[1].split("\n8. ", 1)[0]
    return textwrap.dedent(re.search(r"```[^\n]*\n(.*?)```", step, re.S)[1]).strip()


def _apply_fence():
    from app import config

    script = _fence_script()
    if "<<'SQL'" in script:
        header, body = script.split("<<'SQL'\n", 1)
        argv = shlex.split(header)
        statement = body.rsplit("\nSQL", 1)[0]
    else:
        argv, statement = shlex.split(script), None
    # The local psql shim drops PGDATABASE. An explicit fixture conninfo keeps
    # these exact runbook flags and SQL away from the shim's default database.
    assert conninfo_to_dict(config.DATABASE_URL)["dbname"].startswith(
        ("skein_test_", "skein_scratch_")
    )
    result = subprocess.run(  # noqa: S603
        [_tool("psql"), "--dbname", config.DATABASE_URL, *argv[1:]],
        input=statement,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _oidc_session(monkeypatch):
    from app import config, oidc
    from app.services import browser_sessions

    monkeypatch.setattr(config, "CREDENTIAL_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(config, "OIDC_ISSUER", "https://idp.example.test")
    monkeypatch.setattr(config, "OIDC_CLIENT_ID", "skein-browser")
    monkeypatch.setattr(config, "OIDC_AUDIENCE", "skein-api")
    claims = {
        "iss": config.OIDC_ISSUER,
        "sub": "restore-owner",
        "aud": config.OIDC_AUDIENCE,
        "azp": config.OIDC_CLIENT_ID,
        "exp": datetime.now(UTC).timestamp() + 600,
        "preferred_username": "restore-owner",
        "groups": [],
    }
    monkeypatch.setattr(oidc, "validate", lambda _token: dict(claims))
    monkeypatch.setattr(oidc, "exchange", lambda _form: pytest.fail("unexpected OIDC refresh"))
    return browser_sessions.create_oidc_session(
        {"access_token": "test-access", "refresh_token": "test-refresh"}, claims, mode="oidc"
    )


def _queued_work(db, monkeypatch):
    from app.services import (
        agent_wakeups,
        chat_threads,
        delegation,
        personas,
        shared_chat_agents,
        users,
        work,
    )

    users.ensure_human_identity("restore-owner")
    agent = sorted(personas.bench_slugs())[0]
    room = chat_threads.create_shared_chat("Restore rehearsal", "restore-owner")
    chat_threads.add_shared_chat_agent(room["id"], "restore-owner", agent, share_history=True)
    # Capture committed queue states before workers start, as pg_dump can.
    with monkeypatch.context() as paused:
        paused.setattr(shared_chat_agents, "kick", lambda: False)
        for key in ("running", "pending"):
            chat_threads.post_shared_message(
                room["id"], "restore-owner", f"@{agent} inspect this", key, invoke_agent=agent
            )
    assert shared_chat_agents.claim_next()["status"] == "running"
    for index in range(2):
        task = work.create_task(title=f"Delegation {index}", actor="restore-owner")
        delegation.delegate_task(task["id"], agent, "restore-owner", actor="restore-owner")
        if index == 0:
            assert agent_wakeups.claim_next()["status"] == "running"
    assert db.query_row("SELECT rerun_requested FROM agent_wakeups")["rerun_requested"] == 1
    other = sorted(personas.bench_slugs())[1]
    users.ensure_agent_identity(other)
    task = work.create_task(title="Pending delegation", actor="restore-owner")
    delegation.delegate_task(task["id"], other, "restore-owner", actor="restore-owner")
    assert {row["status"] for row in db.query("SELECT status FROM agent_wakeups")} == {
        "running",
        "pending",
    }
    db.claim_job("restore-receipt", "keep-this-claim")
    return room["id"], agent


def test_preboot_fence_invalidates_restored_revoked_oidc_authority(
    scratch_db, tmp_path, monkeypatch
):
    from app.services import admin, api_keys, browser_sessions

    issued = _oidc_session(monkeypatch)
    key = api_keys.create_key("restore-owner")
    env = admin._pg_env()
    archive = tmp_path / "external-full.dump"
    subprocess.run(  # noqa: S603
        [_tool("pg_dump"), "--format=custom", "--file", str(archive)],
        env=env,
        check=True,
        capture_output=True,
    )
    browser_sessions.revoke(issued.cookie)
    with pytest.raises(browser_sessions.SessionInvalid):
        browser_sessions.authenticate(issued.cookie, mode="oidc")
    subprocess.run(  # noqa: S603
        [
            _tool("pg_restore"),
            "--dbname",
            env["PGDATABASE"],
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            "--single-transaction",
            "--exit-on-error",
            str(archive),
        ],
        env=env,
        check=True,
        capture_output=True,
    )
    assert browser_sessions.authenticate(issued.cookie, mode="oidc").user == "restore-owner"
    _apply_fence()
    assert (
        scratch_db.query_row("SELECT active FROM api_keys WHERE id = ?", (key["id"],))["active"]
        == 0
    )
    with pytest.raises(browser_sessions.SessionInvalid):
        browser_sessions.authenticate(issued.cookie, mode="oidc")


def test_preboot_fence_stops_all_restored_agent_requests(fresh_db, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import activity, agent_wakeups, chat_threads, shared_chat_agents

    room, agent = _queued_work(fresh_db, monkeypatch)
    ledger = fresh_db.query("SELECT * FROM activity ORDER BY id")
    claims = fresh_db.query("SELECT * FROM job_runs")
    messages = fresh_db.query("SELECT * FROM chat_messages ORDER BY id")
    _apply_fence()
    assert fresh_db.query("SELECT * FROM activity ORDER BY id") == ledger
    assert fresh_db.query("SELECT * FROM job_runs") == claims
    with TestClient(app):
        assert shared_chat_agents.wait_for_idle()
        runs = fresh_db.query("SELECT * FROM chat_agent_runs")
        assert {row["status"] for row in runs} == {"completion_unknown"}
        assert all(row["error_code"] == "restore_reconciliation" for row in runs)
        for row in runs:
            finished = datetime.fromisoformat(row["finished_at"])
            assert row["finished_at"] == finished.astimezone(UTC).isoformat(timespec="seconds")
        assert not any(row["execution_active"] for row in runs)
        wakes = fresh_db.query("SELECT * FROM agent_wakeups")
        assert {(wake["status"], wake["rerun_requested"]) for wake in wakes} == {
            ("completion_unknown", 0)
        }
        assert all(wake["reason"] == "restore_reconciliation" for wake in wakes)
        assert agent_wakeups.claim_next() is None
        assert fresh_db.query("SELECT * FROM chat_messages ORDER BY id") == messages
        # Scheduler-off is not a ban on a new human's explicit chat request.
        chat_threads.post_shared_message(
            room, "restore-owner", f"@{agent} inspect this", "new-request", invoke_agent=agent
        )
        assert shared_chat_agents.wait_for_idle()
        assert (
            fresh_db.query_row(
                "SELECT status FROM chat_agent_runs ORDER BY trigger_message_id DESC LIMIT 1"
            )["status"]
            == "completed"
        )
        assert activity.verify_chain()["ok"] is True


def test_preboot_fence_accepts_backups_before_session_and_queue_tables(scratch_db):
    for table in ("browser_sessions", "chat_agent_runs", "agent_wakeups"):
        scratch_db.execute(f"DROP TABLE {table} CASCADE")
    _apply_fence()


def test_preboot_fence_rolls_back_on_unexpected_schema(scratch_db):
    from app.services import api_keys, users

    users.ensure_human_identity("restore-owner")
    key = api_keys.create_key("restore-owner")
    scratch_db.execute("ALTER TABLE chat_agent_runs DROP COLUMN execution_active")
    with pytest.raises(AssertionError, match="execution_active"):
        _apply_fence()
    assert (
        scratch_db.query_row("SELECT active FROM api_keys WHERE id = ?", (key["id"],))["active"]
        == 1
    )


def test_preboot_command_fails_closed_and_runs_before_boot():
    script = _fence_script()
    assert script.startswith("psql -X --set ON_ERROR_STOP=on --single-transaction")
    guide = README.read_text()
    assert guide.index(script.splitlines()[0]) < guide.index("scale the backend to one")


def test_manual_backup_command_excludes_session_data(fresh_db, tmp_path, monkeypatch):
    from app.services import admin

    _oidc_session(monkeypatch)
    guide = README.read_text()
    command = re.search(r"^pg_dump --format=custom.*(?:\n .*?)*", guide, re.M)
    assert command, "The manual recovery point needs an executable pg_dump command"
    argv = shlex.split(command[0].replace("\\\n", ""))
    assert "--exclude-table-data=public.browser_sessions" in argv
    assert "--schema" not in " ".join(argv)
    env = admin._pg_env()
    archive = tmp_path / "manual.dump"
    argv = [str(archive) if arg == "$backup_file" else arg for arg in argv]
    subprocess.run(argv, env=env, check=True, capture_output=True)  # noqa: S603
    result = subprocess.run(  # noqa: S603
        [_tool("pg_restore"), "--list", str(archive)],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert " TABLE public browser_sessions " in result.stdout
    assert " TABLE DATA public browser_sessions " not in result.stdout
    assert " TABLE DATA public users " in result.stdout


@pytest.mark.parametrize("environment", ["dev", "prod"])
def test_upgrade_card_changes_tags_and_matching_digests(tmp_path, environment):
    for path in (OPERATOR, README):
        guide = path.read_text()
        assert "matching reviewed digest" in guide
        assert "kubectl kustomize" in guide
        assert "grep 'image:'" in guide
    kubectl = shutil.which("kubectl")
    if not kubectl:
        pytest.skip("kubectl is required for the render drill")
    tree = tmp_path / "k8s"
    shutil.copytree(ROOT / "deploy/k8s", tree)
    overlay = tree / "overlays" / f"example-{environment}"
    path = overlay / "kustomization.yaml"
    original = path.read_text()

    def render():
        result = subprocess.run(  # noqa: S603
            [kubectl, "kustomize", str(overlay)], check=True, capture_output=True, text=True
        )
        return [line.strip() for line in result.stdout.splitlines() if "image:" in line]

    before = render()
    tagged, count = re.subn(r"newTag: [^\n]+", "newTag: upgrade-probe", original)
    assert count == 2
    path.write_text(tagged)
    tag_only = render()
    assert [line.split("@")[-1] for line in tag_only] == [line.split("@")[-1] for line in before]
    digests = iter(["1" * 64, "2" * 64])
    upgraded, count = re.subn(
        r"digest: sha256:[0-9a-f]{64}", lambda _: f"digest: sha256:{next(digests)}", tagged
    )
    assert count == 2
    path.write_text(upgraded)
    after = render()
    assert any(f"@sha256:{'1' * 64}" in line for line in after)
    assert any(f"@sha256:{'2' * 64}" in line for line in after)
    assert after != tag_only
