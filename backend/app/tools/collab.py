"""Collaboration tools — thin wrappers over app.services.collab."""

import json
from typing import Any

from strands import tool

from .. import db
from ..agents.identity import (
    agent_identity,
    requester_viewer,
    strong_requester,
    workspace_only_tools,
)
from ..extensions.policy import current_policy_engine, current_policy_subject
from ..services import collab, projection_policy, scope, users
from ._gate import gated_write


@tool
def ask_question(question: str, asked_by: str, assigned_to: str = "") -> str:
    """Log a question for the team so it doesn't get lost in chat.

    Args:
        question: The question being asked.
        asked_by: Who is asking (human or agent name).
        assigned_to: Who should answer it, if known.
    """
    payload: dict[str, Any] = {
        "question": question,
        "asked_by": asked_by,
        "assigned_to": assigned_to,
    }
    return gated_write(
        "question",
        "create",
        payload,
        lambda: collab.ask_question(**payload, actor=agent_identity(), origin="agent"),
    )


@tool
def answer_question(question_id: int, answer: str, answered_by: str = "") -> str:
    """Answer an open question and close it.

    Args:
        question_id: ID of the question.
        answer: The answer text.
        answered_by: Who answered.
    """
    payload: dict[str, Any] = {"answer": answer, "answered_by": answered_by}
    return gated_write(
        "question",
        "update",
        payload,
        lambda: collab.answer_question(
            question_id, **payload, actor=agent_identity(), origin="agent"
        ),
        entity_id=question_id,
    )


