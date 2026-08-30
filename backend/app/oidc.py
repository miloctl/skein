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

import contextlib
import http.client
import ipaddress
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import jwt as pyjwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError

from . import config

ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"]
OAUTH_ERROR_CODES = frozenset(
    {
        "access_denied",
        "invalid_client",
        "invalid_grant",
        "invalid_request",
        "invalid_scope",
        "server_error",
        "temporarily_unavailable",
        "unauthorized_client",
        "unsupported_grant_type",
    }
)
CALLER_OAUTH_ERRORS = frozenset({"access_denied", "invalid_grant", "invalid_request"})
TRANSIENT_OAUTH_ERRORS = frozenset({"server_error", "temporarily_unavailable"})
TRANSIENT_HTTP_STATUSES = frozenset({408, 425, 429})
SIGNIN_REFUSED = "The identity provider refused the sign-in. Start the sign-in again."
SIGNIN_UNAVAILABLE = (
    "Skein cannot reach the identity provider. Wait one minute, then start the sign-in again."
)
SIGNIN_UNUSABLE = (
    "The identity provider returned an unusable sign-in response."
    " Ask whoever runs the server to check the server log. Then start the sign-in again."
)
log = logging.getLogger("skein")

# Every network call here has the identity provider on the far end. The
# library default is 30s, long enough for one slow fetch to hold a worker.
FETCH_TIMEOUT = 5.0
MAX_RESPONSE_BYTES = 256 * 1024
# urllib cannot stop a slow-header read after its inactivity timeout resets.
# Fixed daemon slots keep timed-out provider calls out of the app worker pool.
_FETCH_SLOTS = threading.BoundedSemaphore(4)
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


class OIDCProviderError(OIDCError):
    """The provider returned an unusable response, so the server answers 502."""


class OIDCRefused(OIDCError):
    """The identity provider rejected the caller's own submission — a stale
    or replayed code, a mismatched redirect_uri. Caller input, so the route
    answers 4xx: a 5xx here would tell the browser to retry something that
    can never succeed, and page whoever is on call for a user's typo."""


class _ProviderHTTPError(Exception):
    def __init__(self, status: int, oauth_error: str = "") -> None:
        super().__init__(f"HTTP {status}")
        self.status = status
        self.oauth_error = oauth_error


def _read_bounded_json(response: Any, deadline: float) -> Any:
    raw = bytearray()
    read = getattr(response, "read1", response.read)
    while len(raw) <= MAX_RESPONSE_BYTES:
        if time.monotonic() >= deadline:
            raise TimeoutError("The identity provider response timed out.")
        chunk = read(MAX_RESPONSE_BYTES + 1 - len(raw))
        if time.monotonic() >= deadline:
            raise TimeoutError("The identity provider response timed out.")
        if not chunk:
            break
        raw.extend(chunk)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("The identity provider response is too large.")
    return json.loads(raw)


def _fetch_json(request: Any, timeout: float, *, read_oauth_error: bool = False) -> Any:
    deadline = time.monotonic() + timeout
    remaining = max(0.0, deadline - time.monotonic())
    if not _FETCH_SLOTS.acquire(timeout=remaining):
        raise TimeoutError("The identity provider response timed out.")
    done = threading.Event()
    values: list[Any] = []
    errors: list[BaseException] = []

    def fetch() -> None:
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("The identity provider response timed out.")
            try:
                with _open(request, timeout=remaining) as response:
                    values.append(_read_bounded_json(response, deadline))
            except urllib.error.HTTPError as exc:
                oauth_error = ""
                try:
                    if read_oauth_error:
                        with contextlib.suppress(Exception):
                            payload = _read_bounded_json(exc, deadline)
                            if isinstance(payload, dict):
                                oauth_error = str(payload.get("error", "") or "")
                finally:
                    exc.close()
                raise _ProviderHTTPError(exc.code, oauth_error) from exc
        except BaseException as exc:
            errors.append(exc)
        finally:
            done.set()
            _FETCH_SLOTS.release()

    try:
        threading.Thread(target=fetch, name="oidc-provider", daemon=True).start()
    except BaseException:
        _FETCH_SLOTS.release()
        raise
    if not done.wait(max(0.0, deadline - time.monotonic())):
        raise TimeoutError("The identity provider response timed out.")
    if errors:
        raise errors[0]
    return values[0]


