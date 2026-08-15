"""Named chat threads with folders, plus the UI transcript log.

The transcript (chat_messages) is the provider-agnostic history the chat
sidebar rehydrates from — written by the chat route for mock and real
providers alike, so history is keyless-first. It is the UI's copy; the
Strands session files remain the model's own conversation memory.

Threads are owner-scoped by the trusted-network identity (X-User) — a
convenience boundary, not privacy: anything you'd mark fb: belongs in
⌘K capture or the People page, and the chat route enforces that by
refusing fb: lines before any logging. When OIDC lands, these routes
are first in line for strong identity.
"""

import hashlib
import re
import shutil

from .. import config, db
from . import scope
from .users import fold

_THREAD_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
TITLE_LEN = 60


def _check_id(thread_id: str) -> str:
    if not _THREAD_ID.fullmatch(thread_id):
        raise ValueError("invalid thread id")
    return thread_id


DEFAULT_PREFIX = "default-"
# ":" is outside _THREAD_ID's charset, so routes/chat.py's sanitizer strips it
# from anything a caller sends. That is the whole guarantee: a persona session
# id cannot be typed, only minted here from a base the caller already owns.
# "--" held this role and was forgeable — `abc--growth-mentor` sanitized clean,
# named no thread row, and claim_thread waved it through to the session of
# whoever owned `abc`.
PERSONA_SEP = ":"
_LEGACY_PERSONA_SEP = "--"


def persona_session_id(thread_id: str, persona: str) -> str:
    return f"{thread_id}{PERSONA_SEP}{persona}"


def default_thread_id(owner: str) -> str:
    """The thread a caller that names none gets — one per person.

    The literal 'default' was ONE row shared by every caller that omitted a
    thread id (the ChatRequest field default, so an omitted id and an explicit
    'default' are indistinguishable). Each of them restored the same
    model-side session, so a scripted client answered out of whoever posted
    last. Hashed rather than the name itself: two roster names that differ
    only in characters the thread-id charset strips would collide on one row.
    The hash is derived, not secret — claim_thread refuses a mismatched
    claim on this shape, because the roster is readable and a guessable id
    that the first caller owns is a squat.
    """
    return f"{DEFAULT_PREFIX}{hashlib.sha256(owner.encode()).hexdigest()[:16]}"


def claim_thread(thread_id: str, owner: str) -> str:
    """Take the thread for this owner, or refuse. Must run BEFORE the id
    reaches build_agent.

    log_message below refuses to cross-file a transcript, but it runs after
    build_agent has already restored the model-side conversation, and
    agents/session_store.py keys on session_id alone with no owner column.
    So naming another person's thread id answered the caller out of that
    person's history: the transcript write was refused and their sidebar
    stayed empty, which is why nothing on any surface showed what had gone
    out. Claiming here is also what makes an orphaned session unreachable —
    a stream cancelled between build_agent and the first log_message leaves
    session rows behind with no thread row to guard them.

    NotFound, not a refusal that names the owner: main.py's rule is that an
    owner-scoped miss is a 404 everywhere, because any other status confirms
    the row exists.
    """
    _check_id(thread_id)
    # A default- id is derived from a name every caller can read off the
    # roster, and first claim wins. Without this wall, one POST to a
    # teammate's computed id took their unnamed thread for good: every later
    # message of theirs 404s, and they cannot delete it to take it back.
    if thread_id.startswith(DEFAULT_PREFIX) and thread_id != default_thread_id(owner):
        raise db.NotFound(f"no chat '{thread_id}' for {owner}")
    now = db.now()
    db.execute(
        "INSERT INTO chat_threads (id, owner, title, created_at, updated_at)"
        " VALUES (?, ?, 'New chat', ?, ?)"
        " ON CONFLICT DO NOTHING",
        (thread_id, owner, now, now),
    )
    row = db.query_one("SELECT owner FROM chat_threads WHERE id = ?", (thread_id,))
    if not row or row["owner"] != owner:
        raise db.NotFound(f"no chat '{thread_id}' for {owner}")
    return thread_id


def _title_from(text: str) -> str:
    # "/as growth-mentor how do I..." titles as the question, not the plumbing.
    # /flock belongs here too: routes/chat.py logs the RAW command on that path
    # (the /as path logs the stripped message), so without the second branch
    # every flock thread is named after the command instead of the question.
    text = re.sub(r"^/(?:as|flock)\s+[a-z0-9-]+\s+", "", text.strip(), flags=re.I)
    line = text.splitlines()[0].strip() if text.strip() else "New chat"
    return line[:TITLE_LEN] + ("…" if len(line) > TITLE_LEN else "")


