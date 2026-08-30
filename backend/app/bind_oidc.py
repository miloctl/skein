"""Bind existing users before an OIDC migration: `python -m app.bind_oidc sub=name ...`."""

import sys

from . import db, oidc
from .services.oidc_identities import bind_existing


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python -m app.bind_oidc <subject>=<user> [...]", file=sys.stderr)
        raise SystemExit(2)
    pairs: list[tuple[str, str]] = []
    for raw in sys.argv[1:]:
        subject, separator, name = raw.partition("=")
        if not separator or not subject.strip() or not name.strip():
            print("each OIDC binding must use <subject>=<user>", file=sys.stderr)
            raise SystemExit(2)
        pairs.append((subject.strip(), name.strip()))
    db.init_db()
    issuer = oidc.issuer()
    try:
        for subject, name in pairs:
            bind_existing(issuer, subject, name, actor="oidc-bootstrap")
    except ValueError as exc:
        print(f"OIDC binding failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"Bound {len(pairs)} OIDC user(s).")


if __name__ == "__main__":
    main()
