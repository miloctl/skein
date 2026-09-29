"""Markdown documents an agent writes and edits.

The other half of services/uploads.py: a person attaches a file, an agent
produces one. Both are `artifacts` rows under the same containment root, and
the split is what keeps them honest — an UPLOAD is never rewritten. An agent
asked to revise one writes a new document that records the upload in
`derived_from`, so the person's own file is still the file they attached, and
"undo" costs nothing because the source never moved.

An agent's write goes through tools/_gate.py, so a document is created or
changed under the same authority matrix, review inbox and receipts as a note
or a task. A person saves and restores through REST (routes/api.py). Every
write, by either door, is a numbered revision (docs/intent/document-revisions.md).
Nothing in this module writes outside data/artifacts.
"""

import difflib
from pathlib import Path

from .. import config, db
from . import artifact_files, handoff, scope
from .search import index_record

# A document is markdown a person reads on Work → Reports, so it is bounded by
# what that reader can take rather than by what a model can emit.
MAX_DOCUMENT_BYTES = 512 * 1024
TITLE_LIMIT = 120
HISTORY_LIMIT = 100


class StaleRevision(db.Conflict, db.TerminalReject):
    """A write named a base revision the document has moved past.

    Conflict makes a person's save a 409 (app/main.py). TerminalReject makes
    review.approve_change settle an agent's proposal as rejected without
    counting it: the generic handler would put it back in the queue after
    every approval, and the agent's work is not what failed.
    """


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

    An unshared source reads as absent (scope.missing): any other refusal
    tells a caller walking ids which of them are somebody's private files.
    The tool's own docstring carries the guidance (tools/files.py).
    """
    if not source_id:
        return
    row = db.query_one("SELECT visibility FROM artifacts WHERE id = ?", (source_id,))
    if not row or row["visibility"] != "workspace":
        raise scope.missing("artifacts", source_id)


def create_document(
    title: str,
    content: str,
    *,
    actor: str = "system",
    origin: str = "human",
    source_id: int = 0,
    engagement_id: int = 0,
    change_id: int = 0,
    visibility: str = scope.WORKSPACE,
    crew_id: int = 0,
) -> dict:
    """Write a new markdown document.

    An agent's document is workspace: the tool sends no tier. A person picks
    one (POST /api/documents), and a private document stays out of every
    shared sink (docs/intent/document-revisions.md, D10).

    `origin` is accepted because services/review.py::_apply passes
    origin="agent_verified" to EVERY registry applier when a human approves a
    proposal. Without the parameter that call is a TypeError, the generic
    handler resets the row to pending, and the proposal boomerangs in the
    queue forever with no path to approval.
    """
    _check_content(content)
    _check_source(source_id)
    # checked, not left to the foreign key: its violation is a 500 that an
    # approval repeats on every try, and the reader learns nothing
    efrag, ep = scope.visible_filter(scope.Viewer.for_actor(actor), "engagements")
    if engagement_id and not db.query_one(
        f"SELECT id FROM engagements WHERE id = ? AND {efrag}",  # noqa: S608 — scope.visible_filter emits only bound marks
        (engagement_id, *ep),
    ):
        raise ValueError(scope.missing_text("engagements", engagement_id))
    clean_title = title.strip()[:TITLE_LIMIT] or "Untitled document"
    with db.transaction():
        # inside the transaction: the crew row lock resolve_write takes must
        # last until the insert, or a removal in between writes into a crew
        # the author has left
        tier, cid = scope.resolve_write(visibility, crew_id, actor=actor)
        # The row is inserted before the file, because the file is named after
        # the row id — see services/uploads.py::save_upload for the same
        # ordering and the same reason.
        row = db.query_row(
            "INSERT INTO artifacts (engagement_id, kind, title, path, created_by, created_at,"
            " visibility, crew_id, mime, size, derived_from)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *",
            (
                engagement_id or None,
                "document",
                clean_title,
                "",
                actor,
                db.now(),
                tier,
                cid,
                "text/markdown",
                len(content.encode("utf-8")),
                source_id or None,
            ),
        )
        revision = _publish_revision(
            row, content, head=0, actor=actor, origin=origin, change_id=change_id
        )
        db.log_activity(actor, "create_document", _ledger_detail(row, revision))
        artifact_id = int(row["id"])
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
    # an unshared artifact reads as absent, for the reason _check_source gives
    if not row or row["visibility"] != "workspace":
        raise scope.missing("artifacts", artifact_id)
    if row["kind"] != "document":
        # Named, not a generic refusal: an agent told only "no" retries with
        # the same id. An upload is a person's own file and is never rewritten
        # — the revision path is a new document carrying derived_from.
        raise PermissionError(
            f"artifact #{artifact_id} is not a document, so it cannot be changed."
            " If it is a file somebody attached, answer in the conversation instead."
        )
    return row


def _stored_path(row: dict) -> Path:
    """The path a document row names, refused if it escapes the artifact root.

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
    return path


