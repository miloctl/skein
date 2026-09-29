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
    here. The no-op rule runs before the base check, so the retry's stale
    base with the text that is now the head still succeeds."""
    doc = _doc("first text")
    assert _save(client, doc, "same text", 1).json()["revision"] == 2
    again = _save(client, doc, "same text", 1)
    assert again.status_code == 200, again.text
    assert again.json() == {"id": doc, "revision": 2, "unchanged": True}
    assert _count(doc) == 2


def _count(doc: int) -> int:
    return db.query_one(
        "SELECT COUNT(*) AS n FROM document_revisions WHERE artifact_id = ?", (doc,)
    )["n"]


def test_a_restore_from_a_stale_base_is_409_and_writes_nothing(client, fresh_db):
    doc = _doc("good text")
    _save(client, doc, "bad text", 1)
    _save(client, doc, "worse text", 2)
    stale = client.post(
        f"/api/documents/{doc}/revisions/1/restore",
        json={"base_revision": 2},
        headers=_strong(client, "ana"),
    )
    assert stale.status_code == 409
    assert "revision 3 is newer" in stale.json()["detail"]
    assert _head_body(doc) == "worse text"


def test_a_restore_of_the_current_text_writes_nothing(client, fresh_db):
    doc = _doc("good text")
    _save(client, doc, "bad text", 1)
    _save(client, doc, "good text", 2)
    same = client.post(
        f"/api/documents/{doc}/revisions/1/restore",
        json={"base_revision": 1},
        headers=_strong(client, "ana"),
    )
    assert same.json() == {"id": doc, "revision": 3, "restored_from": 1, "unchanged": True}
    assert _count(doc) == 3


def test_a_base_newer_than_the_head_is_refused_with_a_true_sentence(client, fresh_db):
    doc = _doc()
    ahead = _save(client, doc, "text", 5)
    assert ahead.status_code == 409
    assert ahead.json()["detail"] == (
        f"document #{doc} has no revision 5. Revision 1 is the newest."
        " Read revision 1, then make the change again."
    )


def test_a_read_racing_a_save_gets_one_revision_whole(client, fresh_db):
    """A save replaces the file and deletes the old one. A reader that read
    the row before the save committed opened a deleted file (a 500), or read
    the new head's number beside the old text, and the editor's next save
    then wrote over a revision its person never saw."""
    from pathlib import Path

    doc = _doc("alpha")
    _save(client, doc, "beta", 1)
    Path(db.query_one("SELECT path FROM artifacts WHERE id = ?", (doc,))["path"]).unlink()
    read = handoff.read_artifact(doc, scope.NOBODY)
    assert (read["markdown"], read["revision"]) == ("beta", 2)


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


def _create(client, who: str, **body):
    body.setdefault("title", "Runbook")
    body.setdefault("content", "first draft")
    return client.post("/api/documents", json=body, headers=_strong(client, who))


def test_a_person_creates_a_private_document_only_they_can_read(client, fresh_db):
    """Another person's read, history and save answer exactly as an absent id
    does: any other answer tells a caller which ids are somebody's drafts."""
    made = _create(client, "mira", visibility="private")
    assert made.status_code == 200, made.text
    doc = made.json()["id"]
    assert made.json()["revision"] == 1
    mira = _strong(client, "mira")
    assert client.get(f"/api/artifacts/{doc}", headers=mira).json()["markdown"] == "first draft"
    raj = _strong(client, "raj")
    for method, path, body in (
        ("get", f"/api/artifacts/{doc}", None),
        ("get", f"/api/documents/{doc}/revisions", None),
        ("put", f"/api/documents/{doc}", {"content": "x", "base_revision": 1}),
    ):
        hidden = client.request(method, path, json=body, headers=raj)
        absent = client.request(method, path.replace(str(doc), "999999"), json=body, headers=raj)
        assert hidden.status_code == absent.status_code == 404
        assert hidden.text.replace(str(doc), "N") == absent.text.replace("999999", "N")


def test_a_private_document_title_never_enters_the_ledger(client, fresh_db):
    doc = _create(client, "mira", title="SECRET-TITLE", visibility="private").json()["id"]
    _save(client, doc, "second draft", 1, who="mira")
    details = [
        r["detail"]
        for r in db.query(
            "SELECT detail FROM activity WHERE action IN ('create_document', 'edit_document')"
        )
    ]
    assert len(details) == 2
    assert all("SECRET" not in d and f"artifact #{doc} revision" in d for d in details)


def _crew(owner: str = "ava", member: str = "mira") -> int:
    from app.services import crews

    for name in (owner, member, "bo"):
        users.ensure_user(name)
    cid = crews.create_crew("Platform", actor=owner)["id"]
    crews.add_member(cid, member, actor=owner)
    return cid


