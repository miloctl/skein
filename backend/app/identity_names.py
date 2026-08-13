"""Canonical names that the Skein runtime owns.

`anonymous` is a synthetic compatibility subject. Core can store it for old
unnamed writes, but no extension or authenticated person can claim it.
"""

import unicodedata
from collections import Counter
from dataclasses import dataclass
from threading import RLock

CORE_MACHINE_SUBJECTS = frozenset(
    {"agent", "anonymous", "ci", "forge", "mcp", "scheduler", "system", "team"}
)
SYNTHETIC_COMPAT_SUBJECTS = frozenset({"anonymous"})
HUMAN_RESERVED_SUBJECTS = CORE_MACHINE_SUBJECTS - SYNTHETIC_COMPAT_SUBJECTS
ROSTER_CORE_AGENT_SUBJECTS = frozenset({"agent"})

_runtime_lock = RLock()
_runtime_machine_subjects: Counter[str] = Counter()
_runtime_content_owners: Counter[str] = Counter()
_runtime_scopes = 0


@dataclass(frozen=True)
class RuntimeIdentityScope:
    reserved: frozenset[str]
    content_owners: frozenset[str]


def fold_identity(name: str) -> str:
    """Normalize one identity for every ownership comparison."""
    folded = unicodedata.normalize("NFKC", (name or "").strip())
    return "".join(c for c in folded if unicodedata.category(c) != "Cf").casefold()


def activate_runtime_machine_subjects(
    subjects: set[str], content_owners: set[str] | None = None
) -> RuntimeIdentityScope:
    """Reserve composed machine names while one application is running.

    Content file updates are live, but identity-bearing slug additions require
    restart. The active sets prevent a later file from taking an existing
    identity. A counter keeps concurrent test or embedded applications
    fail-safe without making their shutdown order significant.
    """
    global _runtime_scopes
    reserved = frozenset(fold_identity(subject) for subject in subjects if fold_identity(subject))
    owners = frozenset(
        fold_identity(subject) for subject in (content_owners or set()) if fold_identity(subject)
    )
    with _runtime_lock:
        _runtime_machine_subjects.update(reserved)
        _runtime_content_owners.update(owners)
        _runtime_scopes += 1
    return RuntimeIdentityScope(reserved, owners)


def deactivate_runtime_machine_subjects(token: RuntimeIdentityScope) -> None:
    """Release the exact runtime claims returned by activation."""
    global _runtime_scopes
    with _runtime_lock:
        for subject in token.reserved:
            _runtime_machine_subjects[subject] -= 1
            if _runtime_machine_subjects[subject] <= 0:
                del _runtime_machine_subjects[subject]
        for subject in token.content_owners:
            _runtime_content_owners[subject] -= 1
            if _runtime_content_owners[subject] <= 0:
                del _runtime_content_owners[subject]
        _runtime_scopes -= 1


def runtime_content_ownership() -> tuple[frozenset[str], frozenset[str], bool]:
    """Return reserved names, startup content owners, and active-scope state."""
    reserved = {fold_identity(subject) for subject in CORE_MACHINE_SUBJECTS}
    with _runtime_lock:
        reserved.update(_runtime_machine_subjects)
        active = _runtime_scopes > 0
        # When an embedding hosts more than one app, a live slug is safe only
        # if every active composition accepted it at startup. The content
        # directories are process-global, so a union would let one app widen
        # another app's identity roster.
        owners = frozenset(
            subject
            for subject, count in _runtime_content_owners.items()
            if not active or count == _runtime_scopes
        )
    return frozenset(reserved), owners, active


def content_subject_refusal(name: str) -> str:
    """Explain why one persona or flock slug is unavailable at runtime."""
    folded = fold_identity(name)
    reserved, owners, active = runtime_content_ownership()
    if folded in reserved:
        return "slug is reserved for a composed machine identity"
    if active and folded not in owners:
        return "new identity-bearing content requires an application restart"
    return ""
