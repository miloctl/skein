"""Questions, decisions, standups, and knowledge-base services."""

import re

from .. import db
from . import scope
from .search import index_record

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def ask_question(
    question: str,
    asked_by: str,
    assigned_to: str = "",
    *,
    actor: str = "",
    origin: str = "human",
    visibility: str = scope.WORKSPACE,
    crew_id: int = 0,
) -> dict:
    """Create a question and its directed notices in one transaction."""
    with db.transaction():
        return _ask_question_locked(
            question,
            asked_by,
            assigned_to,
            actor=actor,
            origin=origin,
            visibility=visibility,
            crew_id=crew_id,
        )


def _ask_question_locked(
    question: str,
    asked_by: str,
    assigned_to: str = "",
    *,
    actor: str = "",
    origin: str = "human",
    visibility: str = scope.WORKSPACE,
    crew_id: int = 0,
) -> dict:
    if not question.strip():
        raise ValueError("the question text is required")
    # one transaction, because scope.resolve_write checks crew membership and
    # the check has to hold until the row lands — bare, it opens its own
    # connection and a person removed in between still scopes the row
    with db.transaction():
        tier, cid = scope.resolve_write(visibility, crew_id, actor=actor or asked_by)
        scope.assert_readable_by(tier, cid, assigned_to, label="assignee", author=actor or asked_by)
        qid = db.execute(
            "INSERT INTO questions (asked_by, assigned_to, question, origin, created_by,"
            " created_at, visibility, crew_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            " RETURNING id",
            (asked_by, assigned_to, question, origin, actor or asked_by, db.now(), tier, cid),
        )
        db.log_activity(actor or asked_by, "ask_question", f"#{qid}")
        index_record("question", qid, question[:120], question)
    if assigned_to:
        from .notifications import notify

        notify(
            assigned_to,
            lambda source: f"Question #{source['id']} assigned to you: {source['question'][:80]}",
            tier="digest",
            link="/",
            source_entity="question",
            source_id=qid,
        )
    from .mentions import scan

    scan("question", qid, question, actor=actor or asked_by, exclude=(assigned_to,), link="/")
    return {"id": qid, "status": "open"}


def assign_question(
    question_id: int, assigned_to: str, *, actor: str = "", origin: str = "human"
) -> dict:
    """Assign a question and create its notice in one transaction."""
    with db.transaction():
        return _assign_question_locked(
            question_id,
            assigned_to,
            actor=actor,
            origin=origin,
        )


def _assign_question_locked(
    question_id: int, assigned_to: str, *, actor: str = "", origin: str = "human"
) -> dict:
    row = db.query_one("SELECT * FROM questions WHERE id = ?", (question_id,))
    if not row:
        raise scope.missing("questions", question_id)
    scope.assert_editable("questions", row, actor, verb="assign")
    if row["status"] != "open":
        raise ValueError(f"question #{question_id} is already {row['status']}")
    assigned_to = assigned_to.strip()
    if assigned_to:
        # a typo'd assignee looks handled but notifies nobody — refuse it
        from .users import list_users

        known = {u["name"].lower(): u["name"] for u in list_users()}
        match = known.get(assigned_to.lower())
        if not match:
            raise ValueError("assigned_to is not an active user")
        assigned_to = match
        # the same check ask_question makes at the create: the notify below
        # quotes 80 characters of the question, and a reassignment reaches a
        # name the original write never checked
        scope.assert_readable_by(
            row["visibility"],
            row["crew_id"],
            assigned_to,
            label="assignee",
            author=row["created_by"],
        )
    db.execute("UPDATE questions SET assigned_to = ? WHERE id = ?", (assigned_to, question_id))
    db.log_activity(
        actor or "system", "assign_question", f"#{question_id} -> {assigned_to} [{origin}]"
    )
    if assigned_to:
        from .notifications import notify

        notify(
            assigned_to,
            lambda source: f"Question #{source['id']} assigned to you: {source['question'][:80]}",
            tier="digest",
            link="/",
            source_entity="question",
            source_id=question_id,
        )
    return {"id": question_id, "assigned_to": assigned_to}


