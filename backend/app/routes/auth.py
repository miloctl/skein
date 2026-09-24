"""Browser sign-in establishes an opaque server session, never browser-held tokens."""

import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import config, oidc, ratelimit
from ..extensions.contracts import AppSettings
from ..public.errors import PublicError
from ..services import browser_sessions, credentials

router = APIRouter(prefix="/api/auth")
log = logging.getLogger("skein")
CSRF_HEADER = "X-Skein-CSRF"


def auth_settings(request: Request) -> AppSettings:
    if request.app.state.skein_explicit_settings:
        return request.app.state.skein_settings
    return AppSettings.from_config()


def _origin(value: str) -> str:
    try:
        # urlsplit removes tabs/newlines and empty query fragments. An Origin
        # header must not acquire an approved meaning through that cleanup.
        if (
            any(ord(char) <= 32 or ord(char) == 127 for char in value)
            or "?" in value
            or "#" in value
        ):
            return ""
        parts = urlsplit(value)
        if (
            value != value.strip()
            or parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.path
            or parts.query
            or parts.fragment
        ):
            return ""
        host = parts.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        port = parts.port
        if port == 0:
            return ""
        suffix = (
            f":{port}"
            if port is not None and port != (443 if parts.scheme == "https" else 80)
            else ""
        )
        return f"{parts.scheme}://{host}{suffix}"
    except ValueError:
        return ""


def browser_session_error(settings: AppSettings) -> str:
    if "*" in settings.cors_origins:
        return "Browser sign-in needs explicit origins. Set SKEIN_CORS_ORIGINS without a wildcard."
    return ""


def require_browser_origin(request: Request, *, required: bool = True) -> str:
    settings = auth_settings(request)
    if error := browser_session_error(settings):
        raise PublicError("BROWSER_SESSION_UNAVAILABLE", error, status_code=503)
    raw = request.headers.get("origin", "")
    if not raw and not required:
        return ""
    origin = _origin(raw)
    allowed = {_origin(value) for value in settings.cors_origins}
    # A same-origin API needs no CORS grant. Forwarded host/protocol headers
    # are not used: an arbitrary caller must not create an approved origin.
    allowed.add(_origin(f"{request.url.scheme}://{request.url.netloc}"))
    if not origin or origin not in allowed:
        raise PublicError(
            "BROWSER_ORIGIN_DENIED",
            "This browser origin is not allowed. Open Skein at its configured address.",
            status_code=403,
        )
    return origin


def _fault_class(exc: BaseException) -> str:
    cause = exc.__cause__
    return type(cause).__name__ if cause is not None else type(exc).__name__


@router.get("/config")
def get_auth_config(request: Request):
    settings = auth_settings(request)
    mode = settings.auth_mode if settings.auth_mode in config.AUTH_MODES else "invalid"
    out: dict = {"mode": mode, "error": settings.auth_error}
    if settings.auth_error:
        return out
    out["browser_session_error"] = browser_session_error(settings)
    if settings.auth_mode != "oidc":
        return out
    out["client_id"] = config.OIDC_CLIENT_ID
    out["scopes"] = config.OIDC_SCOPES
    if not credentials.available():
        out["browser_session_error"] = (
            "Browser sign-in cannot seal credentials. Whoever runs the server must set"
            " a valid SKEIN_CREDENTIAL_KEY."
        )
    if not config.OIDC_CLIENT_ID:
        out["error"] = "SKEIN_OIDC_CLIENT_ID is not set, so browser sign-in is off."
        return out
    try:
        out["authorize_url"] = oidc.authorize_url()
    except oidc.OIDCError as exc:
        out["error"] = str(exc)
    return out


class TokenIn(BaseModel):
    code: str = Field("", max_length=4096)
    code_verifier: str = Field("", max_length=128)
    redirect_uri: str = Field("", max_length=2048)
    # Retain the field only to return a clear refusal to an old browser bundle.
    refresh_token: str = Field("", max_length=4096)


