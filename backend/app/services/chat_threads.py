"""Named chat threads with folders, plus the UI transcript log.

The transcript (chat_messages) is the provider-agnostic history the chat
sidebar rehydrates from — written by the chat route for mock and real
providers alike, so history is keyless-first. It is the UI's copy; the
Strands session files remain the model's own conversation memory.

Solo threads stay owner-scoped by the trusted-network identity (X-User). That
is a convenience boundary, not privacy: anything marked fb: belongs in quick
capture or the People page. Shared threads are different. Every read and write
requires strong identity plus an active membership, and a nonmember gets the
same not-found response as an absent thread.
"""

import hashlib
import json
import re
import secrets
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
        "INSERT INTO chat_threads"
        " (id, owner, title, created_at, updated_at, kind, created_by)"
        " VALUES (?, ?, 'New chat', ?, ?, 'solo', ?)"
        " ON CONFLICT DO NOTHING",
        (thread_id, owner, now, now, owner),
    )
    row = db.query_one("SELECT owner, kind FROM chat_threads WHERE id = ?", (thread_id,))
    if not row or row["owner"] != owner or row["kind"] != "solo":
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
        "SELECT title FROM chat_threads WHERE id = ? AND owner = ? AND kind = 'solo'",
        (thread_id, owner),
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
            "UPDATE chat_threads SET title = ?"
            " WHERE id = ? AND owner = ? AND kind = 'solo' AND title = ?",
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
        "INSERT INTO chat_threads"
        " (id, owner, title, created_at, updated_at, kind, created_by)"
        " VALUES (?, ?, 'New chat', ?, ?, 'solo', ?)"
        " ON CONFLICT DO NOTHING",
        (thread_id, owner, now, now, owner),
    )
    row = db.query_one("SELECT owner, kind FROM chat_threads WHERE id = ?", (thread_id,))
    if row and (row["owner"] != owner or row["kind"] != "solo"):
        # id collision with another owner or a private group: never cross-file
        # a solo conversation into a transcript this route does not own
        return
    if role == "user":
        db.execute(
            "UPDATE chat_threads SET title = ? WHERE id = ? AND title = 'New chat'",
            (_title_from(content), thread_id),
        )
    db.execute(
        "INSERT INTO chat_messages"
        " (thread_id, role, content, created_at, author_kind, author)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            thread_id,
            role,
            content,
            now,
            "human" if role == "user" else "legacy",
            owner if role == "user" else "",
        ),
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
        " WHERE owner = ? AND kind = 'solo' ORDER BY updated_at DESC, id DESC LIMIT ?",
        (owner, THREAD_LIMIT),
    )


def _own(thread_id: str, owner: str) -> dict:
    _check_id(thread_id)
    row = db.query_one(
        "SELECT * FROM chat_threads WHERE id = ? AND owner = ? AND kind = 'solo'",
        (thread_id, owner),
    )
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
        " UNION SELECT DISTINCT folder FROM chat_threads"
        " WHERE owner = ? AND kind = 'solo' AND folder != ''"
        " ORDER BY 1 LIMIT ?",
        (owner, owner, FOLDER_LIMIT),
    )
    return [r["name"] for r in rows]