# TITLE_PROMPT forbids each of these, which is the reason to strip them: a
# prompt names a failure because models produce it anyway. Order matters —
# the prefix comes off first, then the emphasis, then the quotes, or
# 'Name: "Ship it"' keeps its opening quote after the closing one is gone.
_TITLE_LABEL = re.compile(r"^(?:title|name)\s*[:\-]\s*", re.I)
# the SINGLE curly pair is escaped because ruff rejects those two marks in
# source as ambiguous with the ASCII quote (RUF001 in a string, RUF003 in
# this comment, which is why they are named here and not shown); the double
# pair is escaped only to keep the four together. A cloud model answers with them often — the straight
# pair alone leaves “Ship it” quoted in the sidebar.
_TITLE_QUOTES = "\"'" + "\u201c\u201d\u2018\u2019"


def _clean_title(text: str) -> str:
    """One bare line from whatever shape the model answered in."""
    stripped = text.strip()
    if not stripped:
        return ""
    line = _TITLE_LABEL.sub("", stripped.splitlines()[0].strip())
    line = line.strip("#*_ ").strip()
    return line.strip(_TITLE_QUOTES).strip()[:TITLE_LEN]


def pending_auto_title(thread_id: str, owner: str) -> tuple[str, str] | None:
    """(current title, first user message) for a thread that still carries the
    title _title_from derived, or None when no summary must run.

    The title IS the guard — no column records where a title came from. A
    thread the owner renamed through set_thread below no longer matches
    _title_from, so a summary that finishes after the rename finds no match
    and drops. Without this read the rename would be overwritten by a model
    call the owner never saw, and the name they typed is not recoverable.
    """
    _check_id(thread_id)
    row = db.query_one(
        "SELECT title FROM chat_threads WHERE id = ? AND owner = ?", (thread_id, owner)
    )
    if not row:
        return None
    firsts = db.query(
        "SELECT content FROM chat_messages WHERE thread_id = ? AND role = 'user'"
        " ORDER BY id LIMIT 2",
        (thread_id,),
    )
    # EXACTLY one user message, so a thread is summarized once and never again.
    # Every failure here leaves the title still matching _title_from — a
    # timeout, an empty answer, a model that echoes the derived line back.
    # Without this bound each of those retries on every later turn, and the
    # thread pays one model call per turn for the rest of its life.
    if len(firsts) != 1:
        return None
    if row["title"] != _title_from(firsts[0]["content"]):
        return None
    return row["title"], firsts[0]["content"]


def set_auto_title(thread_id: str, owner: str, previous: str, title: str) -> bool:
    """Compare-and-set a summarized title. False when the owner renamed the
    thread while the model ran: their name wins and the summary is dropped.

    updated_at is left alone on purpose. It orders the chat list, and the turn
    that triggered this summary already touched it — bumping it again here
    would reorder the list for a change the owner never made.
    """
    _check_id(thread_id)
    clean = _clean_title(title)
    if not clean:
        return False
    return bool(
        db.execute_rowcount(
            "UPDATE chat_threads SET title = ? WHERE id = ? AND owner = ? AND title = ?",
            (clean, thread_id, owner, previous),
        )
    )


def log_message(thread_id: str, owner: str, role: str, content: str) -> None:
    """Append to the transcript, creating/touching the thread row. Opening a
    chat in the UI leaves no residue: a row is born only when a turn is
    logged here, or when POST /api/chat claims the id above."""
    _check_id(thread_id)
    if role not in ("user", "assistant"):
        raise ValueError("role must be user or assistant")
    if not content.strip():
        return
    now = db.now()
    db.execute(
        "INSERT INTO chat_threads (id, owner, title, created_at, updated_at)"
        " VALUES (?, ?, 'New chat', ?, ?)"
        " ON CONFLICT DO NOTHING",
        (thread_id, owner, now, now),
    )
    row = db.query_one("SELECT owner FROM chat_threads WHERE id = ?", (thread_id,))
    if row and row["owner"] != owner:
        # id collision with someone else's thread (e.g. the shared "default"):
        # never cross-file a conversation into another owner's transcript
        return
    if role == "user":
        db.execute(
            "UPDATE chat_threads SET title = ? WHERE id = ? AND title = 'New chat'",
            (_title_from(content), thread_id),
        )
    db.execute(
        "INSERT INTO chat_messages (thread_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (thread_id, role, content, now),
    )
    db.execute("UPDATE chat_threads SET updated_at = ? WHERE id = ?", (now, thread_id))


