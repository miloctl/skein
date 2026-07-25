"""Questions, decisions, standups, and knowledge-base services."""

import re

from .. import db
from .search import index_record

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def ask_question(
    question: str, asked_by: str, assigned_to: str = "", *, actor: str = "", origin: str = "human"
) -> dict:
    if not question.strip():
        raise ValueError("the question text is required")
    qid = db.execute(
        "INSERT INTO questions (asked_by, assigned_to, question, origin, created_by, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (asked_by, assigned_to, question, origin, actor or asked_by, db.now()),
    )
    db.log_activity(actor or asked_by, "ask_question", f"#{qid}")
    index_record("question", qid, question[:120], question)
    if assigned_to:
        from .notifications import notify

        notify(
            assigned_to,
            f"Question #{qid} assigned to you: {question[:80]}",
            tier="digest",
            link="/",
        )
    return {"id": qid, "status": "open"}


def assign_question(
    question_id: int, assigned_to: str, *, actor: str = "", origin: str = "human"
) -> dict:
    row = db.query_one("SELECT * FROM questions WHERE id = ?", (question_id,))
    if not row:
        raise ValueError(f"question #{question_id} not found")
    if row["status"] != "open":
        raise ValueError(f"question #{question_id} is already {row['status']}")
    assigned_to = assigned_to.strip()
    if assigned_to:
        # a typo'd assignee looks handled but notifies nobody — refuse it
        from .users import list_users

        known = {u["name"].lower(): u["name"] for u in list_users()}
        match = known.get(assigned_to.lower())
        if not match:
            raise ValueError(f"'{assigned_to}' is not an active user")
        assigned_to = match
    db.execute("UPDATE questions SET assigned_to = ? WHERE id = ?", (assigned_to, question_id))
    db.log_activity(
        actor or "system", "assign_question", f"#{question_id} -> {assigned_to} [{origin}]"
    )
    if assigned_to:
        from .notifications import notify

        notify(
            assigned_to,
            f"Question #{question_id} assigned to you: {row['question'][:80]}",
            tier="digest",
            link="/",
        )
    return {"id": question_id, "assigned_to": assigned_to}


def answer_question(
    question_id: int, answer: str, answered_by: str = "", *, actor: str = "", origin: str = "human"
) -> dict:
    if not db.query_one("SELECT id FROM questions WHERE id = ?", (question_id,)):
        raise ValueError(f"question #{question_id} not found")
    db.execute(
        "UPDATE questions SET answer = ?, status = 'answered', answered_at = ? WHERE id = ?",
        (answer, db.now(), question_id),
    )
    db.log_activity(actor or answered_by or "system", "answer_question", f"#{question_id}")
    row = db.query_one("SELECT * FROM questions WHERE id = ?", (question_id,))
    if row:
        index_record("question", question_id, row["question"][:120], f"{row['question']} {answer}")
    return {"id": question_id, "status": "answered"}


def list_questions(status: str = "") -> list[dict]:
    if status:
        return db.query("SELECT * FROM questions WHERE status = ? ORDER BY id DESC", (status,))
    return db.query("SELECT * FROM questions ORDER BY status = 'answered', id DESC")


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
) -> dict:
    if not title.strip() or not decision.strip():
        raise ValueError("decision title and text are required")
    if review_by:
        from datetime import date

        try:
            date.fromisoformat(review_by)
        except ValueError as exc:
            raise ValueError(f"review_by is not a real date: {review_by}") from exc
    if review_by and not DATE_RE.match(review_by):
        raise ValueError(
            "review_by must be YYYY-MM-DD — anything else would never trigger the stale sweep"
        )
    if category not in DECISION_CATEGORIES:
        raise ValueError(f"category must be one of {DECISION_CATEGORIES}")
    if category == "charter" and not review_by:
        raise ValueError(
            "charter entries need a review_by date — the whole point is that"
            " they get reconfirmed instead of silently rotting"
        )
    did = db.execute(
        "INSERT INTO decisions (title, context, decision, decided_by, review_by, category,"
        " origin, created_by, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        ),
    )
    db.log_activity(actor or decided_by or "system", "record_decision", f"#{did} {title}")
    index_record("decision", did, title, f"{decision} {context}")
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
    # validate successor inputs BEFORE the CAS claim — a failed create after
    # the flip would orphan the old decision as superseded-by-nothing
    if review_by:
        from datetime import date

        try:
            date.fromisoformat(review_by)
        except ValueError as exc:
            raise ValueError(f"review_by is not a real date: {review_by}") from exc
    if review_by and not DATE_RE.match(review_by):
        raise ValueError("review_by must be YYYY-MM-DD")
    old = db.query_one("SELECT * FROM decisions WHERE id = ?", (decision_id,))
    if not old:
        raise ValueError(f"decision #{decision_id} not found")
    # CAS-claim the old decision BEFORE creating the successor — two racing
    # supersedes must not leave two active contradicting decisions
    claimed = db.execute_rowcount(
        "UPDATE decisions SET status = 'superseded' WHERE id = ? AND status != 'superseded'",
        (decision_id,),
    )
    if not claimed:
        current = db.query_row("SELECT superseded_by FROM decisions WHERE id = ?", (decision_id,))
        raise ValueError(
            f"decision #{decision_id} already superseded by #{current['superseded_by']}"
        )
    if old["category"] == "charter" and not review_by:
        # charter replacements keep riding the sweep — default the 90-day push
        from datetime import date, timedelta

        review_by = (date.fromisoformat(db.now()[:10]) + timedelta(days=90)).isoformat()
    new = record_decision(
        title,
        decision,
        context or f"Supersedes #{decision_id}: {old['title']}",
        decided_by,
        review_by,
        category=old["category"],  # a charter entry's replacement stays charter
        actor=actor,
        origin=origin,
    )
    db.execute("UPDATE decisions SET superseded_by = ? WHERE id = ?", (new["id"], decision_id))
    db.log_activity(
        actor or decided_by or "system", "supersede_decision", f"#{decision_id} -> #{new['id']}"
    )
    return {**new, "supersedes": decision_id}