def delete_folder(owner: str, name: str) -> dict:
    """Remove the folder; its chats become unfiled (never deleted)."""
    name = name.strip()
    unfiled = db.execute_rowcount(
        "UPDATE chat_threads SET folder = '' WHERE owner = ? AND kind = 'solo' AND folder = ?",
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
        "SELECT DISTINCT folder AS name FROM chat_threads"
        " WHERE owner = ? AND kind = 'solo' AND folder != ''",
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


SHARED_PREFIX = "shared-"
_MESSAGE_KEY = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_AGENT_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
_LEADING_AGENT = re.compile(r"^@([a-z0-9][a-z0-9-]{1,40})(?:\s+|$)")
MESSAGE_TEXT_LEN = 20_000
SHARED_AGENT_LIMIT = 4


def _shared_missing() -> db.NotFound:
    return db.NotFound("No shared chat was found.")


def _invitation_missing() -> db.NotFound:
    return db.NotFound("No invitation was found.")


def _lock_shared(thread_id: str) -> dict:
    _check_id(thread_id)
    row = db.query_one("SELECT * FROM chat_threads WHERE id = ? FOR UPDATE", (thread_id,))
    if not row or row["kind"] != "shared":
        raise _shared_missing()
    return row


def _active_member(thread_id: str, person: str) -> dict | None:
    return db.query_one(
        "SELECT * FROM chat_members WHERE thread_id = ? AND person = ? AND left_at IS NULL",
        (thread_id, person),
    )


def _advance_member_read(thread_id: str, person: str, message_id: int) -> None:
    db.execute(
        "UPDATE chat_members SET last_read_message_id = GREATEST(last_read_message_id, ?)"
        " WHERE thread_id = ? AND person = ? AND left_at IS NULL",
        (message_id, thread_id, person),
    )


def _require_member(thread_id: str, person: str, *, steward: bool = False) -> tuple[dict, dict]:
    row = db.query_one(
        "SELECT t.*, m.role AS member_role, m.joined_at, m.last_read_message_id"
        " FROM chat_threads t JOIN chat_members m ON m.thread_id = t.id"
        " WHERE t.id = ? AND t.kind = 'shared' AND m.person = ? AND m.left_at IS NULL",
        (thread_id, person),
    )
    if not row:
        raise _shared_missing()
    if steward and row["member_role"] != "steward":
        raise PermissionError("Only a shared-chat steward can do this.")
    return row, {
        "person": person,
        "role": row["member_role"],
        "joined_at": row["joined_at"],
    }


def _require_locked_member(
    thread_id: str, person: str, *, steward: bool = False
) -> tuple[dict, dict]:
    thread = _lock_shared(thread_id)
    member = _active_member(thread_id, person)
    if not member:
        raise _shared_missing()
    if steward and member["role"] != "steward":
        raise PermissionError("Only a shared-chat steward can do this.")
    return thread, member


def _require_open(thread: dict) -> None:
    if thread.get("archived_at"):
        raise db.Conflict("This shared chat is archived. Restore it before you continue.")


def _hold_identities(*names: str) -> None:
    for identity in sorted({fold(name) for name in names if name}):
        db.name_lock(db.LOCK_IDENTITY, identity)


def _system_message(thread_id: str, content: str, created_at: str) -> int:
    return db.execute(
        "INSERT INTO chat_messages"
        " (thread_id, role, content, created_at, author_kind, author)"
        " VALUES (?, 'assistant', ?, ?, 'system', 'Skein') RETURNING id",
        (thread_id, content, created_at),
    )


def _public_message(row: dict) -> dict:
    return {
        key: row.get(key)
        for key in (
            "id",
            "thread_id",
            "role",
            "author_kind",
            "author",
            "content",
            "created_at",
            "turn_id",
            "reply_to_message_id",
            "deleted_at",
        )
    }


def _shared_details(thread_id: str, person: str) -> dict:
    thread, member = _require_member(thread_id, person)
    member_rows = db.query(
        "SELECT m.person, m.role, m.joined_at, m.last_read_message_id,"
        " COALESCE(u.kind, 'human') AS kind"
        " FROM chat_members m LEFT JOIN users u ON u.name = m.person"
        " WHERE m.thread_id = ? AND m.left_at IS NULL"
        " ORDER BY m.role DESC, m.person",
        (thread_id,),
    )
    members = [
        {
            "person": row["person"],
            "role": row["role"],
            "joined_at": row["joined_at"],
            # Read cursors feed the room's "Seen by" line. They go only to
            # members (this whole payload is membership-gated) and only for
            # humans — an agent's cursor never moves and would read as a
            # participant who never looks.
            **(
                {"kind": "agent"}
                if row["kind"] == "agent"
                else {"last_read_message_id": int(row["last_read_message_id"])}
            ),
        }
        for row in member_rows
    ]
    engagement = (
        db.query_one(
            f"SELECT name FROM engagements WHERE id = ? AND {scope.WORKSPACE_ONLY}",  # noqa: S608 — fixed scope fragment
            (thread["engagement_id"],),
        )
        if thread.get("engagement_id")
        else None
    )
    pending = (
        db.query(
            "SELECT id, person, invited_by, created_at FROM chat_invitations"
            " WHERE thread_id = ? AND status = 'pending' ORDER BY id",
            (thread_id,),
        )
        if member["role"] == "steward"
        else []
    )
    return {
        "id": thread["id"],
        "kind": "shared",
        "title": thread["title"],
        "created_by": thread["created_by"],
        "created_at": thread["created_at"],
        "updated_at": thread["updated_at"],
        "archived_at": thread.get("archived_at"),
        "engagement_id": thread.get("engagement_id"),
        "engagement_name": str((engagement or {}).get("name") or ""),
        "viewer": person,
        "role": member["role"],
        "members": members,
        "pending_invitations": pending,
    }


def create_shared_chat(title: str, creator: str) -> dict:
    title = title.strip()[:TITLE_LEN]
    if not title:
        raise ValueError("shared chat title is required")
    thread_id = f"{SHARED_PREFIX}{secrets.token_hex(16)}"
    now = db.now()
    with db.transaction():
        _hold_identities(creator)
        db.execute(
            "INSERT INTO chat_threads"
            " (id, owner, title, folder, created_at, updated_at, kind, created_by)"
            " VALUES (?, ?, ?, '', ?, ?, 'shared', ?)",
            (thread_id, creator, title, now, now, creator),
        )
        db.execute(
            "INSERT INTO chat_members"
            " (thread_id, person, role, joined_at, added_by)"
            " VALUES (?, ?, 'steward', ?, ?)",
            (thread_id, creator, now, creator),
        )
        db.log_activity(creator, "create_shared_chat", f"thread {thread_id}")
    return _shared_details(thread_id, creator)


def list_shared_chats(person: str) -> list[dict]:
    return db.query(
        "SELECT t.id, t.title, t.created_by, t.created_at, t.updated_at,"
        " t.archived_at, t.engagement_id, m.role, m.last_read_message_id,"
        " (SELECT COUNT(*) FROM chat_members members"
        "  WHERE members.thread_id = t.id AND members.left_at IS NULL) AS member_count,"
        " (SELECT COUNT(*) FROM chat_messages messages"
        "  WHERE messages.thread_id = t.id AND messages.id > m.last_read_message_id)"
        " AS unread_count"
        " FROM chat_threads t JOIN chat_members m ON m.thread_id = t.id"
        " WHERE t.kind = 'shared' AND m.person = ? AND m.left_at IS NULL"
        " ORDER BY t.updated_at DESC, t.id DESC LIMIT ?",
        (person, THREAD_LIMIT),
    )


def unread_shared_count(person: str) -> int:
    """The Chat nav badge's number: unread messages across this person's
    active shared-chat memberships, plus their pending invitations. Callers
    pass a STRONG identity's name or "" — a weak identity cannot read shared
    chats, so its badge must read 0, not count rooms it cannot open."""
    if not person:
        return 0
    row = db.query_one(
        "SELECT"
        " (SELECT COUNT(*) FROM chat_messages messages"
        "  JOIN chat_members m ON m.thread_id = messages.thread_id"
        "  JOIN chat_threads t ON t.id = m.thread_id"
        "  WHERE t.kind = 'shared' AND m.person = ? AND m.left_at IS NULL"
        "  AND messages.id > m.last_read_message_id)"
        " + (SELECT COUNT(*) FROM chat_invitations"
        "    WHERE person = ? AND status = 'pending') AS waiting",
        (person, person),
    )
    return int(row["waiting"]) if row else 0


def get_shared_chat(thread_id: str, person: str) -> dict:
    return _shared_details(thread_id, person)


def add_shared_chat_agent(
    thread_id: str,
    actor: str,
    agent: str,
    *,
    share_history: bool,
) -> dict:
    if not share_history:
        raise ValueError("history sharing must be confirmed")
    if not _AGENT_SLUG.fullmatch(agent):
        raise ValueError("agent is not on the configured bench")
    from . import personas, users

    if agent not in personas.bench_slugs():
        raise ValueError("agent is not on the configured bench")
    now = db.now()
    with db.transaction():
        # Reserve both identities before the thread row. The opposite order can
        # deadlock against a concurrent identity rename that then reaches this chat.
        _hold_identities(actor, agent)
        agent_row = users.ensure_agent_identity(agent)
        if not agent_row["active"]:
            raise ValueError("agent is not active")
        thread, _ = _require_locked_member(thread_id, actor, steward=True)
        _require_open(thread)
        existing = _active_member(thread_id, agent)
        if existing:
            return _shared_details(thread_id, actor)
        count = db.query_row(
            "SELECT COUNT(*) AS n FROM chat_members m JOIN users u ON u.name = m.person"
            " WHERE m.thread_id = ? AND m.left_at IS NULL AND u.kind = 'agent'",
            (thread_id,),
        )["n"]
        if int(count) >= SHARED_AGENT_LIMIT:
            raise db.Conflict("This shared chat already has four agents.")
        db.execute(
            "INSERT INTO chat_members"
            " (thread_id, person, role, joined_at, left_at, added_by, last_read_message_id)"
            " VALUES (?, ?, 'member', ?, NULL, ?, 0)"
            " ON CONFLICT (thread_id, person) DO UPDATE SET role = 'member',"
            " joined_at = EXCLUDED.joined_at, left_at = NULL,"
            " added_by = EXCLUDED.added_by, last_read_message_id = 0",
            (thread_id, agent, now, actor),
        )
        _system_message(
            thread_id,
            f"{agent} was added as an agent. It stays silent until a participant calls @{agent}.",
            now,
        )
        db.execute("UPDATE chat_threads SET updated_at = ? WHERE id = ?", (now, thread_id))
        db.log_activity(actor, "add_shared_chat_agent", f"thread {thread_id}")
    return _shared_details(thread_id, actor)


def invite_to_shared_chat(
    thread_id: str, inviter: str, person: str, *, share_history: bool
) -> dict:
    if not share_history:
        raise ValueError("history sharing must be confirmed")
    from . import users

    target = users.resolve_teammate(person, actor=inviter, label="person", allow_team=False)
    if target == inviter:
        raise ValueError("that person is already a participant")
    now = db.now()
    with db.transaction():
        # A rename claims this identity before it rewrites memberships. Take the
        # same lock first, then re-read the roster row, or an invitation can land
        # under the name a concurrent rename just removed.
        _hold_identities(inviter, target)
        target_row = db.query_one("SELECT kind, active FROM users WHERE name = ?", (target,))
        if not target_row or not target_row["active"]:
            raise ValueError("person is not an active teammate")
        if target_row["kind"] != "human":
            raise ValueError("agents cannot be invited as human participants")
        thread, _ = _require_locked_member(thread_id, inviter, steward=True)
        _require_open(thread)
        if _active_member(thread_id, target):
            raise db.Conflict("That person is already a participant.")
        pending = db.query_one(
            "SELECT * FROM chat_invitations"
            " WHERE thread_id = ? AND person = ? AND status = 'pending'",
            (thread_id, target),
        )
        if pending:
            return pending
        invitation_id = db.execute(
            "INSERT INTO chat_invitations"
            " (thread_id, person, invited_by, share_history, status, created_at)"
            " VALUES (?, ?, ?, TRUE, 'pending', ?) RETURNING id",
            (thread_id, target, inviter, now),
        )
        db.log_activity(inviter, "invite_to_shared_chat", f"invitation {invitation_id}")
        from .notifications import notify

        notify(
            target,
            "You have a private shared-chat invitation.",
            tier="immediate",
            link="/chat",
            source_entity="chat_invitation",
            source_id=invitation_id,
        )
    return db.query_row("SELECT * FROM chat_invitations WHERE id = ?", (invitation_id,))


def list_shared_chat_invitations(person: str) -> list[dict]:
    return db.query(
        "SELECT id, invited_by, created_at FROM chat_invitations"
        " WHERE person = ? AND status = 'pending' ORDER BY id",
        (person,),
    )


def revoke_shared_chat_invitation(thread_id: str, actor: str, invitation_id: int) -> dict:
    with db.transaction():
        _hold_identities(actor)
        _require_locked_member(thread_id, actor, steward=True)
        invitation = db.query_one(
            "SELECT id, person FROM chat_invitations"
            " WHERE id = ? AND thread_id = ? AND status = 'pending' FOR UPDATE",
            (invitation_id, thread_id),
        )
        if not invitation:
            raise db.NotFound("No pending invitation was found.")
        now = db.now()
        db.execute(
            "UPDATE chat_invitations SET status = 'revoked', responded_at = ? WHERE id = ?",
            (now, invitation_id),
        )
        _clear_invitation_notice(invitation_id, str(invitation["person"]))
        db.log_activity(actor, "revoke_shared_chat_invitation", f"invitation {invitation_id}")
    return {"id": invitation_id, "status": "revoked"}


def _clear_invitation_notice(invitation_id: int, person: str) -> None:
    db.execute(
        "UPDATE notifications SET read_at = ? WHERE source_entity = 'chat_invitation'"
        ' AND source_id = ? AND "user" = ? AND read_at IS NULL',
        (db.now(), invitation_id, person),
    )


def _invitation_thread(invitation_id: int, person: str) -> str:
    row = db.query_one(
        "SELECT thread_id FROM chat_invitations WHERE id = ? AND person = ?",
        (invitation_id, person),
    )
    if not row:
        raise _invitation_missing()
    return str(row["thread_id"])


def accept_shared_chat_invitation(invitation_id: int, person: str) -> dict:
    now = db.now()
    with db.transaction():
        _hold_identities(person)
        thread_id = _invitation_thread(invitation_id, person)
        thread = _lock_shared(thread_id)
        _require_open(thread)
        invitation = db.query_one(
            "SELECT * FROM chat_invitations WHERE id = ? AND person = ? FOR UPDATE",
            (invitation_id, person),
        )
        if not invitation:
            raise _invitation_missing()
        if invitation["status"] == "accepted":
            return _shared_details(thread_id, person)
        if invitation["status"] != "pending":
            raise db.Conflict("This invitation is no longer pending.")
        db.execute(
            "INSERT INTO chat_members"
            " (thread_id, person, role, joined_at, left_at, added_by, last_read_message_id)"
            " VALUES (?, ?, 'member', ?, NULL, ?, 0)"
            " ON CONFLICT (thread_id, person) DO UPDATE SET"
            " role = CASE WHEN chat_members.left_at IS NULL"
            "   THEN chat_members.role ELSE 'member' END,"
            " joined_at = CASE WHEN chat_members.left_at IS NULL"
            "   THEN chat_members.joined_at ELSE EXCLUDED.joined_at END,"
            " left_at = NULL,"
            " added_by = CASE WHEN chat_members.left_at IS NULL"
            "   THEN chat_members.added_by ELSE EXCLUDED.added_by END,"
            " last_read_message_id = CASE WHEN chat_members.left_at IS NULL"
            "   THEN chat_members.last_read_message_id ELSE 0 END",
            (thread_id, person, now, invitation["invited_by"]),
        )
        db.execute(
            "UPDATE chat_invitations SET status = 'accepted', responded_at = ? WHERE id = ?",
            (now, invitation_id),
        )
        _clear_invitation_notice(invitation_id, person)
        _system_message(thread_id, f"{person} joined the private shared chat.", now)
        db.execute("UPDATE chat_threads SET updated_at = ? WHERE id = ?", (now, thread_id))
        db.log_activity(person, "accept_shared_chat", f"invitation {invitation_id}")
    return _shared_details(thread_id, person)


def decline_shared_chat_invitation(invitation_id: int, person: str) -> dict:
    with db.transaction():
        _hold_identities(person)
        thread_id = _invitation_thread(invitation_id, person)
        _lock_shared(thread_id)
        invitation = db.query_one(
            "SELECT * FROM chat_invitations WHERE id = ? AND person = ? FOR UPDATE",
            (invitation_id, person),
        )
        if not invitation:
            raise _invitation_missing()
        if invitation["status"] == "declined":
            return {"id": invitation_id, "status": "declined"}
        if invitation["status"] != "pending":
            raise db.Conflict("This invitation is no longer pending.")
        now = db.now()
        db.execute(
            "UPDATE chat_invitations SET status = 'declined', responded_at = ? WHERE id = ?",
            (now, invitation_id),
        )
        _clear_invitation_notice(invitation_id, person)
        db.log_activity(person, "decline_shared_chat", f"invitation {invitation_id}")
    return {"id": invitation_id, "status": "declined"}


def _active_agent_run(thread_id: str, agent: str = "") -> bool:
    if agent:
        row = db.query_one(
            "SELECT 1 FROM chat_agent_runs"
            " WHERE thread_id = ? AND agent = ?"
            " AND (status IN ('pending', 'running') OR execution_active = TRUE) LIMIT 1",
            (thread_id, agent),
        )
    else:
        row = db.query_one(
            "SELECT 1 FROM chat_agent_runs"
            " WHERE thread_id = ? AND (status IN ('pending', 'running') OR execution_active = TRUE) LIMIT 1",
            (thread_id,),
        )
    return row is not None


def _invited_agent(thread_id: str, agent: str) -> bool:
    return (
        db.query_one(
            "SELECT 1 FROM chat_members m JOIN users u ON u.name = m.person"
            " WHERE m.thread_id = ? AND m.person = ? AND m.left_at IS NULL"
            " AND u.kind = 'agent' AND u.active = 1",
            (thread_id, agent),
        )
        is not None
    )


def _leading_agent_mentions(content: str) -> list[str]:
    mentions: list[str] = []
    rest = content.lstrip()
    while match := _LEADING_AGENT.match(rest):
        if match[1] not in mentions:
            mentions.append(match[1])
        rest = rest[match.end() :]
    return mentions


def post_shared_message(
    thread_id: str,
    person: str,
    content: str,
    client_key: str,
    *,
    invoke_agent: str = "",
    invoke_agents: list[str] | None = None,
    requester_subject: dict | None = None,
) -> dict:
    if not _MESSAGE_KEY.fullmatch(client_key):
        raise ValueError("invalid message key")
    if not content.strip():
        raise ValueError("message is required")
    if len(content) > MESSAGE_TEXT_LEN:
        raise ValueError("message is too long")
    agents = list(
        dict.fromkeys(([invoke_agent] if invoke_agent else []) + list(invoke_agents or []))
    )
    if len(agents) > SHARED_AGENT_LIMIT:
        raise ValueError("one message can call at most four agents")
    if any(not _AGENT_SLUG.fullmatch(agent) for agent in agents):
        raise ValueError("agent is not available in this shared chat")
    if agents and set(_leading_agent_mentions(content)) != set(agents):
        raise ValueError("agent calls must match the leading @mentions in the message")
    from . import mentions

    people, mentioned_agents = mentions.names_in(content, actor=person)
    identity_names = {person, *people, *mentioned_agents, *agents}
    with db.transaction():
        _hold_identities(*identity_names)
        thread, _ = _require_locked_member(thread_id, person)
        _require_open(thread)
        existing = db.query_one(
            "SELECT * FROM chat_messages WHERE thread_id = ? AND author = ? AND client_key = ?",
            (thread_id, person, client_key),
        )
        if existing:
            runs = db.query(
                "SELECT agent, status FROM chat_agent_runs WHERE trigger_message_id = ?",
                (existing["id"],),
            )
            if {run["agent"] for run in runs} != set(agents):
                raise db.Conflict("This message key already names a different agent call.")
            if any(run["status"] == "pending" for run in runs):
                from . import shared_chat_agents

                db.on_commit(shared_chat_agents.kick_after_commit)
            _advance_member_read(thread_id, person, int(existing["id"]))
            return _public_message(existing)
        if any(not _invited_agent(thread_id, agent) for agent in agents):
            raise ValueError("agent is not available in this shared chat")
        now = db.now()
        batch_id = secrets.token_hex(16) if agents else ""
        turn_id = batch_id
        message_id = db.execute(
            "INSERT INTO chat_messages"
            " (thread_id, role, content, created_at, author_kind, author, client_key, turn_id)"
            " VALUES (?, 'user', ?, ?, 'human', ?, ?, ?) RETURNING id",
            (thread_id, content, now, person, client_key, turn_id),
        )
        _advance_member_read(thread_id, person, int(message_id))
        if agents:
            saved_subject = json.dumps(requester_subject or {}, separators=(",", ":"))
            for agent in agents:
                run_turn_id = batch_id if len(agents) == 1 else secrets.token_hex(16)
                db.execute(
                    "INSERT INTO chat_agent_runs"
                    " (turn_id, batch_id, thread_id, trigger_message_id, agent, requested_by,"
                    " requester_subject, status, requested_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                    (
                        run_turn_id,
                        batch_id,
                        thread_id,
                        message_id,
                        agent,
                        person,
                        saved_subject,
                        now,
                    ),
                )
            from . import shared_chat_agents

            db.on_commit(shared_chat_agents.kick_after_commit)
        mentions.scan_shared_message(
            thread_id,
            message_id,
            content,
            actor=person,
        )
        db.execute("UPDATE chat_threads SET updated_at = ? WHERE id = ?", (now, thread_id))
        if agents:
            db.log_activity(person, "invoke_shared_chat_agent", f"message {message_id}")
        else:
            db.log_activity(person, "post_shared_chat_message", f"message {message_id}")
    return _public_message(db.query_row("SELECT * FROM chat_messages WHERE id = ?", (message_id,)))


