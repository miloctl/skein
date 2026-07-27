"""Cross-thread agent memory: plain rows + FTS relevance, injected into the
agent's system prompt at build time. Fully keyless."""

from .. import db
from .search import index_record


def remember(
    content: str, topic: str = "", user: str = "", thread_id: str = "", *, actor: str = "agent"
) -> dict:
    if not content.strip():
        raise ValueError("nothing to remember")
    mid = db.execute(
        "INSERT INTO memories (topic, content, user, thread_id, created_at) VALUES (?, ?, ?, ?, ?)",
        (topic, content, user, thread_id, db.now()),
    )
    db.log_activity(actor, "remember", topic or content[:60])
    index_record("memory", mid, topic or content[:60], content)
    return {"id": mid, "topic": topic}


def forget(memory_id: int, *, actor: str) -> dict:
    """Memories steer every future conversation — a wrong or injected one
    must be removable, and the removal itself is on the record."""
    row = db.query_one("SELECT topic, content FROM memories WHERE id = ?", (memory_id,))
    if not row:
        raise ValueError(f"no memory #{memory_id}")
    db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    from .search import deindex_record

    deindex_record("memory", memory_id)
    db.log_activity(actor, "forget", f"#{memory_id} {row['topic'] or row['content'][:60]}")
    return {"id": memory_id, "deleted": True}


def recall(query: str = "", user: str = "", limit: int = 10) -> list[dict]:
    if query:
        from .search import search

        hits = [h for h in search(query, limit=limit * 2) if h["entity"] == "memory"]
        ids = [h["entity_id"] for h in hits][:limit]
        if not ids:
            return []
        rows = db.query(
            f"SELECT * FROM memories WHERE id IN ({','.join('?' * len(ids))})",  # noqa: S608
            tuple(ids),
        )
        order = {mid: i for i, mid in enumerate(ids)}
        return sorted(rows, key=lambda r: order.get(r["id"], 99))
    if user:
        return db.query(
            "SELECT * FROM memories WHERE user IN (?, '') ORDER BY id DESC LIMIT ?",
            (user, limit),
        )
    return db.query("SELECT * FROM memories ORDER BY id DESC LIMIT ?", (limit,))


def memory_prompt(user: str, limit: int = 8) -> str:
    """Recent memories rendered for system-prompt injection; empty string when none."""
    rows = recall(user=user, limit=limit)
    if not rows:
        return ""
    lines = [
        f"- [{m['topic']}] {m['content']}" if m["topic"] else f"- {m['content']}" for m in rows
    ]
    return "\n\nTeam memory (from prior conversations):\n" + "\n".join(lines)
