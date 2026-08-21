import sys
from dataclasses import replace

from _expect import ok
from fastapi.testclient import TestClient

from app import identity_audit
from app.extensions import AppSettings
from app.main import create_app

sys.argv = ["identity_audit", "claim-machine", "mcp-agent", "mcp"]
identity_audit.main()
with TestClient(
    create_app(replace(AppSettings.from_config(), scheduler_enabled=False)),
    headers={"X-User": "upgrade-user"},
) as client:
    assert "legacy_delivery" in {row["slug"] for row in ok(client.get("/api/playbooks"))}
    assert "legacy-reviewer" in {row["slug"] for row in ok(client.get("/api/personas"))}
    assert "legacy-team" in {row["slug"] for row in ok(client.get("/api/flocks"))}
    started = client.post(
        "/api/playbooks/instantiate",
        json={
            "playbook": "legacy_delivery",
            "engagement_name": "Typed legacy after upgrade",
        },
    )
    assert started.status_code == 200, started.text
    assert started.json()["engagement"]["name"] == "Typed legacy after upgrade"