def sweep_stale_decisions() -> list[dict]:
    """Flip active decisions past their review_by date to stale (once — the
    status flip is the claim). Scheduled daily; stale ≠ wrong, it means
    'reconfirm or supersede me'."""
    swept = []
    for d in db.query(
        "SELECT * FROM decisions WHERE status = 'active'"
        " AND review_by IS NOT NULL AND review_by < ?",
        (db.now()[:10],),
    ):
        claimed = db.execute_rowcount(
            "UPDATE decisions SET status = 'stale' WHERE id = ? AND status = 'active'", (d["id"],)
        )
        if not claimed:
            continue
        swept.append({**d, "status": "stale"})
        from .notifications import notify

        notify(
            d["decided_by"] or "team",
            f"Decision #{d['id']} '{d['title']}' passed its review-by date"
            f" ({d['review_by']}). Reconfirm it or supersede it.",
            tier="digest",
            link="/",
        )
        db.log_activity("scheduler", "stale_decision", f"#{d['id']} {d['title']}")
    return swept


def reconfirm_decision(decision_id: int, review_by: str = "", *, actor: str = "system") -> dict:
    """Reconfirming without a new date pushes review_by out 90 days — it must
    never silently remove the half-life (that would defeat the sweep)."""
    from datetime import date, timedelta

    row = db.query_one("SELECT * FROM decisions WHERE id = ?", (decision_id,))
    if not row:
        raise ValueError(f"decision #{decision_id} not found")
    if row["status"] == "superseded":
        raise ValueError(f"decision #{decision_id} was superseded — reconfirm the successor")
    if review_by:
        from datetime import date

        try:
            date.fromisoformat(review_by)
        except ValueError as exc:
            raise ValueError(f"review_by is not a real date: {review_by}") from exc
    if review_by and not DATE_RE.match(review_by):
        raise ValueError("review_by must be YYYY-MM-DD")
    if not review_by:
        review_by = (date.fromisoformat(db.now()[:10]) + timedelta(days=90)).isoformat()
    db.execute(
        "UPDATE decisions SET status = 'active', review_by = ? WHERE id = ?",
        (review_by, decision_id),
    )
    db.log_activity(actor, "reconfirm_decision", f"#{decision_id} until {review_by}")
    return {"id": decision_id, "status": "active", "review_by": review_by}


def list_decisions(limit: int = 50, status: str = "", category: str = "") -> list[dict]:
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    if category:
        where.append("category = ?")
        params.append(category)
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    return db.query(
        f"SELECT * FROM decisions{clause} ORDER BY id DESC LIMIT ?",  # noqa: S608 — clauses hardcoded
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
) -> dict:
    sid = db.execute(
        "INSERT INTO standups (author, yesterday, today, blockers, origin, created_by, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (author, yesterday, today, blockers, origin, actor or author, db.now()),
    )
    index_record("standup", sid, f"{author}'s standup", f"{yesterday} {today} {blockers}")
    db.log_activity(actor or author, "post_standup", f"#{sid}")
    if blockers.strip():
        from .blockers import raise_blocker

        raise_blocker(
            title=blockers.strip()[:120],
            detail=f"Auto-extracted from {author}'s standup #{sid}",
            owner=author,
            source=f"standup:{sid}",
            actor=actor or author,
            origin=origin,
        )
    return {"id": sid}


def list_standups(limit: int = 30) -> list[dict]:
    return db.query("SELECT * FROM standups ORDER BY id DESC LIMIT ?", (limit,))


def save_note(
    topic: str, content: str, author: str = "", *, actor: str = "", origin: str = "human"
) -> dict:
    nid = db.execute(
        "INSERT INTO notes (topic, content, author, origin, created_by, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (topic, content, author, origin, actor or author or "system", db.now()),
    )
    db.log_activity(actor or author or "system", "save_note", topic)
    index_record("note", nid, topic, content)
    return {"id": nid, "topic": topic}


def search_notes(keyword: str = "") -> list[dict]:
    if keyword:
        like = f"%{keyword}%"
        return db.query(
            "SELECT * FROM notes WHERE topic LIKE ? OR content LIKE ? ORDER BY id DESC LIMIT 25",
            (like, like),
        )
    return db.query("SELECT * FROM notes ORDER BY id DESC LIMIT 25")
