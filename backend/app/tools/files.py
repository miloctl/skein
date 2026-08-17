"""Document tools: an agent reads what it was given and writes markdown back.

Read is a plain tool. Both writes go through the gate, so a document an agent
creates or changes carries the same authority level, review inbox row and
receipt as any other agent write. Nothing here can name a path — the service
owns every filename, and both calls address an artifact by id.
"""

import json
from typing import Any

from strands import tool

from .. import db
from ..agents.identity import agent_identity
from ..services import documents, handoff, scope
from ._gate import gated_write


@tool
def read_artifact(artifact_id: int) -> str:
    """Read the text of an artifact — a report, a digest, or a document.

    Args:
        artifact_id: The id of the artifact to read.
    """
    # scope.NOBODY, the workspace tier: every agent surface reads at that tier
    # (services/scope.py::Viewer). A person's attached file is private, so it
    # is unreadable here BY CONSTRUCTION — the one path that reaches an upload
    # is the person attaching it to their own turn (routes/chat.py).
    #
    # Both raises are answered as JSON rather than left to propagate: a tool
    # that raises kills the agent loop, and a missing id is the ordinary case
    # of a model guessing a number (tests/test_gate_coverage.py).
    try:
        row = handoff.read_artifact(artifact_id, scope.NOBODY)
    except (handoff.ArtifactUnreadable, db.NotFound) as e:
        return json.dumps({"error": str(e)})
    return json.dumps(
        {
            "artifact_id": row["id"],
            "kind": row["kind"],
            "title": row["title"],
            "markdown": row["markdown"],
        }
    )


@tool
def create_document(title: str, content: str, source_id: int = 0, engagement_id: int = 0) -> str:
    """Write a new markdown document, saved as an artifact the team can read.

    Args:
        title: Short title for the document.
        content: The markdown body. A ```mermaid block renders as a diagram.
        source_id: Optional id of the artifact this was made from.
        engagement_id: Optional engagement to file it under.
    """
    payload: dict[str, Any] = {
        "title": title,
        "content": content,
        "source_id": source_id,
        "engagement_id": engagement_id,
    }
    return gated_write(
        "document",
        "create",
        payload,
        lambda: documents.create_document(**payload, actor=agent_identity()),
        summary=title,
    )


@tool
def edit_document(artifact_id: int, old_text: str, new_text: str) -> str:
    """Replace one exact run of text in a document an agent wrote.

    An uploaded file is never changed, and a file somebody attached is
    private — a document made from one cannot be shared with the team, so
    answer about it in the conversation instead. source_id links a document to
    another SHARED artifact it was made from.

    Args:
        artifact_id: The id of the document to change.
        old_text: The exact text to replace. It must appear exactly once.
        new_text: What to put in its place.
    """
    payload: dict[str, Any] = {"old": old_text, "new": new_text}
    return gated_write(
        "document_edit",
        "update",
        payload,
        lambda: documents.edit_document(artifact_id, **payload, actor=agent_identity()),
        entity_id=artifact_id,
    )