# This lock protects cache and fetch state only. Network calls run outside it,
# so a slow identity provider cannot fill the worker pool with waiting followers.
_lock = threading.Lock()
_client_cache: PyJWKClient | None = None
# -inf, not 0.0: time.monotonic() has an arbitrary origin, so 0.0 would
# block the first refresh on a machine that booted less than a cooldown ago.
_discovery_failed_at = float("-inf")
_discovery_fetching = False
_discovery_failure: tuple[type[OIDCError], str] | None = None
_jwks_failed_at = float("-inf")
_jwks_fetching = False
_key_refreshing = False
_last_refresh = float("-inf")

# One condition, one wording: every path that gives up on reaching the IdP
# says this, so the caller cannot tell which fetch failed (and does not need to).
IDP_UNREACHABLE = "the identity provider cannot be reached. Try again in a minute."


_metadata_cache: dict[str, Any] | None = None


def reset() -> None:
    """Drop the cached client, discovery document and both throttles. For
    tests, and for a deployment that changes issuer configuration without a
    restart."""
    global \
        _client_cache, \
        _discovery_failed_at, \
        _discovery_fetching, \
        _discovery_failure, \
        _jwks_failed_at, \
        _jwks_fetching, \
        _key_refreshing, \
        _last_refresh, \
        _metadata_cache
    with _lock:
        _client_cache = None
        _metadata_cache = None
        _discovery_failed_at = float("-inf")
        _discovery_fetching = False
        _discovery_failure = None
        _jwks_failed_at = float("-inf")
        _jwks_fetching = False
        _key_refreshing = False
        _last_refresh = float("-inf")


def _record_discovery_failure(exc: OIDCError) -> None:
    global _discovery_failed_at, _discovery_failure
    with _lock:
        _discovery_failed_at = time.monotonic()
        _discovery_failure = (type(exc), str(exc))


def _fetch_metadata() -> dict[str, Any]:
    issuer = _issuer_url()
    url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
    try:
        doc = _fetch_json(url, FETCH_TIMEOUT)
    except _ProviderHTTPError as exc:
        if exc.status not in TRANSIENT_HTTP_STATUSES and exc.status < 500:
            raise OIDCProviderError(
                "The identity provider returned an unusable discovery response."
            ) from exc
        raise OIDCUnavailable(
            "OIDC discovery failed (HTTP error)."
            " Check SKEIN_OIDC_ISSUER, or set the endpoint variables directly."
        ) from exc
    except OIDCUnavailable:
        raise
    except OIDCError as exc:
        raise OIDCProviderError(
            "The identity provider returned an unusable discovery document."
        ) from exc
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        http.client.HTTPException,
    ) as exc:
        if isinstance(exc, urllib.error.HTTPError):
            exc.close()
            if exc.code not in TRANSIENT_HTTP_STATUSES and exc.code < 500:
                raise OIDCProviderError(
                    "The identity provider returned an unusable discovery response."
                ) from exc
        raise OIDCUnavailable(
            f"OIDC discovery failed ({exc.__class__.__name__})."
            " Check SKEIN_OIDC_ISSUER, or set the endpoint variables directly."
        ) from exc
    except Exception as exc:
        raise OIDCProviderError(
            "The identity provider returned an unusable discovery document."
        ) from exc
    if not isinstance(doc, dict):
        raise OIDCProviderError("The identity provider returned an unusable discovery document.")
    return doc


def metadata() -> dict[str, Any]:
    """The issuer's discovery document, with one in-flight fetch and cooldown."""
    global _metadata_cache, _discovery_fetching
    with _lock:
        if _metadata_cache is not None:
            return _metadata_cache
        if _discovery_fetching:
            raise OIDCUnavailable(IDP_UNREACHABLE)
        failure = (
            _discovery_failure
            if time.monotonic() - _discovery_failed_at < DISCOVERY_COOLDOWN
            else None
        )
        if failure is None:
            _discovery_fetching = True
    if failure is not None:
        error_type, message = failure
        raise error_type(message)
    try:
        document = _fetch_metadata()
    except (OIDCProviderError, OIDCUnavailable) as exc:
        _record_discovery_failure(exc)
        with _lock:
            _discovery_fetching = False
        raise
    except Exception:
        with _lock:
            _discovery_fetching = False
        raise
    with _lock:
        _metadata_cache = document
        _discovery_fetching = False
        return _metadata_cache


