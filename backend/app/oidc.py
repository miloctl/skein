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

That covers three fetches, not two. Discovery and the unknown-kid refresh
are the obvious ones; the third is the ORDINARY key-set read, which looks
cached until you notice PyJWKClient stores nothing when a fetch fails. A
cold or expired key set plus an unreachable endpoint means every request
carrying any bearer token pays another attempt, so it carries the same
cooldown the other two do.
"""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import jwt as pyjwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError

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


class OIDCUnavailable(OIDCError):
    """The identity provider could not be reached, so the token was never
    judged. Separate from OIDCError because the two need OPPOSITE answers:
    a refused token is 401 and the fix is to sign in again, while an
    unreachable provider is 503 and signing in again cannot work. Telling
    every signed-in person their token is bad during an IdP outage sends the
    whole team to a sign-in that is also down."""


class OIDCRefused(OIDCError):
    """The identity provider rejected the caller's own submission — a stale
    or replayed code, a mismatched redirect_uri. Caller input, so the route
    answers 4xx: a 5xx here would tell the browser to retry something that
    can never succeed, and page whoever is on call for a user's typo."""


# RLock, not Lock: _client() holds this while resolving the JWKS URL, and that
# path calls metadata(), which takes the lock again. A plain Lock deadlocks the
# first oidc request that has to discover — and it deadlocks a worker thread,
# so the symptom is a hang, not an error.
_lock = threading.RLock()
_client_cache: PyJWKClient | None = None
# -inf, not 0.0: time.monotonic() has an arbitrary origin, so 0.0 would
# block the first refresh on a machine that booted less than a cooldown ago.
_discovery_failed_at = float("-inf")
_jwks_failed_at = float("-inf")
_last_refresh = float("-inf")

# One condition, one wording: every path that gives up on reaching the IdP
# says this, so the caller cannot tell which fetch failed (and does not need to).
IDP_UNREACHABLE = "the identity provider cannot be reached. Try again in a minute."


_metadata_cache: dict[str, Any] | None = None


def reset() -> None:
    """Drop the cached client, discovery document and both throttles. For
    tests, and for a deployment that changes issuer configuration without a
    restart."""
    global _client_cache, _discovery_failed_at, _jwks_failed_at, _last_refresh, _metadata_cache
    with _lock:
        _client_cache = None
        _metadata_cache = None
        _discovery_failed_at = float("-inf")
        _jwks_failed_at = float("-inf")
        _last_refresh = float("-inf")


def _fetch_metadata() -> dict[str, Any]:
    url = f"{config.OIDC_ISSUER}/.well-known/openid-configuration"
    try:
        with urllib.request.urlopen(  # noqa: S310 — operator-configured issuer
            url, timeout=FETCH_TIMEOUT
        ) as resp:
            doc = json.load(resp)
    except Exception as exc:
        raise OIDCUnavailable(
            f"OIDC discovery failed ({exc.__class__.__name__})."
            " Check SKEIN_OIDC_ISSUER, or set the endpoint variables directly."
        ) from exc
    if not isinstance(doc, dict):
        raise OIDCError("the OIDC discovery document is not a JSON object.")
    return doc


def metadata() -> dict[str, Any]:
    """The issuer's discovery document, fetched once and reused.

    Shares the failure cooldown with the JWKS client: while the IdP is down,
    one attempt per DISCOVERY_COOLDOWN, not one per request."""
    global _metadata_cache, _discovery_failed_at
    with _lock:
        if _metadata_cache is not None:
            return _metadata_cache
        if time.monotonic() - _discovery_failed_at < DISCOVERY_COOLDOWN:
            raise OIDCUnavailable(IDP_UNREACHABLE)
        try:
            _metadata_cache = _fetch_metadata()
        except OIDCError:
            _discovery_failed_at = time.monotonic()
            raise
        return _metadata_cache


def _web_url(url: str, source: str) -> str:
    """Refuse anything that is not http(s).

    Endpoints arrive from the discovery document, which is REMOTE data: an
    issuer that is hostile or compromised could answer with file:// and turn
    a token request into a local file read. Operator config goes through the
    same gate — a typo deserves the same refusal."""
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise OIDCError(f"{source} is not an http(s) URL. Check the identity provider.")
    return url


def _endpoint(override: str, key: str, env_name: str) -> str:
    """A configured endpoint wins; otherwise discovery supplies it."""
    if override:
        return _web_url(override, env_name)
    url = str(metadata().get(key, "") or "")
    if not url:
        raise OIDCError(f"the OIDC discovery document has no {key}. Set {env_name} directly.")
    return _web_url(url, f"the discovery document's {key}")


def authorize_url() -> str:
    return _endpoint(
        config.OIDC_AUTHORIZE_URL, "authorization_endpoint", "SKEIN_OIDC_AUTHORIZE_URL"
    )


def token_url() -> str:
    return _endpoint(config.OIDC_TOKEN_URL, "token_endpoint", "SKEIN_OIDC_TOKEN_URL")


