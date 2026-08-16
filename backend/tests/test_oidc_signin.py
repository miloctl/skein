"""Browser sign-in (authorization code + PKCE) — /api/auth/config and
/api/auth/token. Both answer before the caller holds any credential, so what
they refuse matters as much as what they return."""

import json
import urllib.error
import urllib.request

import pytest

from app import config, oidc


@pytest.fixture(autouse=True)
def _clean_oidc():
    oidc.reset()
    yield
    oidc.reset()


def _as_oidc(monkeypatch, **over):
    monkeypatch.setattr(config, "AUTH_MODE", "oidc")
    monkeypatch.setattr(config, "OIDC_ISSUER", "https://idp.test")
    monkeypatch.setattr(config, "OIDC_AUDIENCE", "skein")
    monkeypatch.setattr(config, "OIDC_CLIENT_ID", over.get("client_id", "skein-web"))
    monkeypatch.setattr(config, "OIDC_AUTHORIZE_URL", over.get("authorize", ""))
    monkeypatch.setattr(config, "OIDC_TOKEN_URL", over.get("token", ""))


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
        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(url, timeout=None):
        if calls is not None:
            calls.append(url)
        return _Resp()

    monkeypatch.setattr(oidc.urllib.request, "urlopen", fake)


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

    monkeypatch.setattr(oidc.urllib.request, "urlopen", boom)
    body = client.get("/api/auth/config").json()
    assert body["mode"] == "oidc"
    assert "discovery failed" in body["error"]


def test_discovery_is_fetched_once_for_both_the_jwks_and_the_endpoints(monkeypatch):
    """_client() resolves the JWKS URL while holding the module lock, and that
    path calls metadata(), which takes the lock again. With a plain Lock this
    hangs a worker thread instead of raising."""
    _as_oidc(monkeypatch)
    calls: list[str] = []
    _discovery(monkeypatch, calls=calls)
    assert oidc.token_url() == "https://idp.test/token"
    assert oidc._client() is not None  # would deadlock, not fail, if regressed
    assert len(calls) == 1  # one document serves both


def test_token_exchange_returns_a_validated_token(client, monkeypatch, fresh_db):
    _as_oidc(monkeypatch)
    _discovery(monkeypatch)
    sent = {}

    def fake_exchange(form):
        sent.update(form)
        return {"access_token": "opaque", "refresh_token": "r1", "expires_in": 300}

    monkeypatch.setattr(oidc, "exchange", fake_exchange)
    monkeypatch.setattr(oidc, "validate", lambda t: {"preferred_username": "casey"})
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
    monkeypatch.setattr(oidc, "validate", lambda token: {"preferred_username": name})

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
    from app.services import users

    _as_oidc(monkeypatch)
    _discovery(monkeypatch)
    users.ensure_human_identity("departed")
    users.set_active("departed", False, actor="ops")
    monkeypatch.setattr(oidc, "exchange", lambda form: {"access_token": "inactive"})
    monkeypatch.setattr(oidc, "validate", lambda token: {"preferred_username": "departed"})

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
    monkeypatch.setattr(oidc, "validate", lambda token: {"preferred_username": "FUTURE-OIDC"})

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
    monkeypatch.setattr(oidc, "validate", lambda t: {"preferred_username": "casey"})
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
    monkeypatch.setattr(oidc, "validate", lambda t: {"preferred_username": "casey"})
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
    monkeypatch.setattr(oidc, "validate", lambda t: {"preferred_username": "casey"})
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
        assert "http(s)" in str(e.value)


def test_a_non_web_endpoint_from_operator_config_is_refused(monkeypatch):
    _as_oidc(monkeypatch, token="file:///etc/shadow")
    with pytest.raises(oidc.OIDCError):
        oidc.token_url()


def test_idp_error_description_is_not_echoed_back(monkeypatch):
    """An IdP error_description can quote the submitted value. The code is
    useful to an operator; the description is the caller's own input coming
    back through us."""
    _as_oidc(monkeypatch, token="https://idp.test/token")

    def boom(request, timeout=None):
        raise urllib.error.HTTPError(
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

    monkeypatch.setattr(oidc.urllib.request, "urlopen", boom)
    with pytest.raises(oidc.OIDCError) as e:
        oidc.exchange({"grant_type": "refresh_token"})
    assert str(e.value) == "The identity provider refused the sign-in. Start the sign-in again."
    assert "invalid_grant" not in str(e.value)
    assert "SECRETVALUE" not in str(e.value)


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

        monkeypatch.setattr(oidc.urllib.request, "urlopen", boom)
        with pytest.raises(oidc.OIDCUnavailable):
            oidc.exchange({"grant_type": "refresh_token"})


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

    monkeypatch.setattr(oidc.urllib.request, "urlopen", boom)
    with pytest.raises(oidc.OIDCRefused):
        oidc.exchange({"grant_type": "authorization_code"})

    assert canary not in caplog.text
    assert "operator signed in" not in caplog.text
    assert "HTTP 400" in caplog.text