def _normalized_host(hostname: str) -> str:
    try:
        return ipaddress.ip_address(hostname).compressed
    except ValueError:
        try:
            return hostname.encode("idna").decode("ascii").lower().rstrip(".")
        except UnicodeError as exc:
            raise OIDCError(
                "Skein rejected the identity provider host. Check the identity provider."
            ) from exc


def _web_url(url: str, source: str, *, allow_loopback: bool = False) -> str:
    """Require HTTPS, with an explicit literal-loopback exception for tests.

    Endpoints arrive from the discovery document, which is REMOTE data. A
    hostile issuer must not select plaintext transport, local files, embedded
    credentials, or malformed ports. Hostnames do not inherit the loopback
    exception through DNS."""
    try:
        parsed = urllib.parse.urlparse(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise OIDCError(
            f"Skein rejected the URL from {source}. Use HTTPS or literal loopback HTTP. Check the identity provider."
        ) from exc
    normalized = _normalized_host(str(hostname)) if hostname else ""
    authority_ok = bool(
        hostname
        and "%" not in parsed.netloc
        and parsed.username is None
        and parsed.password is None
        and "#" not in url
        and port != 0
        and normalized != "localhost"
        and not normalized.endswith(".localhost")
    )
    safe = authority_ok and parsed.scheme == "https"
    if allow_loopback and authority_ok and parsed.scheme == "http":
        with contextlib.suppress(ValueError):
            safe = ipaddress.ip_address(normalized).is_loopback
    if not safe:
        raise OIDCError(
            f"Skein rejected the URL from {source}. Use HTTPS or literal loopback HTTP. Check the identity provider."
        )
    return url


def _issuer_url() -> str:
    try:
        return _web_url(config.OIDC_ISSUER, "SKEIN_OIDC_ISSUER", allow_loopback=True)
    except OIDCError as exc:
        raise OIDCUnavailable(
            "The identity provider configuration is invalid. Check SKEIN_OIDC_ISSUER."
        ) from exc


def issuer() -> str:
    """The validated issuer used for token checks and subject bindings."""
    return _issuer_url()


def _issuer_allows_loopback() -> bool:
    return urllib.parse.urlparse(_issuer_url()).scheme == "http"


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urllib.parse.urlparse(
        _web_url(url, "the identity provider endpoint", allow_loopback=True)
    )
    normalized = _normalized_host(str(parsed.hostname))
    port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
    return parsed.scheme, normalized, port


def _redirect_url(source: str, target: str) -> str:
    source = _web_url(
        source,
        "the identity provider endpoint",
        allow_loopback=True,
    )
    redirected = urllib.parse.urljoin(source, target)
    _web_url(
        redirected,
        "the identity provider redirect",
        allow_loopback=urllib.parse.urlparse(source).scheme == "http",
    )
    if _origin(source) != _origin(redirected):
        raise OIDCError(
            "The identity provider redirect changed the origin. Check the identity provider."
        )
    return redirected


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        try:
            redirected = _redirect_url(req.full_url, newurl)
            if req.get_method() == "POST":
                if code not in (307, 308):
                    raise OIDCError(
                        "The identity provider returned an unusable token redirect."
                        " Check the identity provider."
                    )
                return urllib.request.Request(  # noqa: S310 — redirected passed _redirect_url
                    redirected,
                    data=req.data,
                    headers=dict(req.headers),
                    origin_req_host=req.origin_req_host,
                    unverifiable=True,
                    method="POST",
                )
            return super().redirect_request(
                req,
                fp,
                code,
                msg,
                headers,
                redirected,
            )
        except OIDCError:
            with contextlib.suppress(Exception):
                fp.close()
            raise


_OPENER = urllib.request.build_opener(_SameOriginRedirect())
_DIRECT_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    _SameOriginRedirect(),
)


def _open(request, timeout: float):
    url = request.full_url if isinstance(request, urllib.request.Request) else str(request)
    safe = _web_url(url, "the identity provider endpoint", allow_loopback=True)
    opener = _DIRECT_OPENER if urllib.parse.urlparse(safe).scheme == "http" else _OPENER
    return opener.open(request, timeout=timeout)


