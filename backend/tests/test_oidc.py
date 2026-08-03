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

from app import config, oidc

ISS = "https://idp.test/realms/team"


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture()
def issuer(monkeypatch, rsa_key):
    monkeypatch.setattr(config, "OIDC_ISSUER", ISS)
    monkeypatch.setattr(config, "OIDC_AUDIENCE", "skein")
    pub = rsa_key.public_key()

    class _Key:
        key = pub

    class _Client:
        def get_signing_key_from_jwt(self, token):
            return _Key()

    monkeypatch.setattr(oidc, "_client", lambda: _Client())
    return rsa_key


def _token(key, **over):
    claims = {
        "iss": ISS,
        "aud": "skein",
        "exp": int(time.time()) + 300,
        "preferred_username": "casey",
        "groups": ["eng"],
    }
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not None}
    return pyjwt.encode(claims, key, algorithm="RS256")


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
    with pytest.raises(oidc.OIDCError) as e:
        oidc.validate(_token(issuer, exp=int(time.time()) - 10))
    # the refusal names the fault class, never the token itself
    assert "ExpiredSignatureError" in str(e.value)


def test_missing_exp_refused(issuer):
    with pytest.raises(oidc.OIDCError):
        oidc.validate(_token(issuer, exp=None))


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


def test_principal_tolerates_a_non_list_groups_claim():
    name, groups = oidc.principal({"preferred_username": "casey", "groups": "eng"})
    assert name == "casey"
    assert groups == []


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
