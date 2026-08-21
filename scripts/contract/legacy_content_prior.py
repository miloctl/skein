from dataclasses import replace

from _expect import ok
from fastapi.testclient import TestClient

from app.extensions import AppSettings
from app.main import create_app

with TestClient(
    create_app(replace(AppSettings.from_config(), scheduler_enabled=False)),
    headers={"X-User": "upgrade-user"},
) as client:
    assert "legacy_delivery" in {row["slug"] for row in ok(client.get("/api/playbooks"))}
    assert "legacy-reviewer" in {row["slug"] for row in ok(client.get("/api/personas"))}
    assert "legacy-team" in {row["slug"] for row in ok(client.get("/api/flocks"))}