def get_shared_messages(
    thread_id: str,
    person: str,
    *,
    after: int | None = None,
    before: int | None = None,
) -> list[dict]:
    _require_member(thread_id, person)
    if after is not None and before is not None:
        raise ValueError("use one shared-chat message cursor")
    if after is not None:
        rows = db.query(
            "SELECT * FROM chat_messages WHERE thread_id = ? AND id > ? ORDER BY id LIMIT ?",
            (thread_id, after, MESSAGE_LIMIT),
        )
    elif before is not None:
        rows = db.query(
            "SELECT * FROM chat_messages WHERE thread_id = ? AND id < ? ORDER BY id DESC LIMIT ?",
            (thread_id, before, MESSAGE_LIMIT),
        )[::-1]
    else:
        rows = db.query(
            "SELECT * FROM chat_messages WHERE thread_id = ? ORDER BY id DESC LIMIT ?",
            (thread_id, MESSAGE_LIMIT),
        )[::-1]
    return [_public_message(row) for row in rows]


def delete_shared_message(thread_id: str, person: str, message_id: int) -> dict:
    """Author-delete with a tombstone: the text leaves the database, the row
    and its attribution stay, so reply references, run triggers, and read
    cursors keep resolving."""
    with db.transaction():
        _hold_identities(person)
        thread, _ = _require_locked_member(thread_id, person)
        _require_open(thread)
        row = db.query_one(
            "SELECT * FROM chat_messages WHERE id = ? AND thread_id = ? FOR UPDATE",
            (message_id, thread_id),
        )
        if not row:
            raise db.NotFound("No message was found.")
        if row["author_kind"] != "human" or row["author"] != person:
            raise PermissionError("Only the author can delete a message.")
        if row["deleted_at"]:
            return _public_message(row)
        live = db.query_one(
            "SELECT 1 FROM chat_agent_runs WHERE trigger_message_id = ?"
            " AND (status IN ('pending', 'running') OR execution_active = TRUE) LIMIT 1",
            (message_id,),
        )
        if live:
            raise db.Conflict(
                "An agent is answering this message. Wait for the response, then delete."
            )
        updated = db.query_row(
            "UPDATE chat_messages SET content = '', deleted_at = ? WHERE id = ? RETURNING *",
            (db.now(), message_id),
        )
        db.log_activity(
            person,
            "delete_shared_chat_message",
            f"thread {thread_id} message {message_id}",
        )
        return _public_message(updated)


