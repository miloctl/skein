"""Cross-thread agent memory: plain rows + FTS relevance, injected into the
agent's system prompt at build time. Fully keyless."""

from .. import db
from . import scope
from .search import index_record


def remember(
    content: str,
    topic: str = "",
    user: str = "",
    thread_id: str = "",
    *,
    actor: str = "agent",
    origin: str = "human",
    visibility: str = scope.WORKSPACE,
    crew_id: int = 0,
) -> dict:
    """Memories are injected into every future conversation's system prompt —
    the highest-leverage write in the app, so it is bounded and carries full
    provenance."""
    content = content.strip()
    if not content:
        raise ValueError("nothing to remember")
    if len(content) > 2000:
        raise ValueError("keep memories under 2000 characters — link a note for the long form")
    if len(topic) > 100 or len(user) > 60:
        raise ValueError("topic is capped at 100 characters, user at 60")
    if origin != "agent_verified":  # an approval must not trip the proposer's cap
        from .. import ratelimit

        ratelimit.check("memory", actor)
    with db.transaction():
        tier, crew = scope.resolve_write(visibility, crew_id, actor=actor)
        mid = db.execute(
            "INSERT INTO memories (topic, content, user, thread_id, origin, created_by,"
            " created_at, visibility, crew_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (topic, content, user, thread_id, origin, actor, db.now(), tier, crew),
        )
        db.log_activity(actor, "remember", scope.detail(tier, f"#{mid}", topic or content[:60]))
        index_record("memory", mid, topic or content[:60], content)
    return {"id": mid, "topic": topic}


def get_memory(memory_id: int, viewer: scope.Viewer = scope.NOBODY) -> dict | None:
    # Filtered, defaulting to NOBODY: tools/memory.py puts the topic and the
    # first 80 characters of the body into a pending_changes summary, and the
    # reviewer who reads that card is not necessarily the memory's owner.
    frag, vp = scope.visible_filter(viewer, "memories")
    return db.query_one(
        f"SELECT * FROM memories WHERE id = ? AND {frag}",  # noqa: S608 — scope.visible_filter emits only bound marks
        (memory_id, *vp),
    )


def forget(memory_id: int, *, actor: str, origin: str = "human") -> dict:
    """Memories steer every future conversation — a wrong or injected one
    must be removable, and the removal itself is on the record."""
    from .search import deindex_record

    # one transaction: a row delete that commits without its index delete
    # leaves the memory's full content queryable through search
    with db.transaction():
        row = db.query_one("SELECT * FROM memories WHERE id = ?", (memory_id,))
        if not row:
            raise scope.missing("memories", memory_id)
        scope.assert_editable("memories", row, actor, verb="forget")
        db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        deindex_record("memory", memory_id)
        db.log_activity(
            actor,
            "forget",
            scope.detail(
                row["visibility"], f"#{memory_id}", f"[{row['topic']}] {row['content'][:200]}"
            ),
        )
    return {"id": memory_id, "deleted": True}


def recall(
    query: str = "", user: str = "", limit: int = 10, viewer: scope.Viewer = scope.NOBODY
) -> list[dict]:
    """Memories for one person, or the ones addressed to everybody.

    `user` and the tier are separate axes and BOTH apply to every branch. The
    query branch used to apply neither, so recall_memories answered one
    person's search out of another person's memories — and memory_prompt
    injects whatever comes back into a system prompt, where nothing later
    distinguishes it from the asker's own.
    """
    frag, vp = scope.visible_filter(viewer, "memories")
    # `user IN (?, '')`: an empty user is a memory addressed to the whole team,
    # which every branch must keep returning
    owner, op = (" AND user IN (?, '')", [user]) if user else ("", [])
    if query:
        from .search import search

        hits = [h for h in search(query, limit=limit * 2, viewer=viewer) if h["entity"] == "memory"]
        ids = [h["entity_id"] for h in hits][:limit]
        if not ids:
            return []
        rows = db.query(
            f"SELECT * FROM memories WHERE id IN ({','.join('?' * len(ids))}) AND {frag}{owner}",  # noqa: S608 — marks generated from the id count; scope.visible_filter emits only bound marks
            (*ids, *vp, *op),
        )
        order = {mid: i for i, mid in enumerate(ids)}
        return sorted(rows, key=lambda r: order.get(r["id"], 99))
    return db.query(
        f"SELECT * FROM memories WHERE {frag}{owner} ORDER BY id DESC LIMIT ?",  # noqa: S608 — scope.visible_filter emits only bound marks
        (*vp, *op, limit),
    )


def memory_prompt(user: str, limit: int = 8) -> str:
    """Recent memories rendered for system-prompt injection; empty string when none."""
    rows = recall(user=user, limit=limit)
    if not rows:
        return ""
    lines = [
        f"- [{m['topic']}] {m['content']}" if m["topic"] else f"- {m['content']}" for m in rows
    ]
    return "\n\nTeam memory (from prior conversations):\n" + "\n".join(lines)
