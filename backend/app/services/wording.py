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


def not_administrator(user: str, action: str = "") -> str:
    """The ONE refusal for the not-an-administrator condition.

    CLAUDE.md's one-condition-one-wording rule: routes/deps.py::admin_user and
    the authority gate in services/review.py::_approve_change_locked refuse the
    same fact, and two hand-written copies drifted to "your name" against "the
    name". A new admin-only door calls this rather than writing a third."""
    refused = f" and cannot {action}" if action else ""
    return (
        f"'{user}' is not an administrator{refused}."
        " Ask whoever runs the server to add the name to SKEIN_ADMINS."
    )


def quoted(text: str, width: int = 0) -> str:
    """User text on its way into a generated sentence, wrapped in the quoted frame.

    services/refs.py treats a single-quoted span as a row title and never
    parses a reference out of one — a task called "chase decision #4 approval"
    must not link decision #4 from a report that never referenced it. Every
    generator that writes user free text after an entity reference has to wrap
    it here, not hand-quote: the frame only holds while the span's own
    apostrophes cannot close it early, so interior straight quotes become
    typographic ones.
    """
    return "'" + flatten(text, width).replace("'", "\u2019") + "'"


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
