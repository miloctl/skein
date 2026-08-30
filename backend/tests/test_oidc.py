"""app/oidc.py validates IdP tokens locally: signature against the JWKS,
then iss / aud / exp. These tests sign real RS256 tokens with a generated
key and pin the refusals — including the HS256 algorithm-confusion attack,
where a forger HMAC-signs with the PUBLIC key material."""

import base64
import concurrent.futures
import hashlib
import hmac
import http.server
import io
import json
import threading
import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt.exceptions import PyJWKClientConnectionError

from app import config, oidc

ISS = "https://idp.test/realms/team"


def _start_server(handler):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@pytest.fixture(autouse=True)
def _clean_oidc():
    """app/oidc.py caches the JWKS client, the discovery document and two
    throttles at module level. Any of them surviving into the next test makes
    that test pass for the wrong reason — a discovery-failure test cannot fail
    if an earlier file already cached a working document."""
    oidc.reset()
    yield
    oidc.reset()


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


KID = "test-key-1"


class _Key:
    """The two attributes app/oidc.py reads off a PyJWK."""

    def __init__(self, key, kid, algorithm_name="RS256"):
        self.key = key
        self.key_id = kid
        self.algorithm_name = algorithm_name


class _Client:
    """Stands in for PyJWKClient with the exact surface _signing_key uses,
    and counts fetches so the unknown-kid throttle can be observed."""

    def __init__(self, key):
        self._keys = [_Key(key, KID)]
        self.fetches = 0

    def get_signing_keys(self, refresh=False):
        self.fetches += 1
        return self._keys

    def match_kid(self, keys, kid):
        return next((k for k in keys if k.key_id == kid), None)


_live: dict = {}


@pytest.fixture()
def issuer(monkeypatch, rsa_key):
    monkeypatch.setattr(config, "OIDC_ISSUER", ISS)
    monkeypatch.setattr(config, "OIDC_AUDIENCE", "skein")
    oidc.reset()  # the refresh throttle is module state — never leak it across tests
    _live["client"] = _Client(rsa_key.public_key())
    monkeypatch.setattr(oidc, "_client", lambda: _live["client"])
    yield rsa_key
    oidc.reset()


@pytest.fixture()
def jwks(issuer):
    return _live["client"]


def _token(key, kid=KID, algorithm="RS256", **over):
    claims = {
        "iss": ISS,
        "aud": "skein",
        "exp": int(time.time()) + 300,
        "sub": "casey-subject",
        "preferred_username": "casey",
        "groups": ["eng"],
    }
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not None}
    return pyjwt.encode(claims, key, algorithm=algorithm, headers={"kid": kid})


def test_valid_token_yields_claims(issuer):
    claims = oidc.validate(_token(issuer))
    assert claims["preferred_username"] == "casey"
    assert claims["groups"] == ["eng"]


def test_wrong_issuer_refused(issuer):
    with pytest.raises(oidc.OIDCError):
        oidc.validate(_token(issuer, iss="https://evil.test"))


def test_wrong_audience_refused(issuer):
    with pytest.raises(oidc.OIDCError):
        oidc.validate(_token(issuer, aud="other-app"))


def test_expired_token_refused(issuer):
    # expiry must clear the clock-skew leeway, or this pins nothing
    with pytest.raises(oidc.OIDCError) as e:
        oidc.validate(_token(issuer, exp=int(time.time()) - config.OIDC_LEEWAY - 60))
    # the refusal names the fault class, never the token itself
    assert "ExpiredSignatureError" in str(e.value)


def test_missing_exp_refused(issuer):
    with pytest.raises(oidc.OIDCError):
        oidc.validate(_token(issuer, exp=None))


def test_missing_subject_refused(issuer):
    with pytest.raises(oidc.OIDCError):
        oidc.validate(_token(issuer, sub=None))


def test_nbf_within_leeway_accepted(issuer):
    # PingFederate stamps nbf at issue time; a validator clock seconds behind
    # the IdP sees a token from the near future. Without leeway this refused
    # every fresh sign-in with ImmatureSignatureError.
    claims = oidc.validate(_token(issuer, nbf=int(time.time()) + 10))
    assert claims["preferred_username"] == "casey"


