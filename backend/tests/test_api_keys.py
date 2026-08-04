"""Per-teammate API keys: lifecycle, attribution, the admin kill switch, and self-service key requests."""

from datetime import UTC


def _bootstrap(owner="tester", label="test"):
    # first key comes from the out-of-band bootstrap (python -m app.bootstrap_key)
    from app.services.api_keys import create_key

    return create_key(owner, label)


def test_api_key_lifecycle_and_attribution(client):
    # minting via the API needs an existing key (X-User alone must not mint)
    assert client.post("/api/keys", json={"label": "spoof"}).status_code == 403
    boot = _bootstrap()
    created = client.post(
        "/api/keys",
        json={"label": "cli"},
        headers={"Authorization": f"Bearer {boot['key']}"},
    ).json()
    key = created["key"]
    assert key.startswith("sk-skein-")

    # key authenticates and attributes as its owner, ignoring X-User
    out = client.post(
        "/api/capture",
        json={"text": "todo: via key"},
        headers={"Authorization": f"Bearer {key}", "X-User": "someone-else"},
    ).json()
    tasks = client.get("/api/tasks").json()
    assert tasks[0]["created_by"] == "tester"

    keys = client.get("/api/keys").json()
    assert keys[0]["last_used_at"] is not None
    assert "key" not in keys[0]  # full key never re-exposed

    # revoking also requires strong identity
    assert client.delete(f"/api/keys/{created['id']}").status_code == 403
    client.delete(f"/api/keys/{created['id']}", headers={"Authorization": f"Bearer {boot['key']}"})
    # a presented-but-revoked key is a hard 401 — never a silent fallback
    r = client.post(
        "/api/capture",
        json={"text": "x"},
        headers={"Authorization": f"Bearer {key}", "X-User": "tester"},
    )
    assert r.status_code == 401
    assert out  # earlier keyed write succeeded


def test_admin_key_visibility_and_kill_switch(client):
    boot = _bootstrap()
    other = _bootstrap("other-person", "one")

    # key metadata is admin surface now — weak identity is a 403
    assert client.get("/api/admin/keys").status_code == 403
    all_keys = client.get(
        "/api/admin/keys", headers={"Authorization": f"Bearer {boot['key']}"}
    ).json()
    owners = {k["owner"] for k in all_keys}
    assert "other-person" in owners

    # the kill switch requires strong identity — a spoofed header can't nuke keys
    assert client.post("/api/admin/keys/revoke-all").status_code == 403
    out = client.post(
        "/api/admin/keys/revoke-all", headers={"Authorization": f"Bearer {boot['key']}"}
    ).json()
    assert out["revoked"] >= 2
    # after the kill switch no strong identity remains — verify via the service
    from app.services.api_keys import list_all_keys

    assert all(not k["active"] for k in list_all_keys())
    assert other  # both bootstrapped keys revoked


def test_api_key_satisfies_shared_token_gate(client, monkeypatch):
    from app import config

    key = _bootstrap()["key"]
    monkeypatch.setattr(config, "API_TOKEN", "sekrit")
    assert client.get("/api/tasks").status_code == 401
    ok = client.get("/api/tasks", headers={"Authorization": f"Bearer {key}"})
    assert ok.status_code == 200


def test_key_request_rejects_anonymous(client):
    r = client.post("/api/keys/request", headers={"X-User": ""})
    assert r.status_code == 400
    assert "pick your name" in r.json()["detail"]


def test_key_request_refiles_after_notification_read(client):
    assert client.post("/api/keys/request").json() == {"requested": True, "already_pending": False}
    assert client.post("/api/keys/request").json()["already_pending"] is True
    notes = client.get("/api/notifications").json()
    assert any("requests a personal API key" in n["message"] for n in notes)
    client.post("/api/notifications/read", json={"notification_id": 0})  # dismiss all
    assert client.post("/api/keys/request").json()["already_pending"] is False


def test_verify_key_throttles_the_last_used_stamp(client, fresh_db):
    from datetime import datetime, timedelta

    from app.services.api_keys import create_key, verify_key

    key = create_key("tester", "probe")["key"]

    recent = (datetime.now(UTC) - timedelta(seconds=30)).isoformat(timespec="seconds")
    fresh_db.execute("UPDATE api_keys SET last_used_at = ?", (recent,))
    assert verify_key(key) == "tester"
    assert fresh_db.query_row("SELECT last_used_at FROM api_keys")["last_used_at"] == recent

    stale = (datetime.now(UTC) - timedelta(seconds=120)).isoformat(timespec="seconds")
    fresh_db.execute("UPDATE api_keys SET last_used_at = ?", (stale,))
    assert verify_key(key) == "tester"
    assert fresh_db.query_row("SELECT last_used_at FROM api_keys")["last_used_at"] != stale

    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat(timespec="seconds")
    fresh_db.execute("UPDATE api_keys SET last_used_at = ?", (future,))
    assert verify_key(key) == "tester"  # a clock-step stamp rewrites instead of freezing
    assert fresh_db.query_row("SELECT last_used_at FROM api_keys")["last_used_at"] != future
