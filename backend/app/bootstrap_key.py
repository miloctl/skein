"""Out-of-band first-key bootstrap: `python -m app.bootstrap_key <name>`.

Minting via POST /api/keys requires strong identity (StrongUser), an existing
key or a validated OIDC sign-in - otherwise anyone who can reach the API could
mint a key for any identity with one X-User header and walk through the
private-record boundary. Without one, the first key per person is issued
here, by someone with shell access to the box (the trusted root of the
deployment). In oidc mode a browser session cannot mint a key:
POST /api/keys answers it 403 (routes/api.py::post_key). Settings sends
POST /api/keys/request instead, which files a notification that carries
this command. An identity-provider token sent as a bearer token is strong
identity, so its holder can mint a key through POST /api/keys. In api-key
mode this is the only path to a person's first key."""

import sys

from . import db
from .services.api_keys import create_key
from .services.users import ensure_human_identity


def main() -> None:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("usage: python -m app.bootstrap_key <name> [label]", file=sys.stderr)
        raise SystemExit(2)
    name = sys.argv[1].strip()
    label = sys.argv[2] if len(sys.argv) > 2 else "bootstrap"
    db.init_db()
    try:
        ensure_human_identity(name)
    except ValueError as exc:
        # a reserved or colliding name is the operator's typo, not a crash:
        # the reason belongs on stderr, never a traceback to decode. No
        # appended imperative - the service message already carries its own
        # ("pick another name"), and one condition gets one wording
        print(f"cannot create '{name}': {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    result = create_key(name, label, at_server=True)
    print(f"API key for {name} (shown once - store it now):\n{result['key']}")


if __name__ == "__main__":
    main()
