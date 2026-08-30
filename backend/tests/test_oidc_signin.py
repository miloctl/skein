"""Browser sign-in (authorization code + PKCE) — /api/auth/config and
/api/auth/token. Both answer before the caller holds any credential, so what
they refuse matters as much as what they return."""

import concurrent.futures
import http.server
import io
import json
import threading
import urllib.error
import urllib.request

import pytest

from app import config, oidc


def _start_server(handler):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@pytest.fixture(autouse=True)
def _clean_oidc():
    oidc.reset()
    yield
    oidc.reset()


def _as_oidc(monkeypatch, **over):
    monkeypatch.setattr(config, "AUTH_MODE", "oidc")
    monkeypatch.setattr(config, "OIDC_ISSUER", over.get("issuer", "https://idp.test"))
    monkeypatch.setattr(config, "OIDC_AUDIENCE", "skein")
    monkeypatch.setattr(config, "OIDC_CLIENT_ID", over.get("client_id", "skein-web"))
    monkeypatch.setattr(config, "OIDC_AUTHORIZE_URL", over.get("authorize", ""))
    monkeypatch.setattr(config, "OIDC_TOKEN_URL", over.get("token", ""))


def _claims(name: str) -> dict[str, str]:
    return {
        "iss": config.OIDC_ISSUER,
        "sub": f"subject:{name}",
        "preferred_username": name,
    }


def _discovery(monkeypatch, doc=None, calls=None):
    body = json.dumps(
        doc
        if doc is not None
        else {
            "authorization_endpoint": "https://idp.test/auth",
            "token_endpoint": "https://idp.test/token",
            "jwks_uri": "https://idp.test/jwks",
        }
    ).encode()

    class _Resp:
        def __init__(self):
            self.payload = body

        def read(self, _size=-1):
            payload, self.payload = self.payload, b""
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(url, timeout=None):
        if calls is not None:
            calls.append(url)
        return _Resp()

    monkeypatch.setattr(oidc, "_open", fake)


def test_config_names_the_mode_in_every_mode(client):
    # the frontend has no other way to learn that the name picker is not the
    # identity model — this answers even in trusted-header mode
    body = client.get("/api/auth/config").json()
    assert body["mode"] == "trusted-header"
    assert body["error"] == ""
    assert "client_id" not in body


def test_config_carries_public_client_parameters(client, monkeypatch):
    _as_oidc(monkeypatch)
    _discovery(monkeypatch)
    body = client.get("/api/auth/config").json()
    assert body["mode"] == "oidc"
    assert body["client_id"] == "skein-web"
    assert body["authorize_url"] == "https://idp.test/auth"
    assert body["scopes"]
    # a client SECRET is not a concept here: the web app is a public client
    assert not any("secret" in k.lower() for k in body)


def test_config_is_reachable_without_any_credential(client, monkeypatch):
    """It is on the perimeter's open-path list on purpose: the sign-in flow
    runs before the caller has a credential. Everything else still 401s."""
    _as_oidc(monkeypatch)
    _discovery(monkeypatch)
    assert client.get("/api/auth/config").status_code == 200
    assert client.get("/api/tasks").status_code == 401


def test_config_says_so_when_browser_sign_in_is_not_configured(client, monkeypatch):
    _as_oidc(monkeypatch, client_id="")
    body = client.get("/api/auth/config").json()
    assert "SKEIN_OIDC_CLIENT_ID" in body["error"]
    assert "authorize_url" not in body


def test_config_reports_a_down_identity_provider_rather_than_failing(client, monkeypatch):
    _as_oidc(monkeypatch)

    def boom(url, timeout=None):
        raise OSError("no route to host")

    monkeypatch.setattr(oidc, "_open", boom)
    body = client.get("/api/auth/config").json()
    assert body["mode"] == "oidc"
    assert "discovery failed" in body["error"]


def test_discovery_closes_an_http_error(monkeypatch):
    _as_oidc(monkeypatch)
    failure = urllib.error.HTTPError(
        "https://idp.test/.well-known/openid-configuration",
        503,
        "unavailable",
        {},
        __import__("io").BytesIO(b"unavailable"),
    )
    monkeypatch.setattr(
        oidc,
        "_open",
        lambda _request, timeout=None: (_ for _ in ()).throw(failure),
    )

    with pytest.raises(oidc.OIDCUnavailable):
        oidc.metadata()
    assert failure.closed


