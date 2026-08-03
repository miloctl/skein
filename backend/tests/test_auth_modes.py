"""SKEIN_AUTH_MODE: which identity doors exist, and who administers.

Every other test file runs in trusted-header mode (the default) and pins that
behavior; this file pins the api-key and oidc modes, the fail-closed handling
of a broken auth config, and the StrongUser/AdminUser split.
"""

import pytest


def _key(owner="tester"):
    from app.services.api_keys import create_key

    return {"Authorization": f"Bearer {create_key(owner, 'test')['key']}"}


def _oidc(monkeypatch, tokens):
    from app import config, oidc

    monkeypatch.setattr(config, "AUTH_MODE", "oidc")

    def fake_validate(token):
        if token in tokens:
            return tokens[token]
        raise oidc.OIDCError("the sign-in token was refused (Fake). Sign in again.")

    monkeypatch.setattr(oidc, "validate", fake_validate)


def test_broken_auth_config_fails_closed(client, monkeypatch):
    from app import config

    headers = _key()
    monkeypatch.setattr(config, "AUTH_ERROR", "unknown SKEIN_AUTH_MODE 'oidk'")
    # everything is refused — even a valid key. A typo'd mode gets fixed,
    # never guessed around.
    assert client.get("/api/tasks").status_code == 503
    assert client.get("/api/tasks", headers=headers).status_code == 503
    h = client.get("/health")
    assert h.status_code == 200
    assert "SKEIN_AUTH_MODE" in h.json()["auth_error"]


def test_health_reports_auth_mode(client):
    h = client.get("/health").json()
    assert h["auth_mode"] == "trusted-header"
    assert h["auth_error"] == ""


def test_api_key_mode_never_trusts_the_header(client, monkeypatch):
    from app import config

    headers = _key()
    monkeypatch.setattr(config, "AUTH_MODE", "api-key")
    # the client fixture sends X-User: tester on every request — not enough
    assert client.get("/api/tasks").status_code == 401
    r = client.post("/api/capture", json={"text": "todo: x"}, headers={"X-User": "mallory"})
    assert r.status_code == 401
    assert client.get("/api/tasks", headers=headers).status_code == 200
    # a presented-but-bogus key keeps its specific refusal
    r = client.get("/api/tasks", headers={"Authorization": "Bearer sk-skein-bogus"})
    assert r.status_code == 401
    assert "invalid or revoked" in r.json()["detail"]


def test_api_key_mode_refuses_the_header_at_the_route_layer_too(monkeypatch, fresh_db):
    # the middleware is the perimeter; the dependency must hold on its own
    from fastapi import HTTPException

    from app import config
    from app.routes.deps import _resolve

    monkeypatch.setattr(config, "AUTH_MODE", "api-key")
    with pytest.raises(HTTPException) as e:
        _resolve("tester", "", "GET")
    assert e.value.status_code == 401


def test_slack_endpoint_keeps_its_own_gate(client, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "AUTH_MODE", "api-key")
    # Slack verifies its own signature — unconfigured is a 404, never a 401
    assert client.post("/api/slack/command").status_code == 404


def test_calendar_feed_fails_closed_outside_trusted_header_mode(client, monkeypatch):
    from app import config

    assert client.get("/api/calendar.ics").status_code == 200
    monkeypatch.setattr(config, "AUTH_MODE", "api-key")
    assert client.get("/api/calendar.ics").status_code == 403
    monkeypatch.setattr(config, "ICS_TOKEN", "feed-secret")
    assert client.get("/api/calendar.ics?token=feed-secret").status_code == 200


def test_oidc_mode_sign_in_is_strong_identity(client, monkeypatch, fresh_db):
    _oidc(monkeypatch, {"good": {"preferred_username": "casey", "groups": ["eng"]}})
    hdr = {"Authorization": "Bearer good"}
    assert client.get("/api/tasks").status_code == 401
    assert client.get("/api/tasks", headers={"Authorization": "Bearer bad"}).status_code == 401
    assert (
        client.post("/api/capture", json={"text": "todo: from sso"}, headers=hdr).status_code == 200
    )
    tasks = client.get("/api/tasks", headers=hdr).json()
    assert tasks[0]["created_by"] == "casey"
    # a validated sign-in is STRONG: minting a first key needs no prior key
    assert client.post("/api/keys", json={"label": "cli"}, headers=hdr).status_code == 200


def test_oidc_sign_in_cannot_claim_an_agent_identity(client, monkeypatch, fresh_db):
    from app.services.users import ensure_user

    ensure_user("scout", kind="agent")
    _oidc(monkeypatch, {"tok": {"preferred_username": "scout"}})
    r = client.post(
        "/api/capture", json={"text": "todo: x"}, headers={"Authorization": "Bearer tok"}
    )
    assert r.status_code == 403
    assert "agent identity" in r.json()["detail"]


def test_admin_surfaces_stay_open_to_key_holders_by_default(client):
    # trusted-header + empty SKEIN_ADMINS = the historical scarcity model,
    # where holding a hand-minted key IS the admin bar
    assert client.get("/api/admin/keys", headers=_key()).status_code == 200


def test_admins_list_closes_admin_surfaces_to_other_key_holders(client, monkeypatch):
    from app import config

    other = _key("pat")
    monkeypatch.setattr(config, "ADMINS", frozenset({"root"}))
    r = client.get("/api/admin/keys", headers=other)
    assert r.status_code == 403
    assert "not an administrator" in r.json()["detail"]
    # self-scoped strong surfaces stay open: pat still mints their own keys
    assert client.post("/api/keys", json={"label": "cli"}, headers=other).status_code == 200
    assert client.get("/api/admin/keys", headers=_key("root")).status_code == 200


def test_api_key_mode_locks_admin_surfaces_until_admins_is_set(client, monkeypatch):
    from app import config

    hdr = _key("pat")
    monkeypatch.setattr(config, "AUTH_MODE", "api-key")
    assert client.get("/api/admin/keys", headers=hdr).status_code == 403
    monkeypatch.setattr(config, "ADMINS", frozenset({"pat"}))
    assert client.get("/api/admin/keys", headers=hdr).status_code == 200


def test_oidc_admin_group_grants_admin(client, monkeypatch, fresh_db):
    from app import config

    _oidc(
        monkeypatch,
        {
            "lead": {"preferred_username": "lead", "groups": ["skein-admins"]},
            "dev": {"preferred_username": "dev", "groups": ["eng"]},
        },
    )
    monkeypatch.setattr(config, "OIDC_ADMIN_GROUP", "skein-admins")
    assert client.get("/api/admin/keys", headers={"Authorization": "Bearer dev"}).status_code == 403
    assert (
        client.get("/api/admin/keys", headers={"Authorization": "Bearer lead"}).status_code == 200
    )
