"""Named chat threads with folders, plus the UI transcript log.

The transcript (chat_messages) is the provider-agnostic history the chat
sidebar rehydrates from — written by the chat route for mock and real
providers alike, so history is keyless-first. It is the UI's copy; the
strands session files remain the model's own conversation memory.

Threads are owner-scoped views (trusted-LAN identity, like everything
team-visible). fb: messages never reach this table — the chat route
refuses them before any logging.
"""

import re
import shutil

from .. import config, db

_THREAD_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
TITLE_LEN = 60


def _check_id(thread_id: str) -> str:
    if not _THREAD_ID.match(thread_id):
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
        " WHERE owner = ? ORDER BY updated_at DESC",
        (owner,),
    )


def _own(thread_id: str, owner: str) -> dict:
    _check_id(thread_id)
    row = db.query_one("SELECT * FROM chat_threads WHERE id = ? AND owner = ?", (thread_id, owner))
    if not row:
        raise ValueError(f"no chat '{thread_id}' for {owner}")
    return row


def get_messages(thread_id: str, owner: str) -> list[dict]:
    _own(thread_id, owner)
    return db.query(
        "SELECT role, content, created_at FROM chat_messages WHERE thread_id = ? ORDER BY id",
        (thread_id,),
    )


def update_thread(
    thread_id: str, owner: str, *, title: str = "", folder: str | None = None
) -> dict:
    _own(thread_id, owner)
    if title:
        db.execute(
            "UPDATE chat_threads SET title = ?, updated_at = ? WHERE id = ?",
            (title.strip()[:TITLE_LEN], db.now(), thread_id),
        )
    if folder is not None:
        db.execute(
            "UPDATE chat_threads SET folder = ?, updated_at = ? WHERE id = ?",
            (folder.strip()[:40], db.now(), thread_id),
        )
    return db.query_row("SELECT * FROM chat_threads WHERE id = ?", (thread_id,))


def delete_thread(thread_id: str, owner: str) -> dict:
    """Remove the thread, its transcript, AND the model-side session files
    (including per-persona session variants) — a deleted chat is gone."""
    _own(thread_id, owner)
    db.execute("DELETE FROM chat_messages WHERE thread_id = ?", (thread_id,))
    db.execute("DELETE FROM chat_threads WHERE id = ?", (thread_id,))
    for path in config.SESSIONS_DIR.glob(f"session_{thread_id}*"):
        shutil.rmtree(path, ignore_errors=True)
    db.log_activity(owner, "delete_chat", f"thread {thread_id}")
    return {"id": thread_id, "deleted": True}
