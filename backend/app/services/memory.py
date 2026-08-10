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
    engagement_id: int = 0,
    source_kind: str = "",
    source_id: str = "",
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
        # the engagement is resolved through the WRITER's filter, not trusted
        # from the caller: a memory filed against an engagement somebody cannot
        # read would be recalled into every conversation about it
        if engagement_id:
            efrag, ep = scope.visible_filter(scope.Viewer.for_actor(actor), "engagements")
            if not db.query_one(
                f"SELECT id FROM engagements WHERE id = ? AND {efrag}",  # noqa: S608 — scope.visible_filter emits only bound marks
                (engagement_id, *ep),
            ):
                raise ValueError(scope.missing_text("engagements", engagement_id))
        mid = db.execute(
            "INSERT INTO memories (topic, content, user, thread_id, origin, created_by,"
            " created_at, visibility, crew_id, engagement_id, source_kind, source_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                topic,
                content,
                user,
                thread_id,
                origin,
                actor,
                db.now(),
                tier,
                crew,
                engagement_id or None,
                source_kind[:40],
                source_id[:80],
            ),
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
    query: str = "",
    user: str = "",
    limit: int = 10,
    viewer: scope.Viewer = scope.NOBODY,
    engagement_id: int | None = None,
) -> list[dict]:
    """Memories for one person, or the ones addressed to everybody.

    `user` and the tier are separate axes and BOTH apply to every branch. The
    query branch used to apply neither, so recall_memories answered one
    person's search out of another person's memories — and memory_prompt
    injects whatever comes back into a system prompt, where nothing later
    distinguishes it from the asker's own.

    `engagement_id` has THREE states, and the middle one is the reason:

    - `None` — no engagement predicate at all. This is BROWSE: the memories
      page is the only surface that lists them and the only one that offers
      "forget for good", so a memory it cannot show is a memory nobody can
      delete. Two states would have made every approved engagement memory
      steer conversations from a row no human could reach.
    - `0` — team-wide only. An unlinked chat recalls what holds everywhere.
    - `N` — team-wide PLUS that engagement's own. It ADDS and never
      substitutes: a fact about how this team works still holds while
      somebody works one piece of it. Another engagement's are excluded,
      which is the whole reason the column exists.
    """
    frag, vp = scope.visible_filter(viewer, "memories")
    # `user IN (?, '')`: an empty user is a memory addressed to the whole team,
    # which every branch must keep returning
    owner, op = (" AND user IN (?, '')", [user]) if user else ("", [])
    if engagement_id is None:
        eng, ep2 = "", []
    elif engagement_id:
        # NULL (the team's own) plus this engagement's, never another's
        eng, ep2 = " AND (engagement_id IS NULL OR engagement_id = ?)", [engagement_id]
    else:
        eng, ep2 = " AND engagement_id IS NULL", []
    if query:
        from .search import search

        hits = [h for h in search(query, limit=limit * 2, viewer=viewer) if h["entity"] == "memory"]
        ids = [h["entity_id"] for h in hits][:limit]
        if not ids:
            return []
        rows = db.query(
            f"SELECT * FROM memories WHERE id IN ({','.join('?' * len(ids))}) AND {frag}{owner}{eng}",  # noqa: S608 — marks generated from the id count; scope.visible_filter emits only bound marks
            (*ids, *vp, *op, *ep2),
        )
        order = {mid: i for i, mid in enumerate(ids)}
        return sorted(rows, key=lambda r: order.get(r["id"], 99))
    return db.query(
        f"SELECT * FROM memories WHERE {frag}{owner}{eng} ORDER BY id DESC LIMIT ?",  # noqa: S608 — scope.visible_filter emits only bound marks
        (*vp, *op, *ep2, limit),
    )


