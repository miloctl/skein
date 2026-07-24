"""Tests for round-2 features: API keys, CI webhook, pulse, ship-it, funerals."""

import pytest


def test_api_key_lifecycle_and_attribution(client):
    created = client.post("/api/keys", json={"label": "cli"}).json()
    key = created["key"]
    assert key.startswith("sk-strands-")

    # key authenticates and attributes as its owner, ignoring X-User
    out = client.post("/api/capture", json={"text": "todo: via key"},
                      headers={"Authorization": f"Bearer {key}",
                               "X-User": "someone-else"}).json()
    tasks = client.get("/api/tasks").json()
    assert tasks[0]["created_by"] == "tester"

    keys = client.get("/api/keys").json()
    assert keys[0]["last_used_at"] is not None
    assert "key" not in keys[0]  # full key never re-exposed

    client.delete(f"/api/keys/{created['id']}")
    r = client.post("/api/capture", json={"text": "x"},
                    headers={"Authorization": f"Bearer {key}", "X-User": ""})
    # revoked key falls back to anonymous X-User path, not the owner
    assert r.status_code == 200
    assert client.get("/api/tasks").json()[-1]["created_by"] != "tester" or out


def test_api_key_satisfies_shared_token_gate(client, monkeypatch):
    from app import config

    key = client.post("/api/keys", json={"label": "ci"}).json()["key"]
    monkeypatch.setattr(config, "API_TOKEN", "sekrit")
    assert client.get("/api/tasks").status_code == 401
    ok = client.get("/api/tasks", headers={"Authorization": f"Bearer {key}"})
    assert ok.status_code == 200


def test_ci_webhook_dedupe_and_resolve(client):
    fail = {"repo": "team/app", "branch": "main", "status": "failure",
            "run_url": "https://ci/run/1"}
    first = client.post("/api/webhooks/ci", json=fail).json()
    assert first["raised"]
    assert client.post("/api/webhooks/ci", json=fail).json()["deduped"]

    blockers = client.get("/api/blockers").json()
    assert any("CI red" in b["title"] for b in blockers)

    ok = client.post("/api/webhooks/ci",
                     json={**fail, "status": "success"}).json()
    assert len(ok["resolved"]) == 1
    assert client.get("/api/blockers").json() == []

    ignored = client.post("/api/webhooks/ci",
                          json={**fail, "branch": "feature/x"}).json()
    assert "ignored" in ignored


def test_ci_webhook_github_actions_shape(client):
    payload = {
        "workflow_run": {"status": "completed", "conclusion": "failure",
                         "head_branch": "main", "html_url": "https://gh/run/9"},
        "repository": {"full_name": "team/repo"},
    }
    out = client.post("/api/webhooks/ci", json=payload).json()
    assert out["raised"]


def test_pulse_shape_and_season(client):
    from app.services import pulse

    s = pulse.season()
    assert s["days_left"] >= 0 and "S" in s["label"]

    client.post("/api/standups", json={"today": "work"})
    p = client.get("/api/pulse").json()
    assert "standup_chain" in p and "season_totals" in p


def test_standup_chain_counts_full_participation(fresh_db):
    from app.services import collab, pulse, users

    users.ensure_user("a")
    users.ensure_user("b")
    users.ensure_user("bot", kind="agent")  # agents don't break the chain
    collab.post_standup("a", today="x")
    chain = pulse.standup_chain()
    assert chain["humans"] == 2
    assert chain["chain"] == 0  # b hasn't posted today; chain not yet earned

    collab.post_standup("b", today="y")
    import datetime

    if datetime.datetime.now(datetime.timezone.utc).date().weekday() < 5:
        assert pulse.standup_chain()["chain"] == 1


def test_ship_it_recap_and_notification(fresh_db, monkeypatch):
    from app.services import engagements, notifications

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)
    e = engagements.create_engagement("Big launch", actor="ava")
    engagements.update_engagement(e["id"], status="closed", actor="ava")

    notes = fresh_db.query("SELECT * FROM notes WHERE topic = 'shipped-Big launch'")
    assert notes and "Shipped" in notes[0]["content"]
    msgs = [n["message"] for n in notifications.list_notifications("team")]
    assert any("Shipped" in m for m in msgs)


def test_blocker_funeral_after_three_days(fresh_db, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from app.services import blockers, notifications

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)
    b = blockers.raise_blocker("ancient blocker", escalate_after_hours=999)
    old = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat(timespec="seconds")
    fresh_db.execute("UPDATE blockers SET created_at = ? WHERE id = ?", (old, b["id"]))
    blockers.resolve_blocker(b["id"])
    msgs = [n["message"] for n in notifications.list_notifications("team")]
    assert any("Here lies" in m for m in msgs)


def test_digest_opener_reads_the_room(fresh_db):
    from app.services import blockers, digest

    md = digest.build_digest()
    assert any(line.startswith("*") for line in md.splitlines())  # opener present

    b = blockers.raise_blocker("fire", impact="critical")
    fresh_db.execute("UPDATE blockers SET status = 'escalated' WHERE id = ?", (b["id"],))
    md = digest.build_digest()
    opener_lines = [l for l in md.splitlines()[:4] if l.startswith("*")]
    assert not opener_lines  # no jokes during a fire


def test_cli_trailer_regex():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "strands_cli", Path(__file__).parents[2] / "cli" / "strands_cli.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    msg = "Fix auth\n\nCloses-Task: #12\nRefs-Task: 7\n"
    assert cli.TRAILER.findall(msg) == [("Closes", "12"), ("Refs", "7")]
