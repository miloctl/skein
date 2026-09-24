"""Cookie authentication must not expose credentials or fall back to another identity."""

import time
from contextlib import closing

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app import config, db, oidc
from app.services import api_keys, browser_sessions, users

ORIGIN = "https://ui.test"


@pytest.fixture
def browser(client, monkeypatch):
    monkeypatch.setattr(config, "CORS_ORIGINS", [ORIGIN])
    monkeypatch.setattr(config, "CREDENTIAL_KEY", Fernet.generate_key().decode())
    # client already owns the lifespan. Entering another replaces the lease
    # maintenance stop handles and leaves the first pair running after DB teardown.
    with closing(
        TestClient(client.app, base_url="https://api.test", headers={"Origin": ORIGIN})
    ) as other:
        yield other


def test_browser_reuses_the_client_owned_lifespan(client, monkeypatch):
    from app.services import leases

    original_stop = leases._stop
    original_threads = (leases._thread, leases._sweeper)
    browser_fixture = browser.__wrapped__(client, monkeypatch)
    try:
        other = next(browser_fixture)
        assert other.get("/health").status_code == 200
        assert (leases._thread, leases._sweeper) == original_threads
        browser_fixture.close()
        assert all(thread.is_alive() for thread in original_threads)
    finally:
        browser_fixture.close()
        # A nested lifespan overwrites the only stop handle. Retain it here so
        # a regression cannot leave maintenance running against the next database.
        original_stop.set()
        for thread in original_threads:
            thread.join(5)
            assert not thread.is_alive()


def _key(owner="ava"):
    users.ensure_human_identity(owner)
    return api_keys.create_key(owner, "browser test")["key"]


def _login(browser, owner="ava"):
    key = _key(owner)
    response = browser.post("/api/auth/session/key", json={"key": key})
    assert response.status_code == 200, response.text
    assert key not in response.text
    return response.json(), key


def test_key_exchange_sets_secure_opaque_cookie_and_metadata_only(browser):
    response = browser.post("/api/auth/session/key", json={"key": _key()})
    assert response.status_code == 200, response.text
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    assert "Path=/" in cookie and "Domain=" not in cookie
    assert response.json()["user"] == "ava"
    assert not ({"access_token", "refresh_token", "key", "cookie"} & response.json().keys())
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "origin",
    [
        "",
        "null",
        "https://evil.test",
        "https://ui.test/path",
        "https://ui.test:0",
        "https://ui.test?",
        "https://ui.test#",
        "https://ui.\ttest",
    ],
)
def test_key_exchange_refuses_unapproved_origins_before_authentication(browser, origin):
    response = browser.post(
        "/api/auth/session/key", json={"key": _key()}, headers={"Origin": origin}
    )
    assert response.status_code == 403
    assert "set-cookie" not in response.headers


def test_cookie_binding_covers_reads_writes_and_never_falls_back_to_name(browser, fresh_db):
    info, _ = _login(browser)
    assert browser.get("/api/tasks", headers={"X-User": "bo"}).status_code == 403
    headers = {"X-Skein-CSRF": info["csrf_token"], "X-User": "bo"}
    response = browser.post("/api/tasks", json={"title": "bound identity"}, headers=headers)
    assert response.status_code == 200, response.text
    assert (
        fresh_db.query_one("SELECT created_by FROM tasks WHERE id = ?", (response.json()["id"],))[
            "created_by"
        ]
        == "ava"
    )
    browser.cookies.clear()
    response = browser.post("/api/tasks", json={"title": "must not downgrade"}, headers=headers)
    assert response.status_code == 401


def test_cookie_cannot_mint_a_durable_key_but_automation_bearer_can(browser):
    info, key = _login(browser)
    response = browser.post(
        "/api/keys", json={"label": "must not escape"}, headers={"X-Skein-CSRF": info["csrf_token"]}
    )
    assert response.status_code == 403
    assert "sk-skein-" not in response.text
    response = browser.post(
        "/api/keys", json={"label": "automation"}, headers={"Authorization": f"Bearer {key}"}
    )
    assert response.status_code == 200 and response.json()["key"].startswith("sk-skein-")


def test_invalid_explicit_bearer_never_uses_valid_cookie(browser):
    info, _ = _login(browser)
    response = browser.get(
        "/api/whoami",
        headers={"X-Skein-CSRF": info["csrf_token"], "Authorization": "Bearer sk-skein-invalid"},
    )
    assert response.status_code == 401