def test_discovery_http_404_is_a_provider_error(monkeypatch):
    _as_oidc(monkeypatch)
    failure = urllib.error.HTTPError(
        "https://idp.test/.well-known/openid-configuration",
        404,
        "not found",
        {},
        io.BytesIO(b"not found"),
    )
    monkeypatch.setattr(
        oidc,
        "_open",
        lambda _request, timeout=None: (_ for _ in ()).throw(failure),
    )
    with pytest.raises(oidc.OIDCProviderError):
        oidc.metadata()
    assert failure.closed


def test_discovery_failure_is_single_flight(monkeypatch):
    _as_oidc(monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def slow():
        nonlocal calls
        calls += 1
        entered.set()
        release.wait(timeout=2)
        return {
            "authorization_endpoint": "https://idp.test/auth",
            "token_endpoint": "https://idp.test/token",
            "jwks_uri": "https://idp.test/jwks",
        }

    monkeypatch.setattr(oidc, "_fetch_metadata", slow)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        leader = pool.submit(oidc.metadata)
        assert entered.wait(timeout=1)
        followers = [pool.submit(oidc.metadata) for _ in range(4)]
        for follower in followers:
            with pytest.raises(oidc.OIDCUnavailable):
                follower.result(timeout=1)
        release.set()
        assert leader.result(timeout=2)["token_endpoint"] == "https://idp.test/token"
    assert calls == 1


def test_discovery_publishes_before_followers_can_start_another_fetch(monkeypatch):
    _as_oidc(monkeypatch)
    calls = 0
    follower_results: list[dict] = []

    def fetched():
        nonlocal calls
        calls += 1
        return {
            "authorization_endpoint": "https://idp.test/auth",
            "token_endpoint": "https://idp.test/token",
            "jwks_uri": "https://idp.test/jwks",
        }

    monkeypatch.setattr(oidc, "_fetch_metadata", fetched)
    real_lock = oidc._lock

    class HandoffLock:
        def __init__(self):
            self.entries = 0

        def __enter__(self):
            real_lock.acquire()
            self.entries += 1
            return self

        def __exit__(self, *_args):
            entry = self.entries
            real_lock.release()
            if entry == 2:
                follower = threading.Thread(
                    target=lambda: follower_results.append(oidc.metadata()),
                    daemon=True,
                )
                follower.start()
                follower.join(timeout=1)
                assert not follower.is_alive()

    monkeypatch.setattr(oidc, "_lock", HandoffLock())
    assert oidc.metadata()["token_endpoint"] == "https://idp.test/token"
    assert calls == 1
    assert follower_results[0]["jwks_uri"] == "https://idp.test/jwks"


def test_malformed_discovery_keeps_its_error_during_cooldown(monkeypatch):
    _as_oidc(monkeypatch)
    calls = 0

    def malformed():
        nonlocal calls
        calls += 1
        raise oidc.OIDCProviderError("The discovery document is unusable.")

    monkeypatch.setattr(oidc, "_fetch_metadata", malformed)
    for _ in range(3):
        with pytest.raises(oidc.OIDCProviderError) as error:
            oidc.metadata()
        assert str(error.value) == "The discovery document is unusable."
    assert calls == 1


def test_discovery_is_fetched_once_for_both_the_jwks_and_the_endpoints(monkeypatch):
    """One discovery document supplies the browser endpoints and JWKS URL."""
    _as_oidc(monkeypatch)
    calls: list[str] = []
    _discovery(monkeypatch, calls=calls)
    assert oidc.token_url() == "https://idp.test/token"
    assert oidc._client() is not None
    assert len(calls) == 1  # one document serves both


def test_token_exchange_returns_a_validated_token(client, monkeypatch, fresh_db):
    _as_oidc(monkeypatch)
    _discovery(monkeypatch)
    sent = {}

    def fake_exchange(form):
        sent.update(form)
        return {"access_token": "opaque", "refresh_token": "r1", "expires_in": 300}

    monkeypatch.setattr(oidc, "exchange", fake_exchange)
    monkeypatch.setattr(oidc, "validate", lambda t: _claims("casey"))
    out = client.post(
        "/api/auth/token",
        json={"code": "c", "code_verifier": "v", "redirect_uri": "http://app/cb"},
    )
    assert out.status_code == 200
    assert out.json()["user"] == "casey"
    assert out.json()["refresh_token"] == "r1"
    # PKCE is carried across, and no client secret is ever sent
    assert sent["grant_type"] == "authorization_code"
    assert sent["code_verifier"] == "v"
    assert sent["client_id"] == "skein-web"
    assert "client_secret" not in sent
    assert fresh_db.query_one("SELECT kind FROM users WHERE name = 'casey'") == {"kind": "human"}


@pytest.mark.parametrize("name", ["anonymous", "agent", "ci", "mcp", "system"])
def test_token_exchange_refuses_synthetic_and_core_identities(
    client, monkeypatch, fresh_db, name, caplog
):
    _as_oidc(monkeypatch)
    _discovery(monkeypatch)
    monkeypatch.setattr(oidc, "exchange", lambda form: {"access_token": "reserved"})
    monkeypatch.setattr(oidc, "validate", lambda token: _claims(name))

    response = client.post("/api/auth/token", json={"refresh_token": "r1"})

    assert response.status_code == 403
    assert response.json()["detail"] == oidc.SIGNIN_UNUSABLE
    assert name not in response.text
    assert name not in caplog.text
    row = fresh_db.query_one("SELECT kind FROM users WHERE name = ?", (name,))
    if name == "agent":
        assert row == {"kind": "agent"}  # application startup owns it
    else:
        assert row is None


def test_token_exchange_refuses_an_inactive_principal(client, monkeypatch, fresh_db):
    from app.routes.deps import INACTIVE
    from app.services import oidc_identities, users

    _as_oidc(monkeypatch)
    _discovery(monkeypatch)
    users.ensure_human_identity("departed")
    oidc_identities.bind_existing(config.OIDC_ISSUER, "subject:departed", "departed", actor="ops")
    users.set_active("departed", False, actor="ops")
    monkeypatch.setattr(oidc, "exchange", lambda form: {"access_token": "inactive"})
    monkeypatch.setattr(oidc, "validate", lambda token: _claims("departed"))

    response = client.post("/api/auth/token", json={"refresh_token": "r1"})

    assert response.status_code == 403
    assert response.json()["detail"] == INACTIVE


def test_token_exchange_cannot_claim_pending_content_identity(
    client, monkeypatch, fresh_db, tmp_path
):
    overlay = tmp_path / "personas"
    overlay.mkdir()
    monkeypatch.setattr(config, "PERSONAS_OVERLAY", overlay)
    (overlay / "future-oidc.md").write_text(
        "---\nname: Future OIDC\ndescription: Pending restart\n---\nWait for restart.\n"
    )
    _as_oidc(monkeypatch)
    _discovery(monkeypatch)
    monkeypatch.setattr(oidc, "exchange", lambda form: {"access_token": "pending"})
    monkeypatch.setattr(oidc, "validate", lambda token: _claims("FUTURE-OIDC"))

    response = client.post("/api/auth/token", json={"refresh_token": "r1"})

    assert response.status_code == 403
    assert response.json()["detail"] == oidc.SIGNIN_UNUSABLE
    assert "FUTURE-OIDC" not in response.text
    assert fresh_db.query_one("SELECT 1 FROM users WHERE name = 'FUTURE-OIDC'") is None


def test_token_refuses_a_token_it_cannot_validate(client, monkeypatch, fresh_db):
    """Answering 200 here would leave the browser holding a token that every
    later request rejects — a signed-in UI that 401s on everything."""
    _as_oidc(monkeypatch)
    _discovery(monkeypatch)
    monkeypatch.setattr(oidc, "exchange", lambda form: {"access_token": "bad"})
    monkeypatch.setattr(
        oidc, "validate", lambda t: (_ for _ in ()).throw(oidc.OIDCError("refused"))
    )
    r = client.post(
        "/api/auth/token",
        json={"code": "c", "code_verifier": "v", "redirect_uri": "http://app/cb"},
    )
    assert r.status_code == 502
    assert r.json()["detail"] == (
        "The identity provider returned an unusable sign-in response."
        " Ask whoever runs the server to check the server log. Then start the sign-in again."
    )
    assert "refused" not in r.text


def test_token_validation_log_uses_only_the_fault_class(client, monkeypatch, fresh_db, caplog):
    _as_oidc(monkeypatch)
    _discovery(monkeypatch)
    monkeypatch.setattr(oidc, "exchange", lambda form: {"access_token": "bad"})
    canary = "FORGED-LOG-LINE\noperator signed in"

    def refused(_token):
        try:
            raise ValueError(canary)
        except ValueError as exc:
            raise oidc.OIDCError("safe") from exc

    monkeypatch.setattr(oidc, "validate", refused)
    response = client.post("/api/auth/token", json={"refresh_token": "r1"})
    assert response.status_code == 502
    assert canary not in caplog.text
    assert "operator signed in" not in caplog.text
    assert "ValueError" in caplog.text


def test_missing_access_token_writes_a_safe_server_diagnostic(
    client, monkeypatch, fresh_db, caplog
):
    _as_oidc(monkeypatch)
    _discovery(monkeypatch)
    monkeypatch.setattr(oidc, "exchange", lambda form: {"refresh_token": "provider-value"})

    response = client.post("/api/auth/token", json={"refresh_token": "r1"})

    assert response.status_code == 502
    assert response.json()["detail"] == oidc.SIGNIN_UNUSABLE
    assert "omitted access_token" in caplog.text
    assert "provider-value" not in caplog.text


def test_token_accepts_a_refresh_token(client, monkeypatch, fresh_db):
    _as_oidc(monkeypatch)
    _discovery(monkeypatch)
    sent = {}
    monkeypatch.setattr(
        oidc, "exchange", lambda form: (sent.update(form), {"access_token": "a"})[1]
    )
    monkeypatch.setattr(oidc, "validate", lambda t: _claims("casey"))
    assert client.post("/api/auth/token", json={"refresh_token": "r1"}).status_code == 200
    assert sent["grant_type"] == "refresh_token"


def test_token_needs_a_complete_request(client, monkeypatch, fresh_db):
    _as_oidc(monkeypatch)
    _discovery(monkeypatch)
    # a code without its verifier is exactly the interception PKCE prevents
    r = client.post("/api/auth/token", json={"code": "c"})
    assert r.status_code == 400


def test_a_stale_code_is_the_callers_fault_not_a_server_fault(client, monkeypatch, fresh_db):
    """A code that expired while the tab sat open is caller input. A 5xx tells
    the browser to retry something that can never succeed, and pages whoever is
    on call for a person walking away from a sign-in."""
    _as_oidc(monkeypatch)
    _discovery(monkeypatch)

    def refused(form):
        raise oidc.OIDCRefused("the identity provider refused the sign-in (invalid_grant).")

    monkeypatch.setattr(oidc, "exchange", refused)
    r = client.post(
        "/api/auth/token",
        json={"code": "stale", "code_verifier": "v", "redirect_uri": "http://app/cb"},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == (
        "The identity provider refused the sign-in. Start the sign-in again."
    )
    assert "invalid_grant" not in r.text


def test_an_unreachable_provider_is_a_503_not_a_refusal(client, monkeypatch, fresh_db):
    _as_oidc(monkeypatch)
    _discovery(monkeypatch)

    def down(form):
        raise oidc.OIDCUnavailable("the identity provider cannot be reached.")

    monkeypatch.setattr(oidc, "exchange", down)
    r = client.post("/api/auth/token", json={"refresh_token": "r1"})
    assert r.status_code == 503
    assert r.json()["detail"] == (
        "Skein cannot reach the identity provider. Wait one minute, then start the sign-in again."
    )
    assert r.headers["Retry-After"] == "60"


def test_a_non_numeric_lifetime_does_not_become_a_400_quoting_it(client, monkeypatch, fresh_db):
    """expires_in is the IdP's own field. A junk value must not reach the
    error path, which would echo the provider's string back to the browser."""
    _as_oidc(monkeypatch)
    _discovery(monkeypatch)
    monkeypatch.setattr(
        oidc, "exchange", lambda form: {"access_token": "a", "expires_in": "soon-ish"}
    )
    monkeypatch.setattr(oidc, "validate", lambda t: _claims("casey"))
    r = client.post("/api/auth/token", json={"refresh_token": "r1"})
    assert r.status_code == 200
    assert r.json()["expires_in"] == 0
    assert "soon-ish" not in r.text


def test_token_is_off_outside_oidc_mode(client):
    r = client.post("/api/auth/token", json={"refresh_token": "r"})
    assert r.status_code == 404


def test_token_is_rate_capped_for_anonymous_callers(client, monkeypatch, fresh_db):
    """The one surface a signed-out caller can use to make the server call the
    identity provider. Uncapped, it is an amplifier pointed at the IdP."""
    _as_oidc(monkeypatch)
    _discovery(monkeypatch)
    monkeypatch.setattr(oidc, "exchange", lambda form: {"access_token": "a"})
    monkeypatch.setattr(oidc, "validate", lambda t: _claims("casey"))
    codes = {
        client.post("/api/auth/token", json={"refresh_token": "r"}).status_code for _ in range(15)
    }
    assert 429 in codes  # the cap answers before the IdP is called again


def test_a_non_web_endpoint_from_the_discovery_document_is_refused(monkeypatch):
    """The discovery document is REMOTE data. An issuer answering with a
    file:// token_endpoint would turn a sign-in into a local file read."""
    _as_oidc(monkeypatch)
    _discovery(
        monkeypatch,
        {
            "authorization_endpoint": "file:///etc/passwd",
            "token_endpoint": "file:///etc/shadow",
            "jwks_uri": "https://idp.test/jwks",
        },
    )
    for call in (oidc.token_url, oidc.authorize_url):
        with pytest.raises(oidc.OIDCError) as e:
            call()
        assert "HTTPS" in str(e.value)


def test_a_non_web_endpoint_from_operator_config_is_refused(monkeypatch):
    _as_oidc(monkeypatch, token="file:///etc/shadow")
    with pytest.raises(oidc.OIDCError):
        oidc.token_url()


@pytest.mark.parametrize(
    "url",
    (
        "http://idp.example/token",
        "http://localhost:8610/token",
        "https://localhost/token",
        "https://user:secret@idp.example/token",
        "https://:@idp.example/token",
        "https://idp.example/token#fragment",
        "https://idp.example/token#",
        "https:///missing-host",
        "https://idp.example:0/token",
        "https://idp.example:invalid/token",
        "https://%6c%6f%63%61%6c%68%6f%73%74/token",
        "https://localhost%3a8443/token",
        "https://idp.example%3a0/token",
        "https://localhost。/token",
        "https://ｌｏｃａｌｈｏｓｔ/token",  # noqa: RUF001 — intentional IDNA-confusable host
        "https://foo.localhost/token",
    ),
)
def test_an_unsafe_identity_provider_endpoint_is_refused(url):
    with pytest.raises(oidc.OIDCError) as error:
        oidc._web_url(url, "the test endpoint")
    assert url not in str(error.value)


@pytest.mark.parametrize(
    "url",
    (
        "http://127.0.0.1:8610/token",
        "http://127.12.1.1/token",
        "http://[::1]:8610/token",
        "https://idp.example/token",
    ),
)
def test_https_and_literal_loopback_endpoints_are_accepted(url):
    assert (
        oidc._web_url(
            url,
            "the test endpoint",
            allow_loopback=url.startswith("http://"),
        )
        == url
    )


def test_external_issuer_cannot_select_a_loopback_http_endpoint(monkeypatch):
    _as_oidc(monkeypatch)
    _discovery(
        monkeypatch,
        {
            "authorization_endpoint": "http://127.0.0.1:8610/authorize",
            "token_endpoint": "http://127.0.0.1:8610/token",
            "jwks_uri": "http://127.0.0.1:8610/jwks",
        },
    )
    for call in (oidc.authorize_url, oidc.token_url, oidc._discover_jwks_url):
        with pytest.raises(oidc.OIDCError):
            call()


def test_server_side_redirects_stay_on_the_identity_provider_origin():
    assert (
        oidc._redirect_url(
            "https://idp.example/token",
            "https://idp.example/renewed-token",
        )
        == "https://idp.example/renewed-token"
    )
    assert (
        oidc._redirect_url(
            "https://bücher.example/token",
            "https://xn--bcher-kva.example/renewed-token",
        )
        == "https://xn--bcher-kva.example/renewed-token"
    )
    assert (
        oidc._redirect_url(
            "http://[::1]/token",
            "http://[0:0:0:0:0:0:0:1]:80/renewed-token",
        )
        == "http://[0:0:0:0:0:0:0:1]:80/renewed-token"
    )
    for target in (
        "https://other.example/token",
        "http://idp.example/token",
        "file:///etc/passwd",
    ):
        with pytest.raises(oidc.OIDCError) as error:
            oidc._redirect_url("https://idp.example/token", target)
        assert target not in str(error.value)

    response = io.BytesIO()
    request = urllib.request.Request("https://idp.example/token", data=b"grant=x")
    with pytest.raises(oidc.OIDCError):
        oidc._SameOriginRedirect().redirect_request(
            request,
            response,
            302,
            "Found",
            {},
            "https://other.example/token",
        )
    assert response.closed


def test_token_redirect_does_not_reach_another_origin(monkeypatch):
    target_calls: list[str] = []

    class Target(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            target_calls.append(self.path)
            body = b'{"access_token":"redirected"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return None

    target, target_thread = _start_server(Target)

    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(307)
            self.send_header(
                "Location",
                f"http://127.0.0.1:{target.server_address[1]}/stolen",
            )
            self.end_headers()

        def log_message(self, *_args):
            return None

    redirect, redirect_thread = _start_server(Redirect)
    try:
        _as_oidc(
            monkeypatch,
            issuer=f"http://127.0.0.1:{redirect.server_address[1]}",
            token=f"http://127.0.0.1:{redirect.server_address[1]}/token",
        )
        with pytest.raises(oidc.OIDCError):
            oidc.exchange({"grant_type": "refresh_token"})
        assert target_calls == []
    finally:
        redirect.shutdown()
        target.shutdown()
        redirect.server_close()
        target.server_close()
        redirect_thread.join(timeout=2)
        target_thread.join(timeout=2)


def test_token_redirect_can_stay_on_the_same_origin(monkeypatch):
    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path == "/token":
                self.send_response(307)
                self.send_header("Location", "/final")
                self.end_headers()
                return
            body = b'{"access_token":"same-origin"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return None

    server, thread = _start_server(Redirect)
    try:
        _as_oidc(
            monkeypatch,
            issuer=f"http://127.0.0.1:{server.server_address[1]}",
            token=f"http://127.0.0.1:{server.server_address[1]}/token",
        )
        assert oidc.exchange({"grant_type": "refresh_token"}) == {"access_token": "same-origin"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_loopback_token_exchange_bypasses_proxies(monkeypatch):
    proxy_calls: list[bytes] = []
    target_calls = 0

    class Proxy(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            proxy_calls.append(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            body = b'{"access_token":"proxy"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return None

    proxy, proxy_thread = _start_server(Proxy)

    class Target(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            nonlocal target_calls
            target_calls += 1
            body = b'{"access_token":"target"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return None

    target, target_thread = _start_server(Target)
    regular = oidc.urllib.request.build_opener(
        oidc.urllib.request.ProxyHandler({"http": f"http://127.0.0.1:{proxy.server_address[1]}"}),
        oidc._SameOriginRedirect(),
    )
    monkeypatch.setattr(oidc, "_OPENER", regular)
    try:
        _as_oidc(
            monkeypatch,
            issuer=f"http://127.0.0.1:{target.server_address[1]}",
            token=f"http://127.0.0.1:{target.server_address[1]}/token",
        )
        assert oidc.exchange({"refresh_token": "secret"}) == {"access_token": "target"}
        assert target_calls == 1
        assert proxy_calls == []
    finally:
        target.shutdown()
        proxy.shutdown()
        target.server_close()
        proxy.server_close()
        target_thread.join(timeout=2)
        proxy_thread.join(timeout=2)


def test_token_post_does_not_follow_a_method_changing_redirect(monkeypatch):
    reached = False

    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(302)
            self.send_header("Location", "/final")
            self.end_headers()

        def do_GET(self):
            nonlocal reached
            reached = True
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            return None

    server, thread = _start_server(Redirect)
    try:
        _as_oidc(
            monkeypatch,
            issuer=f"http://127.0.0.1:{server.server_address[1]}",
            token=f"http://127.0.0.1:{server.server_address[1]}/token",
        )
        with pytest.raises(oidc.OIDCError) as error:
            oidc.exchange({"grant_type": "refresh_token"})
        assert not isinstance(error.value, oidc.OIDCRefused)
        assert not reached
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_idp_error_description_is_not_echoed_back(monkeypatch):
    """An IdP error_description can quote the submitted value. The code is
    useful to an operator; the description is the caller's own input coming
    back through us."""
    _as_oidc(monkeypatch, token="https://idp.test/token")

    failure = urllib.error.HTTPError(
        "https://idp.test/token",
        400,
        "Bad Request",
        {},
        __import__("io").BytesIO(
            json.dumps(
                {"error": "invalid_grant", "error_description": "SECRETVALUE was wrong"}
            ).encode()
        ),
    )

    def boom(request, timeout=None):
        raise failure

    monkeypatch.setattr(oidc, "_open", boom)
    with pytest.raises(oidc.OIDCError) as e:
        oidc.exchange({"grant_type": "refresh_token"})
    assert str(e.value) == "The identity provider refused the sign-in. Start the sign-in again."
    assert "invalid_grant" not in str(e.value)
    assert "SECRETVALUE" not in str(e.value)
    assert failure.closed


def test_malformed_successful_token_response_is_a_502(client, monkeypatch, fresh_db):
    _as_oidc(monkeypatch, token="https://idp.test/token")
    monkeypatch.setattr(
        oidc,
        "_open",
        lambda _request, timeout=None: io.BytesIO(b"not-json"),
    )
    response = client.post("/api/auth/token", json={"refresh_token": "r1"})
    assert response.status_code == 502
    assert response.json()["detail"] == oidc.SIGNIN_UNUSABLE


def test_token_endpoint_transient_http_errors_are_unavailable(monkeypatch):
    _as_oidc(monkeypatch, token="https://idp.test/token")

    for status in (429, 503):

        def boom(request, timeout=None, status=status):
            raise urllib.error.HTTPError(
                "https://idp.test/token",
                status,
                "temporary",
                {},
                __import__("io").BytesIO(b'{"error":"temporarily_unavailable"}'),
            )

        monkeypatch.setattr(oidc, "_open", boom)
        with pytest.raises(oidc.OIDCUnavailable):
            oidc.exchange({"grant_type": "refresh_token"})


def test_transient_oauth_code_is_unavailable_on_http_400(monkeypatch):
    _as_oidc(monkeypatch, token="https://idp.test/token")
    failure = urllib.error.HTTPError(
        "https://idp.test/token",
        400,
        "Bad Request",
        {},
        __import__("io").BytesIO(b'{"error":"temporarily_unavailable"}'),
    )
    monkeypatch.setattr(
        oidc,
        "_open",
        lambda _request, timeout=None: (_ for _ in ()).throw(failure),
    )
    with pytest.raises(oidc.OIDCUnavailable):
        oidc.exchange({"grant_type": "refresh_token"})


def test_provider_configuration_error_is_not_a_caller_refusal(monkeypatch):
    _as_oidc(monkeypatch, token="https://idp.test/token")
    failure = urllib.error.HTTPError(
        "https://idp.test/token",
        400,
        "Bad Request",
        {},
        __import__("io").BytesIO(b'{"error":"invalid_client"}'),
    )
    monkeypatch.setattr(
        oidc,
        "_open",
        lambda _request, timeout=None: (_ for _ in ()).throw(failure),
    )
    with pytest.raises(oidc.OIDCError) as error:
        oidc.exchange({"grant_type": "refresh_token"})
    assert not isinstance(error.value, (oidc.OIDCRefused, oidc.OIDCUnavailable))


def test_token_endpoint_log_allows_only_standard_oauth_error_codes(monkeypatch, caplog):
    _as_oidc(monkeypatch, token="https://idp.test/token")
    canary = "FORGED-LOG-LINE\noperator signed in"

    def boom(request, timeout=None):
        raise urllib.error.HTTPError(
            "https://idp.test/token",
            400,
            "Bad Request",
            {},
            __import__("io").BytesIO(json.dumps({"error": canary}).encode()),
        )

    monkeypatch.setattr(oidc, "_open", boom)
    with pytest.raises(oidc.OIDCError) as error:
        oidc.exchange({"grant_type": "authorization_code"})
    assert not isinstance(error.value, oidc.OIDCRefused)

    assert canary not in caplog.text
    assert "operator signed in" not in caplog.text
    assert "HTTP 400" in caplog.text