def list_shared_agent_runs(thread_id: str, person: str, *, after: int = 0) -> list[dict]:
    _require_member(thread_id, person)
    select = (
        "SELECT turn_id, batch_id, trigger_message_id, response_message_id, agent, requested_by,"
        " status, requested_at, started_at, finished_at, error_code"
        " FROM chat_agent_runs WHERE thread_id = ? AND trigger_message_id > ?"
    )
    if after:
        return db.query(
            f"{select} ORDER BY trigger_message_id, turn_id LIMIT ?",
            (thread_id, after, MESSAGE_LIMIT),
        )
    return db.query(
        f"{select} ORDER BY trigger_message_id DESC, turn_id DESC LIMIT ?",
        (thread_id, 0, MESSAGE_LIMIT),
    )[::-1]


def mark_shared_chat_read(thread_id: str, person: str, message_id: int) -> dict:
    with db.transaction():
        _hold_identities(person)
        _require_locked_member(thread_id, person)
        latest = db.query_row(
            "SELECT COALESCE(MAX(id), 0) AS id FROM chat_messages WHERE thread_id = ?",
            (thread_id,),
        )["id"]
        seen = min(max(0, message_id), int(latest))
        stored = db.execute(
            "UPDATE chat_members SET last_read_message_id = GREATEST(last_read_message_id, ?)"
            " WHERE thread_id = ? AND person = ? AND left_at IS NULL"
            " RETURNING last_read_message_id",
            (seen, thread_id, person),
        )
    return {"thread_id": thread_id, "last_read_message_id": stored}