def test_logout_revokes_and_clears_without_provider_access(browser, monkeypatch):
    info, _ = _login(browser)
    cookie = browser.cookies.get(browser_sessions.COOKIE_NAME)
    monkeypatch.setattr(oidc, "exchange", lambda *_: pytest.fail("logout must be local"))
    assert browser.delete("/api/auth/session").status_code == 403
    response = browser.delete("/api/auth/session", headers={"X-Skein-CSRF": info["csrf_token"]})
    assert response.status_code == 200
    # a key session holds no provider session to end
    assert response.json() == {"logout_url": ""}
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert browser_sessions.metadata(cookie, mode="trusted-header")["authenticated"] is False


def test_signing_out_everywhere_ends_every_session_of_that_person_only(browser):
    """A lost laptop kept its session for up to 8 hours: signing out on
    another browser ended that browser's session alone."""
    elsewhere = browser_sessions.create_key_session(_key(), mode="trusted-header").cookie
    teammate = browser_sessions.create_key_session(_key("bo"), mode="trusted-header").cookie
    info, _ = _login(browser)
    here = browser.cookies.get(browser_sessions.COOKIE_NAME)

    def signed_in(cookie: str) -> bool:
        return browser_sessions.metadata(cookie, mode="trusted-header")["authenticated"]

    # a cross-site request carries the cookie and never the header
    assert browser.delete("/api/auth/sessions").status_code == 403
    assert signed_in(elsewhere)
    response = browser.delete("/api/auth/sessions", headers={"X-Skein-CSRF": info["csrf_token"]})
    assert response.status_code == 200
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert not signed_in(elsewhere) and not signed_in(here)
    assert signed_in(teammate)


def test_bootstrap_does_not_clear_a_newer_cookie(browser):
    browser.cookies.set(browser_sessions.COOKIE_NAME, "expired-cookie", domain="api.test", path="/")
    response = browser.get("/api/auth/session")
    assert response.status_code == 200 and not response.json()["authenticated"]
    assert "set-cookie" not in response.headers


def test_oidc_exchange_returns_no_provider_credentials(browser, monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "oidc")
    monkeypatch.setattr(config, "OIDC_ISSUER", "https://idp.test")
    monkeypatch.setattr(config, "OIDC_AUDIENCE", "skein")
    monkeypatch.setattr(config, "OIDC_CLIENT_ID", "skein-web")
    claims = {
        "iss": "https://idp.test",
        "sub": "subject:ava",
        "aud": "skein",
        "exp": time.time() + 600,
        "preferred_username": "ava",
    }
    monkeypatch.setattr(oidc, "validate", lambda _: claims)
    monkeypatch.setattr(
        oidc,
        "exchange",
        lambda _: {
            "access_token": "provider-access-secret",
            "refresh_token": "provider-refresh-secret",
            "expires_in": 600,
        },
    )
    response = browser.post(
        "/api/auth/token",
        json={"code": "code", "code_verifier": "v" * 43, "redirect_uri": ORIGIN + "/auth/callback"},
    )
    assert response.status_code == 200, response.text
    assert "provider-" not in response.text
    assert "access_token" not in response.json() and "refresh_token" not in response.json()
    assert response.json()["authenticated"] is True
    assert "HttpOnly" in response.headers["set-cookie"]


def _oidc_sign_in(browser, monkeypatch) -> dict:
    monkeypatch.setattr(config, "AUTH_MODE", "oidc")
    monkeypatch.setattr(config, "OIDC_ISSUER", "https://idp.test")
    monkeypatch.setattr(config, "OIDC_AUDIENCE", "skein")
    monkeypatch.setattr(config, "OIDC_CLIENT_ID", "skein-web")
    claims = {
        "iss": "https://idp.test",
        "sub": "subject:ava",
        "aud": "skein",
        "exp": time.time() + 600,
        "preferred_username": "ava",
    }
    monkeypatch.setattr(oidc, "validate", lambda _: claims)
    monkeypatch.setattr(
        oidc,
        "exchange",
        lambda _: {
            "access_token": "provider-access-secret",
            "refresh_token": "provider-refresh-secret",
            "id_token": "provider-id-token",
            "expires_in": 600,
        },
    )
    response = browser.post(
        "/api/auth/token",
        json={"code": "code", "code_verifier": "v" * 43, "redirect_uri": ORIGIN + "/auth/callback"},
    )
    assert response.status_code == 200, response.text
    assert "provider-id-token" not in response.text
    return response.json()