def answer_question(
    question_id: int, answer: str, answered_by: str = "", *, actor: str = "", origin: str = "human"
) -> dict:
    """Answer a question and create its notices in one transaction."""
    with db.transaction():
        return _answer_question_locked(
            question_id,
            answer,
            answered_by,
            actor=actor,
            origin=origin,
        )


def _answer_question_locked(
    question_id: int, answer: str, answered_by: str = "", *, actor: str = "", origin: str = "human"
) -> dict:
    row = db.query_one("SELECT * FROM questions WHERE id = ?", (question_id,))
    if not row:
        raise scope.missing("questions", question_id)
    scope.assert_editable("questions", row, actor or answered_by, verb="answer")
    if row["status"] == "answered" and row["answer"] and row["answer"] != answer:
        raise ValueError(
            f"question #{question_id} already has an answer — read it first,"
            " then ask a follow-up question. Do not overwrite it"
        )
    db.execute(
        "UPDATE questions SET answer = ?, status = 'answered', answered_at = ? WHERE id = ?",
        (answer, db.now(), question_id),
    )
    db.log_activity(actor or answered_by or "system", "answer_question", f"#{question_id}")
    row = db.query_one("SELECT * FROM questions WHERE id = ?", (question_id,))
    if row:
        index_record("question", question_id, row["question"][:120], f"{row['question']} {answer}")
        who = actor or answered_by
        if row["asked_by"] and row["asked_by"] != who:
            from .notifications import notify

            notify(
                row["asked_by"],
                lambda source: (
                    f"Your question #{source['id']} was answered: {source['answer'][:120]}"
                ),
                tier="digest",
                link="/dashboard",
                source_entity="question",
                source_id=question_id,
            )
        from .mentions import scan

        scan("question", question_id, answer, actor=who, exclude=(row["asked_by"],), link="/")
    return {"id": question_id, "status": "answered"}


def list_questions(status: str = "", viewer: scope.Viewer = scope.NOBODY) -> list[dict]:
    # NOBODY is the default so a caller that passes nothing reads the
    # workspace tier, which is what a job, an agent tool or an MCP call must
    # read anyway (docs/VISIBILITY.md). The REST door passes a real viewer.
    frag, vp = scope.visible_filter(viewer, "questions")
    if status:
        return db.query(
            f"SELECT * FROM questions WHERE status = ? AND {frag}"  # noqa: S608 — scope.visible_filter emits only bound marks
            " ORDER BY id DESC LIMIT 200",
            (status, *vp),
        )
    return db.query(
        f"SELECT * FROM questions WHERE {frag}"  # noqa: S608 — scope.visible_filter emits only bound marks
        " ORDER BY status = 'answered', id DESC LIMIT 200",
        tuple(vp),
    )


DECISION_CATEGORIES = ("", "charter")  # charter: team mission/ownership/norms


def record_decision(
    title: str,
    decision: str,
    context: str = "",
    decided_by: str = "",
    review_by: str = "",
    category: str = "",
    *,
    actor: str = "",
    origin: str = "human",
    visibility: str = scope.WORKSPACE,
    crew_id: int = 0,
) -> dict:
    """Create a decision and its mention notices in one transaction."""
    with db.transaction():
        return _record_decision_locked(
            title,
            decision,
            context,
            decided_by,
            review_by,
            category,
            actor=actor,
            origin=origin,
            visibility=visibility,
            crew_id=crew_id,
        )