def test_nbf_beyond_leeway_refused(issuer):
    with pytest.raises(oidc.OIDCError) as e:
        oidc.validate(_token(issuer, nbf=int(time.time()) + config.OIDC_LEEWAY + 60))
    assert "ImmatureSignatureError" in str(e.value)


def test_tampered_signature_refused(issuer):
    good = _token(issuer)
    head, payload, sig = good.rsplit(".", 2)
    with pytest.raises(oidc.OIDCError):
        oidc.validate(f"{head}.{payload}.{sig[:-4]}AAAA")


def test_hs256_confusion_refused(issuer):
    # pyjwt itself refuses to HMAC-encode with PEM material, so forge by hand:
    # a token HMAC-signed with the PUBLIC key must never verify
    pub_pem = issuer.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )

    def b64(b: bytes) -> bytes:
        return base64.urlsafe_b64encode(b).rstrip(b"=")

    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(json.dumps({"iss": ISS, "aud": "skein", "exp": int(time.time()) + 300}).encode())
    sig = b64(hmac.new(pub_pem, head + b"." + body, hashlib.sha256).digest())
    forged = (head + b"." + body + b"." + sig).decode()
    with pytest.raises(oidc.OIDCError):
        oidc.validate(forged)


def test_none_is_not_an_accepted_algorithm():
    # the allowlist is a security boundary, not a compatibility knob
    assert "none" not in oidc.ALGORITHMS
    assert all(a[:2] != "HS" for a in oidc.ALGORITHMS)


def test_unknown_kid_refresh_is_throttled(jwks, issuer):
    """An attacker-chosen kid is read BEFORE any signature check, and
    PyJWKClient refreshes its key set on every miss. Without a throttle each
    forged kid becomes one outbound JWKS fetch, blocking a worker for the
    fetch timeout — an unauthenticated denial of service."""
    forged = _token(issuer, kid="attacker-chosen")
    with pytest.raises(oidc.OIDCError):
        oidc.validate(forged)
    after_first = jwks.fetches
    assert after_first == 2  # one cached lookup, one throttled refresh
    for _ in range(20):
        with pytest.raises(oidc.OIDCError):
            oidc.validate(forged)
    # 20 more forged kids cost lookups against the CACHED set only — the
    # refresh does not fire again inside the cooldown
    assert jwks.fetches == after_first + 20


def test_failed_unknown_kid_refresh_stays_unavailable(monkeypatch, issuer):
    client = _Client(issuer.public_key())
    refreshes = 0

    def fail_refresh(refresh=False):
        nonlocal refreshes
        if refresh:
            refreshes += 1
            raise PyJWKClientConnectionError("unreachable")
        return client._keys

    client.get_signing_keys = fail_refresh
    monkeypatch.setattr(oidc, "_client", lambda: client)
    token = _token(issuer, kid="rotated")
    for _ in range(2):
        with pytest.raises(oidc.OIDCUnavailable):
            oidc.validate(token)
    assert refreshes == 1


def test_key_refresh_follower_is_unavailable_not_refused(monkeypatch, issuer):
    entered = threading.Event()
    release = threading.Event()
    rotated = _Key(issuer.public_key(), "rotated")
    client = _Client(issuer.public_key())

    def keys(refresh=False):
        if not refresh:
            return client._keys
        entered.set()
        release.wait(timeout=2)
        return [rotated]

    client.get_signing_keys = keys
    monkeypatch.setattr(oidc, "_client", lambda: client)
    token = _token(issuer, kid="rotated")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        leader = pool.submit(oidc._signing_key, token)
        assert entered.wait(timeout=1)
        follower = pool.submit(oidc._signing_key, token)
        with pytest.raises(oidc.OIDCUnavailable):
            follower.result(timeout=1)
        release.set()
        assert leader.result(timeout=2).key_id == "rotated"


def test_token_without_a_kid_is_refused_without_a_fetch(jwks, issuer):
    headerless = pyjwt.encode({"iss": ISS, "aud": "skein"}, issuer, algorithm="RS256")
    with pytest.raises(oidc.OIDCError) as e:
        oidc.validate(headerless)
    assert "names no signing key" in str(e.value)
    assert jwks.fetches == 0