def set_shared_member_role(thread_id: str, actor: str, person: str, role: str) -> dict:
    if role not in ("steward", "member"):
        raise ValueError("invalid shared-chat role")
    with db.transaction():
        _hold_identities(actor, person)
        _require_locked_member(thread_id, actor, steward=True)
        target = _active_member(thread_id, person)
        if not target:
            raise _shared_missing()
        target_user = db.query_one("SELECT kind FROM users WHERE name = ?", (person,))
        if target_user and target_user["kind"] == "agent" and role == "steward":
            raise ValueError("an agent cannot steward a shared chat")
        if target["role"] == "steward" and role == "member":
            stewards = db.query_row(
                "SELECT COUNT(*) AS n FROM chat_members"
                " WHERE thread_id = ? AND role = 'steward' AND left_at IS NULL",
                (thread_id,),
            )["n"]
            if int(stewards) <= 1:
                raise db.Conflict("A shared chat must keep at least one steward.")
        db.execute(
            "UPDATE chat_members SET role = ?"
            " WHERE thread_id = ? AND person = ? AND left_at IS NULL",
            (role, thread_id, person),
        )
        db.log_activity(actor, "set_shared_chat_role", f"thread {thread_id}")
    return {"thread_id": thread_id, "person": person, "role": role}


def _leave_shared_chat(
    thread_id: str,
    actor: str,
    person: str,
    *,
    steward: bool,
    agent_only: bool = False,
) -> dict:
    with db.transaction():
        _hold_identities(actor, person)
        _require_locked_member(thread_id, actor, steward=steward)
        target = _active_member(thread_id, person)
        if not target:
            raise _shared_missing()
        target_user = db.query_one("SELECT kind FROM users WHERE name = ?", (person,))
        if agent_only and (not target_user or target_user["kind"] != "agent"):
            raise _shared_missing()
        if target_user and target_user["kind"] == "agent" and _active_agent_run(thread_id, person):
            raise db.Conflict("Wait for the agent response before you remove this agent.")
        if target_user and target_user["kind"] == "human":
            running = db.query_one(
                "SELECT 1 FROM chat_agent_runs WHERE thread_id = ? AND requested_by = ?"
                " AND (status = 'running' OR execution_active = TRUE) LIMIT 1",
                (thread_id, person),
            )
            if running:
                raise db.Conflict("Wait for the agent response before you remove this participant.")
            now = db.now()
            db.execute(
                "UPDATE chat_agent_runs SET status = 'refused', finished_at = ?,"
                " error_code = 'requester_removed'"
                " WHERE thread_id = ? AND requested_by = ? AND status = 'pending'",
                (now, thread_id, person),
            )
        if target["role"] == "steward":
            stewards = db.query_row(
                "SELECT COUNT(*) AS n FROM chat_members"
                " WHERE thread_id = ? AND role = 'steward' AND left_at IS NULL",
                (thread_id,),
            )["n"]
            if int(stewards) <= 1:
                raise db.Conflict("A shared chat must keep at least one steward.")
        now = db.now()
        if target_user and target_user["kind"] == "human":
            db.execute(
                "UPDATE notifications notice SET read_at = ? FROM chat_messages message"
                " WHERE notice.source_entity = 'chat_message'"
                ' AND notice.source_id = message.id AND notice."user" = ?'
                " AND notice.read_at IS NULL AND message.thread_id = ?",
                (now, person, thread_id),
            )
        db.execute(
            "UPDATE chat_members SET left_at = ?"
            " WHERE thread_id = ? AND person = ? AND left_at IS NULL",
            (now, thread_id, person),
        )
        db.execute("UPDATE chat_threads SET updated_at = ? WHERE id = ?", (now, thread_id))
        if actor == person:
            db.log_activity(actor, "leave_shared_chat", f"thread {thread_id}")
        else:
            db.log_activity(actor, "remove_shared_chat_member", f"thread {thread_id}")
    return {"thread_id": thread_id, "person": person, "left": True}