def _record_decision_locked(
    title: str,
    decision: str,
    context: str = "",
    decided_by: str = "",
    review_by: str = "",
    category: str = "",
    *,
    actor: str = "",
    origin: str = "human",
    visibility: str = scope.WORKSPACE,
    crew_id: int = 0,
) -> dict:
    if not title.strip() or not decision.strip():
        raise ValueError("decision title and text are required")
    from .users import resolve_teammate

    decided_by = resolve_teammate(decided_by, actor, "decided_by")
    if review_by:
        from datetime import date

        try:
            date.fromisoformat(review_by)
        except ValueError as exc:
            raise ValueError("review_by must be a real date (YYYY-MM-DD)") from exc
    if review_by and not DATE_RE.match(review_by):
        raise ValueError(
            "review_by must be YYYY-MM-DD. The stale sweep does not read any other format."
        )
    if category not in DECISION_CATEGORIES:
        raise ValueError(f"category must be one of {DECISION_CATEGORIES}")
    if category == "charter" and not review_by:
        raise ValueError(
            "charter entries need a review_by date — the whole point is that"
            " they get reconfirmed instead of silently rotting"
        )
    with db.transaction():
        tier, cid = scope.resolve_write(visibility, crew_id, actor=actor or decided_by)
        # decided_by is checked as a READER, the same way work.py checks an
        # assignee and blockers.py checks an owner. sweep_stale_decisions says
        # it follows the blocker sweep's rule — that rule holds only because
        # raise_blocker ran this check, and without it here the sweep quoted a
        # crew decision's title to somebody who cannot open it.
        scope.assert_readable_by(tier, cid, decided_by, label="decider", author=actor or decided_by)
        did = db.execute(
            "INSERT INTO decisions (title, context, decision, decided_by, review_by, category,"
            " origin, created_by, created_at, visibility, crew_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " RETURNING id",
            (
                title,
                context,
                decision,
                decided_by,
                review_by or None,
                category,
                origin,
                actor or decided_by,
                db.now(),
                tier,
                cid,
            ),
        )
        db.log_activity(
            actor or decided_by or "system",
            "record_decision",
            scope.detail(tier, f"#{did}", title),
        )
        index_record("decision", did, title, f"{decision} {context}")
    from .mentions import scan

    scan(
        "decision",
        did,
        f"{title} {decision} {context}",
        actor=actor or decided_by or "system",
        link="/charter",
    )
    return {"id": did, "title": title}


def supersede_decision(
    decision_id: int,
    title: str,
    decision: str,
    context: str = "",
    decided_by: str = "",
    review_by: str = "",
    *,
    actor: str = "",
    origin: str = "human",
) -> dict:
    """Decisions have a half-life: the record chains rather than mutates, so
    nobody cites a dead decision without seeing what replaced it."""
    # One transaction over the CAS claim AND the successor create. Every
    # input this rejects is validated inside record_decision, so a partial
    # run leaves the old decision superseded-by-nothing: supersede then
    # refuses it as already superseded, reconfirm redirects to a successor
    # that does not exist, and the decision leaves the charter with no way
    # back. Do not pre-validate here instead — record_decision's checks grow,
    # and a copy of them drifts.
    with db.transaction():
        old = db.query_one("SELECT * FROM decisions WHERE id = ?", (decision_id,))
        if not old:
            raise scope.missing("decisions", decision_id)
        scope.assert_editable("decisions", old, actor or decided_by, verb="supersede")
        # CAS-claim the old decision BEFORE creating the successor — two racing
        # supersedes must not leave two active contradicting decisions
        claimed = db.execute_rowcount(
            "UPDATE decisions SET status = 'superseded' WHERE id = ? AND status != 'superseded'",
            (decision_id,),
        )
        if not claimed:
            current = db.query_row(
                "SELECT superseded_by FROM decisions WHERE id = ?", (decision_id,)
            )
            raise ValueError(
                f"decision #{decision_id} already superseded by #{current['superseded_by']}"
            )
        if old["category"] == "charter" and not review_by:
            # charter replacements keep riding the sweep — default the 90-day push
            from datetime import timedelta

            review_by = (db.today() + timedelta(days=90)).isoformat()
        new = record_decision(
            title,
            decision,
            context or f"Supersedes #{decision_id}: {old['title']}",
            decided_by,
            review_by,
            category=old["category"],  # a charter entry's replacement stays charter
            actor=actor,
            origin=origin,
            # the successor's default context is f"Supersedes #N: {old title}",
            # so a workspace replacement for a crew decision copies that title
            # into a row every reader can see, and indexes it
            visibility=old["visibility"],
            crew_id=old["crew_id"] or 0,
        )
        db.execute("UPDATE decisions SET superseded_by = ? WHERE id = ?", (new["id"], decision_id))
        db.log_activity(
            actor or decided_by or "system",
            "supersede_decision",
            f"#{decision_id} -> #{new['id']}",
        )
    return {**new, "supersedes": decision_id}


def sweep_stale_decisions() -> list[dict]:
    """Mark stale decisions and create their notices in one transaction."""
    with db.transaction():
        return _sweep_stale_decisions_locked()


