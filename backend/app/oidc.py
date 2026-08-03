"""In-process OIDC token validation (SKEIN_AUTH_MODE=oidc).

A caller presents an IdP-issued JWT as `Authorization: Bearer <token>`.
Validation is local: fetch the issuer's JWKS once (cached, refreshed on an
unknown kid), check the signature, then iss / aud / exp. No sidecar, no
session state, no per-request call to the IdP.

Asymmetric algorithms only. Accepting HS* would let anyone mint a valid
token by signing with the PUBLIC JWKS material as the HMAC secret — the
classic algorithm-confusion attack — so the allowlist below is a security
boundary, not a compatibility knob. `none` is absent for the same reason.

EVERY outbound fetch here is throttled, because the token is unverified
when the fetch is decided. A `kid` is read from the token header before any
signature is checked, and PyJWKClient refreshes its key set on any miss —
so without the throttles below, an unauthenticated caller sending random
kids turns each request into an outbound JWKS fetch, against both this
server and the identity provider.
"""

import json
import threading
import time
import urllib.request
from typing import Any

import jwt as pyjwt
from jwt import PyJWKClient

from . import config

ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"]

# Every network call here has the identity provider on the far end. The
# library default is 30s, long enough for one slow fetch to hold a worker.
FETCH_TIMEOUT = 5.0
# How long a signing-key set is trusted before a re-fetch.
KEY_LIFESPAN = 3600
# Shortest gap between unknown-kid refreshes. A key rotation is picked up
# within this window; a flood of forged kids costs one fetch per window.
REFRESH_COOLDOWN = 60.0
# How long a failed discovery is remembered. Without it, every request
# re-runs discovery while the IdP is down, and the outage becomes a
# self-inflicted load test.
DISCOVERY_COOLDOWN = 30.0


class OIDCError(Exception):
    """Token or key-fetch fault. The message is safe to return to the caller:
    it never carries the token, and validate() reduces PyJWT's own messages
    to the exception class name so a crafted token cannot reflect content."""


_lock = threading.Lock()
_client_cache: PyJWKClient | None = None
# -inf, not 0.0: time.monotonic() has an arbitrary origin, so 0.0 would
# block the first refresh on a machine that booted less than a cooldown ago.
_discovery_failed_at = float("-inf")
_last_refresh = float("-inf")


def reset() -> None:
    """Drop the cached client and both throttles. For tests, and for a
    deployment that changes issuer configuration without a restart."""
    global _client_cache, _discovery_failed_at, _last_refresh
    with _lock:
        _client_cache = None
        _discovery_failed_at = float("-inf")
        _last_refresh = float("-inf")


def _discover_jwks_url() -> str:
    url = f"{config.OIDC_ISSUER}/.well-known/openid-configuration"
    try:
        with urllib.request.urlopen(  # noqa: S310 — operator-configured issuer
            url, timeout=FETCH_TIMEOUT
        ) as resp:
            doc = json.load(resp)
    except Exception as exc:
        raise OIDCError(
            f"OIDC discovery failed ({exc.__class__.__name__})."
            " Check SKEIN_OIDC_ISSUER, or set SKEIN_OIDC_JWKS_URL directly."
        ) from exc
    jwks = str(doc.get("jwks_uri", "") or "")
    if not jwks:
        raise OIDCError(
            "the OIDC discovery document has no jwks_uri. Set SKEIN_OIDC_JWKS_URL directly."
        )
    return jwks


def _client() -> PyJWKClient:
    global _client_cache, _discovery_failed_at
    with _lock:
        if _client_cache is not None:
            return _client_cache
        if time.monotonic() - _discovery_failed_at < DISCOVERY_COOLDOWN:
            raise OIDCError("the identity provider cannot be reached. Try again in a minute.")
        try:
            url = config.OIDC_JWKS_URL or _discover_jwks_url()
        except OIDCError:
            _discovery_failed_at = time.monotonic()
            raise
        _client_cache = PyJWKClient(
            url, cache_keys=True, lifespan=KEY_LIFESPAN, timeout=FETCH_TIMEOUT
        )
        return _client_cache


def _may_refresh() -> bool:
    global _last_refresh
    with _lock:
        if time.monotonic() - _last_refresh < REFRESH_COOLDOWN:
            return False
        _last_refresh = time.monotonic()
        return True


def _signing_key(token: str) -> Any:
    """The JWKS key matching the token's kid.

    Deliberately NOT PyJWKClient.get_signing_key_from_jwt: that helper
    refreshes the key set on EVERY unknown kid, and the kid is attacker-
    controlled at this point. The lookup order is the same, the refresh is
    throttled.
    """
    try:
        kid = pyjwt.get_unverified_header(token).get("kid")
    except pyjwt.PyJWTError as exc:
        raise OIDCError(_refused(exc)) from exc
    if not kid:
        raise OIDCError("the sign-in token names no signing key. Sign in again.")
    client = _client()
    key = client.match_kid(client.get_signing_keys(), kid)
    if key is None and _may_refresh():
        key = client.match_kid(client.get_signing_keys(refresh=True), kid)
    if key is None:
        raise OIDCError("the sign-in token is signed by an unknown key. Sign in again.")
    return key


def validate(token: str) -> dict[str, Any]:
    """Verified claims for a raw JWT. Raises OIDCError on any fault."""
    try:
        key = _signing_key(token)
    except OIDCError:
        raise
    except pyjwt.PyJWTError as exc:
        raise OIDCError(_refused(exc)) from exc
    except Exception as exc:
        raise OIDCError(
            f"OIDC key fetch failed ({exc.__class__.__name__}). Check the JWKS endpoint."
        ) from exc
    try:
        return pyjwt.decode(
            token,
            key.key,
            algorithms=ALGORITHMS,
            audience=config.OIDC_AUDIENCE,
            issuer=config.OIDC_ISSUER,
            options={"require": ["exp", "iss", "aud"]},
        )
    except pyjwt.PyJWTError as exc:
        raise OIDCError(_refused(exc)) from exc


def _refused(exc: pyjwt.PyJWTError) -> str:
    return f"the sign-in token was refused ({exc.__class__.__name__}). Sign in again."


def principal(claims: dict[str, Any]) -> tuple[str, list[str]]:
    """(username, groups) from verified claims. The claim names are
    deployment config because IdPs disagree — Keycloak sends
    preferred_username, Entra sends upn, groups arrive under many names.

    The 64-character cut matches the roster's own limit (services/users.py),
    so an IdP with long usernames cannot mint a name no other surface holds.
    """
    name = str(claims.get(config.OIDC_USERNAME_CLAIM) or "").strip()[:64]
    if not name:
        raise OIDCError(
            f"the sign-in token has no {config.OIDC_USERNAME_CLAIM!r} claim."
            " Set SKEIN_OIDC_USERNAME_CLAIM to a claim the identity provider sends."
        )
    raw = claims.get(config.OIDC_GROUPS_CLAIM)
    if isinstance(raw, str):
        # some IdPs send a lone group as a bare string. Taking it whole is the
        # only reading that cannot invent a group: splitting on spaces would
        # turn "Domain Admins" into two.
        groups = [raw] if raw else []
    elif isinstance(raw, list):
        groups = [str(g) for g in raw]
    else:
        groups = []
    return name, groups
