"""app/oidc.py validates IdP tokens locally: signature against the JWKS,
then iss / aud / exp. These tests sign real RS256 tokens with a generated
key and pin the refusals — including the HS256 algorithm-confusion attack,
where a forger HMAC-signs with the PUBLIC key material."""

import base64
import hashlib
import hmac
import io
import json
import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.exceptions import PyJWKClientConnectionError

from app import config, oidc

ISS = "https://idp.test/realms/team"


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

    def __init__(self, key, kid):
        self.key = key
        self.key_id = kid


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


def _token(key, kid=KID, **over):
    claims = {
        "iss": ISS,
        "aud": "skein",
        "exp": int(time.time()) + 300,
        "preferred_username": "casey",
        "groups": ["eng"],
    }
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not None}
    return pyjwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


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


def test_failed_discovery_is_not_retried_on_every_request(monkeypatch):
    monkeypatch.setattr(config, "OIDC_ISSUER", ISS)
    monkeypatch.setattr(config, "OIDC_JWKS_URL", "")
    oidc.reset()
    calls = []

    def boom(url, timeout):
        calls.append(url)
        raise OSError("no route to host")

    monkeypatch.setattr(oidc.urllib.request, "urlopen", boom)
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


def test_principal_truncates_a_long_username_to_the_roster_limit():
    name, _ = oidc.principal({"preferred_username": "x" * 200})
    assert len(name) == 64


def test_discovery_without_jwks_uri_is_a_config_error(monkeypatch):
    monkeypatch.setattr(config, "OIDC_ISSUER", ISS)
    monkeypatch.setattr(oidc.urllib.request, "urlopen", lambda url, timeout: io.BytesIO(b"{}"))
    with pytest.raises(oidc.OIDCError) as e:
        oidc._discover_jwks_url()
    assert "SKEIN_OIDC_JWKS_URL" in str(e.value)


def test_discovery_network_fault_is_reported(monkeypatch):
    monkeypatch.setattr(config, "OIDC_ISSUER", ISS)

    def boom(url, timeout):
        raise OSError("no route to host")

    monkeypatch.setattr(oidc.urllib.request, "urlopen", boom)
    with pytest.raises(oidc.OIDCError) as e:
        oidc._discover_jwks_url()
    assert "SKEIN_OIDC_ISSUER" in str(e.value)


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


def test_a_forged_signature_still_fails_after_the_refresh(jwks, issuer):
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(oidc.OIDCError):
        oidc.validate(_token(attacker))
    assert jwks.fetches >= 2  # it looked again, and refused again