def test_a_rotated_key_is_picked_up_on_refresh(jwks, issuer):
    rotated = _Key(issuer.public_key(), "rotated-kid")

    def rotate(refresh=False):
        jwks.fetches += 1
        return [rotated] if refresh else jwks._keys

    jwks.get_signing_keys = rotate
    claims = oidc.validate(_token(issuer, kid="rotated-kid"))
    assert claims["preferred_username"] == "casey"


def test_external_plaintext_issuer_is_refused_before_discovery(monkeypatch):
    monkeypatch.setattr(config, "OIDC_ISSUER", "http://idp.example/realms/team")
    called = False

    def opened(*_args, **_kwargs):
        nonlocal called
        called = True
        return io.BytesIO(b"{}")

    monkeypatch.setattr(oidc, "_open", opened)
    with pytest.raises(oidc.OIDCError) as error:
        oidc.metadata()
    assert not called
    assert isinstance(error.value, oidc.OIDCUnavailable)
    assert "http://idp.example" not in str(error.value)


@pytest.mark.parametrize(
    "jwks_url",
    ("http://idp.example/jwks", "http://127.0.0.1:8610/jwks"),
)
def test_external_plaintext_jwks_override_is_refused(monkeypatch, jwks_url):
    monkeypatch.setattr(config, "OIDC_ISSUER", ISS)
    monkeypatch.setattr(config, "OIDC_JWKS_URL", jwks_url)
    with pytest.raises(oidc.OIDCError) as error:
        oidc._client()
    assert isinstance(error.value, oidc.OIDCUnavailable)
    assert jwks_url not in str(error.value)


