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


def flatten(text: str, width: int = 0) -> str:
    """User text on its way into a MARKDOWN line, collapsed to one line.

    A newline in a title forges structure in the packet. That was survivable
    while artifacts rendered inside a `<pre>`; frontend/components/
    artifact-markdown.tsx turns a leading `#` into a real heading, so a
    milestone called "x\n\n# Shipped this season" now writes a section nobody
    wrote, and a screen reader navigating by heading gets a document outline
    that is partly forged. Every generator that interpolates user text into a
    markdown line has to pass it through here: digest, readout, handoff,
    rituals, context_pack.
    """
    one = " ".join(str(text).split())
    return one[:width] if width else one
