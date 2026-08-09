"""Author-private notes: 1:1 prep + the feedback journal.

Stored in a separate SQLite file (config.PRIVATE_DB_PATH) that the platform
never opens anywhere else — not app.db, not backup/export, not FTS, not MCP,
not the agent layer. Exclusion is structural (no code path touches the
file), not a filter every query must remember. Provenance is kept in a
local audit table inside the same file; the team-visible activity ledger
gets nothing, because even write cadence here is private (a deliberate,
documented narrowing of the provenance norm).

Access rules enforced at the route layer (StrongUser) and re-checked here:
author-scoped reads only, human-only writes. The 1:1 brief is the one
function that reads the PLATFORM db — team-visible data only, assembled for
1:1 prep; it never writes.
"""

import os
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .. import config, db
from . import scope

KINDS = ("note", "feedback")
FEEDBACK_GAP_DAYS = 21

_SCHEMA = """
CREATE TABLE IF NOT EXISTS private_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    author TEXT NOT NULL,
    person TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'note' CHECK (kind IN ('note', 'feedback')),
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_private_notes_author
    ON private_notes (author, person, created_at);
CREATE TABLE IF NOT EXISTS private_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    author TEXT NOT NULL,
    action TEXT NOT NULL,
    note_id INTEGER,
    created_at TEXT NOT NULL
);
"""


_schema_ready: set[str] = set()


def _connect() -> sqlite3.Connection:
    path = Path(config.PRIVATE_DB_PATH)
    fresh = not path.exists()
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    if str(path) not in _schema_ready:
        conn.executescript(_SCHEMA)
        _schema_ready.add(str(path))
    if fresh:
        # evaluative content about named people: owner-only on disk, and the
        # WAL sidecars must not be looser than the db itself
        os.chmod(path, 0o600)
    for sidecar in (f"{path}-wal", f"{path}-shm"):
        if os.path.exists(sidecar):
            os.chmod(sidecar, 0o600)
    return conn


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
    with closing(_connect()) as conn:
        cur = conn.execute(
            "INSERT INTO private_notes (author, person, kind, body, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (author, person, kind, body, _now()),
        )
        note_id = cur.lastrowid
        _audit(conn, author, f"add_{kind}", note_id)
        conn.commit()
    return {"id": note_id, "person": person, "kind": kind}


def _audit(conn: sqlite3.Connection, author: str, action: str, note_id: int | None) -> None:
    conn.execute(
        "INSERT INTO private_audit (author, action, note_id, created_at) VALUES (?, ?, ?, ?)",
        (author, action, note_id, _now()),
    )


def list_notes(author: str, person: str = "") -> list[dict]:
    with closing(_connect()) as conn:
        if person:
            rows = conn.execute(
                # same 200 cap as the unfiltered branch below: self-scoped, so
                # the risk is a slow render rather than a leak, but a long 1:1
                # history returned whole is still an unbounded response
                "SELECT * FROM private_notes WHERE author = ? AND person = ?"
                " ORDER BY id DESC LIMIT 200",
                (author, person.strip()),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM private_notes WHERE author = ? ORDER BY id DESC LIMIT 200",
                (author,),
            ).fetchall()
        _audit(conn, author, f"list:{person.strip() or 'all'}", None)
        conn.commit()
        return [dict(r) for r in rows]


def feedback_gap_days(author: str, person: str) -> int | None:
    """Days since the author's last feedback note for person; None if never.
    Computed at read time for the author's own page — never stored, never
    notified, never aggregated (anti-surveillance rule)."""
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT MAX(created_at) AS ts FROM private_notes"
            " WHERE author = ? AND person = ? AND kind = 'feedback'",
            (author, person.strip()),
        ).fetchone()
    if not row or not row["ts"]:
        return None
    last = datetime.fromisoformat(row["ts"])
    return (datetime.now(UTC) - last).days


def delete_note(author: str, note_id: int) -> dict:
    """Author-only delete with a tombstone audit row: proves note #N existed
    and was destroyed (deliberately NOT whom it concerned — less residue)."""
    with closing(_connect()) as conn:
        cur = conn.execute(
            "DELETE FROM private_notes WHERE id = ? AND author = ?", (note_id, author)
        )
        if not cur.rowcount:
            raise db.NotFound(f"note #{note_id} not found (or not yours)")
        _audit(conn, author, "delete", note_id)
        conn.commit()
    return {"id": note_id, "deleted": True}


def author_has_notes(author: str) -> bool:
    """Does this author hold private rows? Callers outside private.db need
    this to REFUSE an operation, never to read one — it returns a boolean and
    no content, so it leaks nothing the caller could not already infer."""
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT 1 FROM private_notes WHERE author = ? LIMIT 1", (author,)
        ).fetchone()
    return row is not None


def rename_author(old: str, new: str) -> None:
    """Move OWNERSHIP: the author's own notes and audit trail. Access is keyed
    by author name, so only the author may trigger this — see the guard in
    users.rename_user."""
    with closing(_connect()) as conn:
        conn.execute("UPDATE private_notes SET author = ? WHERE author = ?", (new, old))
        conn.execute("UPDATE private_audit SET author = ? WHERE author = ?", (new, old))
        conn.commit()


def rename_subject(old: str, new: str) -> None:
    """Move the SUBJECT reference: notes other people keep ABOUT this person.

    Safe for any actor to trigger, and it must run on every rename. It changes
    no ownership — each author still reads only their own rows — so it leaks
    nothing. Skipping it stranded every teammate's 1:1 journal about the
    renamed person under a name with no roster row: their brief rendered empty
    and their feedback-gap nudge reset to 'never'."""
    with closing(_connect()) as conn:
        conn.execute("UPDATE private_notes SET person = ? WHERE person = ?", (new, old))
        conn.commit()


def list_audit(author: str, limit: int = 100) -> list[dict]:
    """The author's own audit trail: adds, reads, briefs, deletes."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM private_audit WHERE author = ? ORDER BY id DESC LIMIT ?",
            (author, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def audit_brief(author: str, person: str) -> None:
    """Record that author pulled person's 1:1 brief (audit lives in
    private.db — the fact a brief was pulled is itself private)."""
    with closing(_connect()) as conn:
        _audit(conn, author, f"brief:{person.strip()}", None)
        conn.commit()


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
