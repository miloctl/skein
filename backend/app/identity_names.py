"""Canonical names that the Skein runtime owns.

`anonymous` is a synthetic compatibility subject. Core can store it for old
unnamed writes, but no extension or authenticated person can claim it.
"""

CORE_MACHINE_SUBJECTS = frozenset(
    {"agent", "anonymous", "ci", "forge", "mcp", "scheduler", "system", "team"}
)
SYNTHETIC_COMPAT_SUBJECTS = frozenset({"anonymous"})
HUMAN_RESERVED_SUBJECTS = CORE_MACHINE_SUBJECTS - SYNTHETIC_COMPAT_SUBJECTS
ROSTER_CORE_AGENT_SUBJECTS = frozenset({"agent"})
