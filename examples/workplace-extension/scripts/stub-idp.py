#!/usr/bin/env python
"""A minimal OpenID provider for the local browser contract.

    python scripts/stub-idp.py [port] [audience] ['{"user":["group"]}']

The backend validates a real RS256 signature against this provider's JWKS.
Never run this provider beside a real deployment. It authorizes every named
user and issues a token for the requested name.
"""

from __future__ import annotations

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8610
AUDIENCE = sys.argv[2] if len(sys.argv) > 2 else "skein"
GROUPS_BY_USER = json.loads(sys.argv[3]) if len(sys.argv) > 3 else None
ISSUER = f"http://127.0.0.1:{PORT}"
KEY_ID = "stub-1"

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(_key.public_key()))
_public_jwk.update({"kid": KEY_ID, "use": "sig", "alg": "RS256"})
_codes: dict[str, str] = {}


def groups_for(user: str) -> list[str]:
    if GROUPS_BY_USER is None:
        return ["skein-admins"]
    return GROUPS_BY_USER.get(user, [])


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
        form = {key: values[0] for key, values in parse_qs(raw).items()}
        user = _codes.pop(form.get("code", ""), "")
        if not user:
            self._send(400, {"error": "invalid_grant"})
            return
        now = int(time.time())
        token = jwt.encode(
            {
                "iss": ISSUER,
                "aud": AUDIENCE,
                "sub": user,
                "preferred_username": user,
                "groups": groups_for(user),
                "iat": now,
                "exp": now + 3600,
            },
            _key,
            algorithm="RS256",
            headers={"kid": KEY_ID},
        )
        self._send(200, {"access_token": token, "token_type": "Bearer", "expires_in": 3600})

    def log_message(self, *_args: object) -> None:
        """Keep the contract output quiet."""


if __name__ == "__main__":
    bind_host = os.environ.get("SKEIN_STUB_IDP_BIND", "127.0.0.1")
    server = ThreadingHTTPServer((bind_host, PORT), Handler)
    ISSUER = f"http://127.0.0.1:{server.server_port}"
    print(f"stub IdP on {ISSUER} (audience {AUDIENCE})", flush=True)
    server.serve_forever()