def _document_path(row: dict) -> Path:
    """The file for a document row, which must exist."""
    path = _stored_path(row)
    if not path.is_file():
        raise handoff.ArtifactUnreadable(
            f"document #{row['id']} has no file on disk."
            " Check that the volume holding data/artifacts is mounted."
        )
    return path


def _head(row: dict) -> dict:
    """The head revision of a document row the caller holds.

    A document written before revision rows existed has none, and a migration
    cannot read files. Its first write seeds revision 1 from the file under the
    same row lock, as the body stands: earlier edits were never kept. A file
    that fails its digest is not taken as the truth.
    """
    # first, whatever the write: a row whose stored path escapes the root is a
    # restored or hand-edited row, and no write may go on to publish over it
    _stored_path(row)
    head = db.query_one(
        "SELECT revision, body FROM document_revisions WHERE artifact_id = ?"
        " ORDER BY revision DESC LIMIT 1",
        (row["id"],),
    )
    if head:
        return head
    source = _document_path(row).read_bytes()
    if not artifact_files.content_matches(source, row.get("content_sha256")):
        raise handoff.ArtifactUnreadable(
            f"document #{row['id']} does not match its stored digest."
            " Restore the matching artifact volume, or create a new document."
        )
    body = source.decode("utf-8")
    # origin agent: agents were the only writers before people could edit
    db.execute(
        "INSERT INTO document_revisions (artifact_id, revision, body, content_sha256,"
        " author, origin, created_at) VALUES (?, 1, ?, ?, ?, 'agent', ?)",
        (
            row["id"],
            body,
            artifact_files.content_sha256(source),
            row["created_by"],
            row["created_at"],
        ),
    )
    return {"revision": 1, "body": body}


def _publish_revision(
    row: dict,
    body: str,
    *,
    head: int,
    actor: str,
    origin: str,
    change_id: int = 0,
    restored_from: int | None = None,
) -> int:
    """The one writer of document_revisions: the head row and the file together.

    The caller holds the artifacts row (FOR UPDATE, or its own fresh insert),
    so `head` cannot move under it. The primary key is the backstop: a writer
    without the lock fails on the duplicate revision instead of overwriting
    one. The file stays the published head that handoff.read_artifact reads,
    written in the same transaction with the same digest.
    """
    data = body.encode("utf-8")
    revision = head + 1
    logical = _root() / f"{row['id']}.md"
    if row["path"]:
        # From the LOGICAL name, never the stored path: a revision derived from
        # the previous revision compounds one uuid per edit and crosses
        # NAME_MAX on the seventh, making the document permanently uneditable.
        target = artifact_files.unique_revision(logical)
        digest = artifact_files.publish(target, data, old=_stored_path(row))
    else:
        target = logical
        digest = artifact_files.publish(target, data)
    db.execute(
        "UPDATE artifacts SET path = ?, size = ?, content_sha256 = ? WHERE id = ?",
        (str(target), len(data), digest, row["id"]),
    )
    # the current text only: the index upserts on (entity, entity_id), so an
    # old revision's words stop matching, and a private document is kept out
    # by index_record itself (search._is_private)
    index_record("document", int(row["id"]), row["title"], body)
    db.execute(
        "INSERT INTO document_revisions (artifact_id, revision, body, content_sha256, author,"
        " origin, change_id, restored_from, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            row["id"],
            revision,
            body,
            digest,
            actor,
            origin,
            change_id or None,
            restored_from,
            db.now(),
        ),
    )
    return revision


def _ledger_detail(row: dict, revision: int) -> str:
    """An id and a revision number, never the body: the ledger is hash-chained,
    and the title only at the workspace tier (scope.detail)."""
    return scope.detail(
        row["visibility"], f"artifact #{row['id']} revision {revision}", row["title"]
    )


def head_revision(artifact_id: int) -> int:
    """The newest revision number, or 1 for a document with no rows yet."""
    row = db.query_one(
        "SELECT MAX(revision) AS n FROM document_revisions WHERE artifact_id = ?", (artifact_id,)
    )
    return int(row["n"]) if row and row["n"] else 1


