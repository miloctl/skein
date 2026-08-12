"""Inspect and repair legacy case-folded roster ownership conflicts.

Usage:
    python -m app.identity_audit
    python -m app.identity_audit rename <old-name> <new-name>

The command requires shell access to the Skein deployment. It never merges
human and agent rows automatically because that would combine provenance,
authority, and private ownership without an operator decision.
"""

import sys

from . import db
from .services.users import identity_ownership_conflicts, repair_identity_ownership


def main() -> None:
    db.init_db()
    if len(sys.argv) == 1:
        collisions = identity_ownership_conflicts()
        if not collisions:
            print("No conflicting identity ownership found.")
            return
        for conflict in collisions:
            names = ", ".join(conflict["names"])
            claim = f"; machine content: {conflict['claim']}" if conflict.get("claim") else ""
            print(f"{conflict['kind']}: {names}{claim}")
        print(
            "Rename one row in each group. Do not merge human and agent rows."
            " Use: python -m app.identity_audit rename <old-name> <new-name>",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if len(sys.argv) == 4 and sys.argv[1] == "rename":
        old, new = sys.argv[2], sys.argv[3]
        try:
            result = repair_identity_ownership(old, new)
        except (ValueError, db.NotFound) as exc:
            print(f"identity-audit: repair refused: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        print(f"Renamed {result['old']} to {result['new']}.")
        return
    print(
        "usage: python -m app.identity_audit [rename <old-name> <new-name>]",
        file=sys.stderr,
    )
    raise SystemExit(2)


if __name__ == "__main__":
    main()
