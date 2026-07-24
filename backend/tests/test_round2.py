"""Tests for round-2 features: API keys, CI webhook, pulse, ship-it, funerals."""



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
    # a presented-but-revoked key is a hard 401 — never a silent fallback
    r = client.post("/api/capture", json={"text": "x"},
                    headers={"Authorization": f"Bearer {key}", "X-User": "tester"})
    assert r.status_code == 401
    assert out  # earlier keyed write succeeded


def test_admin_key_visibility_and_kill_switch(client):
    client.post("/api/keys", json={"label": "one"},
                headers={"X-User": "spoofed-bot"})
    client.post("/api/keys", json={"label": "two"})

    all_keys = client.get("/api/admin/keys").json()
    owners = {k["owner"] for k in all_keys}
    assert "spoofed-bot" in owners  # hidden keys are discoverable by anyone

    out = client.post("/api/admin/keys/revoke-all").json()
    assert out["revoked"] >= 2
    assert all(not k["active"] for k in client.get("/api/admin/keys").json())


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

    cancelled = {**payload, "workflow_run": {**payload["workflow_run"],
                                             "conclusion": "cancelled"}}
    assert "ignored" in client.post("/api/webhooks/ci", json=cancelled).json()


def test_pulse_shape_and_season(client):
    from app.services import pulse

    s = pulse.season()
    assert s["days_left"] >= 0 and "S" in s["label"]

    client.post("/api/standups", json={"today": "work"})
    p = client.get("/api/pulse").json()
    assert "standup_chain" in p and "season_totals" in p


def test_standup_chain_roster_is_participation_based(fresh_db):
    import datetime

    from app.services import collab, pulse, users

    users.ensure_user("a")
    users.ensure_user("b")
    users.ensure_user("anonymous")          # pre-name-pick frontend traffic
    users.ensure_user("bot", kind="agent")  # agents don't break the chain

    # nobody has ever posted: no roster, no chain — and no permanent zero
    assert pulse.standup_chain() == {"chain": 0, "humans": 0}

    weekday = datetime.datetime.now(datetime.timezone.utc).date().weekday() < 5
    collab.post_standup("a", today="x")
    chain = pulse.standup_chain()
    assert chain["humans"] == 1  # b joins the roster by playing, not by existing
    if weekday:
        assert chain["chain"] == 1

    collab.post_standup("b", today="y")
    chain = pulse.standup_chain()
    assert chain["humans"] == 2  # anonymous and the agent never count
    if weekday:
        assert chain["chain"] == 1


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
    opener_lines = [line for line in md.splitlines()[:4] if line.startswith("*")]
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


def test_api_tester_regressions(client):
    # FK violations are clean 400s, not 500s
    assert client.post("/api/tasks", json={"title": "orphan", "milestone_id": 999999}).status_code == 400
    assert client.post("/api/engagements/999999/allocate", json={"person": "a"}).status_code == 400
    assert client.post("/api/lessons", json={"lesson": "x", "engagement_id": 999999}).status_code == 400

    # playbook slug traversal rejected
    r = client.post("/api/playbooks/instantiate",
                    json={"playbook": "/tmp/pwned", "engagement_name": "t"})
    assert r.status_code == 400
    r = client.post("/api/playbooks/instantiate",
                    json={"playbook": "../secrets", "engagement_name": "t"})
    assert r.status_code == 400

    # 0-row updates are 400s, not silent success
    assert client.patch("/api/tasks/999999", json={"status": "done"}).status_code == 400
    assert client.patch("/api/milestones/999999", json={"status": "done"}).status_code == 400
    assert client.post("/api/intake/999999/score",
                       json={"reach": 3, "impact": 3, "confidence": 3, "effort": 3}).status_code == 400

    # disposition is terminal
    req = client.post("/api/intake", json={"title": "once"}).json()
    client.post(f"/api/intake/{req['id']}/score",
                json={"reach": 3, "impact": 3, "confidence": 3, "effort": 3})
    client.post(f"/api/intake/{req['id']}/disposition",
                json={"disposition": "accepted", "reason": "yes"})
    r = client.post(f"/api/intake/{req['id']}/disposition",
                    json={"disposition": "declined", "reason": "no"})
    assert r.status_code == 400

    # double-resolve is a 400
    b = client.post("/api/blockers", json={"title": "once-only"}).json()
    client.post(f"/api/blockers/{b['id']}/resolve", json={})
    assert client.post(f"/api/blockers/{b['id']}/resolve", json={}).status_code == 400


def test_team_notifications_visible_in_inbox(client, monkeypatch):
    from app.services import notifications

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)
    notifications.notify("team", "ship recap here", tier="immediate")
    inbox = client.get("/api/notifications").json()
    assert any("ship recap" in n["message"] for n in inbox)
