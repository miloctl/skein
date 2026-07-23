"""Questions, decisions, standups, and knowledge-base services."""

from .. import db
from .search import index_record


def ask_question(question: str, asked_by: str, assigned_to: str = "",
                 *, actor: str = "", origin: str = "human") -> dict:
    qid = db.execute(
        "INSERT INTO questions (asked_by, assigned_to, question, origin, created_by, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (asked_by, assigned_to, question, origin, actor or asked_by, db.now()),
    )
    db.log_activity(actor or asked_by, "ask_question", f"#{qid}")
    index_record("question", qid, question[:120], question)
    return {"id": qid, "status": "open"}


def answer_question(question_id: int, answer: str, answered_by: str = "",
                    *, actor: str = "", origin: str = "human") -> dict:
    if not db.query_one("SELECT id FROM questions WHERE id = ?", (question_id,)):
        raise ValueError(f"question #{question_id} not found")
    db.execute(
        "UPDATE questions SET answer = ?, status = 'answered', answered_at = ? WHERE id = ?",
        (answer, db.now(), question_id),
    )
    db.log_activity(actor or answered_by or "system", "answer_question", f"#{question_id}")
    row = db.query_one("SELECT * FROM questions WHERE id = ?", (question_id,))
    if row:
        index_record("question", question_id, row["question"][:120],
                     f"{row['question']} {answer}")
    return {"id": question_id, "status": "answered"}


def list_questions(status: str = "") -> list[dict]:
    if status:
        return db.query("SELECT * FROM questions WHERE status = ? ORDER BY id DESC", (status,))
    return db.query("SELECT * FROM questions ORDER BY status = 'answered', id DESC")


def record_decision(title: str, decision: str, context: str = "", decided_by: str = "",
                    *, actor: str = "", origin: str = "human") -> dict:
    did = db.execute(
        "INSERT INTO decisions (title, context, decision, decided_by, origin, created_by, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (title, context, decision, decided_by, origin, actor or decided_by, db.now()),
    )
    db.log_activity(actor or decided_by or "system", "record_decision", f"#{did} {title}")
    index_record("decision", did, title, f"{decision} {context}")
    return {"id": did, "title": title}


def list_decisions(limit: int = 50) -> list[dict]:
    return db.query("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,))


def post_standup(author: str, yesterday: str = "", today: str = "", blockers: str = "",
                 *, actor: str = "", origin: str = "human") -> dict:
    sid = db.execute(
        "INSERT INTO standups (author, yesterday, today, blockers, origin, created_by, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (author, yesterday, today, blockers, origin, actor or author, db.now()),
    )
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


def save_note(topic: str, content: str, author: str = "",
              *, actor: str = "", origin: str = "human") -> dict:
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