def _sweep_stale_decisions_locked() -> list[dict]:
    """Flip active decisions past their review_by date to stale (once — the
    status flip is the claim). Scheduled daily; stale ≠ wrong, it means
    'reconfirm or supersede me'."""
    swept = []
    for d in db.query(
        "SELECT * FROM decisions WHERE status = 'active'"
        " AND review_by IS NOT NULL AND review_by < ?",
        (db.today().isoformat(),),  # vs review_by, a date column
    ):
        claimed = db.execute_rowcount(
            "UPDATE decisions SET status = 'stale' WHERE id = ? AND status = 'active'", (d["id"],)
        )
        if not claimed:
            continue
        swept.append({**d, "status": "stale"})
        from .notifications import notify

        # the same rule sweep_escalations follows (services/blockers.py): the
        # message quotes the title, and the "team" fallback is the whole
        # roster, so a scoped decision tells its decider or nobody
        if d["decided_by"] or d["visibility"] == scope.WORKSPACE:
            notify(
                d["decided_by"] or "team",
                lambda source: (
                    f"Decision #{source['id']} '{source['title']}' passed its review-by date"
                    f" ({source['review_by']}). Reconfirm it or supersede it."
                    if source["status"] == "stale"
                    else None
                ),
                tier="digest",
                link="/",
                source_entity="decision",
                source_id=int(d["id"]),
            )
        db.log_activity(
            "scheduler", "stale_decision", scope.detail(d["visibility"], f"#{d['id']}", d["title"])
        )
    return swept


def reconfirm_decision(decision_id: int, review_by: str = "", *, actor: str = "system") -> dict:
    """Reconfirming without a new date pushes review_by out 90 days — it must
    never silently remove the half-life (that would defeat the sweep)."""
    from datetime import date, timedelta

    row = db.query_one("SELECT * FROM decisions WHERE id = ?", (decision_id,))
    if not row:
        raise scope.missing("decisions", decision_id)
    scope.assert_editable("decisions", row, actor, verb="reconfirm")
    if row["status"] == "superseded":
        raise ValueError(f"decision #{decision_id} was superseded — reconfirm the successor")
    if review_by:
        from datetime import date

        try:
            date.fromisoformat(review_by)
        except ValueError as exc:
            raise ValueError("review_by must be a real date (YYYY-MM-DD)") from exc
    if review_by and not DATE_RE.match(review_by):
        raise ValueError("review_by must be YYYY-MM-DD")
    if not review_by:
        review_by = (db.today() + timedelta(days=90)).isoformat()
    db.execute(
        "UPDATE decisions SET status = 'active', review_by = ? WHERE id = ?",
        (review_by, decision_id),
    )
    db.log_activity(actor, "reconfirm_decision", f"#{decision_id} until {review_by}")
    return {"id": decision_id, "status": "active", "review_by": review_by}


def list_decisions(
    limit: int = 50, status: str = "", category: str = "", viewer: scope.Viewer = scope.NOBODY
) -> list[dict]:
    frag, vp = scope.visible_filter(viewer, "decisions")
    # the scope fragment SEEDS the AND list rather than being appended to a
    # clause that may be empty: a builder that can emit no WHERE at all drops
    # the filter silently, and `where` starting non-empty prevents that
    where, params = [frag], list(vp)
    if status:
        where.append("status = ?")
        params.append(status)
    if category:
        where.append("category = ?")
        params.append(category)
    return db.query(
        f"SELECT * FROM decisions WHERE {' AND '.join(where)}"  # noqa: S608 — clauses hardcoded, and scope.visible_filter emits only bound marks
        " ORDER BY id DESC LIMIT ?",
        (*params, limit),
    )


