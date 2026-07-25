"""Collaboration tools — thin wrappers over app.services.collab."""

import json

from strands import tool

from ..services import collab
from ._gate import gated_write


@tool
def ask_question(question: str, asked_by: str, assigned_to: str = "") -> str:
    """Log a question for the team so it doesn't get lost in chat.

    Args:
        question: The question being asked.
        asked_by: Who is asking (human or agent name).
        assigned_to: Who should answer it, if known.
    """
    payload = {"question": question, "asked_by": asked_by, "assigned_to": assigned_to}
    return gated_write(
        "question",
        "create",
        payload,
        lambda: collab.ask_question(**payload, actor="agent", origin="agent"),
    )


@tool
def answer_question(question_id: int, answer: str, answered_by: str = "") -> str:
    """Answer an open question and close it.

    Args:
        question_id: ID of the question.
        answer: The answer text.
        answered_by: Who answered.
    """
    payload = {"answer": answer, "answered_by": answered_by}
    return gated_write(
        "question",
        "update",
        payload,
        lambda: collab.answer_question(question_id, **payload, actor="agent", origin="agent"),
        entity_id=question_id,
    )


@tool
def list_questions(status: str = "open") -> str:
    """List logged questions.

    Args:
        status: 'open', 'answered', or empty for all.
    """
    return json.dumps(collab.list_questions(status))


@tool
def record_decision(
    title: str,
    decision: str,
    context: str = "",
    decided_by: str = "",
    review_by: str = "",
    category: str = "",
) -> str:
    """Record a team decision in the decision log so future work can reference it.

    Args:
        title: Short name of the decision.
        decision: What was decided.
        context: Why — the options considered and reasoning.
        decided_by: Who made or ratified the decision.
        review_by: YYYY-MM-DD date when the decision should be revisited.
        category: '' for normal decisions, 'charter' for team charter /
            decision-rights entries (charter requires review_by).
    """
    optional = {"context": context, "decided_by": decided_by, "review_by": review_by}
    if category:
        optional["category"] = category
    payload = {"title": title, "decision": decision, **optional}
    return gated_write(
        "decision",
        "create",
        payload,
        lambda: collab.record_decision(**payload, actor="agent", origin="agent"),
    )


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
    payload = {"author": author, "yesterday": yesterday, "today": today, "blockers": blockers}
    return gated_write(
        "standup",
        "create",
        payload,
        lambda: collab.post_standup(**payload, actor="agent", origin="agent"),
    )


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
    payload = {"topic": topic, "content": content, "author": author}
    return gated_write(
        "note",
        "create",
        payload,
        lambda: collab.save_note(**payload, actor="agent", origin="agent"),
    )


@tool
def search_notes(keyword: str = "") -> str:
    """Search the knowledge base notes by keyword.

    Args:
        keyword: Text to search for; empty returns the most recent notes.
    """
    return json.dumps(collab.search_notes(keyword))