@tool
def assign_question(question_id: int, assigned_to: str) -> str:
    """Assign an open question to a teammate (must be an active user).

    Args:
        question_id: ID of the open question.
        assigned_to: Who should answer it.
    """
    payload: dict[str, Any] = {"assigned_to": assigned_to}
    return gated_write(
        "question_assign",
        "update",
        payload,
        lambda: collab.assign_question(
            question_id, **payload, actor=agent_identity(), origin="agent"
        ),
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
    payload: dict[str, Any] = {"title": title, "decision": decision, **optional}
    return gated_write(
        "decision",
        "create",
        payload,
        lambda: collab.record_decision(**payload, actor=agent_identity(), origin="agent"),
    )


@tool
def list_decisions(limit: int = 20) -> str:
    """List recent team decisions, newest first.

    Args:
        limit: Maximum number of decisions to return.
    """
    return json.dumps(collab.list_decisions(limit))


@tool
def post_standup(
    author: str,
    yesterday: str = "",
    today: str = "",
    blockers: str = "",
    share_with_team: bool = False,
) -> str:
    """Post an async standup update for a team member. On a standup shared
    with the team, any blockers mentioned are filed in the blocker register.

    Args:
        author: Whose update this is.
        yesterday: What was accomplished since the last update.
        today: What's planned next.
        blockers: Anything blocking progress.
        share_with_team: True makes it visible to everyone on the roster.
            Keep False (only the author sees it) unless the author asked the
            team to see it. A standup for anybody but the person you help
            is visible to everyone on the roster.
    """
    # "only the author" where the author is the strong requester: a weak
    # viewer reads no private row. A standup in SOMEBODY ELSE's name is
    # theirs: private to them, so they judge the proposal (review.
    # personal_owner) and the roster does not read words put in their
    # mouth. A shared chat writes workspace rows only (tools/_gate.py
    # refuses the rest).
    strong = "" if workspace_only_tools() else strong_requester()
    own = bool(strong) and users.fold(author) == users.fold(strong)
    private = (not share_with_team and own) or (bool(strong) and not own)
    payload: dict[str, Any] = {
        "author": author,
        "yesterday": yesterday,
        "today": today,
        "blockers": blockers,
        "visibility": scope.PRIVATE if private else scope.WORKSPACE,
    }
    return gated_write(
        "standup",
        "create",
        payload,
        lambda: collab.post_standup(**payload, actor=agent_identity(), origin="agent"),
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
    # a note in a teammate's name is indexed as theirs and has no delete
    # for them; the writer is the requester, or the agent itself
    strong = "" if workspace_only_tools() else strong_requester()
    if author and strong and users.fold(author) != users.fold(strong):
        return json.dumps({"error": "A note carries your own name. Leave author empty."})
    payload: dict[str, Any] = {"topic": topic, "content": content, "author": author}
    return gated_write(
        "note",
        "create",
        payload,
        lambda: collab.save_note(**payload, actor=agent_identity(), origin="agent"),
    )


@tool
def search_notes(keyword: str = "") -> str:
    """Search notes by keyword: notes the whole team can read, notes of the
    crews the person you are talking to belongs to, and that person's private
    notes (`visibility` says which). You can read a private note but not
    change it. Do not copy a private note's text into a record other people
    can read unless the person asks you to.

    Args:
        keyword: Text to search for; empty returns the most recent notes.
    """
    # The REQUESTER's viewer, as get_attention reads it (tools/portfolio.py).
    # A strong identity's quick captures start private (docs/VISIBILITY.md),
    # so a NOBODY read here hides every note a person dumps from the agent in
    # their own chat. A shared chat sets NOBODY (services/shared_chat_agents.py)
    # and an unattended run sets nothing, so neither reads a private row: a
    # reply in a shared chat is read by every member.
    rv = requester_viewer()
    viewer = rv if isinstance(rv, scope.Viewer) else scope.NOBODY
    # Per row, as get_attention filters: the wrapper's policy check sees the
    # tool, not the notes, so a workplace rule that denies agents a private
    # or crew row by its classification would otherwise never run.
    policy = projection_policy.ProjectionPolicy(
        current_policy_engine(),
        current_policy_subject(),
        "skein.tool.search_notes",
        "agent_tool",
        viewer,
        agent=agent_identity(),
        tool="search_notes",
    )
    with db.read_transaction():
        return json.dumps(policy.filter_rows("note", collab.search_notes(keyword, viewer)))


@tool
def edit_note(note_id: int, topic: str = "", content: str = "") -> str:
    """Correct a knowledge-base note's topic or content. Only pass the fields
    to change; the rest stay as they are.

    Args:
        note_id: ID of the note.
        topic: New topic, if changing it.
        content: New content (markdown), if changing it.
    """
    payload: dict[str, Any] = {k: v for k, v in {"topic": topic, "content": content}.items() if v}
    if not payload:
        return json.dumps({"error": "nothing to change — pass topic and/or content"})
    return gated_write(
        "note_edit",
        "update",
        payload,
        lambda: collab.update_note(note_id, **payload, actor=agent_identity(), origin="agent"),
        entity_id=note_id,
        summary=f"edit note #{note_id}",
    )


@tool
def delete_note(note_id: int) -> str:
    """Delete a knowledge-base note for good (it also leaves search). Prefer
    edit_note when the note is wrong but salvageable.

    Args:
        note_id: ID of the note to delete.
    """
    # read as the requester, so their own private note reaches the gate and
    # its refusal names the real reason, not "no note"
    rv = requester_viewer()
    row = collab.get_note(note_id, rv if isinstance(rv, scope.Viewer) else scope.NOBODY)
    if not row:
        return json.dumps({"error": f"no note #{note_id}"})
    return gated_write(
        "note_delete",
        "update",
        {},
        lambda: collab.delete_note(note_id, actor=agent_identity(), origin="agent"),
        entity_id=note_id,
        # the reviewer must see what would be destroyed, right on the card —
        # but only a reviewer who can already read it. scope.detail drops the
        # body for a scoped row: GET /api/review serves this summary to every
        # CurrentUser and propose_change quotes it into a `team` notification,
        # so the note's own text reached the roster by being deleted.
        # review.change_diff shows the full body to a reader who passes the
        # tier check, which is where a destructive verdict gets its evidence.
        summary=scope.detail(
            row["visibility"],
            f"delete note #{note_id}",
            f"'{row['topic']}': {row['content'][:80]}",
        ),
    )