def post_standup(
    author: str,
    yesterday: str = "",
    today: str = "",
    blockers: str = "",
    *,
    actor: str = "",
    origin: str = "human",
    visibility: str = scope.WORKSPACE,
    crew_id: int = 0,
) -> dict:
    with db.transaction():
        tier, cid = scope.resolve_write(visibility, crew_id, actor=actor or author)
        sid = db.execute(
            "INSERT INTO standups (author, yesterday, today, blockers, origin, created_by,"
            " created_at, visibility, crew_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " RETURNING id",
            (author, yesterday, today, blockers, origin, actor or author, db.now(), tier, cid),
        )
        index_record("standup", sid, f"{author}'s standup", f"{yesterday} {today} {blockers}")
        db.log_activity(actor or author, "post_standup", f"#{sid}")
        if blockers.strip():
            from .blockers import raise_blocker

            # the child takes the standup's tier. Without it a crew standup's
            # blocker text lands at workspace and goes on to the digest, the
            # exec readout and the FTS index — the standup is scoped and the
            # sentence lifted out of it is not.
            raise_blocker(
                title=blockers.strip()[:120],
                detail=f"Auto-extracted from {author}'s standup #{sid}",
                owner=author,
                source=f"standup:{sid}",
                actor=actor or author,
                origin=origin,
                visibility=tier,
                crew_id=cid or 0,
            )
    return {"id": sid}


def list_standups(limit: int = 30, viewer: scope.Viewer = scope.NOBODY) -> list[dict]:
    frag, vp = scope.visible_filter(viewer, "standups")
    return db.query(
        f"SELECT * FROM standups WHERE {frag} ORDER BY id DESC LIMIT ?",  # noqa: S608 — scope.visible_filter emits only bound marks
        (*vp, limit),
    )


def save_note(
    topic: str,
    content: str,
    author: str = "",
    *,
    actor: str = "",
    origin: str = "human",
    visibility: str = scope.WORKSPACE,
    crew_id: int = 0,
) -> dict:
    """Create a note and its mention notices in one transaction."""
    with db.transaction():
        return _save_note_locked(
            topic,
            content,
            author,
            actor=actor,
            origin=origin,
            visibility=visibility,
            crew_id=crew_id,
        )


def _save_note_locked(
    topic: str,
    content: str,
    author: str = "",
    *,
    actor: str = "",
    origin: str = "human",
    visibility: str = scope.WORKSPACE,
    crew_id: int = 0,
) -> dict:
    # every sibling create refuses an empty record; this one used to accept
    # topic="" content="", indexing a blank row for search and burning a
    # hash-chained activity seq on a note with nothing in it
    if not topic.strip() and not content.strip():
        raise ValueError("a note needs a topic or content")
    with db.transaction():
        tier, cid = scope.resolve_write(visibility, crew_id, actor=actor or author)
        nid = db.execute(
            "INSERT INTO notes (topic, content, author, origin, created_by, created_at,"
            " visibility, crew_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            " RETURNING id",
            (topic, content, author, origin, actor or author or "system", db.now(), tier, cid),
        )
        db.log_activity(
            actor or author or "system", "save_note", scope.detail(tier, f"#{nid}", topic)
        )
        index_record("note", nid, topic, content)
    from .mentions import scan

    scan("note", nid, f"{topic} {content}", actor=actor or author or "system", link="/")
    return {"id": nid, "topic": topic}


def get_note(note_id: int, viewer: scope.Viewer = scope.NOBODY) -> dict | None:
    # Filtered, defaulting to NOBODY: a note's own text reaches the review
    # queue two ways — tools/collab.py::delete_note puts the topic and the
    # first 80 characters into the summary (scope.detail drops that half for a
    # scoped row), and save_note puts the whole content in the PAYLOAD, which
    # review.list_changes returns. The reviewer who reads that card is not
    # necessarily in the note's crew.
    frag, vp = scope.visible_filter(viewer, "notes")
    return db.query_one(
        f"SELECT * FROM notes WHERE id = ? AND {frag}",  # noqa: S608 — scope.visible_filter emits only bound marks
        (note_id, *vp),
    )


