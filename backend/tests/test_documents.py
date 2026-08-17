"""Documents an agent writes, and the two rules that keep them honest:

an uploaded file is never rewritten, and a document made from a source is
never more visible than the source was.
"""

import io

import pytest

from app import db
from app.services import documents, handoff, scope


def _upload(client, name: str = "notes.md", data: bytes = b"private plans"):
    r = client.post("/api/files", files={"file": (name, io.BytesIO(data), "text/markdown")})
    assert r.status_code == 200
    return r.json()["id"]


def test_creates_a_readable_document(fresh_db):
    out = documents.create_document("Plan", "# Plan\n\nStep one.\n", actor="agent")
    body = handoff.read_artifact(out["artifact_id"], scope.NOBODY)
    assert body["title"] == "Plan"
    assert body["kind"] == "document"
    assert "Step one." in body["markdown"]


def test_a_document_is_a_report_and_an_upload_is_not(client):
    """The reader on Work → Reports sees what agents wrote and never somebody
    else's attached file."""
    upload_id = _upload(client)
    doc_id = documents.create_document("Plan", "# Plan\n", actor="agent")["artifact_id"]
    listed = {a["id"] for a in client.get("/api/artifacts").json()}
    assert doc_id in listed
    assert upload_id not in listed


def test_an_upload_is_never_rewritten(client):
    """The person's own file stays the file they attached. Revising one means
    a new document, so undo costs nothing."""
    upload_id = _upload(client)
    with pytest.raises(PermissionError, match="not written by an agent"):
        documents.edit_document(upload_id, "private", "public", actor="agent")
    row = db.query_one("SELECT path FROM artifacts WHERE id = ?", (upload_id,))
    from pathlib import Path

    assert Path(row["path"]).read_bytes() == b"private plans"


def test_a_private_source_cannot_become_a_shared_document(client):
    """The laundering guard. An agent that can read a private upload can also
    write a document, and a summary of a private file at the workspace tier is
    that file's content, published, with nobody asked."""
    upload_id = _upload(client)
    with pytest.raises(PermissionError, match="not shared with the team"):
        documents.create_document("Summary", "the plans say...", actor="agent", source_id=upload_id)
    assert not db.query("SELECT 1 FROM artifacts WHERE kind = 'document'")


def test_a_shared_source_can_become_a_document(fresh_db):
    source = documents.create_document("Source", "# Source\n", actor="agent")["artifact_id"]
    out = documents.create_document("Derived", "# Derived\n", actor="agent", source_id=source)
    row = db.query_one("SELECT derived_from FROM artifacts WHERE id = ?", (out["artifact_id"],))
    assert row["derived_from"] == source


def test_an_edit_replaces_one_exact_run(fresh_db):
    doc = documents.create_document("Plan", "alpha beta gamma", actor="agent")["artifact_id"]
    documents.edit_document(doc, "beta", "delta", actor="agent")
    assert handoff.read_artifact(doc, scope.NOBODY)["markdown"] == "alpha delta gamma"


def test_an_edit_that_matches_nothing_is_refused(fresh_db):
    doc = documents.create_document("Plan", "alpha beta", actor="agent")["artifact_id"]
    with pytest.raises(ValueError, match="not in document"):
        documents.edit_document(doc, "omega", "delta", actor="agent")


def test_an_ambiguous_edit_is_refused_rather_than_guessed(fresh_db):
    """A model that read half a file must not silently rewrite both halves."""
    doc = documents.create_document("Plan", "beta and beta", actor="agent")["artifact_id"]
    with pytest.raises(ValueError, match="2 times"):
        documents.edit_document(doc, "beta", "delta", actor="agent")
    assert handoff.read_artifact(doc, scope.NOBODY)["markdown"] == "beta and beta"


def test_the_document_is_recorded_in_the_ledger(fresh_db):
    out = documents.create_document("Plan", "# Plan\n", actor="agent")
    row = db.query_one(
        "SELECT * FROM activity WHERE action = 'create_document' ORDER BY id DESC LIMIT 1"
    )
    assert row and f"#{out['artifact_id']}" in row["detail"] and row["actor"] == "agent"


def test_a_document_written_outside_the_root_is_refused(fresh_db):
    """The containment check is about a restored or hand-edited row, not about
    create_document — it is what stops a stored string becoming a read or a
    write of any file the server user can open."""
    doc = documents.create_document("Plan", "alpha", actor="agent")["artifact_id"]
    db.execute("UPDATE artifacts SET path = ? WHERE id = ?", ("/etc/passwd", doc))
    with pytest.raises(db.NotFound):
        documents.edit_document(doc, "root", "pwned", actor="agent")


def test_an_approved_document_proposal_actually_applies(fresh_db):
    """The review applier passes origin="agent_verified" to EVERY registry
    handler. A service that cannot take it is a TypeError at apply, which the
    generic handler turns into a pending reset — so the proposal boomerangs in
    the inbox forever with no path to approval, and the person clicking
    approve is told nothing. Nothing else covers this: the gate coverage test
    only exercises the direct-apply path."""
    from app.main import create_app
    from app.services import review

    out = review.propose_change(
        "document",
        "create",
        {"title": "Plan", "content": "# Plan\n"},
        actor="agent",
    )
    review.approve_change(
        out["id"], actor="mira", policy_registry=create_app().state.skein_registry
    )
    row = db.query_one("SELECT * FROM artifacts WHERE kind = 'document'")
    assert row and row["title"] == "Plan"
    # and the lineage stamp lands: review.py reads result["id"]
    change = db.query_one(
        "SELECT result_id, status FROM pending_changes WHERE id = ?", (out["id"],)
    )
    assert change["status"] == "approved"
    assert change["result_id"] == row["id"]


def test_an_approved_edit_proposal_actually_applies(fresh_db):
    doc = documents.create_document("Plan", "alpha beta", actor="agent")["artifact_id"]
    from app.main import create_app
    from app.services import review

    out = review.propose_change(
        "document_edit",
        "update",
        {"old": "beta", "new": "delta"},
        entity_id=doc,
        actor="agent",
    )
    review.approve_change(
        out["id"], actor="mira", policy_registry=create_app().state.skein_registry
    )
    assert handoff.read_artifact(doc, scope.NOBODY)["markdown"] == "alpha delta"


def test_a_refused_document_write_leaves_a_receipt_not_a_raw_error(client):
    """documents.py is the first registry service raising PermissionError from
    inside the applier. Uncaught by the gate, it escapes as a raw tool error:
    no receipt in the transcript, and the refusal wording never reaches the
    model that has to act on it."""
    import json

    from app.agents import receipts
    from app.tools import files

    upload_id = _upload(client)
    receipts.start()
    fn = getattr(files.create_document, "_tool_func", None) or files.create_document.__wrapped__
    out = json.loads(fn("Summary", "the plans say...", source_id=upload_id))
    assert "not shared with the team" in out["error"]
    assert [r["kind"] for r in receipts.drain()] == ["failed"]


def test_the_tool_answers_a_missing_id_instead_of_raising(fresh_db):
    """A tool that raises kills the agent loop, and a model guessing an id is
    the ordinary case (tests/test_gate_coverage.py)."""
    import json

    from app.tools import files

    # unwrapped the way tests/test_gate_coverage.py unwraps every tool: the
    # strands decorator's attribute name is the SDK's to change
    fn = getattr(files.read_artifact, "_tool_func", None) or files.read_artifact.__wrapped__
    out = json.loads(fn(9999))
    assert "error" in out
