"""Tests for the panel-recommendation build: adoption telemetry, onboarding,
forecast snapshots, backup mirroring."""

import os


def test_adoption_records_by_surface(client, fresh_db):
    client.post("/api/capture", json={"text": "todo: from web"}, headers={"X-Client": "web"})
    client.post("/api/capture", json={"text": "todo: from cli"}, headers={"X-Client": "cli"})
    client.post("/api/capture", json={"text": "todo: no client header"})

    out = client.get("/api/adoption").json()
    surfaces = {r["surface"]: r["actions"] for r in out["by_surface"]}
    assert surfaces.get("web") == 1 and surfaces.get("cli") == 1
    assert surfaces.get("api") >= 1
    assert out["weekly_active_users"] == 1
    assert out["captures_in_window"] == 3
    assert out["non_web_share"] is not None


def test_adoption_ignores_anonymous(client, fresh_db):
    client.get("/api/briefing", headers={"X-User": ""})
    rows = fresh_db.query("SELECT * FROM tool_usage")
    assert all(r["user"] != "anonymous" for r in rows)


def test_adoption_upserts_daily_row(client, fresh_db):
    for _ in range(3):
        client.get("/api/briefing", headers={"X-Client": "web"})
    rows = fresh_db.query("SELECT * FROM tool_usage WHERE user = 'tester' AND surface = 'web'")
    assert len(rows) == 1 and rows[0]["actions"] == 3


def test_onboarding_checklist_progresses(client):
    first = client.get("/api/onboarding").json()
    assert first["complete"] is False
    by_id = {s["id"]: s["done"] for s in first["steps"]}
    assert by_id["pick_name"] is True  # X-User: tester
    assert by_id["first_capture"] is False

    client.post("/api/capture", json={"text": "todo: onboard me"})
    client.post("/api/engagements", json={"name": "First real work"})
    client.post("/api/standups", json={"today": "getting started"})
    # keys are bootstrapped out-of-band now (python -m app.bootstrap_key)
    from app.services.api_keys import create_key

    create_key("tester", "cli")

    after = client.get("/api/onboarding").json()
    by_id = {s["id"]: s["done"] for s in after["steps"]}
    assert by_id["first_capture"] and by_id["first_engagement"]
    assert by_id["first_standup"] and by_id["setup_key"]
    assert after["next"]["id"] == "invite_team"  # still a team of one


def test_forecast_snapshot_idempotent_per_day(client, fresh_db):
    from app.services import adoption

    client.post("/api/engagements", json={"name": "Fx"})
    client.post("/api/milestones", json={"title": "m", "project": "Fx", "due_date": "2030-01-01"})
    adoption.snapshot_forecasts()
    adoption.snapshot_forecasts()
    rows = fresh_db.query("SELECT * FROM forecast_snapshots")
    assert len(rows) == 1
    assert rows[0]["due_date"] == "2030-01-01"


def test_backup_mirror_guarded_to_production_data_dir(fresh_db, tmp_path, monkeypatch):
    from pathlib import Path

    from app import config
    from app.services import admin

    mirror = tmp_path / "offbox"
    monkeypatch.setenv("SKEIN_BACKUP_MIRROR", str(mirror))

    # non-default data dir (tests, sandboxes): mirror must be skipped —
    # a throwaway instance overwrote the real NAS mirror before this guard
    assert admin.backup()["mirrored"] is None
    assert not mirror.exists()

    # "production": DATA_DIR == BASE_DIR/data → mirror happens
    prod_data = tmp_path / "data"
    prod_data.mkdir()
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", prod_data)
    out = admin.backup()
    assert out["mirrored"] and os.path.exists(out["mirrored"])
    assert Path(out["mirrored"]).parent == mirror

    monkeypatch.delenv("SKEIN_BACKUP_MIRROR")
    assert admin.backup()["mirrored"] is None