def update_note(
    note_id: int, topic: str = "", content: str = "", *, actor: str = "", origin: str = "human"
) -> dict:
    # The existence check and the re-index are one transaction, taking the
    # same write lock delete_note takes. Split, this reads a live note,
    # delete_note removes it and clears its index, and then the index_record
    # below puts the deleted body back — searchable forever.
    with db.transaction():
        row = db.query_one("SELECT * FROM notes WHERE id = ?", (note_id,))
        if not row:
            raise scope.missing("notes", note_id)
        scope.assert_editable("notes", row, actor, verb="update")
        fields = {k: v for k, v in [("topic", topic), ("content", content)] if v}
        if not fields:
            raise ValueError("nothing to update")
        sets = ", ".join(f"{k} = ?" for k in fields)
        db.execute(
            f"UPDATE notes SET {sets} WHERE id = ?",  # noqa: S608 — keys hardcoded
            (*fields.values(), note_id),
        )
        if topic and topic != row["topic"]:
            # both topics are the note's own text, so a scoped rename logs
            # the identifier only
            db.log_activity(
                actor or "system",
                "update_note",
                scope.detail(row["visibility"], f"#{note_id}", f"'{row['topic']}' -> '{topic}'"),
            )
        else:
            db.log_activity(
                actor or "system",
                "update_note",
                scope.detail(row["visibility"], f"#{note_id}", row["topic"]),
            )
        new = db.query_one("SELECT topic, content FROM notes WHERE id = ?", (note_id,))
        if new:
            index_record("note", note_id, new["topic"], new["content"])
            from .mentions import scan

            scan(
                "note",
                note_id,
                f"{new['topic']} {new['content']}",
                actor=actor or "system",
                link="/",
            )
    return {"id": note_id, "updated": list(fields)}


def delete_note(note_id: int, *, actor: str = "", origin: str = "human") -> dict:
    from .search import deindex_record

    # The row delete and the index delete are one transaction, and update_note
    # takes the same lock: split, a concurrent edit re-indexes the note after
    # this deindex runs, and the FULL body of a deleted note stays queryable
    # through search forever. That path also outlives the 300-char ledger
    # snapshot below, which is deliberately bounded.
    with db.transaction():
        row = db.query_one("SELECT * FROM notes WHERE id = ?", (note_id,))
        if not row:
            raise scope.missing("notes", note_id)
        scope.assert_editable("notes", row, actor, verb="delete")
        db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        deindex_record("note", note_id)
        # bounded content snapshot: a workspace note deleted between backups
        # must be reviewable (and partially recoverable) from the ledger. A
        # scoped one gets the identifier only — the chain is append-only, so
        # the snapshot outlives every tier change and every later delete.
        db.log_activity(
            actor or "system",
            "delete_note",
            scope.detail(
                row["visibility"], f"#{note_id}", f"{row['topic']}: {row['content'][:300]}"
            ),
        )
    return {"id": note_id, "deleted": True}


def recent_activity(viewer: str, limit: int = 50) -> list[dict]:
    """Raw ledger rows, SCOPED like the feed: the viewer's own strand plus
    agents and system processes — another human's rows do not appear here
    either, or the raw endpoint would be the one-curl bypass of the rule the
    feed enforces. Includes pre-036 unchained rows (the feed cannot; its
    cursor is seq).

    Ordered by seq, not id. `id` is outside the chain digest
    digest, so ordering the provenance feed by it would let the visible
    timeline be resequenced while verification still reports intact."""
    from .activity import visible_actor_filter

    actor_sql, params = visible_actor_filter(viewer)
    return db.query(
        f"SELECT * FROM activity WHERE {actor_sql}"  # noqa: S608 — placeholders built above
        " ORDER BY COALESCE(seq, 0) DESC, id DESC LIMIT ?",
        (*params, limit),
    )


def search_notes(keyword: str = "", viewer: scope.Viewer = scope.NOBODY) -> list[dict]:
    frag, vp = scope.visible_filter(viewer, "notes")
    if keyword:
        like = f"%{keyword}%"
        # the keyword OR is PARENTHESIZED. Left bare, `a LIKE ? OR b LIKE ? AND
        # {frag}` binds AND tighter than OR, so every row matching the topic
        # came back whatever its tier — the exact shape visible_filter's
        # docstring names as failing silently.
        return db.query(
            f"SELECT * FROM notes WHERE (topic LIKE ? OR content LIKE ?)"  # noqa: S608 — scope.visible_filter emits only bound marks
            f" AND {frag} ORDER BY id DESC LIMIT 25",
            (like, like, *vp),
        )
    return db.query(
        f"SELECT * FROM notes WHERE {frag} ORDER BY id DESC LIMIT 25",  # noqa: S608 — scope.visible_filter emits only bound marks
        tuple(vp),
    )