# The sidebar's own bound. Most-recently-touched first, so the cap drops the
# threads nobody has opened in longest — the least wrong ones to drop, not
# none: past this number the oldest thread does leave the sidebar, and only a
# delete removes its rows.
THREAD_LIMIT = 500
# alphabetical, so the cap drops the tail of the list rather than the newest
FOLDER_LIMIT = 200
# One transcript. A thread that reaches this has already been trimmed on the
# model side by the conversation manager (agents/team_agent.py), so the cap
# bounds the RESPONSE, not the conversation.
MESSAGE_LIMIT = 1000


def list_threads(owner: str) -> list[dict]:
    return db.query(
        "SELECT id, title, folder, engagement_id, created_at, updated_at FROM chat_threads"
        " WHERE owner = ? ORDER BY updated_at DESC, id DESC LIMIT ?",
        (owner, THREAD_LIMIT),
    )


def _own(thread_id: str, owner: str) -> dict:
    _check_id(thread_id)
    row = db.query_one("SELECT * FROM chat_threads WHERE id = ? AND owner = ?", (thread_id, owner))
    if not row:
        # NotFound, not ValueError: main.py's rule is that entity-lookup
        # failures are 404 everywhere. This one answered 400, which the UI
        # hits on every new chat (it reads messages before the first send
        # stores the thread) and which reads as "your request was malformed"
        # when the request was fine and the row simply is not there yet.
        raise db.NotFound(f"no chat '{thread_id}' for {owner}")
    return row


def thread_contains(thread_id: str, needle: str) -> bool:
    """Existence-only content probe (no ownership check) — the chat route
    uses it to emit a persona masthead once per persona per thread on EVERY
    provider, including mock (which never creates a session dir). Transcripts
    are logged under the BASE thread id, so this must be probed there."""
    return bool(
        db.query_one(
            "SELECT 1 AS x FROM chat_messages WHERE thread_id = ? AND content LIKE ? LIMIT 1",
            (thread_id, f"%{needle}%"),
        )
    )


def get_messages(thread_id: str, owner: str) -> list[dict]:
    _own(thread_id, owner)
    # newest MESSAGE_LIMIT, handed back oldest-first. Ordering DESC in the
    # query and reversing here is what keeps the cap on the right end: a plain
    # "ORDER BY id LIMIT n" returns the START of a long thread and hides
    # everything the reader was last talking about.
    rows = db.query(
        "SELECT role, content, created_at FROM chat_messages WHERE thread_id = ?"
        " ORDER BY id DESC LIMIT ?",
        (thread_id, MESSAGE_LIMIT),
    )
    return rows[::-1]


FOLDER_LEN = 40


def create_folder(owner: str, name: str) -> dict:
    name = name.strip()[:FOLDER_LEN]
    if not name:
        raise ValueError("folder name is required")
    existing = _snap_folder(owner, name)
    db.execute(
        "INSERT INTO chat_folders (owner, name, created_at) VALUES (?, ?, ?)"
        " ON CONFLICT DO NOTHING",
        (owner, existing, db.now()),
    )
    return {"name": existing}


def list_folders(owner: str) -> list[str]:
    """Union of registered folders and any legacy folder still on a thread."""
    # Bounded like the two lists beside it. This one grows on a second axis —
    # the UNION picks up every distinct folder string ever set on a thread, so
    # it counts names that no chat_folders row remembers.
    rows = db.query(
        "SELECT name FROM chat_folders WHERE owner = ?"
        " UNION SELECT DISTINCT folder FROM chat_threads WHERE owner = ? AND folder != ''"
        " ORDER BY 1 LIMIT ?",
        (owner, owner, FOLDER_LIMIT),
    )
    return [r["name"] for r in rows]


def delete_folder(owner: str, name: str) -> dict:
    """Remove the folder; its chats become unfiled (never deleted)."""
    name = name.strip()
    unfiled = db.execute_rowcount(
        "UPDATE chat_threads SET folder = '' WHERE owner = ? AND folder = ?",
        (owner, name),
    )
    db.execute("DELETE FROM chat_folders WHERE owner = ? AND name = ?", (owner, name))
    return {"name": name, "unfiled": unfiled}


