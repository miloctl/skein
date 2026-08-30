"""Browser sign-in for SKEIN_AUTH_MODE=oidc: authorization code + PKCE.

Both endpoints answer BEFORE the caller has any credential, so both are on the
perimeter's open-path list and neither reveals anything a signed-out visitor
must not see. /config carries public client parameters only. /token relays a
code the browser already holds to the IdP that issued it.

The exchange is relayed rather than run in the browser for two reasons: the
identity provider then needs no CORS grant for the web app's origin (the usual
way this deployment fails), and the token is validated here before the browser
is told the sign-in worked. PKCE is untouched by the relay — the verifier is
generated in the browser, never stored here, and the server holds no client
secret, so the web app remains a public client.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import config, db, oidc, ratelimit

router = APIRouter(prefix="/api/auth")
log = logging.getLogger("skein")


def _fault_class(exc: BaseException) -> str:
    cause = exc.__cause__
    return type(cause).__name__ if cause is not None else type(exc).__name__


@router.get("/config")
def get_auth_config(request: Request):
    """What the web app needs to render the right sign-in affordance.

    Always answers, in every mode: the frontend has no other way to learn that
    the self-asserted name picker is not the identity model here."""
    settings = request.app.state.skein_settings
    if not request.app.state.skein_explicit_settings:
        from ..extensions import AppSettings

        settings = AppSettings.from_config()
    mode = settings.auth_mode if settings.auth_mode in config.AUTH_MODES else "invalid"
    out: dict = {"mode": mode, "error": settings.auth_error}
    if settings.auth_error or settings.auth_mode != "oidc":
        return out
    out["client_id"] = config.OIDC_CLIENT_ID
    out["scopes"] = config.OIDC_SCOPES
    if not config.OIDC_CLIENT_ID:
        # the API still accepts IdP tokens; only the browser flow is off
        out["error"] = "SKEIN_OIDC_CLIENT_ID is not set, so browser sign-in is off."
        return out
    try:
        out["authorize_url"] = oidc.authorize_url()
    except oidc.OIDCError as exc:
        out["error"] = str(exc)
    return out


class TokenIn(BaseModel):
    """Either an authorization code with its PKCE verifier, or a refresh
    token. Both are the browser's own credentials, carried across."""

    code: str = Field("", max_length=4096)
    code_verifier: str = Field("", max_length=128)
    redirect_uri: str = Field("", max_length=2048)
    refresh_token: str = Field("", max_length=4096)


@router.post("/token")
def post_token(body: TokenIn, request: Request):
    settings = request.app.state.skein_settings
    if not request.app.state.skein_explicit_settings:
        from ..extensions import AppSettings

        settings = AppSettings.from_config()
    if settings.auth_error:
        raise HTTPException(status_code=503, detail=settings.auth_error)
    if settings.auth_mode != "oidc":
        raise HTTPException(
            status_code=404,
            detail="If SKEIN_AUTH_MODE is not oidc, browser sign-in is off.",
        )
    if not config.OIDC_CLIENT_ID:
        raise HTTPException(
            status_code=503,
            detail="SKEIN_OIDC_CLIENT_ID is not set, so browser sign-in is off.",
        )
    # this endpoint makes an OUTBOUND request for an unauthenticated caller.
    # The cap is what stops it being used to hammer the identity provider.
    # Keyed by client address because a signed-out caller has no name yet.
    ratelimit.check("signin", ratelimit.client_addr(request))

    if body.refresh_token:
        form = {
            "grant_type": "refresh_token",
            "refresh_token": body.refresh_token,
            "client_id": config.OIDC_CLIENT_ID,
        }
    elif body.code and body.code_verifier and body.redirect_uri:
        form = {
            "grant_type": "authorization_code",
            "code": body.code,
            "code_verifier": body.code_verifier,
            "redirect_uri": body.redirect_uri,
            "client_id": config.OIDC_CLIENT_ID,
        }
    else:
        raise HTTPException(
            status_code=400,
            detail="send code, code_verifier and redirect_uri, or send refresh_token",
        )

    # Three outcomes, three answers. A refusal is the caller's own stale or
    # replayed code (4xx — retrying cannot help); an unreachable provider is
    # 503 (retrying later can); anything else is a genuine relay fault (502).
    try:
        payload = oidc.exchange(form)
    except oidc.OIDCRefused as exc:
        raise HTTPException(status_code=400, detail=oidc.SIGNIN_REFUSED) from exc
    except oidc.OIDCUnavailable as exc:
        log.warning("identity provider token exchange unavailable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=oidc.SIGNIN_UNAVAILABLE,
            headers={"Retry-After": "60"},
        ) from exc
    except oidc.OIDCError as exc:
        log.error("identity provider token exchange failed (%s)", _fault_class(exc))
        raise HTTPException(status_code=502, detail=oidc.SIGNIN_UNUSABLE) from exc

    token = str(payload.get("access_token") or "")
    if not token:
        log.error("identity provider token response omitted access_token")
        raise HTTPException(status_code=502, detail=oidc.SIGNIN_UNUSABLE)
    # Validate before answering. Otherwise the browser stores a token that
    # every later request rejects, and the person sees a signed-in UI that
    # 401s on everything.
    try:
        claims = oidc.validate(token)
        issuer, subject = oidc.identity(claims)
        display_name, _ = oidc.principal(claims)
    except oidc.OIDCUnavailable as exc:
        log.warning("identity provider token validation unavailable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=oidc.SIGNIN_UNAVAILABLE,
            headers={"Retry-After": "60"},
        ) from exc
    except oidc.OIDCError as exc:
        log.error("identity provider returned an unusable token (%s)", _fault_class(exc))
        raise HTTPException(status_code=502, detail=oidc.SIGNIN_UNUSABLE) from exc
    from ..services import oidc_identities
    from ..services.users import is_active
    from .deps import INACTIVE

    try:
        human = oidc_identities.resolve(issuer, subject, display_name)
    except db.BUSY_ERRORS as exc:
        raise HTTPException(
            status_code=503,
            detail="The database is busy. Wait 5 seconds, then send the request again.",
            headers={"Retry-After": "5"},
        ) from exc
    except ValueError as exc:
        # The rejected claim can contain provider-controlled text. Logging or
        # returning it lets a claim forge a log line or reflect into the browser.
        log.error("identity provider claim conflicts with an existing identity")
        raise HTTPException(status_code=403, detail=oidc.SIGNIN_UNUSABLE) from exc
    name = human["name"]
    if not is_active(name):
        raise HTTPException(status_code=403, detail=INACTIVE)
    return {
        "access_token": token,
        "refresh_token": str(payload.get("refresh_token") or ""),
        # the IdP's own field: a non-numeric value must not become a 400 that
        # quotes it back. 0 means "no lifetime given", which the browser reads
        # as a token it cannot refresh ahead of time.
        "expires_in": _seconds(payload.get("expires_in")),
        "user": name,
    }


def _seconds(raw: object) -> int:
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, (int, float)):
        return max(0, int(raw))
    if isinstance(raw, str):
        try:
            return max(0, int(raw.strip()))
        except ValueError:
            return 0
    return 0
