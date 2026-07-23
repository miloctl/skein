"""Collaboration tools — thin wrappers over app.services.collab."""

import json

from strands import tool

from ..services import collab


def _safe(fn):
    try:
        return json.dumps(fn())
    except ValueError as exc:
        return json.dumps({"error": str(exc)})


@tool
def ask_question(question: str, asked_by: str, assigned_to: str = "") -> str:
    """Log a question for the team so it doesn't get lost in chat.

    Args:
        question: The question being asked.
        asked_by: Who is asking (human or agent name).
        assigned_to: Who should answer it, if known.
    """
    return _safe(lambda: collab.ask_question(question, asked_by, assigned_to,
                                             actor="agent", origin="agent"))


@tool
def answer_question(question_id: int, answer: str, answered_by: str = "") -> str:
    """Answer an open question and close it.

    Args:
        question_id: ID of the question.
        answer: The answer text.
        answered_by: Who answered.
    """
    return _safe(lambda: collab.answer_question(question_id, answer, answered_by,
                                                actor="agent", origin="agent"))


@tool
def list_questions(status: str = "open") -> str:
    """List logged questions.

    Args:
        status: 'open', 'answered', or empty for all.
    """
    return json.dumps(collab.list_questions(status))


@tool
def record_decision(title: str, decision: str, context: str = "", decided_by: str = "") -> str:
    """Record a team decision in the decision log so future work can reference it.

    Args:
        title: Short name of the decision.
        decision: What was decided.
        context: Why — the options considered and reasoning.
        decided_by: Who made or ratified the decision.
    """
    return _safe(lambda: collab.record_decision(title, decision, context, decided_by,
                                                actor="agent", origin="agent"))


@tool
def list_decisions(limit: int = 20) -> str:
    """List recent team decisions, newest first.

    Args:
        limit: Maximum number of decisions to return.
    """
    return json.dumps(collab.list_decisions(limit))


@tool
def post_standup(author: str, yesterday: str = "", today: str = "", blockers: str = "") -> str:
    """Post an async standup update for a team member. Any blockers mentioned
    are automatically filed in the blocker register.

    Args:
        author: Whose update this is.
        yesterday: What was accomplished since the last update.
        today: What's planned next.
        blockers: Anything blocking progress.
    """
    return _safe(lambda: collab.post_standup(author, yesterday, today, blockers,
                                             actor="agent", origin="agent"))


@tool
def list_standups(limit: int = 10) -> str:
    """List recent standup updates, newest first.

    Args:
        limit: Maximum number of updates to return.
    """
    return json.dumps(collab.list_standups(limit))


@tool
def save_note(topic: str, content: str, author: str = "") -> str:
    """Save a note to the shared team knowledge base (conventions, learnings, context).

    Args:
        topic: Short topic/slug the note is about.
        content: The knowledge to persist.
        author: Who wrote it.
    """
    return _safe(lambda: collab.save_note(topic, content, author,
                                          actor="agent", origin="agent"))


@tool
def search_notes(keyword: str = "") -> str:
    """Search the knowledge base notes by keyword.

    Args:
        keyword: Text to search for; empty returns the most recent notes.
    """
    return json.dumps(collab.search_notes(keyword))
