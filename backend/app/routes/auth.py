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

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import config, oidc, ratelimit

router = APIRouter(prefix="/api/auth")


@router.get("/config")
def get_auth_config():
    """What the web app needs to render the right sign-in affordance.

    Always answers, in every mode: the frontend has no other way to learn that
    the self-asserted name picker is not the identity model here."""
    out: dict = {"mode": config.AUTH_MODE, "error": config.AUTH_ERROR}
    if config.AUTH_ERROR or config.AUTH_MODE != "oidc":
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
    if config.AUTH_ERROR:
        raise HTTPException(status_code=503, detail=config.AUTH_ERROR)
    if config.AUTH_MODE != "oidc":
        raise HTTPException(
            status_code=404, detail="browser sign-in is off unless SKEIN_AUTH_MODE=oidc"
        )
    if not config.OIDC_CLIENT_ID:
        raise HTTPException(
            status_code=503,
            detail="SKEIN_OIDC_CLIENT_ID is not set, so browser sign-in is off.",
        )
    # this endpoint makes an OUTBOUND request for an unauthenticated caller.
    # The cap is what stops it being used to hammer the identity provider.
    # Keyed by client address because a signed-out caller has no name yet.
    ratelimit.check("signin", request.client.host if request.client else "anonymous")

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

    try:
        payload = oidc.exchange(form)
    except oidc.OIDCError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    token = str(payload.get("access_token") or "")
    if not token:
        raise HTTPException(
            status_code=502, detail="the identity provider returned no access token."
        )
    # Validate before answering. Otherwise the browser stores a token that
    # every later request rejects, and the person sees a signed-in UI that
    # 401s on everything.
    try:
        name, _ = oidc.principal(oidc.validate(token))
    except oidc.OIDCError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "access_token": token,
        "refresh_token": str(payload.get("refresh_token") or ""),
        "expires_in": int(payload.get("expires_in") or 0),
        "user": name,
    }