@pytest.mark.parametrize("path", ["/api/auth/session", "/api/auth/sessions"])
def test_signing_out_also_ends_the_provider_session(browser, monkeypatch, path):
    """Sign-out was local only: the next person at the browser selected Sign
    in and was back in the first person's account without a password."""
    from urllib.parse import parse_qs, urlsplit

    info = _oidc_sign_in(browser, monkeypatch)
    monkeypatch.setattr(
        oidc, "metadata", lambda: {"end_session_endpoint": "https://idp.test/logout"}
    )
    response = browser.delete(path, headers={"X-Skein-CSRF": info["csrf_token"]})
    assert response.status_code == 200, response.text
    target = urlsplit(response.json()["logout_url"])
    assert (target.scheme, target.netloc, target.path) == ("https", "idp.test", "/logout")
    assert parse_qs(target.query) == {
        "id_token_hint": ["provider-id-token"],
        "post_logout_redirect_uri": [ORIGIN + "/"],
        "client_id": ["skein-web"],
    }
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_the_provider_sign_out_leaves_the_id_token_out_where_it_would_be_a_bearer(
    browser, monkeypatch
):
    """With the audience set to the client id, an ID token passes validate()
    as an API bearer, and the sign-out URL put it in the address bar."""
    from urllib.parse import parse_qs, urlsplit

    info = _oidc_sign_in(browser, monkeypatch)
    monkeypatch.setattr(config, "OIDC_AUDIENCE", "skein-web")
    monkeypatch.setattr(
        oidc, "metadata", lambda: {"end_session_endpoint": "https://idp.test/logout"}
    )
    response = browser.delete("/api/auth/session", headers={"X-Skein-CSRF": info["csrf_token"]})
    query = parse_qs(urlsplit(response.json()["logout_url"]).query)
    assert "id_token_hint" not in query and query["client_id"] == ["skein-web"]


def test_signing_out_after_the_session_expired_still_reaches_the_provider(browser, monkeypatch):
    """The prune deletes an expired session with its ID token, and the gate
    still offers Sign out: without the provider step, the next person at the
    browser signs straight back in."""
    from urllib.parse import parse_qs, urlsplit

    info = _oidc_sign_in(browser, monkeypatch)
    db.execute("DELETE FROM browser_sessions")
    monkeypatch.setattr(
        oidc, "metadata", lambda: {"end_session_endpoint": "https://idp.test/logout"}
    )
    response = browser.delete("/api/auth/session", headers={"X-Skein-CSRF": info["csrf_token"]})
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()["logout_url"]).query)
    assert query == {"post_logout_redirect_uri": [ORIGIN + "/"], "client_id": ["skein-web"]}


def test_signing_out_stays_local_when_the_provider_publishes_no_endpoint(browser, monkeypatch):
    info = _oidc_sign_in(browser, monkeypatch)

    def unreachable():
        raise oidc.OIDCUnavailable("down")

    for metadata in (lambda: {}, unreachable):
        monkeypatch.setattr(oidc, "metadata", metadata)
        response = browser.delete("/api/auth/session", headers={"X-Skein-CSRF": info["csrf_token"]})
        assert response.status_code == 200
        assert response.json() == {"logout_url": ""}
        info = _oidc_sign_in(browser, monkeypatch)


def test_oidc_exchange_rejects_foreign_redirect_before_contacting_provider(browser, monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "oidc")
    monkeypatch.setattr(config, "OIDC_CLIENT_ID", "skein-web")
    monkeypatch.setattr(oidc, "exchange", lambda _: pytest.fail("unapproved redirect reached IdP"))
    response = browser.post(
        "/api/auth/token",
        json={
            "code": "code",
            "code_verifier": "v" * 43,
            "redirect_uri": "https://evil.test/auth/callback",
        },
    )
    assert response.status_code == 400


def test_browser_refresh_token_input_is_refused(browser, monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "oidc")
    monkeypatch.setattr(config, "OIDC_CLIENT_ID", "skein-web")
    monkeypatch.setattr(
        oidc, "exchange", lambda _: pytest.fail("browser-owned refresh token accepted")
    )
    response = browser.post("/api/auth/token", json={"refresh_token": "must stay server-side"})
    assert response.status_code == 400


