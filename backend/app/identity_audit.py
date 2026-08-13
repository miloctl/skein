"""Inspect and repair legacy case-folded roster ownership conflicts.

Usage:
    python -m app.identity_audit
    python -m app.identity_audit rename <old-name> <new-name>
    python -m app.identity_audit claim-content <slug>
    python -m app.identity_audit claim-machine <name> <owner>

The command requires shell access to the Skein deployment. It never merges
human and agent rows automatically because that would combine provenance,
authority, and private ownership without an operator decision.
"""

import sys

from . import db
from .services.users import (
    claim_content_identity,
    claim_machine_identity,
    identity_ownership_conflicts,
    repair_identity_ownership,
)


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
    if len(sys.argv) == 3 and sys.argv[1] == "claim-content":
        try:
            result = claim_content_identity(sys.argv[2])
        except (ValueError, db.NotFound) as exc:
            print(f"identity-audit: content claim refused: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        print(f"Assigned {result['name']} to configured content.")
        return
    if len(sys.argv) == 4 and sys.argv[1] == "claim-machine":
        try:
            result = claim_machine_identity(sys.argv[2], sys.argv[3])
        except (ValueError, db.NotFound) as exc:
            print(f"identity-audit: machine claim refused: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        print(f"Assigned {result['name']} to {result['identity_owner']}.")
        return
    print(
        "usage: python -m app.identity_audit"
        " [rename <old-name> <new-name> | claim-content <slug>"
        " | claim-machine <name> <owner>]",
        file=sys.stderr,
    )
    raise SystemExit(2)


if __name__ == "__main__":
    main()