def leave_shared_chat(thread_id: str, person: str) -> dict:
    return _leave_shared_chat(thread_id, person, person, steward=False)


def remove_shared_chat_member(thread_id: str, actor: str, person: str) -> dict:
    return _leave_shared_chat(thread_id, actor, person, steward=True)


def remove_shared_chat_agent(thread_id: str, actor: str, agent: str) -> dict:
    return _leave_shared_chat(
        thread_id,
        actor,
        agent,
        steward=True,
        agent_only=True,
    )


def set_shared_chat_archived(thread_id: str, actor: str, archived: bool) -> dict:
    with db.transaction():
        _hold_identities(actor)
        _require_locked_member(thread_id, actor, steward=True)
        if archived and _active_agent_run(thread_id):
            raise db.Conflict("Wait for the agent response before you archive this chat.")
        value = db.now() if archived else None
        db.execute(
            "UPDATE chat_threads SET archived_at = ?, updated_at = ? WHERE id = ?",
            (value, db.now(), thread_id),
        )
        if archived:
            db.log_activity(actor, "archive_shared_chat", f"thread {thread_id}")
        else:
            db.log_activity(actor, "restore_shared_chat", f"thread {thread_id}")
    return _shared_details(thread_id, actor)


def update_shared_chat(
    thread_id: str,
    actor: str,
    *,
    title: str | None = None,
    engagement_id: int | None = None,
) -> dict:
    if title is None and engagement_id is None:
        raise ValueError("one shared-chat field is required")
    clean_title = title.strip()[:TITLE_LEN] if title is not None else ""
    if title is not None and not clean_title:
        raise ValueError("shared chat title is required")
    with db.transaction():
        _hold_identities(actor)
        _require_locked_member(thread_id, actor, steward=True)
        now = db.now()
        if title is not None:
            db.execute(
                "UPDATE chat_threads SET title = ?, updated_at = ? WHERE id = ?",
                (clean_title, now, thread_id),
            )
            db.log_activity(actor, "rename_shared_chat", f"thread {thread_id}")
        if engagement_id is not None:
            linked = None
            if engagement_id:
                linked = db.query_one(
                    f"SELECT id FROM engagements WHERE id = ? AND {scope.WORKSPACE_ONLY} FOR UPDATE",  # noqa: S608 — fixed scope fragment
                    (engagement_id,),
                )
                if not linked:
                    raise db.NotFound("No workspace engagement was found.")
            db.execute(
                "UPDATE chat_threads SET engagement_id = ?, updated_at = ? WHERE id = ?",
                (linked["id"] if linked else None, now, thread_id),
            )
            db.log_activity(actor, "link_shared_chat_engagement", f"thread {thread_id}")
    return _shared_details(thread_id, actor)