def test_composed_oidc_session_uses_its_app_mode_not_another_apps_global(fresh_db, monkeypatch):
    from dataclasses import replace

    from app.extensions.contracts import AppSettings
    from app.main import create_app

    monkeypatch.setattr(config, "CREDENTIAL_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(config, "OIDC_ISSUER", "https://idp.test")
    monkeypatch.setattr(config, "OIDC_AUDIENCE", "skein")
    monkeypatch.setattr(config, "OIDC_CLIENT_ID", "skein-web")
    monkeypatch.setattr(config, "AUTH_MODE", "trusted-header")
    claims = {
        "iss": "https://idp.test",
        "sub": "subject:ava",
        "aud": "skein",
        "exp": time.time() + 600,
        "preferred_username": "ava",
    }
    monkeypatch.setattr(oidc, "validate", lambda _: claims)
    monkeypatch.setattr(
        oidc, "exchange", lambda _: {"access_token": "test-access", "refresh_token": "test-refresh"}
    )
    settings = replace(AppSettings.from_config(), auth_mode="oidc", cors_origins=(ORIGIN,))
    with TestClient(
        create_app(settings=settings), base_url="https://api.test", headers={"Origin": ORIGIN}
    ) as browser:
        response = browser.post(
            "/api/auth/token",
            json={
                "code": "code",
                "code_verifier": "v" * 43,
                "redirect_uri": ORIGIN + "/auth/callback",
            },
        )
        assert response.status_code == 200, response.text
        monkeypatch.setattr(config, "AUTH_MODE", "api-key")
        assert browser.get("/api/auth/session").json()["authenticated"] is True


def test_cookie_authentication_precedes_atomic_transaction_and_runs_once(
    browser, monkeypatch, fresh_db
):
    from app import db

    info, _ = _login(browser)
    original = browser_sessions.authenticate
    calls = []

    def authenticate(*args, **kwargs):
        assert not db.in_transaction()
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(browser_sessions, "authenticate", authenticate)
    response = browser.post(
        "/api/promises",
        json={"promise": "session-bound"},
        headers={"X-Skein-CSRF": info["csrf_token"], "X-User": "bo"},
    )
    assert response.status_code == 200, response.text
    assert calls == [1]
    assert (
        fresh_db.query_one(
            "SELECT created_by FROM promises WHERE id = ?", (response.json()["id"],)
        )["created_by"]
        == "ava"
    )


def test_session_outage_is_retryable_and_never_clears_cookie(browser, monkeypatch):
    info, _ = _login(browser)

    def unavailable(*args, **kwargs):
        raise browser_sessions.SessionUnavailable(retry_after=1)

    monkeypatch.setattr(browser_sessions, "authenticate", unavailable)
    response = browser.get("/api/tasks", headers={"X-Skein-CSRF": info["csrf_token"]})
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert "set-cookie" not in response.headers
    assert browser.get("/api/auth/session").json()["authenticated"] is True


def test_real_key_that_is_also_shared_token_wins_over_cookie(browser, monkeypatch, fresh_db):
    key = _key("bo")
    info, _ = _login(browser, "ava")
    monkeypatch.setattr(config, "API_TOKEN", key)
    response = browser.post(
        "/api/tasks",
        json={"title": "explicit credential"},
        headers={"Authorization": f"Bearer {key}", "X-Skein-CSRF": info["csrf_token"]},
    )
    assert response.status_code == 200, response.text
    assert (
        fresh_db.query_one("SELECT created_by FROM tasks WHERE id = ?", (response.json()["id"],))[
            "created_by"
        ]
        == "bo"
    )


def test_unapproved_origin_cannot_read_session_metadata_or_logout(browser):
    info, _ = _login(browser)
    assert (
        browser.get("/api/auth/session", headers={"Origin": "https://evil.test"}).status_code == 403
    )
    response = browser.delete(
        "/api/auth/session",
        headers={"Origin": "https://evil.test", "X-Skein-CSRF": info["csrf_token"]},
    )
    assert response.status_code == 403 and "set-cookie" not in response.headers
    assert browser.get("/api/auth/session").json()["authenticated"] is True


def test_cookie_mcp_post_checks_the_actual_method(browser):
    info, _ = _login(browser)
    response = browser.post(
        "/api/mcp-server",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={
            "Origin": "",
            "X-Skein-CSRF": info["csrf_token"],
            "Accept": "application/json, text/event-stream",
        },
    )
    assert response.status_code == 403


def test_explicit_cors_origin_allows_credentialed_requests(fresh_db):
    from dataclasses import replace

    from app.extensions.contracts import AppSettings
    from app.main import create_app

    settings = replace(AppSettings.from_config(), cors_origins=(ORIGIN,))
    with TestClient(create_app(settings=settings)) as client:
        response = client.options(
            "/api/auth/session/key",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-skein-csrf",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == ORIGIN
        assert response.headers["access-control-allow-credentials"] == "true"