def test_an_agent_proposal_on_a_crew_document_is_refused_when_filed(client, fresh_db, monkeypatch):
    """Filed, it would settle as "target vanished" at approval while the
    document is on the reviewer's screen: agents write workspace documents."""
    from app import config

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    cid = _crew()
    doc = _create(client, "ava", visibility="crew", crew_id=cid).json()["id"]
    out = _agent_edit(doc, "first", "second")
    assert out["error"] == scope.missing_text("artifacts", doc)
    assert _pending_edit(doc) is None


def test_a_crew_documents_history_reaches_its_members_only(client, fresh_db):
    cid = _crew()
    doc = _create(client, "ava", visibility="crew", crew_id=cid).json()["id"]
    _save(client, doc, "second draft", 1, who="ava")
    member = client.get(f"/api/documents/{doc}/revisions", headers=_strong(client, "mira"))
    assert [r["author"] for r in member.json()["revisions"]] == ["ava", "ava"]
    bo = _strong(client, "bo")
    outsider = client.get(f"/api/documents/{doc}/revisions", headers=bo)
    absent = client.get("/api/documents/999999/revisions", headers=bo)
    assert outsider.status_code == 404
    assert "ava" not in outsider.text
    assert outsider.text.replace(str(doc), "N") == absent.text.replace("999999", "N")


def test_an_agent_edit_that_empties_the_document_is_refused_when_filed(fresh_db, monkeypatch):
    """Filed, the empty body failed at every approval and came back to the
    queue: nothing about the revision it names can change."""
    from app import config

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    doc = _doc("alpha beta")
    assert "a document needs content" in _agent_edit(doc, "alpha beta", " ")["error"]
    assert "changes nothing" in _agent_edit(doc, "beta", "beta")["error"]
    assert _pending_edit(doc) is None


def test_an_agent_edit_that_changes_nothing_writes_no_revision(fresh_db):
    doc = _doc("alpha beta")
    out = _agent_edit(doc, "beta", "beta")
    assert out["revision"] == 1
    assert _count(doc) == 1


def test_a_proposal_without_a_base_shows_its_fields_once_the_quote_is_gone(
    client, fresh_db, monkeypatch
):
    """A proposal filed before bases were stamped diffs against the head. If
    the head no longer holds the quote, the diff was empty and the card
    showed no change at all."""
    import json

    from app import config

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    doc = _doc("alpha beta")
    _agent_edit(doc, "beta", "gamma")
    payload = json.loads(_pending_edit(doc)["payload"])
    del payload["base_revision"]
    _save(client, doc, "alpha delta", 1)
    diff = documents.proposal_diff(doc, payload)
    assert diff["unified"] == ""
    assert (diff["current"], diff["proposed"]) == ({"old": "beta"}, {"new": "gamma"})


def test_a_rule_that_denies_private_documents_holds_in_any_letter_case(fresh_db):
    from fastapi.testclient import TestClient

    from app.extensions import PolicyContribution, PolicyDecision, PolicyEffect, SkeinModule
    from app.main import create_app

    def deny_private(request):
        if request.resource.type == "document" and request.resource.classification == "private":
            return PolicyDecision(PolicyEffect.DENY, ("private documents are off",))
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.7.0",
        policies=(PolicyContribution("acme.workplace.no-private-documents", deny_private),),
    )
    users.ensure_user("mira")
    with TestClient(create_app(modules=(module,)), headers={"X-User": "mira"}) as client:
        made = client.post(
            "/api/documents",
            json={"title": "Draft", "content": "text", "visibility": " PRIVATE"},
        )
    assert made.status_code == 403, made.text
    assert db.query_one("SELECT 1 FROM artifacts WHERE kind = 'document'") is None


def test_a_new_document_without_a_tier_starts_with_the_writers_default(client, fresh_db):
    """Only you for a signed-in person, the roster for a weak name: a weak
    name reads no private row and would lose its own document."""
    strong = _create(client, "mira").json()["id"]
    users.ensure_user("raj")
    weak = client.post(
        "/api/documents", json={"title": "Runbook", "content": "text"}, headers={"X-User": "raj"}
    ).json()["id"]
    tiers = {
        r["id"]: r["visibility"]
        for r in db.query("SELECT id, visibility FROM artifacts WHERE kind = 'document'")
    }
    assert (tiers[strong], tiers[weak]) == ("private", "workspace")


def test_your_data_deletes_a_private_document_with_every_revision(client, fresh_db):
    from pathlib import Path

    mira = _strong(client, "mira")
    doc = _create(client, "mira", visibility="private").json()["id"]
    _save(client, doc, "second draft", 1, who="mira")
    path = db.query_one("SELECT path FROM artifacts WHERE id = ?", (doc,))["path"]
    assert client.delete(f"/api/my-data/artifacts/{doc}", headers=mira).status_code == 200
    assert _count(doc) == 0
    assert not Path(path).exists()