def _check_base(artifact_id: int, base_revision: int, head: int) -> None:
    # base 0 states no base: a proposal filed before agent edits pinned one
    if base_revision and base_revision != head:
        raise StaleRevision(
            f"document #{artifact_id} changed after revision {base_revision}, and revision"
            f" {head} is newer. Read revision {head}, then make the change again."
        )


def _editable_row(artifact_id: int, actor: str) -> dict:
    """The document a person may change, held for the caller's transaction.

    FOR UPDATE for the reason _document_row gives. The tier check runs before
    the kind check, so a hidden artifact of any kind reads as absent, and any
    reader of a document may change it (scope.assert_editable).
    """
    row = db.query_one("SELECT * FROM artifacts WHERE id = ? FOR UPDATE", (artifact_id,))
    if not row:
        raise scope.missing("artifacts", artifact_id)
    scope.assert_editable("artifacts", row, actor)
    if row["kind"] != "document":
        raise ValueError(
            f"artifact #{artifact_id} is not a document. Only a document can be changed."
        )
    return row


def _readable_document(artifact_id: int, viewer: scope.Viewer) -> dict:
    frag, vp = scope.visible_filter(viewer, "artifacts")
    row = db.query_one(
        f"SELECT * FROM artifacts WHERE id = ? AND {frag}",  # noqa: S608 — scope.visible_filter emits only bound marks
        (artifact_id, *vp),
    )
    if not row:
        raise scope.missing("artifacts", artifact_id)
    if row["kind"] != "document":
        raise ValueError(
            f"artifact #{artifact_id} is not a document. Only a document has revisions."
        )
    return row


def save_document(artifact_id: int, content: str, base_revision: int, *, actor: str) -> dict:
    """A person's save of the whole body, as revision head + 1.

    The same text as the head writes nothing and succeeds, and that runs
    before the base check: a double-selected Save, a retry after a lost
    response and a stale tab holding the current text are all safe.
    """
    _check_content(content)
    with db.transaction():
        row = _editable_row(artifact_id, actor)
        head = _head(row)
        if content == head["body"]:
            return {"id": artifact_id, "revision": int(head["revision"]), "unchanged": True}
        _check_base(artifact_id, base_revision, int(head["revision"]))
        revision = _publish_revision(
            row, content, head=int(head["revision"]), actor=actor, origin="human"
        )
        db.log_activity(actor, "edit_document", _ledger_detail(row, revision))
        return {"id": artifact_id, "revision": revision, "unchanged": False}


def restore_revision(artifact_id: int, revision: int, base_revision: int, *, actor: str) -> dict:
    """Copy revision n as the new head. The bad revision stays in history, so
    restoring the previous head undoes a restore."""
    with db.transaction():
        row = _editable_row(artifact_id, actor)
        head = _head(row)
        source = db.query_one(
            "SELECT body FROM document_revisions WHERE artifact_id = ? AND revision = ?",
            (artifact_id, revision),
        )
        if not source:
            raise db.NotFound(f"no revision {revision} of document #{artifact_id}")
        if source["body"] == head["body"]:
            return {
                "id": artifact_id,
                "revision": int(head["revision"]),
                "restored_from": revision,
                "unchanged": True,
            }
        _check_base(artifact_id, base_revision, int(head["revision"]))
        new = _publish_revision(
            row,
            source["body"],
            head=int(head["revision"]),
            actor=actor,
            origin="human",
            restored_from=revision,
        )
        db.log_activity(actor, "restore_document", _ledger_detail(row, new))
        return {"id": artifact_id, "revision": new, "restored_from": revision, "unchanged": False}


def history(artifact_id: int, viewer: scope.Viewer) -> dict:
    """The newest revisions of a document the viewer can read, without bodies.

    Each row names its author: authorship of shared text is content, and a
    reader who finds a wrong section needs to know whom to ask. It is keyed on
    one document only. No route or query here lists revisions across
    documents or by author (docs/intent/document-revisions.md, D7).
    """
    _readable_document(artifact_id, viewer)
    # ponytail: the newest 100, an older one still opens by number. Add a
    # before= cursor when a document passes 100 revisions.
    rows = db.query(
        "SELECT revision, author, origin, change_id, restored_from, created_at,"
        " octet_length(body) AS size FROM document_revisions WHERE artifact_id = ?"
        " ORDER BY revision DESC LIMIT ?",
        (artifact_id, HISTORY_LIMIT),
    )
    return {"id": artifact_id, "head": int(rows[0]["revision"]) if rows else 1, "revisions": rows}


