"""Named chat threads with folders, plus the UI transcript log.

The transcript (chat_messages) is the provider-agnostic history the chat
sidebar rehydrates from — written by the chat route for mock and real
providers alike, so history is keyless-first. It is the UI's copy; the
Strands session files remain the model's own conversation memory.

Threads are owner-scoped by the trusted-LAN identity (X-User) — a
convenience boundary, not privacy: anything you'd mark fb: belongs in
⌘K capture or the People page, and the chat route enforces that by
refusing fb: lines before any logging. When OIDC lands, these routes
are first in line for strong identity.
"""

import re
import shutil

from .. import config, db

_THREAD_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
TITLE_LEN = 60


def _check_id(thread_id: str) -> str:
    if not _THREAD_ID.fullmatch(thread_id):
        raise ValueError("invalid thread id")
    return thread_id


def _title_from(text: str) -> str:
    # "/as growth-mentor how do I..." should title as the question, not the plumbing
    text = re.sub(r"^/as\s+[a-z0-9-]+\s+", "", text.strip(), flags=re.I)
    line = text.splitlines()[0].strip() if text.strip() else "New chat"
    return line[:TITLE_LEN] + ("…" if len(line) > TITLE_LEN else "")


def log_message(thread_id: str, owner: str, role: str, content: str) -> None:
    """Append to the transcript, creating/touching the thread row. Rows are
    only born here — an opened-but-never-used chat leaves no residue."""
    _check_id(thread_id)
    if role not in ("user", "assistant"):
        raise ValueError("role must be user or assistant")
    if not content.strip():
        return
    now = db.now()
    db.execute(
        "INSERT OR IGNORE INTO chat_threads (id, owner, title, created_at, updated_at)"
        " VALUES (?, ?, 'New chat', ?, ?)",
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


def list_threads(owner: str) -> list[dict]:
    return db.query(
        "SELECT id, title, folder, created_at, updated_at FROM chat_threads"
        " WHERE owner = ? ORDER BY updated_at DESC, rowid DESC",
        (owner,),
    )


def _own(thread_id: str, owner: str) -> dict:
    _check_id(thread_id)
    row = db.query_one("SELECT * FROM chat_threads WHERE id = ? AND owner = ?", (thread_id, owner))
    if not row:
        raise ValueError(f"no chat '{thread_id}' for {owner}")
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
    return db.query(
        "SELECT role, content, created_at FROM chat_messages WHERE thread_id = ? ORDER BY id",
        (thread_id,),
    )


FOLDER_LEN = 40


def create_folder(owner: str, name: str) -> dict:
    name = name.strip()[:FOLDER_LEN]
    if not name:
        raise ValueError("folder name is required")
    existing = _snap_folder(owner, name)
    db.execute(
        "INSERT OR IGNORE INTO chat_folders (owner, name, created_at) VALUES (?, ?, ?)",
        (owner, existing, db.now()),
    )
    return {"name": existing}


def list_folders(owner: str) -> list[str]:
    """Union of registered folders and any legacy folder still on a thread."""
    rows = db.query(
        "SELECT name FROM chat_folders WHERE owner = ?"
        " UNION SELECT DISTINCT folder FROM chat_threads WHERE owner = ? AND folder != ''"
        " ORDER BY 1",
        (owner, owner),
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
    """Case-insensitively reuse an existing folder spelling."""
    for existing in list_folders(owner):
        if existing.lower() == wanted.lower():
            return existing
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
            if not db.query_one("SELECT 1 FROM engagements WHERE id = ?", (engagement_id,)):
                raise db.NotFound(f"engagement #{engagement_id} not found")
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
                "INSERT OR IGNORE INTO chat_folders (owner, name, created_at) VALUES (?, ?, ?)",
                (owner, wanted, db.now()),
            )
        db.execute(
            "UPDATE chat_threads SET folder = ?, updated_at = ? WHERE id = ?",
            (wanted, db.now(), thread_id),
        )
    return db.query_row("SELECT * FROM chat_threads WHERE id = ?", (thread_id,))


def delete_thread(thread_id: str, owner: str) -> dict:
    """Remove the thread, its transcript, AND the model-side session files
    (including per-persona session variants) — a deleted chat is gone."""
    _own(thread_id, owner)
    db.execute("DELETE FROM chat_messages WHERE thread_id = ?", (thread_id,))
    db.execute("DELETE FROM chat_threads WHERE id = ?", (thread_id,))
    for pattern in (f"session_{thread_id}", f"session_{thread_id}--*"):
        for path in config.SESSIONS_DIR.glob(pattern):
            shutil.rmtree(path, ignore_errors=True)
    db.log_activity(owner, "delete_chat", f"thread {thread_id}")
    return {"id": thread_id, "deleted": True}