def _endpoint(override: str, key: str, env_name: str) -> str:
    """A configured endpoint wins; otherwise discovery supplies it."""
    if override:
        return _web_url(
            override,
            env_name,
            allow_loopback=_issuer_allows_loopback(),
        )
    url = str(metadata().get(key, "") or "")
    if not url:
        raise OIDCError(f"the OIDC discovery document has no {key}. Set {env_name} directly.")
    return _web_url(
        url,
        f"the discovery document's {key}",
        allow_loopback=_issuer_allows_loopback(),
    )


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
    for the app's origin. Only standard OAuth error codes reach the operator
    log. Provider descriptions and unknown codes can contain submitted values
    or forged log lines, so neither leaves this function.
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
        payload = _fetch_json(request, FETCH_TIMEOUT, read_oauth_error=True)
    except OIDCError:
        raise
    except _ProviderHTTPError as exc:
        code = exc.oauth_error if exc.oauth_error in OAUTH_ERROR_CODES else ""
        diagnostic = code or f"HTTP {exc.status}"
        if (
            exc.status in TRANSIENT_HTTP_STATUSES
            or exc.status >= 500
            or code in TRANSIENT_OAUTH_ERRORS
        ):
            log.warning("identity provider token exchange unavailable (%s)", diagnostic)
            raise OIDCUnavailable(SIGNIN_UNAVAILABLE) from exc
        if code in CALLER_OAUTH_ERRORS:
            log.warning("identity provider refused token exchange (%s)", diagnostic)
            raise OIDCRefused(SIGNIN_REFUSED) from exc
        log.warning("identity provider token exchange was unusable (%s)", diagnostic)
        raise OIDCProviderError(SIGNIN_UNUSABLE) from exc
    except (RecursionError, TypeError, ValueError) as exc:
        raise OIDCProviderError(SIGNIN_UNUSABLE) from exc
    except Exception as exc:
        raise OIDCUnavailable(
            f"the identity provider cannot be reached ({exc.__class__.__name__})."
            " Try again in a minute."
        ) from exc
    if not isinstance(payload, dict):
        raise OIDCProviderError("The identity provider returned an unusable token response.")
    return payload


def _discover_jwks_url() -> str:
    jwks = str(metadata().get("jwks_uri", "") or "")
    if not jwks:
        raise OIDCError(
            "the OIDC discovery document has no jwks_uri. Set SKEIN_OIDC_JWKS_URL directly."
        )
    return _web_url(
        jwks,
        "the discovery document's jwks_uri",
        allow_loopback=_issuer_allows_loopback(),
    )


class _SafeJWKClient(PyJWKClient):
    def fetch_data(self) -> Any:
        global _jwks_failed_at, _jwks_fetching
        with _lock:
            if _jwks_fetching or time.monotonic() - _jwks_failed_at < DISCOVERY_COOLDOWN:
                raise OIDCUnavailable(IDP_UNREACHABLE)
            _jwks_fetching = True
        try:
            request = urllib.request.Request(  # noqa: S310 — _client validates transport; _open blocks cross-origin redirects
                url=self.uri,
                headers=self.headers,
            )
            try:
                jwk_set = _fetch_json(request, self.timeout)
            except _ProviderHTTPError as exc:
                with _lock:
                    _jwks_failed_at = time.monotonic()
                if exc.status not in TRANSIENT_HTTP_STATUSES and exc.status < 500:
                    raise OIDCProviderError(
                        "The identity provider returned an unusable signing-key response."
                    ) from exc
                raise PyJWKClientConnectionError(
                    "The identity provider signing keys could not be read."
                ) from exc
            except OIDCError as exc:
                with _lock:
                    _jwks_failed_at = time.monotonic()
                raise OIDCProviderError(
                    "The identity provider returned an unusable signing-key response."
                ) from exc
            except (
                urllib.error.URLError,
                TimeoutError,
                OSError,
                http.client.HTTPException,
            ) as exc:
                if isinstance(exc, urllib.error.HTTPError):
                    exc.close()
                    if exc.code not in TRANSIENT_HTTP_STATUSES and exc.code < 500:
                        with _lock:
                            _jwks_failed_at = time.monotonic()
                        raise OIDCProviderError(
                            "The identity provider returned an unusable signing-key response."
                        ) from exc
                with _lock:
                    _jwks_failed_at = time.monotonic()
                raise PyJWKClientConnectionError(
                    "The identity provider signing keys could not be read."
                ) from exc
            except Exception as exc:
                with _lock:
                    _jwks_failed_at = time.monotonic()
                raise OIDCProviderError(
                    "The identity provider returned an unusable signing-key response."
                ) from exc
            try:
                if not isinstance(jwk_set, dict):
                    raise ValueError("not an object")
                keys = jwk_set.get("keys")
                if not isinstance(keys, list) or not all(isinstance(key, dict) for key in keys):
                    raise ValueError("invalid keys")
                parsed = pyjwt.PyJWKSet.from_dict(jwk_set)
                if not any(
                    key.key_id
                    and key.public_key_use in ("sig", None)
                    and key.algorithm_name in ALGORITHMS
                    for key in parsed.keys
                ):
                    raise ValueError("no signing keys")
            except Exception as exc:
                with _lock:
                    _jwks_failed_at = time.monotonic()
                raise OIDCProviderError(
                    "The identity provider returned an unusable signing-key response."
                ) from exc
            if self.jwk_set_cache is not None:
                # PyJWT fetch_data stores this raw dict despite its PyJWKSet annotation.
                self.jwk_set_cache.put(jwk_set)  # type: ignore[arg-type]
            with _lock:
                _jwks_failed_at = float("-inf")
            return jwk_set
        finally:
            with _lock:
                _jwks_fetching = False