def read_revision(artifact_id: int, revision: int, viewer: scope.Viewer) -> dict:
    """One revision's body and its diff against the revision before it."""
    _readable_document(artifact_id, viewer)
    rows = {
        int(r["revision"]): r
        for r in db.query(
            "SELECT revision, body, author, origin, change_id, restored_from, created_at"
            " FROM document_revisions WHERE artifact_id = ? AND revision IN (?, ?)",
            (artifact_id, revision, revision - 1),
        )
    }
    current = rows.get(revision)
    if not current:
        raise db.NotFound(f"no revision {revision} of document #{artifact_id}")
    previous = rows.get(revision - 1)
    meta = {key: value for key, value in current.items() if key != "body"}
    return {
        **meta,
        "id": artifact_id,
        "markdown": current["body"],
        "diff": unified(
            previous["body"], current["body"], f"revision {revision - 1}", f"revision {revision}"
        )
        if previous
        else "",
    }


def proposal_diff(artifact_id: int, payload: dict) -> dict:
    """What an edit proposal changes: its base revision with the one
    replacement applied, as a unified diff, and whether the head has moved
    past that base (approval then refuses it, StaleRevision).

    A proposal filed before bases were stamped states none and diffs against
    the head. A document with no revision rows yet has no base text to show,
    so the replacement stays as fields.
    """
    head = head_revision(artifact_id)
    base = int(payload.get("base_revision") or 0) or head
    old, new = str(payload.get("old") or ""), str(payload.get("new") or "")
    source = db.query_one(
        "SELECT body FROM document_revisions WHERE artifact_id = ? AND revision = ?",
        (artifact_id, base),
    )
    if source is None:
        return {
            "current": {"old": old},
            "proposed": {"new": new},
            "unified": "",
            "base_revision": base,
            "head_revision": head,
        }
    body = source["body"]
    after = body.replace(old, new, 1) if old else body
    return {
        "current": {},
        "proposed": {},
        "unified": unified(body, after, f"revision {base}", "proposed"),
        "base_revision": base,
        "head_revision": head,
    }


def unified(before: str, after: str, before_label: str, after_label: str) -> str:
    """A unified diff, computed per request and never stored."""
    # ponytail: difflib is quadratic in the worst case and the 512 KB cap
    # bounds it. Cap the compared lines if a slow diff shows in traces.
    return "\n".join(
        difflib.unified_diff(
            before.splitlines(), after.splitlines(), before_label, after_label, n=3, lineterm=""
        )
    )


def edit_refusal(artifact_id: int, payload: dict) -> str:
    """Why a document_edit proposal can never apply, checked when it is filed.

    Filed, a quote that matches nothing failed at approval and came back to
    the queue after every try. A revision row never changes, so this read
    takes no lock and its answer holds until the approval. A document with no
    rows yet leaves the quote to the apply.
    """
    row = db.query_one("SELECT visibility, kind FROM artifacts WHERE id = ?", (artifact_id,))
    # the _document_row sentence: an agent writes workspace documents only, and
    # a proposal on any other row would settle as "target vanished" while the
    # document is on the reviewer's screen
    if not row or row["visibility"] != scope.WORKSPACE:
        return scope.missing_text("artifacts", artifact_id)
    if row["kind"] != "document":
        return f"artifact #{artifact_id} is not a document, so it cannot be changed."
    old = str(payload.get("old") or "")
    base = int(payload.get("base_revision") or 0)
    source = db.query_one(
        "SELECT body FROM document_revisions WHERE artifact_id = ? AND revision = ?",
        (artifact_id, base),
    )
    if not old or source is None:
        return ""
    found = source["body"].count(old)
    if found == 0:
        return f"that text is not in document #{artifact_id}. Read the document, then quote it exactly."
    if found > 1:
        return (
            f"that text is in document #{artifact_id} {found} times."
            " Quote more of the surrounding text so it matches one place."
        )
    return ""


def edit_document(
    artifact_id: int,
    old: str,
    new: str,
    base_revision: int = 0,
    change_id: int = 0,
    *,
    actor: str = "system",
    origin: str = "human",
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
        # the head ROW, never the file: a changed file is republished by this
        # write, and handoff.read_artifact refuses the mismatch until then
        head = _head(row)
        # before the quote: a quote that still matches after a person's save
        # would land on text the agent never read (StaleRevision settles the
        # proposal once, uncounted)
        _check_base(artifact_id, base_revision, int(head["revision"]))
        body = head["body"]
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
        revision = _publish_revision(
            row,
            updated,
            head=int(head["revision"]),
            actor=actor,
            origin=origin,
            change_id=change_id,
        )
        db.log_activity(actor, "edit_document", _ledger_detail(row, revision))
        return {
            "id": artifact_id,
            "artifact_id": artifact_id,
            "title": row["title"],
            "revision": revision,
        }
