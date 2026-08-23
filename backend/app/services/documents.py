"""Markdown documents an agent writes and edits.

The other half of services/uploads.py: a person attaches a file, an agent
produces one. Both are `artifacts` rows under the same containment root, and
the split is what keeps them honest — an UPLOAD is never rewritten. An agent
asked to revise one writes a new document that records the upload in
`derived_from`, so the person's own file is still the file they attached, and
"undo" costs nothing because the source never moved.

Every write here goes through tools/_gate.py, so a document is created or
changed under the same authority matrix, review inbox and receipts as a note
or a task. Nothing in this module writes outside data/artifacts.
"""

from pathlib import Path

from .. import config, db
from . import artifact_files, handoff, scope

# A document is markdown a person reads on Work → Reports, so it is bounded by
# what that reader can take rather than by what a model can emit.
MAX_DOCUMENT_BYTES = 512 * 1024
TITLE_LIMIT = 120


def _root() -> Path:
    path = Path(config.DATA_DIR) / "artifacts" / "documents"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _check_content(content: str) -> None:
    if not content.strip():
        raise ValueError("a document needs content. Write the body, then save it.")
    if len(content.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise ValueError(
            f"the document is larger than {MAX_DOCUMENT_BYTES // 1024} KB. Write a shorter one."
        )


def _check_source(source_id: int) -> None:
    """Refuse to derive a shared document from a source that is not shared.

    THE LAUNDERING GUARD. An agent that can read a private upload can also
    write a document, and a summary of a private file stored at the workspace
    tier is that file's content, published, with no human in the loop. One
    workspace means everybody.

    Refused rather than clamped: a private document would have to carry its
    source's OWNER to stay reachable, and a row whose created_by is a person
    who did not write it is a worse lie than a refusal. The agent can still
    answer about the file in the chat turn — that answer goes to the one
    person who attached it, which is the reader who was always allowed it.
    """
    if not source_id:
        return
    row = db.query_one("SELECT visibility, kind FROM artifacts WHERE id = ?", (source_id,))
    if not row:
        raise scope.missing("artifacts", source_id)
    if row["visibility"] != "workspace":
        raise PermissionError(
            f"artifact #{source_id} is not shared with the team,"
            " so a document made from it cannot be shared either."
            " Answer in the conversation instead."
        )


def create_document(
    title: str,
    content: str,
    *,
    actor: str = "system",
    origin: str = "human",
    source_id: int = 0,
    engagement_id: int = 0,
) -> dict:
    """Write a new markdown document.

    `origin` is accepted because services/review.py::_apply passes
    origin="agent_verified" to EVERY registry applier when a human approves a
    proposal. Without the parameter that call is a TypeError, the generic
    handler resets the row to pending, and the proposal boomerangs in the
    queue forever with no path to approval.
    """
    _check_content(content)
    _check_source(source_id)
    clean_title = title.strip()[:TITLE_LIMIT] or "Untitled document"
    with db.transaction():
        # The row is inserted before the file, because the file is named after
        # the row id — see services/uploads.py::save_upload for the same
        # ordering and the same reason.
        artifact_id = db.execute(
            "INSERT INTO artifacts (engagement_id, kind, title, path, created_by, created_at,"
            " visibility, mime, size, derived_from)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (
                engagement_id or None,
                "document",
                clean_title,
                "",
                actor,
                db.now(),
                "workspace",
                "text/markdown",
                len(content.encode("utf-8")),
                source_id or None,
            ),
        )
        path = _root() / f"{artifact_id}.md"
        db.execute("UPDATE artifacts SET path = ? WHERE id = ?", (str(path), artifact_id))
        artifact_files.publish(path, content.encode("utf-8"))
        db.log_activity(actor, "create_document", f"artifact #{artifact_id} {clean_title}")
        # "id" as well as "artifact_id": tools/_gate.py stamps the receipt ref
        # from result["id"] and review.py stamps the proposal's lineage from
        # it. Absent, both silently become 0 — a receipt with no reference is
        # dropped from the transcript rather than reported wrong.
        return {"id": artifact_id, "artifact_id": artifact_id, "title": clean_title}


def _document_row(artifact_id: int) -> dict:
    """The document row, held for the caller's transaction.

    FOR UPDATE because edit_document reads the FILE, decides from what it
    finds, and writes it back: two edits of one document otherwise both read
    the old body and the second silently discards the first. The row is the
    only thing both paths share, so holding it serializes them (CLAUDE.md,
    "a read whose RESULT decides a later write must hold something").
    """
    row = db.query_one("SELECT * FROM artifacts WHERE id = ? FOR UPDATE", (artifact_id,))
    if not row:
        raise scope.missing("artifacts", artifact_id)
    if row["kind"] != "document":
        # Named, not a generic refusal: an agent told only "no" retries with
        # the same id. An upload is a person's own file and is never rewritten
        # — the revision path is a new document carrying derived_from.
        raise PermissionError(
            f"artifact #{artifact_id} was not written by an agent, so it cannot be changed."
            " If it is a file somebody attached, answer in the conversation instead."
        )
    return row


def _document_path(row: dict) -> Path:
    """The file for a document row, refused if it escapes the artifact root.

    Same containment as services/uploads.py::upload_bytes: resolve() runs
    BEFORE the test, so a symlink planted under the directory is followed to
    its target and then refused.
    """
    root = (Path(config.DATA_DIR) / "artifacts").resolve()
    try:
        path = Path(row["path"]).resolve()
    except ValueError as e:
        raise scope.missing("artifacts", int(row["id"])) from e
    if not path.is_relative_to(root):
        raise scope.missing("artifacts", int(row["id"]))
    if not path.is_file():
        raise handoff.ArtifactUnreadable(
            f"document #{row['id']} has no file on disk."
            " Check that the volume holding data/artifacts is mounted."
        )
    return path


def edit_document(
    artifact_id: int, old: str, new: str, *, actor: str = "system", origin: str = "human"
) -> dict:
    """Replace one exact run of text in a document.

    `origin` is the review applier's contract — see create_document above.

    A whole-body rewrite would let a model that read half a file replace all
    of it, so the edit states what it expects to find. A match that is not
    unique is refused rather than guessed at.
    """
    if not old:
        raise ValueError("an edit needs the text to replace. Quote the exact text.")
    with db.transaction():
        row = _document_row(artifact_id)
        path = _document_path(row)
        body = path.read_text(encoding="utf-8")
        found = body.count(old)
        if found == 0:
            raise ValueError(
                f"that text is not in document #{artifact_id}. Read the document, then quote it exactly."
            )
        if found > 1:
            raise ValueError(
                f"that text is in document #{artifact_id} {found} times."
                " Quote more of the surrounding text so it matches one place."
            )
        updated = body.replace(old, new)
        _check_content(updated)
        data = updated.encode("utf-8")
        # From the LOGICAL name, never the stored path: a revision derived from
        # the previous revision compounds one uuid per edit and crosses
        # NAME_MAX on the seventh, making the document permanently uneditable.
        revision = artifact_files.unique_revision(_root() / f"{artifact_id}.md")
        artifact_files.publish(revision, data, old=path)
        db.execute(
            "UPDATE artifacts SET path = ?, size = ? WHERE id = ?",
            (str(revision), len(data), artifact_id),
        )
        db.log_activity(actor, "edit_document", f"artifact #{artifact_id} {row['title']}")
        return {"id": artifact_id, "artifact_id": artifact_id, "title": row["title"]}
