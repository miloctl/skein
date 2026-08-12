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
from .services.users import folded_identity_collisions, rename_user


def main() -> None:
    db.init_db()
    if len(sys.argv) == 1:
        collisions = folded_identity_collisions()
        if not collisions:
            print("No conflicting folded identities found.")
            return
        for rows in collisions:
            print(", ".join(f"{row['name']} ({row['kind']})" for row in rows))
        print(
            "Rename one row in each group. Do not merge human and agent rows."
            " Use: python -m app.identity_audit rename <old-name> <new-name>",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if len(sys.argv) == 4 and sys.argv[1] == "rename":
        old, new = sys.argv[2], sys.argv[3]
        result = rename_user(old, new, actor=old)
        print(f"Renamed {result['old']} to {result['new']}.")
        return
    print(
        "usage: python -m app.identity_audit [rename <old-name> <new-name>]",
        file=sys.stderr,
    )
    raise SystemExit(2)


if __name__ == "__main__":
    main()