def _snap_folder(owner: str, wanted: str) -> str:
    """Case-insensitively reuse an existing folder spelling.

    Asks the database rather than scanning `list_folders`, which is capped:
    past FOLDER_LIMIT the scan stopped finding `zebra` for `Zebra`, and the
    unique index is BINARY-collated, so a second folder differing only in case
    was filed. A decision computed over a truncated list is the bug
    api_keys.active_key_count exists to avoid, one file over.
    """
    # Folded in Python, not in SQL. PostgreSQL's lower() is Unicode-aware, so
    # it would fold "Été"/"été" together — but it still is not users.fold(),
    # which also applies NFKC and strips zero-width joiners. One folding rule, in one place, or a
    # name can be the same person here and a different one on the roster.
    #
    # Both queries stay UNCAPPED: this reads one owner's folders, and the
    # docstring above is about a scan that stopped finding matches past
    # FOLDER_LIMIT.
    target = fold(wanted)
    for row in db.query("SELECT name FROM chat_folders WHERE owner = ?", (owner,)):
        if fold(row["name"]) == target:
            return str(row["name"])
    for row in db.query(
        "SELECT DISTINCT folder AS name FROM chat_threads WHERE owner = ? AND folder != ''",
        (owner,),
    ):
        if fold(row["name"]) == target:
            return str(row["name"])
    return wanted


def update_thread(
    thread_id: str,
    owner: str,
    *,
    title: str = "",
    folder: str | None = None,
    engagement_id: int | None = None,
) -> dict:
    _own(thread_id, owner)
    if engagement_id is not None:
        # 0 clears; anything else must be a real engagement, because the link
        # feeds cost attribution and a dangling id would silently bucket the
        # thread's spend under an engagement that never existed
        if engagement_id == 0:
            db.execute(
                "UPDATE chat_threads SET engagement_id = NULL, updated_at = ? WHERE id = ?",
                (db.now(), thread_id),
            )
        else:
            # filtered on the thread's OWNER, like every other link probe:
            # unfiltered it accepted a scoped id and refused an absent one,
            # and it attributed this thread's token spend to an engagement the
            # owner cannot read (services/scope.py::Viewer.for_actor).
            efrag, ep = scope.visible_filter(scope.Viewer.for_actor(owner), "engagements")
            if not db.query_one(
                f"SELECT 1 FROM engagements WHERE id = ? AND {efrag}",  # noqa: S608 — scope.visible_filter emits only bound marks
                (engagement_id, *ep),
            ):
                raise db.NotFound(scope.missing_text("engagements", engagement_id))
            db.execute(
                "UPDATE chat_threads SET engagement_id = ?, updated_at = ? WHERE id = ?",
                (engagement_id, db.now(), thread_id),
            )
    if title:
        db.execute(
            "UPDATE chat_threads SET title = ?, updated_at = ? WHERE id = ?",
            (title.strip()[:TITLE_LEN], db.now(), thread_id),
        )
    if folder is not None:
        wanted = folder.strip()[:FOLDER_LEN]
        if wanted:
            wanted = _snap_folder(owner, wanted)
            # register it: a folder emptied later must not vanish
            db.execute(
                "INSERT INTO chat_folders (owner, name, created_at) VALUES (?, ?, ?)"
                " ON CONFLICT DO NOTHING",
                (owner, wanted, db.now()),
            )
        db.execute(
            "UPDATE chat_threads SET folder = ?, updated_at = ? WHERE id = ?",
            (wanted, db.now(), thread_id),
        )
    return db.query_row("SELECT * FROM chat_threads WHERE id = ?", (thread_id,))


def delete_thread(thread_id: str, owner: str) -> dict:
    """Remove the thread, its transcript, the model-side sessions (including
    per-persona session variants) AND its flock traces — a deleted chat is
    gone."""
    from ..agents.session_store import delete_thread_sessions

    _own(thread_id, owner)
    db.execute("DELETE FROM chat_messages WHERE thread_id = ?", (thread_id,))
    db.execute("DELETE FROM chat_threads WHERE id = ?", (thread_id,))
    # a trace names the person, the thread, every member and what each spent,
    # and GET /api/flocks/traces?thread=<id> keeps serving it. Left behind, the
    # docstring above is false for every chat that ever called a flock.
    db.execute("DELETE FROM flock_traces WHERE thread_id = ?", (thread_id,))
    delete_thread_sessions(thread_id)
    # the pre-045 file store, until a cleanup release drops the directory:
    # leftover files must go too, or they linger for a thread that is gone.
    # Only the legacy separator here — the file store predates PERSONA_SEP,
    # so no file on disk carries the new one.
    for pattern in (f"session_{thread_id}", f"session_{thread_id}{_LEGACY_PERSONA_SEP}*"):
        for path in config.SESSIONS_DIR.glob(pattern):
            shutil.rmtree(path, ignore_errors=True)
    db.log_activity(owner, "delete_chat", f"thread {thread_id}")
    return {"id": thread_id, "deleted": True}