def test_jwks_redirect_does_not_reach_another_origin(monkeypatch):
    source_calls: list[str] = []
    target_calls: list[str] = []

    class Target(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            target_calls.append(self.path)
            body = b'{"keys":[]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return None

    target, target_thread = _start_server(Target)

    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            source_calls.append(self.path)
            self.send_response(302)
            self.send_header(
                "Location",
                f"http://127.0.0.1:{target.server_address[1]}/stolen-jwks",
            )
            self.end_headers()

        def log_message(self, *_args):
            return None

    redirect, redirect_thread = _start_server(Redirect)
    try:
        monkeypatch.setattr(
            config,
            "OIDC_ISSUER",
            f"http://127.0.0.1:{redirect.server_address[1]}",
        )
        monkeypatch.setattr(
            config,
            "OIDC_JWKS_URL",
            f"http://127.0.0.1:{redirect.server_address[1]}/jwks",
        )
        with pytest.raises(oidc.OIDCProviderError):
            oidc._keys()
        with pytest.raises(oidc.OIDCUnavailable):
            oidc._keys()
        assert source_calls == ["/jwks"]
        assert target_calls == []
    finally:
        redirect.shutdown()
        target.shutdown()
        redirect.server_close()
        target.server_close()
        redirect_thread.join(timeout=2)
        target_thread.join(timeout=2)


def test_jwks_redirect_can_stay_on_the_same_origin(monkeypatch, rsa_key):
    key = json.loads(pyjwt.algorithms.RSAAlgorithm.to_jwk(rsa_key.public_key()))
    key.update({"kid": KID, "use": "sig", "alg": "RS256"})
    body = json.dumps({"keys": [key]}).encode()

    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/jwks":
                self.send_response(302)
                self.send_header("Location", "/final")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return None

    server, thread = _start_server(Redirect)
    try:
        monkeypatch.setattr(config, "OIDC_ISSUER", f"http://127.0.0.1:{server.server_address[1]}")
        monkeypatch.setattr(
            config,
            "OIDC_JWKS_URL",
            f"http://127.0.0.1:{server.server_address[1]}/jwks",
        )
        assert oidc._client().fetch_data() == {"keys": [key]}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_jwks_http_404_is_a_provider_error(monkeypatch):
    class Missing(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(404)
            self.end_headers()

        def log_message(self, *_args):
            return None

    server, thread = _start_server(Missing)
    try:
        monkeypatch.setattr(config, "OIDC_ISSUER", f"http://127.0.0.1:{server.server_address[1]}")
        monkeypatch.setattr(
            config,
            "OIDC_JWKS_URL",
            f"http://127.0.0.1:{server.server_address[1]}/jwks",
        )
        with pytest.raises(oidc.OIDCProviderError):
            oidc._keys()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    "body",
    (
        b"not-json",
        b'{"keys":"bad"}',
        b'{"keys":[null]}',
        b'{"keys":["bad"]}',
        b'{"keys":[1]}',
        b'{"keys":' + b"[" * 1_200 + b"0" + b"]" * 1_200 + b"}",
    ),
)
def test_malformed_jwks_is_not_cached_or_retried(monkeypatch, body):
    calls = 0

    class Malformed(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal calls
            calls += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return None

    server, thread = _start_server(Malformed)
    try:
        monkeypatch.setattr(config, "OIDC_ISSUER", f"http://127.0.0.1:{server.server_address[1]}")
        monkeypatch.setattr(
            config,
            "OIDC_JWKS_URL",
            f"http://127.0.0.1:{server.server_address[1]}/jwks",
        )
        with pytest.raises(oidc.OIDCProviderError):
            oidc._keys()
        with pytest.raises(oidc.OIDCUnavailable):
            oidc._keys()
        client = oidc._client()
        assert client.jwk_set_cache is not None
        assert client.jwk_set_cache.get() is None
        assert calls == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("variant", ("missing-kid", "encryption-only", "unsupported-algorithm"))
def test_non_signing_jwks_is_not_cached(monkeypatch, rsa_key, variant):
    good = json.loads(pyjwt.algorithms.RSAAlgorithm.to_jwk(rsa_key.public_key()))
    good.update({"kid": KID, "use": "sig", "alg": "RS256"})
    bad = dict(good)
    if variant == "missing-kid":
        bad.pop("kid")
    elif variant == "encryption-only":
        bad["use"] = "enc"
    else:
        bad = {
            "kty": "oct",
            "k": base64.urlsafe_b64encode(b"not-an-asymmetric-key").rstrip(b"=").decode(),
            "kid": KID,
            "use": "sig",
            "alg": "HS256",
        }
    bodies = [json.dumps({"keys": [bad]}).encode(), json.dumps({"keys": [good]}).encode()]
    calls = 0

    class Keys(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal calls
            body = bodies[min(calls, 1)]
            calls += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return None

    server, thread = _start_server(Keys)
    try:
        monkeypatch.setattr(config, "OIDC_ISSUER", f"http://127.0.0.1:{server.server_address[1]}")
        monkeypatch.setattr(
            config,
            "OIDC_JWKS_URL",
            f"http://127.0.0.1:{server.server_address[1]}/jwks",
        )
        with pytest.raises(oidc.OIDCProviderError):
            oidc._keys()
        client = oidc._client()
        assert client.jwk_set_cache is not None
        assert client.jwk_set_cache.get() is None
        monkeypatch.setattr(oidc, "_jwks_failed_at", float("-inf"))
        assert len(oidc._keys()) == 1
        assert calls == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_jwks_failure_is_single_flight(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    class Down(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal calls
            calls += 1
            entered.set()
            release.wait(timeout=2)
            self.send_response(503)
            self.end_headers()

        def log_message(self, *_args):
            return None

    server, thread = _start_server(Down)
    try:
        monkeypatch.setattr(config, "OIDC_ISSUER", f"http://127.0.0.1:{server.server_address[1]}")
        monkeypatch.setattr(
            config,
            "OIDC_JWKS_URL",
            f"http://127.0.0.1:{server.server_address[1]}/jwks",
        )

        def read_keys():
            with pytest.raises(oidc.OIDCUnavailable):
                oidc._keys()

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            first = pool.submit(read_keys)
            assert entered.wait(timeout=1)
            followers = [pool.submit(read_keys) for _ in range(5)]
            for follower in followers:
                follower.result(timeout=1)
            release.set()
            first.result(timeout=2)
        assert calls == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_concurrent_cached_jwks_reads_do_not_fail(monkeypatch, rsa_key):
    key = json.loads(pyjwt.algorithms.RSAAlgorithm.to_jwk(rsa_key.public_key()))
    key.update({"kid": KID, "use": "sig", "alg": "RS256"})
    body = json.dumps({"keys": [key]}).encode()
    calls = 0

    class Keys(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal calls
            calls += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return None

    server, thread = _start_server(Keys)
    try:
        monkeypatch.setattr(config, "OIDC_ISSUER", f"http://127.0.0.1:{server.server_address[1]}")
        monkeypatch.setattr(
            config,
            "OIDC_JWKS_URL",
            f"http://127.0.0.1:{server.server_address[1]}/jwks",
        )
        assert len(oidc._keys()) == 1
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _index: oidc._keys(), range(6)))
        assert all(len(result) == 1 for result in results)
        assert calls == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_failed_discovery_is_not_retried_on_every_request(monkeypatch):
    monkeypatch.setattr(config, "OIDC_ISSUER", ISS)
    monkeypatch.setattr(config, "OIDC_JWKS_URL", "")
    oidc.reset()
    calls = []

    def boom(url, timeout):
        calls.append(url)
        raise OSError("no route to host")

    monkeypatch.setattr(oidc, "_open", boom)
    for _ in range(5):
        with pytest.raises(oidc.OIDCError):
            oidc._client()
    # one attempt, then the cooldown answers — a down IdP must not turn every
    # request into an outbound connection attempt
    assert len(calls) == 1
    oidc.reset()


class _Down:
    """A JWKS endpoint that cannot be read. PyJWKClient raises this family for
    every fetch fault, and it caches nothing on failure."""

    def __init__(self):
        self.calls: list[bool] = []

    def get_signing_keys(self, refresh=False):
        self.calls.append(refresh)
        raise PyJWKClientConnectionError("unreachable")

    def match_kid(self, keys, kid):
        return None


def test_a_failing_jwks_fetch_is_not_retried_on_every_request(monkeypatch, issuer):
    """PyJWKClient caches only SUCCESSFUL fetches, so a cold key set plus an
    unreachable endpoint made every bearer token cost another outbound attempt.
    The perimeter middleware reaches this before the caller is authenticated
    and runs it on the shared threadpool, so an unauthenticated flood both
    hammered the IdP and starved the workers the rest of the API needs."""
    down = _Down()
    monkeypatch.setattr(oidc, "_client", lambda: down)
    token = _token(issuer)
    for _ in range(20):
        with pytest.raises(oidc.OIDCError):
            oidc.validate(token)
    assert len(down.calls) == 1  # one attempt, then the cooldown answers


def test_an_unreachable_jwks_is_not_reported_as_a_refused_token(monkeypatch, issuer):
    """PyJWKClientConnectionError subclasses PyJWTError, so it used to land in
    the same branch as a bad signature: every signed-in person was told their
    token was refused and to sign in again — at an identity provider that is
    the very thing that is down."""
    monkeypatch.setattr(oidc, "_client", lambda: _Down())
    with pytest.raises(oidc.OIDCUnavailable) as e:
        oidc.validate(_token(issuer))
    assert "Sign in again" not in str(e.value)
    assert "refused" not in str(e.value)
    # still the family every route already catches
    assert isinstance(e.value, oidc.OIDCError)


def test_a_genuinely_bad_token_is_still_refused_not_excused(issuer):
    """The other side of the split: a real token fault must NOT become a 503
    that tells the caller to wait for a provider that is answering fine."""
    with pytest.raises(oidc.OIDCError) as e:
        oidc.validate(_token(issuer, exp=int(time.time()) - config.OIDC_LEEWAY - 60))
    assert not isinstance(e.value, oidc.OIDCUnavailable)


def test_principal_maps_configured_claims(monkeypatch):
    monkeypatch.setattr(config, "OIDC_USERNAME_CLAIM", "upn")
    monkeypatch.setattr(config, "OIDC_GROUPS_CLAIM", "roles")
    name, groups = oidc.principal({"upn": "casey@corp", "roles": ["a", 1]})
    assert name == "casey@corp"
    assert groups == ["a", "1"]


def test_principal_requires_the_username_claim():
    with pytest.raises(oidc.OIDCError) as e:
        oidc.principal({"sub": "abc123"})
    assert "SKEIN_OIDC_USERNAME_CLAIM" in str(e.value)


def test_principal_reads_a_lone_string_groups_claim_whole():
    # some IdPs send a single group as a bare string. Splitting on spaces
    # would invent two groups out of "Domain Admins", so it is taken whole.
    name, groups = oidc.principal({"preferred_username": "casey", "groups": "Domain Admins"})
    assert name == "casey"
    assert groups == ["Domain Admins"]
    assert oidc.principal({"preferred_username": "c", "groups": {"a": 1}})[1] == []


def test_principal_refuses_a_username_over_the_roster_limit():
    with pytest.raises(oidc.OIDCError, match="longer than 64"):
        oidc.principal({"preferred_username": "x" * 200})


def test_identity_requires_the_verified_issuer_and_subject(monkeypatch):
    monkeypatch.setattr(config, "OIDC_ISSUER", ISS)
    assert oidc.identity({"iss": ISS, "sub": "casey-subject"}) == (
        ISS,
        "casey-subject",
    )
    with pytest.raises(oidc.OIDCError, match="subject"):
        oidc.identity({"iss": ISS})


def test_discovery_without_jwks_uri_is_a_config_error(monkeypatch):
    monkeypatch.setattr(config, "OIDC_ISSUER", ISS)
    monkeypatch.setattr(oidc, "_open", lambda url, timeout: io.BytesIO(b"{}"))
    with pytest.raises(oidc.OIDCError) as e:
        oidc._discover_jwks_url()
    assert "SKEIN_OIDC_JWKS_URL" in str(e.value)


def test_discovery_network_fault_is_reported(monkeypatch):
    monkeypatch.setattr(config, "OIDC_ISSUER", ISS)

    def boom(url, timeout):
        raise OSError("no route to host")

    monkeypatch.setattr(oidc, "_open", boom)
    with pytest.raises(oidc.OIDCError) as e:
        oidc._discover_jwks_url()
    assert "SKEIN_OIDC_ISSUER" in str(e.value)


def test_discovery_response_is_bounded_before_json_parsing(monkeypatch):
    requested: list[int] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size=-1):
            requested.append(size)
            return b"x" * max(0, size)

    monkeypatch.setattr(config, "OIDC_ISSUER", ISS)
    monkeypatch.setattr(oidc, "_open", lambda *_args, **_kwargs: Response())
    with pytest.raises(oidc.OIDCProviderError):
        oidc.metadata()
    assert requested == [oidc.MAX_RESPONSE_BYTES + 1]


def test_discovery_has_an_absolute_response_deadline(monkeypatch):
    from time import monotonic, sleep

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size=-1):
            for _ in range(10):
                sleep(0.04)
            return b"{}"

    monkeypatch.setattr(config, "OIDC_ISSUER", ISS)
    monkeypatch.setattr(oidc, "FETCH_TIMEOUT", 0.1)
    monkeypatch.setattr(oidc, "_open", lambda *_args, **_kwargs: Response())
    started = monotonic()
    with pytest.raises(oidc.OIDCUnavailable):
        oidc.metadata()
    assert monotonic() - started < 0.3


def test_a_rotation_that_keeps_the_kid_heals_on_refresh(jwks, issuer):
    """The kid matches the CACHED key, so the unknown-kid refresh never fires
    — before the signature-failure refresh, every sign-in failed for the
    cache lifetime after a same-kid rotation."""
    new_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def rotate(refresh=False):
        jwks.fetches += 1
        return [_Key(new_key.public_key(), KID)] if refresh else jwks._keys

    jwks.get_signing_keys = rotate
    claims = oidc.validate(_token(new_key))
    assert claims["preferred_username"] == "casey"
    assert jwks.fetches >= 2  # only the signature failure forced the second look


def test_same_kid_cross_family_rotation_refreshes_the_key(monkeypatch, issuer):
    ec_key = ec.generate_private_key(ec.SECP256R1())
    client = _Client(issuer.public_key())
    rotated = _Key(ec_key.public_key(), KID, "ES256")

    def rotate(refresh=False):
        client.fetches += 1
        return [rotated] if refresh else client._keys

    client.get_signing_keys = rotate
    monkeypatch.setattr(oidc, "_client", lambda: client)
    claims = oidc.validate(_token(ec_key, algorithm="ES256"))
    assert claims["preferred_username"] == "casey"
    assert client.fetches >= 2


def test_a_forged_signature_still_fails_after_the_refresh(jwks, issuer):
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(oidc.OIDCError):
        oidc.validate(_token(attacker))
    assert jwks.fetches >= 2  # it looked again, and refused again
