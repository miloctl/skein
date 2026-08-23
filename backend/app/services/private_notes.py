"""Author-private notes: 1:1 prep + the feedback journal.

Stored in their OWN SCHEMA (`private`). Portable export, search, MCP, and the
agent layer never read it. Exclusion is structural, not a filter every query
must remember. The local database backup includes this schema in its one
recovery unit. The configured platform mirror excludes it.

Provenance is kept in a local audit table inside the same schema; the
team-visible activity ledger gets nothing, because even write cadence here is
private (a deliberate, documented narrowing of the provenance norm).

Access rules enforced at the route layer (StrongUser) and re-checked here:
author-scoped reads only, human-only writes. The 1:1 brief is the one
function that reads the PLATFORM tables — team-visible data only, assembled
for 1:1 prep; it never writes.
"""

import re
from datetime import UTC, datetime, timedelta

from .. import config, db
from . import scope

KINDS = ("note", "feedback")
FEEDBACK_GAP_DAYS = 21

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {config.PRIVATE_SCHEMA}.notes (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    author text NOT NULL,
    person text NOT NULL,
    kind text NOT NULL DEFAULT 'note' CHECK (kind IN ('note', 'feedback')),
    body text NOT NULL,
    created_at text NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_private_notes_author
    ON {config.PRIVATE_SCHEMA}.notes (author, person, created_at);
CREATE TABLE IF NOT EXISTS {config.PRIVATE_SCHEMA}.audit (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    author text NOT NULL,
    action text NOT NULL,
    note_id bigint,
    created_at text NOT NULL
);
"""

_schema_ready = False


def _mark_schema_ready() -> None:
    global _schema_ready
    _schema_ready = True


def _ready() -> None:
    """Create the schema on first use, here rather than in a core migration.

    Keeping the definition in this file is what makes the exclusion auditable:
    the only place that names these tables is the only place that describes
    them."""
    if not _schema_ready:
        with db.transaction():
            # ensure_owned_schema's xact lock stays held until every table and
            # index exists, so a concurrent first use cannot enter halfway.
            db.ensure_owned_schema(config.PRIVATE_SCHEMA)
            db.execute(_SCHEMA)
            db.on_commit(_mark_schema_ready)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def add_note(author: str, person: str, body: str, kind: str = "note") -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    person = person.strip()
    body = body.strip()
    if not person:
        raise ValueError("person is required")
    if not body:
        raise ValueError("note body is required")
    _ready()
    with db.transaction():
        note_id = db.execute(
            "INSERT INTO private.notes (author, person, kind, body, created_at)"
            " VALUES (?, ?, ?, ?, ?) RETURNING id",
            (author, person, kind, body, _now()),
        )
        _audit(author, f"add_{kind}", note_id)
    return {"id": note_id, "person": person, "kind": kind}


def _audit(author: str, action: str, note_id: int | None) -> None:
    db.execute(
        "INSERT INTO private.audit (author, action, note_id, created_at) VALUES (?, ?, ?, ?)",
        (author, action, note_id, _now()),
    )


def list_notes(author: str, person: str = "") -> list[dict]:
    _ready()
    with db.transaction():
        if person:
            rows = db.query(
                # same 200 cap as the unfiltered branch below: self-scoped, so
                # the risk is a slow render rather than a leak, but a long 1:1
                # history returned whole is still an unbounded response
                "SELECT * FROM private.notes WHERE author = ? AND person = ?"
                " ORDER BY id DESC LIMIT 200",
                (author, person.strip()),
            )
        else:
            rows = db.query(
                "SELECT * FROM private.notes WHERE author = ? ORDER BY id DESC LIMIT 200",
                (author,),
            )
        _audit(author, f"list:{person.strip() or 'all'}", None)
        return [dict(r) for r in rows]


def feedback_gap_days(author: str, person: str) -> int | None:
    """Days since the author's last feedback note for person; None if never.
    Computed at read time for the author's own page — never stored, never
    notified, never aggregated (anti-surveillance rule)."""
    _ready()
    with db.transaction():
        row = db.query_one(
            "SELECT MAX(created_at) AS ts FROM private.notes"
            " WHERE author = ? AND person = ? AND kind = 'feedback'",
            (author, person.strip()),
        )
    if not row or not row["ts"]:
        return None
    last = datetime.fromisoformat(row["ts"])
    return (datetime.now(UTC) - last).days


def delete_note(author: str, note_id: int) -> dict:
    """Author-only delete with a tombstone audit row: proves note #N existed
    and was destroyed (deliberately NOT whom it concerned — less residue)."""
    _ready()
    with db.transaction():
        deleted = db.execute_rowcount(
            "DELETE FROM private.notes WHERE id = ? AND author = ?", (note_id, author)
        )
        if not deleted:
            raise db.NotFound(f"note #{note_id} not found (or not yours)")
        _audit(author, "delete", note_id)
    return {"id": note_id, "deleted": True}


def author_has_notes(author: str) -> bool:
    """Does this author hold private rows? Callers outside this module need
    this to REFUSE an operation, never to read one — it returns a boolean and
    no content, so it leaks nothing the caller could not already infer."""
    _ready()
    with db.transaction():
        row = db.query_one("SELECT 1 FROM private.notes WHERE author = ? LIMIT 1", (author,))
    return row is not None


def rename_author(old: str, new: str) -> None:
    """Move OWNERSHIP: the author's own notes and audit trail. Access is keyed
    by author name, so only the author may trigger this — see the guard in
    users.rename_user."""
    _ready()
    with db.transaction():
        db.execute("UPDATE private.notes SET author = ? WHERE author = ?", (new, old))
        db.execute("UPDATE private.audit SET author = ? WHERE author = ?", (new, old))


def rename_subject(old: str, new: str) -> None:
    """Move the SUBJECT reference: notes other people keep ABOUT this person.

    Safe for any actor to trigger, and it must run on every rename. It changes
    no ownership — each author still reads only their own rows — so it leaks
    nothing. Skipping it stranded every teammate's 1:1 journal about the
    renamed person under a name with no roster row: their brief rendered empty
    and their feedback-gap nudge reset to 'never'."""
    _ready()
    with db.transaction():
        db.execute("UPDATE private.notes SET person = ? WHERE person = ?", (new, old))


def recover_identity_ownership(old: str, new: str) -> dict:
    """Move private identity references during an operator collision repair.

    This operation records an administrative tombstone but no note content.
    The private schema commits before the core roster rename. If the core
    step fails, the operator can safely repeat the same repair command.
    """
    _ready()
    with db.transaction():
        marker = f"system_identity_repair:{old}->{new}"
        prior = db.query_one(
            "SELECT 1 FROM private.audit WHERE author = ? AND action = ? LIMIT 1",
            (new, marker),
        )
        new_notes = db.query_one("SELECT 1 FROM private.notes WHERE author = ? LIMIT 1", (new,))
        new_audit = db.query_one("SELECT 1 FROM private.audit WHERE author = ? LIMIT 1", (new,))
        if (new_notes or new_audit) and not prior:
            raise ValueError(
                f"private identity ownership already exists for '{new}'. Pick another name."
            )
        author_rows = db.query_row(
            "SELECT COUNT(*) AS n FROM private.notes WHERE author = ?", (old,)
        )["n"]
        subject_rows = db.query_row(
            "SELECT COUNT(*) AS n FROM private.notes WHERE person = ?", (old,)
        )["n"]
        db.execute("UPDATE private.notes SET author = ? WHERE author = ?", (new, old))
        db.execute("UPDATE private.audit SET author = ? WHERE author = ?", (new, old))
        db.execute("UPDATE private.notes SET person = ? WHERE person = ?", (new, old))
        if not prior:
            _audit(new, marker, None)
    return {"author_rows": author_rows, "subject_rows": subject_rows}


def list_audit(author: str, limit: int = 100) -> list[dict]:
    """The author's own audit trail: adds, reads, briefs, deletes."""
    _ready()
    with db.transaction():
        rows = db.query(
            "SELECT * FROM private.audit WHERE author = ? ORDER BY id DESC LIMIT ?",
            (author, limit),
        )
        return [dict(r) for r in rows]


def audit_brief(author: str, person: str) -> None:
    """Record that author pulled person's 1:1 brief (the audit stays in the
    private schema — the fact a brief was pulled is itself private)."""
    _ready()
    with db.transaction():
        _audit(author, f"brief:{person.strip()}", None)


def one_on_one_brief(person: str, days: int = 14, viewer: scope.Viewer = scope.NOBODY) -> dict:
    """Deterministic "since last time" brief, filtered to what the READER may
    see — not to what the subject wrote.

    `person` is a free path parameter and there is no manager relation in this
    schema, so every StrongUser can name every teammate. Unfiltered, the six
    queries below handed any caller another person's PRIVATE standup and
    promise rows in full, which defeats the private tier rather than the crew
    one. The viewer is the caller (routes/private.py), never the subject.

    Every section degrades to empty pre-adoption.
    """
    person = person.strip()
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
    f = {
        t: scope.visible_filter(viewer, t)
        for t in ("standups", "blockers", "questions", "tasks", "promises")
    }
    return {
        "person": person,
        "since": since,
        "standups": db.query(
            f"SELECT * FROM standups WHERE author = ? AND created_at >= ? AND {f['standups'][0]}"  # noqa: S608 — scope.visible_filter emits only bound marks
            " ORDER BY id DESC LIMIT 5",
            (person, since, *f["standups"][1]),
        ),
        "open_blockers": db.query(
            f"SELECT * FROM blockers WHERE owner = ? AND status != 'resolved' AND {f['blockers'][0]}"  # noqa: S608 — `scoped` is a module-local literal with one bound mark
            " ORDER BY id DESC",
            (person, *f["blockers"][1]),
        ),
        "open_questions": db.query(
            f"SELECT * FROM questions WHERE assigned_to = ? AND status = 'open' AND {f['questions'][0]}"  # noqa: S608 — `scoped` is a module-local literal with one bound mark
            " ORDER BY id",
            (person, *f["questions"][1]),
        ),
        "in_progress": db.query(
            f"SELECT id, title, updated_at FROM tasks WHERE assignee = ? AND {f['tasks'][0]}"  # noqa: S608 — `scoped` is a module-local literal with one bound mark
            " AND status = 'in_progress' ORDER BY updated_at",
            (person, *f["tasks"][1]),
        ),
        "recently_done": db.query(
            f"SELECT id, title, completed_at FROM tasks WHERE assignee = ? AND {f['tasks'][0]}"  # noqa: S608 — `scoped` is a module-local literal with one bound mark
            " AND completed_at >= ? ORDER BY completed_at DESC LIMIT 10",
            (person, *f["tasks"][1], since),
        ),
        "promises_made": db.query(
            # direction = 'given': the heading says "promises they made"
            f"SELECT * FROM promises WHERE created_by = ? AND created_at >= ?"  # noqa: S608 — scope filters emit only bound marks
            f" AND direction = 'given' AND {f['promises'][0]}"
            " ORDER BY id DESC",
            (person, since, *f["promises"][1]),
        ),
    }


# a bare '-' only separates when whitespace-surrounded, so hyphenated names
# (mary-jane) never get split into person="mary", body="jane — …"
FB_LINE = re.compile(r"^\s*fb:", re.I)
# FB_LINE plus the command-wrapped shape ("/remember fb: …"): surfaces that
# sink to disk or a model provider must refuse both, or a slash prefix
# becomes a private-data smuggling route (chat gate, session bridge)
FB_GUARD = re.compile(r"^\s*(?:/[a-z]+\s+)?fb:", re.I)
_FB = re.compile(r"^\s*fb:\s*(?P<person>.+?)\s*(?:—|:|\s-\s)\s*(?P<body>.+)$", re.I | re.S)


def parse_feedback(text: str) -> tuple[str, str]:
    """Parse 'fb: <person> — <note>' (also ':' or spaced '-' separators).
    Only call on FB_LINE-matching text; raises on malformed input."""
    m = _FB.match(text)
    if not m:
        raise ValueError("feedback format: fb: <person> — <note>")
    return m.group("person").strip(), m.group("body").strip()
