"""Collaboration tools: questions, decisions, standups, and the shared knowledge base."""

import json

from strands import tool

from .. import db


@tool
def ask_question(question: str, asked_by: str, assigned_to: str = "") -> str:
    """Log a question for the team so it doesn't get lost in chat.

    Args:
        question: The question being asked.
        asked_by: Who is asking (human or agent name).
        assigned_to: Who should answer it, if known.
    """
    qid = db.execute(
        "INSERT INTO questions (asked_by, assigned_to, question, created_at) VALUES (?, ?, ?, ?)",
        (asked_by, assigned_to, question, db.now()),
    )
    db.log_activity(asked_by, "ask_question", f"#{qid}")
    return json.dumps({"id": qid, "status": "open"})


@tool
def answer_question(question_id: int, answer: str, answered_by: str = "") -> str:
    """Answer an open question and close it.

    Args:
        question_id: ID of the question.
        answer: The answer text.
        answered_by: Who answered.
    """
    db.execute(
        "UPDATE questions SET answer = ?, status = 'answered', answered_at = ? WHERE id = ?",
        (answer, db.now(), question_id),
    )
    db.log_activity(answered_by or "agent", "answer_question", f"#{question_id}")
    return json.dumps({"id": question_id, "status": "answered"})


@tool
def list_questions(status: str = "open") -> str:
    """List logged questions.

    Args:
        status: 'open', 'answered', or empty for all.
    """
    if status:
        return json.dumps(db.query("SELECT * FROM questions WHERE status = ? ORDER BY id DESC", (status,)))
    return json.dumps(db.query("SELECT * FROM questions ORDER BY id DESC"))


@tool
def record_decision(title: str, decision: str, context: str = "", decided_by: str = "") -> str:
    """Record a team decision in the decision log so future work can reference it.

    Args:
        title: Short name of the decision.
        decision: What was decided.
        context: Why — the options considered and reasoning.
        decided_by: Who made or ratified the decision.
    """
    did = db.execute(
        "INSERT INTO decisions (title, context, decision, decided_by, created_at) VALUES (?, ?, ?, ?, ?)",
        (title, context, decision, decided_by, db.now()),
    )
    db.log_activity(decided_by or "agent", "record_decision", f"#{did} {title}")
    return json.dumps({"id": did, "title": title})


@tool
def list_decisions(limit: int = 20) -> str:
    """List recent team decisions, newest first.

    Args:
        limit: Maximum number of decisions to return.
    """
    return json.dumps(db.query("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,)))


@tool
def post_standup(author: str, yesterday: str = "", today: str = "", blockers: str = "") -> str:
    """Post an async standup update for a team member.

    Args:
        author: Whose update this is.
        yesterday: What was accomplished since the last update.
        today: What's planned next.
        blockers: Anything blocking progress.
    """
    sid = db.execute(
        "INSERT INTO standups (author, yesterday, today, blockers, created_at) VALUES (?, ?, ?, ?, ?)",
        (author, yesterday, today, blockers, db.now()),
    )
    db.log_activity(author, "post_standup", f"#{sid}")
    return json.dumps({"id": sid})


@tool
def list_standups(limit: int = 10) -> str:
    """List recent standup updates, newest first.

    Args:
        limit: Maximum number of updates to return.
    """
    return json.dumps(db.query("SELECT * FROM standups ORDER BY id DESC LIMIT ?", (limit,)))


@tool
def save_note(topic: str, content: str, author: str = "") -> str:
    """Save a note to the shared team knowledge base (conventions, learnings, context).

    Args:
        topic: Short topic/slug the note is about.
        content: The knowledge to persist.
        author: Who wrote it.
    """
    nid = db.execute(
        "INSERT INTO notes (topic, content, author, created_at) VALUES (?, ?, ?, ?)",
        (topic, content, author, db.now()),
    )
    db.log_activity(author or "agent", "save_note", topic)
    return json.dumps({"id": nid, "topic": topic})


@tool
def search_notes(keyword: str = "") -> str:
    """Search the shared knowledge base by keyword (matches topic and content).

    Args:
        keyword: Text to search for; empty returns the most recent notes.
    """
    if keyword:
        like = f"%{keyword}%"
        return json.dumps(db.query(
            "SELECT * FROM notes WHERE topic LIKE ? OR content LIKE ? ORDER BY id DESC LIMIT 25",
            (like, like),
        ))
    return json.dumps(db.query("SELECT * FROM notes ORDER BY id DESC LIMIT 25"))
