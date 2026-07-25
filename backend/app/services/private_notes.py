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

import re
import sqlite3
from datetime import datetime, timedelta, timezone

from .. import config, db

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


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.PRIVATE_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.executescript(_SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def add_note(author: str, person: str, body: str, kind: str = "note") -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    person = person.strip()
    body = body.strip()
    if not person:
        raise ValueError("person is required")
    if not body:
        raise ValueError("note body is required")
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO private_notes (author, person, kind, body, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (author, person, kind, body, _now()),
        )
        note_id = cur.lastrowid
        conn.execute(
            "INSERT INTO private_audit (author, action, note_id, created_at) VALUES (?, ?, ?, ?)",
            (author, f"add_{kind}", note_id, _now()),
        )
        conn.commit()
    return {"id": note_id, "person": person, "kind": kind}


def list_notes(author: str, person: str = "") -> list[dict]:
    with _connect() as conn:
        if person:
            rows = conn.execute(
                "SELECT * FROM private_notes WHERE author = ? AND person = ? ORDER BY id DESC",
                (author, person.strip()),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM private_notes WHERE author = ? ORDER BY id DESC LIMIT 200",
                (author,),
            ).fetchall()
        return [dict(r) for r in rows]


def feedback_gap_days(author: str, person: str) -> int | None:
    """Days since the author's last feedback note for person; None if never.
    Computed at read time for the author's own page — never stored, never
    notified, never aggregated (anti-surveillance rule)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT MAX(created_at) AS ts FROM private_notes"
            " WHERE author = ? AND person = ? AND kind = 'feedback'",
            (author, person.strip()),
        ).fetchone()
    if not row or not row["ts"]:
        return None
    last = datetime.fromisoformat(row["ts"])
    return (datetime.now(timezone.utc) - last).days


def one_on_one_brief(person: str, days: int = 14) -> dict:
    """Deterministic 'since last time' brief from TEAM-VISIBLE platform data
    only. Every section degrades to empty pre-adoption."""
    person = person.strip()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    return {
        "person": person,
        "since": since,
        "standups": db.query(
            "SELECT * FROM standups WHERE author = ? AND created_at >= ? ORDER BY id DESC LIMIT 5",
            (person, since),
        ),
        "open_blockers": db.query(
            "SELECT * FROM blockers WHERE owner = ? AND status != 'resolved' ORDER BY id DESC",
            (person,),
        ),
        "open_questions": db.query(
            "SELECT * FROM questions WHERE assigned_to = ? AND status = 'open' ORDER BY id",
            (person,),
        ),
        "in_progress": db.query(
            "SELECT id, title, updated_at FROM tasks WHERE assignee = ?"
            " AND status = 'in_progress' ORDER BY updated_at",
            (person,),
        ),
        "recently_done": db.query(
            "SELECT id, title, completed_at FROM tasks WHERE assignee = ?"
            " AND completed_at >= ? ORDER BY completed_at DESC LIMIT 10",
            (person, since),
        ),
        "commitments_made": db.query(
            "SELECT * FROM commitments WHERE created_by = ? AND created_at >= ? ORDER BY id DESC",
            (person, since),
        ),
    }


_FB = re.compile(r"^\s*fb:\s*(?P<person>[^—:-]+?)\s*[—:-]\s*(?P<body>.+)$", re.I | re.S)


def parse_feedback(text: str) -> tuple[str, str] | None:
    """Parse 'fb: <person> — <note>' (also ':' or '-' separators).
    Returns (person, body) or None if the text is not an fb: line."""
    if not re.match(r"^\s*fb:", text, re.I):
        return None
    m = _FB.match(text)
    if not m:
        raise ValueError("feedback format: fb: <person> — <note>")
    return m.group("person").strip(), m.group("body").strip()