def _client() -> PyJWKClient:
    global _client_cache, _discovery_failed_at
    with _lock:
        if _client_cache is not None:
            return _client_cache
        failure = (
            _discovery_failure
            if time.monotonic() - _discovery_failed_at < DISCOVERY_COOLDOWN
            else None
        )
    if failure is not None:
        error_type, message = failure
        raise error_type(message)
    try:
        issuer = _issuer_url()
        url = (
            _web_url(
                config.OIDC_JWKS_URL,
                "SKEIN_OIDC_JWKS_URL",
                allow_loopback=urllib.parse.urlparse(issuer).scheme == "http",
            )
            if config.OIDC_JWKS_URL
            else _discover_jwks_url()
        )
    except OIDCProviderError:
        raise
    except OIDCUnavailable as exc:
        _record_discovery_failure(exc)
        raise
    except OIDCError as exc:
        unavailable = OIDCUnavailable(
            "The identity provider configuration is invalid. Check the JWKS endpoint."
        )
        _record_discovery_failure(unavailable)
        raise unavailable from exc
    candidate = _SafeJWKClient(url, cache_keys=True, lifespan=KEY_LIFESPAN, timeout=FETCH_TIMEOUT)
    with _lock:
        if _client_cache is None:
            _client_cache = candidate
        return _client_cache


def _keys(refresh: bool = False) -> list[Any]:
    """The issuer's signing keys, with one network fetch and one cooldown."""
    global _jwks_failed_at
    client = _client()
    cache = getattr(client, "jwk_set_cache", None)
    cached = cache.get() if cache is not None else None
    if refresh or cached is None:
        with _lock:
            if time.monotonic() - _jwks_failed_at < DISCOVERY_COOLDOWN:
                raise OIDCUnavailable(IDP_UNREACHABLE)
    try:
        return client.get_signing_keys(refresh=refresh)
    except OIDCProviderError:
        with _lock:
            _jwks_failed_at = time.monotonic()
        raise
    except OIDCUnavailable:
        raise
    except (OIDCError, PyJWKClientError, pyjwt.PyJWTError, TypeError, ValueError) as exc:
        with _lock:
            _jwks_failed_at = time.monotonic()
        raise OIDCUnavailable(
            f"The identity provider signing keys cannot be read ({exc.__class__.__name__})."
            " Try again in a minute."
        ) from exc


def _recent_jwks_failure() -> bool:
    with _lock:
        return time.monotonic() - _jwks_failed_at < REFRESH_COOLDOWN


def _claim_key_refresh() -> bool:
    global _key_refreshing, _last_refresh
    with _lock:
        if _key_refreshing:
            raise OIDCUnavailable(IDP_UNREACHABLE)
        if time.monotonic() - _last_refresh < REFRESH_COOLDOWN:
            return False
        _key_refreshing = True
        _last_refresh = time.monotonic()
        return True


def _finish_key_refresh() -> None:
    global _key_refreshing
    with _lock:
        _key_refreshing = False


def _kid(token: str) -> str:
    try:
        kid = pyjwt.get_unverified_header(token).get("kid")
    except pyjwt.PyJWTError as exc:
        raise OIDCError(_refused(exc)) from exc
    if not kid:
        raise OIDCError("the sign-in token names no signing key. Sign in again.")
    return kid


