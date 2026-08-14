#!/usr/bin/env python
"""A minimal OpenID provider, for the oidc browser walk only.

    python scripts/stub-idp.py [port] [audience]

It is NOT a mock: the backend validates a real RS256 signature against the
JWKS this serves, then iss / aud / exp (app/oidc.py). A fake that skipped the
signature would pass while proving nothing, so this mints a key pair at start
and signs with it.

NEVER run this beside a real deployment. It authorizes anyone who asks and
issues a token for whatever name the request carries.
"""

from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8610
AUDIENCE = sys.argv[2] if len(sys.argv) > 2 else "skein"
ISSUER = f"http://127.0.0.1:{PORT}"
KEY_ID = "stub-1"

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(_key.public_key()))
_public_jwk.update({"kid": KEY_ID, "use": "sig", "alg": "RS256"})

# code -> the username it was issued for. The walk drives one sign-in at a
# time, so a dict with no expiry is enough; a real provider must not do this.
_codes: dict[str, str] = {}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # the backend relays the exchange server-side, so CORS is not needed
        # for /token. Discovery and JWKS are read the same way.
        self.end_headers()
        self.wfile.write(body)

    # do_GET / do_POST are BaseHTTPRequestHandler's dispatch names, not ours.
    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/.well-known/openid-configuration":
            self._send(
                200,
                {
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                },
            )
            return
        if url.path == "/jwks":
            self._send(200, {"keys": [_public_jwk]})
            return
        if url.path == "/authorize":
            query = parse_qs(url.query)
            # `login_hint` picks who signs in, so one walk can be somebody
            # the seeded roster already knows.
            user = (query.get("login_hint") or ["ava"])[0]
            code = f"code-{len(_codes)}-{user}"
            _codes[code] = user
            back = (query.get("redirect_uri") or [""])[0]
            state = (query.get("state") or [""])[0]
            self.send_response(302)
            self.send_header("Location", f"{back}?{urlencode({'code': code, 'state': state})}")
            self.end_headers()
            return
        self._send(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/token":
            self._send(404, {"error": "not_found"})
            return
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        form = {k: v[0] for k, v in parse_qs(raw).items()}
        user = _codes.pop(form.get("code", ""), "")
        if not user:
            # one code, one exchange: the walk asserts a replay is refused
            self._send(400, {"error": "invalid_grant"})
            return
        now = int(time.time())
        token = jwt.encode(
            {
                "iss": ISSUER,
                "aud": AUDIENCE,
                "sub": user,
                "preferred_username": user,
                "groups": ["skein-admins"],
                "iat": now,
                "exp": now + 3600,
            },
            _key,
            algorithm="RS256",
            headers={"kid": KEY_ID},
        )
        self._send(200, {"access_token": token, "token_type": "Bearer", "expires_in": 3600})

    def log_message(self, *_args: object) -> None:
        """Quiet: playwright prints this server's stdout on every run."""


if __name__ == "__main__":
    print(f"stub IdP on {ISSUER} (audience {AUDIENCE})", flush=True)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
