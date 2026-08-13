"""Validate versioned playbook, persona, and flock content for deployment CI."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import config
from .services import flocks, personas, playbooks


def validate(workflow_actions: set[str] | None = None) -> list[str]:
    errors: list[str] = []
    errors.extend(f"playbook: {error}" for error in playbooks.validate_all(workflow_actions))
    errors.extend(f"persona: {error}" for error in personas.validate_all())
    errors.extend(f"flock: {error}" for error in flocks.validate_all())
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--playbooks", type=Path)
    parser.add_argument("--personas", type=Path)
    parser.add_argument("--flocks", type=Path)
    parser.add_argument(
        "--workflow-action",
        action="append",
        default=[],
        help="registered workflow action name; repeat for each contributed action",
    )
    args = parser.parse_args()
    if args.playbooks:
        config.PLAYBOOKS_OVERLAY = args.playbooks
    if args.personas:
        config.PERSONAS_OVERLAY = args.personas
    if args.flocks:
        config.FLOCKS_OVERLAY = args.flocks
    errors = validate(set(args.workflow_action))
    for error in errors:
        print(error)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
