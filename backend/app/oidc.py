"""In-process OIDC token validation (SKEIN_AUTH_MODE=oidc).

A caller presents an IdP-issued JWT as `Authorization: Bearer <token>`.
Validation is local: fetch the issuer's JWKS once (cached, refreshed on an
unknown kid), check the signature, then iss / aud / exp. No sidecar, no
session state, no per-request call to the IdP.

Asymmetric algorithms only. Accepting HS* would let anyone mint a valid
token by signing with the PUBLIC JWKS material as the HMAC secret — the
classic algorithm-confusion attack — so the allowlist below is a security
boundary, not a compatibility knob.
"""

import json
import threading
import urllib.request
from typing import Any

import jwt as pyjwt
from jwt import PyJWKClient

from . import config

ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"]


class OIDCError(Exception):
    """Token or key-fetch fault. The message is safe to return to the caller:
    it never carries the token, and validate() reduces PyJWT's own messages
    to the exception class name so a crafted token cannot reflect content."""


_lock = threading.Lock()
_client_cache: PyJWKClient | None = None


def _discover_jwks_url() -> str:
    url = f"{config.OIDC_ISSUER}/.well-known/openid-configuration"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 — operator-configured issuer
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
    global _client_cache
    with _lock:
        if _client_cache is None:
            url = config.OIDC_JWKS_URL or _discover_jwks_url()
            _client_cache = PyJWKClient(url, cache_keys=True, lifespan=3600)
        return _client_cache


def validate(token: str) -> dict[str, Any]:
    """Verified claims for a raw JWT. Raises OIDCError on any fault."""
    try:
        key = _client().get_signing_key_from_jwt(token)
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
    preferred_username, Entra sends upn, groups arrive under many names."""
    name = str(claims.get(config.OIDC_USERNAME_CLAIM) or "").strip()[:64]
    if not name:
        raise OIDCError(
            f"the sign-in token has no {config.OIDC_USERNAME_CLAIM!r} claim."
            " Set SKEIN_OIDC_USERNAME_CLAIM to a claim the identity provider sends."
        )
    raw = claims.get(config.OIDC_GROUPS_CLAIM)
    groups = [str(g) for g in raw] if isinstance(raw, list) else []
    return name, groups
