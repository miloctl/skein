"""People edit, restore and read the history of a document
(docs/intent/document-revisions.md). Every save is a revision, a stale
base is a 409 that keeps the newer text, and history reaches readers of
that document only."""

import pytest
from conftest import _strong

from app import db
from app.services import documents, handoff, scope, users


def _doc(body: str = "alpha beta", actor: str = "scribe") -> int:
    users.ensure_user(actor, kind="agent")
    return documents.create_document("Runbook", body, actor=actor)["artifact_id"]


def _save(client, doc: int, content: str, base: int, who: str = "raj"):
    return client.put(
        f"/api/documents/{doc}",
        json={"content": content, "base_revision": base},
        headers=_strong(client, who),
    )


def _head_body(doc: int) -> str:
    return handoff.read_artifact(doc, scope.NOBODY)["markdown"]


def test_a_save_from_an_old_revision_is_409_and_keeps_the_newer_text(client, fresh_db):
    doc = _doc()
    first = _save(client, doc, "raj's fix", 1)
    assert first.status_code == 200, first.text
    assert first.json() == {"id": doc, "revision": 2, "unchanged": False}
    late = _save(client, doc, "ana's older draft", 1, who="ana")
    assert late.status_code == 409
    assert "revision 2 is newer" in late.json()["detail"]
    assert "older draft" not in late.text
    assert _head_body(doc) == "raj's fix"


def test_restore_writes_a_new_revision_and_keeps_the_bad_one(client, fresh_db):
    doc = _doc("good text")
    _save(client, doc, "bad text", 1)
    restored = client.post(
        f"/api/documents/{doc}/revisions/1/restore",
        json={"base_revision": 2},
        headers=_strong(client, "ana"),
    )
    assert restored.status_code == 200, restored.text
    assert restored.json() == {"id": doc, "revision": 3, "restored_from": 1, "unchanged": False}
    assert _head_body(doc) == "good text"
    history = client.get(f"/api/documents/{doc}/revisions", headers=_strong(client, "ana")).json()
    assert [(r["revision"], r["restored_from"]) for r in history["revisions"]] == [
        (3, 1),
        (2, None),
        (1, None),
    ]
    bad = client.get(f"/api/documents/{doc}/revisions/2", headers=_strong(client, "ana")).json()
    assert bad["markdown"] == "bad text"
    assert "-good text" in bad["diff"] and "+bad text" in bad["diff"]


def test_a_save_identical_to_the_head_writes_nothing(client, fresh_db):
    """A double-selected Save and a retry after a lost response both land
    here. The no-op rule runs before the base check, so a stale base with
    the same text still succeeds."""
    doc = _doc("same text")
    for base in (1, 1):
        again = _save(client, doc, "same text", base)
        assert again.json() == {"id": doc, "revision": 1, "unchanged": True}
    assert (
        db.query_one("SELECT COUNT(*) AS n FROM document_revisions WHERE artifact_id = ?", (doc,))[
            "n"
        ]
        == 1
    )


def test_history_of_an_unreadable_artifact_reads_as_absent(client, fresh_db):
    """Any other answer tells a caller walking ids which are somebody's
    private artifacts."""
    from app.services import engagements

    users.ensure_user("mira")
    eng = engagements.create_engagement("Quiet", actor="mira", visibility="private")
    private = handoff.generate_handoff(eng["id"], actor="mira", viewer=scope.Viewer("mira", True))[
        "artifact_id"
    ]
    raj = _strong(client, "raj")
    shared = _doc()
    assert client.get(f"/api/documents/{shared}/revisions", headers=raj).status_code == 200
    hidden = client.get(f"/api/documents/{private}/revisions", headers=raj)
    absent = client.get("/api/documents/999999/revisions", headers=raj)
    assert hidden.status_code == absent.status_code == 404
    assert hidden.text.replace(str(private), "N") == absent.text.replace("999999", "N")


def test_a_generated_report_cannot_be_edited(client, fresh_db):
    """A digest's generator rewrites it on its next run, so a hand edit would
    be lost without notice."""
    from app.services import digest

    digest.publish_digest(actor="scheduler", force=True)
    report = db.query_one("SELECT id FROM artifacts WHERE kind = 'digest' ORDER BY id DESC")["id"]
    refused = _save(client, report, "my notes", 1)
    assert refused.status_code == 400
    assert "not a document" in refused.json()["detail"]


def test_the_ledger_carries_ids_and_never_the_body(client, fresh_db):
    doc = _doc("opening line")
    _save(client, doc, "SECRET-BODY-TEXT here", 1)
    client.post(
        f"/api/documents/{doc}/revisions/1/restore",
        json={"base_revision": 2},
        headers=_strong(client, "raj"),
    )
    rows = db.query(
        "SELECT action, detail FROM activity WHERE action IN ('edit_document', 'restore_document')"
    )
    assert {r["action"] for r in rows} == {"edit_document", "restore_document"}
    assert all("SECRET" not in r["detail"] and "opening" not in r["detail"] for r in rows)
    assert all(f"artifact #{doc} revision" in r["detail"] for r in rows)