def exchange(form: dict[str, str]) -> dict[str, Any]:
    """Relay a token request to the IdP and return its JSON answer.

    The browser runs PKCE and keeps the verifier; this only carries the request
    across, so the web app stays a public client and the IdP never needs CORS
    for the app's origin. An IdP error is passed through as OIDCError with the
    IdP's own error CODE (never its description, which can echo the submitted
    value back into our response).
    """
    body = urllib.parse.urlencode(form).encode()
    request = urllib.request.Request(  # noqa: S310 — scheme checked by _web_url
        token_url(),
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(  # noqa: S310 — operator-configured issuer
            request, timeout=FETCH_TIMEOUT
        ) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = str(json.load(exc).get("error", "") or "")[:80]
        except Exception:
            detail = ""
        # OIDCRefused, not OIDCError: the provider judged the caller's own code
        # or redirect_uri and said no. That is 4xx input, not a server fault.
        raise OIDCRefused(
            f"the identity provider refused the sign-in{f' ({detail})' if detail else ''}."
            " Start the sign-in again."
        ) from exc
    except Exception as exc:
        raise OIDCUnavailable(
            f"the identity provider cannot be reached ({exc.__class__.__name__})."
            " Try again in a minute."
        ) from exc
    if not isinstance(payload, dict):
        raise OIDCError("the identity provider returned an unusable token response.")
    return payload


def _discover_jwks_url() -> str:
    jwks = str(metadata().get("jwks_uri", "") or "")
    if not jwks:
        raise OIDCError(
            "the OIDC discovery document has no jwks_uri. Set SKEIN_OIDC_JWKS_URL directly."
        )
    return _web_url(jwks, "the discovery document's jwks_uri")


def _client() -> PyJWKClient:
    global _client_cache, _discovery_failed_at
    with _lock:
        if _client_cache is not None:
            return _client_cache
        if time.monotonic() - _discovery_failed_at < DISCOVERY_COOLDOWN:
            raise OIDCUnavailable(IDP_UNREACHABLE)
        try:
            url = config.OIDC_JWKS_URL or _discover_jwks_url()
        except OIDCError:
            _discovery_failed_at = time.monotonic()
            raise
        _client_cache = PyJWKClient(
            url, cache_keys=True, lifespan=KEY_LIFESPAN, timeout=FETCH_TIMEOUT
        )
        return _client_cache


def _keys(refresh: bool = False) -> list[Any]:
    """The issuer's signing keys, with the same failure cooldown discovery has.

    PyJWKClient caches only SUCCESSFUL fetches, so a cold or expired key set
    plus an unreachable endpoint means every request that carries any bearer
    token pays another outbound attempt. The perimeter middleware reaches this
    BEFORE the caller is authenticated and runs it on the shared threadpool,
    so without this cooldown a signed-out flood both hammers a struggling IdP
    and starves the workers the rest of the API needs.
    """
    global _jwks_failed_at
    with _lock:
        if time.monotonic() - _jwks_failed_at < DISCOVERY_COOLDOWN:
            raise OIDCUnavailable(IDP_UNREACHABLE)
    try:
        return _client().get_signing_keys(refresh=refresh)
    except PyJWKClientError as exc:
        with _lock:
            _jwks_failed_at = time.monotonic()
        raise OIDCUnavailable(
            f"the identity provider signing keys cannot be read ({exc.__class__.__name__})."
            " Try again in a minute."
        ) from exc


def _may_refresh() -> bool:
    global _last_refresh
    with _lock:
        if time.monotonic() - _last_refresh < REFRESH_COOLDOWN:
            return False
        _last_refresh = time.monotonic()
        return True


def _kid(token: str) -> str:
    try:
        kid = pyjwt.get_unverified_header(token).get("kid")
    except pyjwt.PyJWTError as exc:
        raise OIDCError(_refused(exc)) from exc
    if not kid:
        raise OIDCError("the sign-in token names no signing key. Sign in again.")
    return kid


def _signing_key(token: str) -> Any:
    """The JWKS key matching the token's kid.

    Deliberately NOT PyJWKClient.get_signing_key_from_jwt: that helper
    refreshes the key set on EVERY unknown kid, and the kid is attacker-
    controlled at this point. The lookup order is the same, the refresh is
    throttled.
    """
    kid = _kid(token)
    client = _client()
    key = client.match_kid(_keys(), kid)
    if key is None and _may_refresh():
        key = client.match_kid(_keys(refresh=True), kid)
    if key is None:
        raise OIDCError("the sign-in token is signed by an unknown key. Sign in again.")
    return key


def _decode(token: str, key: Any) -> dict[str, Any]:
    return pyjwt.decode(
        token,
        key.key,
        algorithms=ALGORITHMS,
        audience=config.OIDC_AUDIENCE,
        issuer=config.OIDC_ISSUER,
        leeway=config.OIDC_LEEWAY,
        options={"require": ["exp", "iss", "aud"]},
    )


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
        return _decode(token, key)
    except pyjwt.InvalidSignatureError as exc:
        # a provider that rotates a key but KEEPS its kid leaves the cache
        # matching the kid with the stale key, and every sign-in fails until
        # the cache expires. One re-fetch heals that; it shares the unknown-
        # kid throttle because a forged signature triggers it just as easily.
        if not _may_refresh():
            raise OIDCError(_refused(exc)) from exc
        try:
            fresh = _client().match_kid(_keys(refresh=True), _kid(token))
        except OIDCError:
            raise
        except Exception as exc2:
            raise OIDCError(
                f"OIDC key fetch failed ({exc2.__class__.__name__}). Check the JWKS endpoint."
            ) from exc2
        if fresh is None:
            raise OIDCError(
                "the sign-in token is signed by an unknown key. Sign in again."
            ) from exc
        try:
            return _decode(token, fresh)
        except pyjwt.PyJWTError as exc2:
            raise OIDCError(_refused(exc2)) from exc2
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
