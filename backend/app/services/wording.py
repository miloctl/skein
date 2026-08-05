"""Shared formatting for user-visible text.

CLAUDE.md requires sentence-form text to compute its own plurals ("1 promise
carries", "2 promises carry"), and a bare "1 tasks" shipped on My Day,
Approvals, and a notification because each writer rebuilt the f-string by
hand. One helper, so a new surface cannot reinvent the bug.
"""


def count(n: int, word: str) -> str:
    """`3 tasks` / `1 task`. Regular -s plurals only — pass the plural form
    yourself for anything irregular."""
    return f"{n} {word}{'' if n == 1 else 's'}"