def test_history_names_editors_to_readers_only(client, fresh_db):
    """Authorship of shared text is content: a reader who finds a wrong
    section needs to know whom to ask."""
    from app.main import create_app
    from app.services import review

    doc = _doc()
    _save(client, doc, "alpha beta gamma", 1)
    proposal = review.propose_change(
        "document_edit", "update", {"old": "gamma", "new": "delta"}, entity_id=doc, actor="scribe"
    )
    review.approve_change(
        proposal["id"], actor="mira", policy_registry=create_app().state.skein_registry
    )
    history = client.get(f"/api/documents/{doc}/revisions", headers=_strong(client, "ana")).json()
    assert history["head"] == 3
    assert [(r["author"], r["origin"]) for r in history["revisions"]] == [
        ("scribe", "agent_verified"),
        ("raj", "human"),
        ("scribe", "human"),
    ]


def test_a_document_links_a_reference_after_an_apostrophe(client, fresh_db):
    """A document has no generated frame, so an apostrophe is a word. Parsed
    as a report, the span between two apostrophes read as a quoted title and
    its reference was blanked."""
    from app.services import work

    task = work.create_task(title="migrate billing", actor="raj")["id"]
    doc = _doc(f"Raj's task #{task}, don't skip it")
    threads = handoff.read_artifact(doc, scope.NOBODY)["threads"]
    assert {"entity": "task", "id": task} in [
        {"entity": t["entity"], "id": t["id"]} for t in threads
    ]


def _agent_edit(doc: int, old: str, new: str, agent: str = "scribe") -> dict:
    """File an edit through the real agent tool, review on, so the payload
    carries what the tool stamps."""
    import json

    from app.agents.identity import reset_agent_identity, set_agent_identity
    from app.tools import files

    fn = getattr(files.edit_document, "_tool_func", None) or files.edit_document.__wrapped__
    token = set_agent_identity(agent)
    try:
        return json.loads(fn(doc, old, new))
    finally:
        reset_agent_identity(token)


def _approve(proposal_id: int):
    from app.main import create_app
    from app.services import review

    return review.approve_change(
        proposal_id,
        actor="mira",
        strong=True,
        policy_registry=create_app().state.skein_registry,
    )


def _pending_edit(doc: int) -> dict:
    return db.query_one(
        "SELECT * FROM pending_changes WHERE entity = 'document_edit' AND entity_id = ?"
        " ORDER BY id DESC",
        (doc,),
    )


def test_a_proposal_filed_before_a_human_save_is_refused_and_not_counted(
    client, fresh_db, monkeypatch
):
    """Filed against revision 1, applied after a person saved revision 2: the
    quote still matched, so the agent's change landed on top of text it never
    read. Now it settles as rejected once, and the agent's record does not
    count it."""
    from app import config

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    doc = _doc("alpha beta")
    _agent_edit(doc, "beta", "gamma")
    proposal = _pending_edit(doc)
    assert proposal["status"] == "pending"
    _save(client, doc, "alpha beta delta", 1)
    # the reviewer is told why, and the proposal is settled, not requeued
    with pytest.raises(ValueError, match="changed after revision 1"):
        _approve(proposal["id"])
    settled = db.query_one(
        "SELECT status, reviewed_strong, review_note FROM pending_changes WHERE id = ?",
        (proposal["id"],),
    )
    assert settled["status"] == "rejected"
    assert settled["reviewed_strong"] == 0
    assert _head_body(doc) == "alpha beta delta"


def test_a_quote_missing_from_the_document_is_refused_when_filed(fresh_db, monkeypatch):
    """Filed, it failed at approval and came back after every try."""
    from app import config

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    doc = _doc("alpha beta")
    out = _agent_edit(doc, "zeta", "eta")
    assert "not in document" in out["error"]
    assert _pending_edit(doc) is None


def test_an_approved_edit_names_its_proposal_on_the_revision(fresh_db, monkeypatch):
    import json

    from app import config
    from app.tools import files

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    doc = _doc("alpha beta")
    _agent_edit(doc, "beta", "gamma")
    proposal = _pending_edit(doc)
    _approve(proposal["id"])
    revision = db.query_one(
        "SELECT revision, origin, change_id FROM document_revisions"
        " WHERE artifact_id = ? ORDER BY revision DESC",
        (doc,),
    )
    assert (revision["revision"], revision["origin"], revision["change_id"]) == (
        2,
        "agent_verified",
        proposal["id"],
    )
    read = getattr(files.read_artifact, "_tool_func", None) or files.read_artifact.__wrapped__
    assert json.loads(read(doc))["revision"] == 2