def _issue(request: Request, response: Response, issued: browser_sessions.IssuedSession) -> dict:
    old = request.cookies.get(browser_sessions.COOKIE_NAME, "")
    if old:
        browser_sessions.revoke(old)
    response.set_cookie(
        browser_sessions.COOKIE_NAME,
        issued.cookie,
        max_age=browser_sessions.SESSION_SECONDS,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    return issued.metadata


@router.post("/token")
def post_token(body: TokenIn, request: Request, response: Response):
    origin = require_browser_origin(request)
    settings = auth_settings(request)
    if settings.auth_error:
        raise HTTPException(status_code=503, detail=settings.auth_error)
    if settings.auth_mode != "oidc":
        raise HTTPException(404, "If SKEIN_AUTH_MODE is not oidc, browser sign-in is off.")
    if not config.OIDC_CLIENT_ID:
        raise HTTPException(503, "SKEIN_OIDC_CLIENT_ID is not set, so browser sign-in is off.")
    ratelimit.check("signin", ratelimit.client_addr(request))
    if body.refresh_token:
        raise HTTPException(
            400, "Browser token refresh is no longer supported. Reload and sign in again."
        )
    if not body.code or not body.code_verifier:
        raise HTTPException(400, "The sign-in request is incomplete. Start sign-in again.")
    if body.redirect_uri != origin + "/auth/callback":
        raise HTTPException(400, "The sign-in callback is not allowed. Start sign-in from Skein.")
    if not credentials.available():
        raise PublicError(
            "BROWSER_SESSION_UNAVAILABLE",
            "Browser sign-in cannot seal credentials. Whoever runs the server must set"
            " a valid SKEIN_CREDENTIAL_KEY.",
            status_code=503,
        )
    try:
        payload = oidc.exchange(
            {
                "grant_type": "authorization_code",
                "code": body.code,
                "code_verifier": body.code_verifier,
                "redirect_uri": body.redirect_uri,
                "client_id": config.OIDC_CLIENT_ID,
            }
        )
    except oidc.OIDCRefused as exc:
        raise HTTPException(400, oidc.SIGNIN_REFUSED) from exc
    except oidc.OIDCUnavailable as exc:
        log.warning("identity provider token exchange unavailable (%s)", _fault_class(exc))
        raise HTTPException(503, oidc.SIGNIN_UNAVAILABLE, headers={"Retry-After": "60"}) from exc
    except oidc.OIDCError as exc:
        log.error("identity provider token exchange failed (%s)", _fault_class(exc))
        raise HTTPException(502, oidc.SIGNIN_UNUSABLE) from exc
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        log.error("identity provider token response omitted access_token")
        raise HTTPException(502, oidc.SIGNIN_UNUSABLE)
    try:
        claims = oidc.validate(token)
    except oidc.OIDCUnavailable as exc:
        raise HTTPException(503, oidc.SIGNIN_UNAVAILABLE, headers={"Retry-After": "60"}) from exc
    except oidc.OIDCError as exc:
        log.error("identity provider returned an unusable token (%s)", _fault_class(exc))
        raise HTTPException(502, oidc.SIGNIN_UNUSABLE) from exc
    # The same reserved, ambiguous, inactive and machine-identity walls as a
    # direct bearer request must hold before a browser receives a session.
    from .deps import INACTIVE, _resolve

    request.state.auth_claims = claims
    try:
        _resolve("", f"Bearer {token}", "GET", request)
    except HTTPException as exc:
        if exc.status_code != 403 or exc.detail == INACTIVE:
            raise
        # A claim can contain provider-controlled text. Do not reflect that
        # name through an identity-collision or machine-identity refusal.
        log.error("identity provider claim conflicts with an existing identity")
        raise HTTPException(403, oidc.SIGNIN_UNUSABLE) from exc
    issued = browser_sessions.create_oidc_session(payload, claims, mode=settings.auth_mode)
    return _issue(request, response, issued)


class KeySessionIn(BaseModel):
    key: str = Field(min_length=1, max_length=256)


@router.post("/session/key")
def post_key_session(body: KeySessionIn, request: Request, response: Response):
    require_browser_origin(request)
    settings = auth_settings(request)
    if settings.auth_error:
        raise HTTPException(503, settings.auth_error)
    ratelimit.check("signin", ratelimit.client_addr(request))
    from .deps import _resolve

    _resolve("", f"Bearer {body.key}", "GET", request)
    issued = browser_sessions.create_key_session(body.key, mode=settings.auth_mode)
    return _issue(request, response, issued)


@router.get("/session")
def get_session(request: Request, response: Response):
    require_browser_origin(request, required=False)
    settings = auth_settings(request)
    if settings.auth_error:
        raise HTTPException(503, settings.auth_error)
    response.headers["Cache-Control"] = "no-store"
    # Never clear cookies here: a late anonymous response could erase a newer
    # login's cookie. Only serialized login/logout requests change cookies.
    return browser_sessions.metadata(
        request.cookies.get(browser_sessions.COOKIE_NAME, ""), mode=settings.auth_mode
    )


@router.delete("/sessions")
def delete_every_session(request: Request, response: Response):
    """Sign out of every browser. The CSRF binding is required, not optional
    as on DELETE /session: a cross-site request must not end a person's
    sessions everywhere."""
    origin = require_browser_origin(request)
    settings = auth_settings(request)
    if settings.auth_error:
        raise HTTPException(503, settings.auth_error)
    cookie = request.cookies.get(browser_sessions.COOKIE_NAME, "")
    if not browser_sessions.validate_binding(cookie, request.headers.get(CSRF_HEADER, "")):
        raise browser_sessions.SessionChanged()
    id_token = browser_sessions.id_token_for(cookie)
    browser_sessions.revoke_all(cookie, mode=settings.auth_mode)
    response.delete_cookie(
        browser_sessions.COOKIE_NAME, path="/", secure=True, httponly=True, samesite="lax"
    )
    response.headers["Cache-Control"] = "no-store"
    provider = settings.auth_mode == "oidc"
    return {"logout_url": oidc.logout_url(id_token, origin + "/") if provider else ""}


@router.delete("/session")
def delete_session(request: Request, response: Response):
    """Sign out of this browser. logout_url, when the provider publishes a
    sign-out endpoint, is where the browser goes next to end its provider
    session as well."""
    origin = require_browser_origin(request)
    # the mode only: sign-out must work while authentication is misconfigured
    settings = auth_settings(request)
    cookie = request.cookies.get(browser_sessions.COOKIE_NAME, "")
    if browser_sessions.csrf_token(cookie) and not browser_sessions.validate_binding(
        cookie, request.headers.get(CSRF_HEADER, "")
    ):
        raise browser_sessions.SessionChanged()
    id_token = browser_sessions.id_token_for(cookie)
    browser_sessions.revoke(cookie)
    response.delete_cookie(
        browser_sessions.COOKIE_NAME, path="/", secure=True, httponly=True, samesite="lax"
    )
    response.headers["Cache-Control"] = "no-store"
    provider = settings.auth_mode == "oidc"
    return {"logout_url": oidc.logout_url(id_token, origin + "/") if provider else ""}