def _match_key(keys: list[Any], kid: str) -> Any | None:
    return next(
        (key for key in keys if key.key_id == kid and key.algorithm_name in ALGORITHMS),
        None,
    )


def _signing_key(token: str) -> Any:
    """The JWKS key matching the token's kid.

    Deliberately NOT PyJWKClient.get_signing_key_from_jwt: that helper
    refreshes the key set on EVERY unknown kid, and the kid is attacker-
    controlled at this point. The lookup order is the same, the refresh is
    throttled.
    """
    kid = _kid(token)
    key = _match_key(_keys(), kid)
    if key is None:
        claimed = _claim_key_refresh()
        if claimed:
            try:
                key = _match_key(_keys(refresh=True), kid)
            finally:
                _finish_key_refresh()
        elif _recent_jwks_failure():
            raise OIDCUnavailable(IDP_UNREACHABLE)
    if key is None:
        raise OIDCError("the sign-in token is signed by an unknown key. Sign in again.")
    return key


def _decode(token: str, key: Any) -> dict[str, Any]:
    return pyjwt.decode(
        token,
        key.key,
        algorithms=ALGORITHMS,
        audience=config.OIDC_AUDIENCE,
        issuer=_issuer_url(),
        leeway=config.OIDC_LEEWAY,
        options={"require": ["exp", "iss", "aud", "sub"]},
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
    except (pyjwt.InvalidSignatureError, pyjwt.InvalidKeyError, TypeError) as exc:
        # a provider that rotates a key but KEEPS its kid leaves the cache
        # matching the kid with the stale key, and every sign-in fails until
        # the cache expires. One re-fetch heals that; it shares the unknown-
        # kid throttle because a forged signature triggers it just as easily.
        claimed = _claim_key_refresh()
        if not claimed:
            if _recent_jwks_failure():
                raise OIDCUnavailable(IDP_UNREACHABLE) from exc
            raise OIDCError(_refused(exc)) from exc
        try:
            fresh = _match_key(_keys(refresh=True), _kid(token))
        except OIDCError:
            raise
        except Exception as exc2:
            raise OIDCError(
                f"OIDC key fetch failed ({exc2.__class__.__name__}). Check the JWKS endpoint."
            ) from exc2
        finally:
            _finish_key_refresh()
        if fresh is None:
            raise OIDCError(
                "the sign-in token is signed by an unknown key. Sign in again."
            ) from exc
        try:
            return _decode(token, fresh)
        except (pyjwt.PyJWTError, TypeError) as exc2:
            raise OIDCError(_refused(exc2)) from exc2
    except pyjwt.PyJWTError as exc:
        raise OIDCError(_refused(exc)) from exc


def _refused(exc: Exception) -> str:
    return f"the sign-in token was refused ({exc.__class__.__name__}). Sign in again."


def identity(claims: dict[str, Any]) -> tuple[str, str]:
    """The immutable issuer and subject from verified claims."""
    issuer = claims.get("iss")
    subject = claims.get("sub")
    if not isinstance(issuer, str) or issuer != _issuer_url():
        raise OIDCError("the sign-in token has no valid issuer. Sign in again.")
    if (
        not isinstance(subject, str)
        or not subject.strip()
        or len(subject) > 255
        or not subject.isprintable()
    ):
        raise OIDCError("the sign-in token has no valid subject. Sign in again.")
    return issuer, subject


def principal(claims: dict[str, Any]) -> tuple[str, list[str]]:
    """(username, groups) from verified claims. The claim names are
    deployment config because IdPs disagree — Keycloak sends
    preferred_username, Entra sends upn, groups arrive under many names.
    """
    raw_name = claims.get(config.OIDC_USERNAME_CLAIM)
    name = raw_name.strip() if isinstance(raw_name, str) else ""
    if not name:
        raise OIDCError(
            f"the sign-in token has no {config.OIDC_USERNAME_CLAIM!r} claim."
            " Set SKEIN_OIDC_USERNAME_CLAIM to a claim the identity provider sends."
        )
    if len(name) > 64:
        raise OIDCError(
            "the sign-in token user name is longer than 64 characters."
            " Set SKEIN_OIDC_USERNAME_CLAIM to a shorter claim."
        )
    if not name.isprintable():
        raise OIDCError("the sign-in token user name has control characters. Sign in again.")
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