def memory_prompt(
    user: str, limit: int = 8, engagement_id: int = 0, viewer: scope.Viewer = scope.NOBODY
) -> str:
    """Recent memories rendered for system-prompt injection; empty string when none.

    `engagement_id` comes from the chat thread's own link
    (services/chat_threads.py). A thread about one engagement recalls that
    engagement's memories on top of the team's, which is what makes filing one
    worth the reviewer's time.

    `viewer` is the human whose message caused the turn, passed down from the
    route (agents/team_agent.py::build_agent). Without it this read defaults to
    NOBODY, which admits the workspace tier only — and a crew engagement's
    memory is deliberately stored at crew tier by `propose_engagement_memory`,
    so the whole propose-review-approve pipeline produced a row that steered no
    conversation at all. NOBODY stays the default for the unattended runner,
    where no human is asking and the workspace tier is the honest ceiling.
    """
    rows = recall(user=user, limit=limit, viewer=viewer, engagement_id=engagement_id)
    if not rows:
        return ""
    lines = [
        f"- [{m['topic']}] {m['content']}" if m["topic"] else f"- {m['content']}" for m in rows
    ]
    return "\n\nTeam memory (from prior conversations):\n" + "\n".join(lines)


def propose_engagement_memory(
    engagement_id: int,
    content: str,
    topic: str = "",
    thread_id: str = "",
    *,
    actor: str,
    viewer: scope.Viewer = scope.NOBODY,
) -> dict:
    """File what a conversation produced as this engagement's memory.

    ALWAYS a proposal, whatever `SKEIN_AGENT_REVIEW` is set to. A memory is
    injected into every future conversation about this engagement, so it is the
    highest-leverage write in the app — and this text comes out of a chat,
    which is the one place a model's summary and a person's own words are hard
    to tell apart. A second human reads it before it steers anything.
    """
    from .review import propose_change

    efrag, ep = scope.visible_filter(viewer, "engagements")
    eng = db.query_one(
        f"SELECT name, visibility, crew_id FROM engagements WHERE id = ? AND {efrag}",  # noqa: S608 — scope.visible_filter emits only bound marks
        (engagement_id, *ep),
    )
    if not eng:
        raise scope.missing("engagements", engagement_id)
    if eng["visibility"] == scope.PRIVATE:
        # `review._governing_tier` resolves a create's readability from the
        # payload's tier plus its `author` key, and `memories` records the
        # person a memory is ADDRESSED TO rather than its writer — so a private
        # proposal resolves to "nobody may read this", including the person who
        # filed it. The row would sit pending forever with no surface able to
        # approve or reject it.
        raise ValueError(
            "a private engagement has one reader, so no second person can"
            " review a memory filed against it. Move the engagement to a crew,"
            " or remember the fact for the team instead."
        )
    return propose_change(
        "memory",
        "create",
        {
            "content": content.strip(),
            "topic": topic.strip(),
            "engagement_id": engagement_id,
            "source_kind": "chat",
            "source_id": thread_id,
            # the ENGAGEMENT's tier, threaded by hand — this is a parent-to-child
            # crossing and `scope.inherit` names it. Without the pair the
            # proposal is workspace by default: `review._governing_tier` finds
            # no parent row for a create, so every reader sees the summary and
            # any of them may approve it, and `remember` then indexes the body
            # for search at the workspace tier. A crew engagement's memory
            # would reach the whole roster twice over.
            "visibility": eng["visibility"],
            "crew_id": eng["crew_id"] or 0,
        },
        # scope.detail, and notify_team gated on the tier — the pair
        # `delegation.submit_completion` uses, for the same reason. The summary
        # is served by GET /api/review and by my_day's pending_reviews, and
        # `propose_change` ALSO posts it to the 'team' notification feed, which
        # carries no tier and reaches every roster member. Without both halves
        # a crew engagement's NAME and eighty characters of its memory reached
        # somebody who cannot read either.
        summary=scope.detail(
            eng["visibility"],
            f"remember for engagement #{engagement_id}",
            f"{eng['name']}: {content.strip()[:80]}",
        ),
        actor=actor,
        origin="human",
        requested_by=actor,
        notify_team=eng["visibility"] == scope.WORKSPACE,
    )
