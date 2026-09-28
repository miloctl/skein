"""Notes: keyword filter, patch bounds, edit, delete, and deindex."""

import pytest


def test_notes_keyword_filter_and_patch_bounds(client):
    n = client.post("/api/notes", json={"topic": "infra", "content": "postgres vacuum tips"}).json()
    client.post("/api/notes", json={"topic": "team", "content": "friday demo schedule"})

    hits = client.get("/api/notes", params={"q": "vacuum"}).json()
    assert [h["id"] for h in hits] == [n["id"]]
    assert len(client.get("/api/notes").json()) == 2

    over = client.patch(f"/api/notes/{n['id']}", json={"content": "x" * 20_001})
    assert over.status_code == 422


def test_note_edit_delete_and_deindex(client):
    n = client.post("/api/notes", json={"topic": "conv", "content": "old text zebra"}).json()
    client.patch(f"/api/notes/{n['id']}", json={"content": "new text giraffe"})
    assert client.get("/api/search", params={"q": "giraffe"}).json()
    client.delete(f"/api/notes/{n['id']}")
    assert client.get("/api/search", params={"q": "giraffe"}).json() == []
    assert client.delete(f"/api/notes/{n['id']}").status_code == 404


def test_a_delete_that_cannot_deindex_keeps_the_note(client, fresh_db, monkeypatch):
    """All or nothing. Split across transactions, the row delete commits and
    the index delete does not, so the note is gone from `notes` while its FULL
    body stays queryable through /api/search — unreachable by any delete, and
    unbounded, where the ledger snapshot delete_note keeps is capped at 300
    chars. Concurrency produces the same split; this reaches it deterministically."""
    from app.services import collab, search

    n = client.post("/api/notes", json={"topic": "conv", "content": "ghost zebra"}).json()
    assert client.get("/api/search", params={"q": "zebra"}).json()

    def boom(*_a, **_k):
        raise RuntimeError("index backend down")

    monkeypatch.setattr(search, "deindex_record", boom)
    with pytest.raises(RuntimeError):
        collab.delete_note(n["id"], actor="tester")
    assert fresh_db.query_one("SELECT * FROM notes WHERE id = ?", (n["id"],)) is not None
    assert client.get("/api/search", params={"q": "zebra"}).json()


def test_notes_keyword_filter_ignores_case(client):
    """A person typing a keyword must not have to match the author's capitals.

    PostgreSQL LIKE is case-sensitive where SQLite's was not for ASCII, so
    this silently returned nothing after the engine change — and an empty
    result reads as "no such note", never as "wrong case"."""
    client.post("/api/notes", json={"topic": "Infra", "content": "PostgreSQL vacuum tips"})
    for keyword in ("PostgreSQL", "postgresql", "POSTGRESQL", "postgres", "infra", "INFRA"):
        found = client.get(f"/api/notes?q={keyword}").json()
        assert [row["topic"] for row in found] == ["Infra"], keyword


def test_an_agent_note_carries_the_requesters_own_name(fresh_db, monkeypatch):
    """A note filed in a teammate's name was indexed as theirs, with no
    delete for them."""
    import json

    from conftest import _turn

    from app import config
    from app.agents import identity
    from app.services import users
    from app.tools.collab import save_note

    monkeypatch.setattr(config, "AGENT_REVIEW", False)
    for name in ("ava", "bob"):
        users.ensure_user(name)
    token = identity.set_agent_identity("scout")
    try:
        with _turn("ava", strong=True):
            refused = json.loads(save_note(topic="runbook", content="step 1", author="bob"))
            assert "your own name" in refused["error"]
            own = json.loads(save_note(topic="runbook", content="step 1", author="ava"))
            assert "error" not in own
    finally:
        identity.reset_agent_identity(token)
    assert fresh_db.query("SELECT author FROM notes") == [{"author": "ava"}]


def test_the_agent_reads_its_requesters_private_notes_and_no_one_elses(fresh_db):
    """A strong identity's capture starts private, so an agent that read notes
    as nobody found none of the notes a person dumped from their own chat."""
    import json

    from conftest import _turn

    from app.agents import identity
    from app.services import capture, users
    from app.tools.collab import search_notes

    for name in ("ava", "bob"):
        users.ensure_user(name)
    capture.capture("ZZDUMP ava vendor call", actor="ava", strong_auth=True, visibility="private")
    capture.capture("ZZDUMP bob keeps this", actor="bob", strong_auth=True, visibility="private")
    capture.capture("ZZDUMP team runbook", actor="bob", visibility="workspace")

    def seen() -> list[str]:
        return sorted(n["content"] for n in json.loads(search_notes("ZZDUMP")))

    team = ["ZZDUMP team runbook"]
    token = identity.set_agent_identity("scout")
    try:
        with _turn("ava"):
            assert seen() == ["ZZDUMP ava vendor call", *team]
        with _turn("ava", strong=False):  # a typed name reads no private row
            assert seen() == team
        with _turn("ava", shared_chat=True):  # every member reads the reply
            assert seen() == team
        assert seen() == team  # an unattended run has no requester
    finally:
        identity.reset_agent_identity(token)


def test_the_notes_list_pages_past_the_newest_25(client, fresh_db):
    """Only the newest 25 notes were listable, so an older dump was
    unreachable unless the reader remembered its exact words."""
    from app.services import collab

    ids = [collab.save_note(f"n{i}", "c", author="tester", actor="tester")["id"] for i in range(30)]
    newest_first = ids[::-1]
    first = client.get("/api/notes").json()
    assert [n["id"] for n in first] == newest_first[:25]
    older = client.get("/api/notes", params={"before": first[-1]["id"]}).json()
    assert [n["id"] for n in older] == newest_first[25:]
    assert client.get("/api/notes", params={"limit": 101}).status_code == 422


def test_a_workplace_rule_that_denies_private_rows_holds_for_note_search(fresh_db):
    """The wrapper's policy check sees the tool, not the notes, so a rule that
    denies agents a private row by classification never ran on this read."""
    import json

    from conftest import _turn

    from app.agents import identity
    from app.extensions import PolicyContribution, PolicyDecision, PolicyEffect, SkeinModule
    from app.extensions.policy import reset_policy_engine, set_policy_engine
    from app.extensions.registry import ExtensionRegistry
    from app.services import capture, users
    from app.tools.collab import search_notes

    users.ensure_user("ava")
    capture.capture("ZZRULE ava private", actor="ava", strong_auth=True, visibility="private")
    capture.capture("ZZRULE team runbook", actor="ava", visibility="workspace")

    def deny_private(request):
        if request.resource.classification == "private":
            return PolicyDecision(PolicyEffect.DENY, ("private records are protected",))
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.7.0",
        policies=(PolicyContribution("acme.workplace.private-records", deny_private),),
    )
    engine = set_policy_engine(ExtensionRegistry.build((module,)).policy_engine)
    token = identity.set_agent_identity("scout")
    try:
        with _turn("ava"):
            seen = [n["content"] for n in json.loads(search_notes("ZZRULE"))]
    finally:
        identity.reset_agent_identity(token)
        reset_policy_engine(engine)
    assert seen == ["ZZRULE team runbook"]


def test_an_agent_refuses_to_change_a_private_note_before_filing_anything(fresh_db):
    """The change applies as the agent, which scope.assert_editable refuses on
    a private row, so a filed proposal auto-rejected on approval as "target no
    longer exists" and the note never changed. delete_note read the note as
    nobody first, so its refusal said the note did not exist."""
    import json

    from conftest import _turn

    from app.agents import identity
    from app.services import capture, users
    from app.tools.collab import delete_note, edit_note

    users.ensure_user("ava")
    note = capture.capture(
        "note: the vendor name has a typo", actor="ava", strong_auth=True, visibility="private"
    )
    assert note["kind"] == "note"
    token = identity.set_agent_identity("scout")
    try:
        with _turn("ava"):
            edited = json.loads(edit_note(note["id"], content="fixed"))
            deleted = json.loads(delete_note(note["id"]))
    finally:
        identity.reset_agent_identity(token)
    for result in (edited, deleted):
        assert result == {"error": "An agent cannot change a private record. Change it yourself."}
    assert fresh_db.query("SELECT id FROM pending_changes") == []
    assert fresh_db.query_one("SELECT content FROM notes WHERE id = ?", (note["id"],)) == {
        "content": "the vendor name has a typo"
    }


def test_the_gate_refuses_an_agent_change_to_any_private_record(fresh_db):
    """The refusal lives in the gate, so it covers every entity an agent can
    update, not notes alone."""
    import json

    from conftest import _turn

    from app.agents import identity
    from app.services import capture, memory, schedule, users
    from app.tools.memory import forget_memory
    from app.tools.schedule import cancel_event
    from app.tools.work import update_task

    users.ensure_user("ava")
    task = capture.capture(
        "todo: call the vendor", actor="ava", strong_auth=True, visibility="private"
    )
    assert task["kind"] == "task"
    event = schedule.schedule_event(
        "dentist", "2026-12-01T09:00:00+00:00", actor="ava", visibility="private"
    )
    remembered = memory.remember("prefers mornings", user="ava", actor="ava", visibility="private")
    token = identity.set_agent_identity("scout")
    try:
        with _turn("ava"):
            results = [
                json.loads(update_task(task["id"], priority="high")),
                # these two read the row as nobody first, so the agent was
                # told the event or memory did not exist
                json.loads(cancel_event(event["id"])),
                json.loads(forget_memory(remembered["id"])),
            ]
    finally:
        identity.reset_agent_identity(token)
    refused = {"error": "An agent cannot change a private record. Change it yourself."}
    assert results == [refused, refused, refused]
    assert fresh_db.query("SELECT id FROM pending_changes") == []


def test_a_rule_that_denies_agents_crew_rows_holds_for_a_crew_note_delete(fresh_db):
    """Policy context read the row through the agent's own filter, and an
    agent is in no crew, so a crew note reached every rule unclassified."""
    import json

    from conftest import _turn

    from app.agents import identity
    from app.extensions import PolicyContribution, PolicyDecision, PolicyEffect, SkeinModule
    from app.extensions.policy import reset_policy_engine, set_policy_engine
    from app.extensions.registry import ExtensionRegistry
    from app.services import collab, crews, users
    from app.tools.collab import delete_note

    for name in ("ava", "bob"):
        users.ensure_user(name)
    users.ensure_user("scout", kind="agent")
    crew = crews.create_crew("Alpha", actor="ava")["id"]
    crews.add_member(crew, "bob", actor="ava")
    note = collab.save_note(
        "crew", "crew words", author="bob", actor="bob", visibility="crew", crew_id=crew
    )

    def deny_agent_crew(request):
        if request.origin == "agent" and request.resource.classification == "crew":
            return PolicyDecision(PolicyEffect.DENY, ("crew records are protected",))
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.7.0",
        policies=(PolicyContribution("acme.workplace.crew-records", deny_agent_crew),),
    )
    engine = set_policy_engine(ExtensionRegistry.build((module,)).policy_engine)
    token = identity.set_agent_identity("scout")
    try:
        with _turn("ava"):
            result = json.loads(delete_note(note["id"]))
    finally:
        identity.reset_agent_identity(token)
        reset_policy_engine(engine)
    assert "error" in result, result
    assert fresh_db.query("SELECT id FROM pending_changes") == []
    assert fresh_db.query_one("SELECT id FROM notes WHERE id = ?", (note["id"],))


def test_a_write_after_a_private_read_is_the_requesters_to_approve(fresh_db, monkeypatch):
    """search_notes hands the model private text beside notes any teammate can
    write, so one injected line could copy the private text into a team note.
    After such a read, a write waits for the requester, or it does not happen."""
    import json

    from conftest import _turn

    from app import config
    from app.agents import identity
    from app.services import capture, delegation, users
    from app.tools.collab import save_note, search_notes

    users.ensure_user("ava")
    capture.capture(
        "note: ZZTAINT ava private", actor="ava", strong_auth=True, visibility="private"
    )
    capture.capture("note: ZZCLEAN team runbook", actor="ava", visibility="workspace")
    delegation.set_authority("scout", "note", "autonomous", actor="tester")
    monkeypatch.setattr(config, "AGENT_REVIEW", False)
    token = identity.set_agent_identity("scout")
    try:
        with _turn("ava"):  # a team-only read leaves autonomy alone
            search_notes("ZZCLEAN")
            assert "id" in json.loads(save_note("clean", "from the runbook", author="ava"))
        with _turn("ava"):
            search_notes("ZZTAINT")
            queued = json.loads(save_note("digest", "ZZTAINT ava private", author="ava"))
        monkeypatch.setattr(config, "REVIEW_SEPARATION", True)
        with _turn("ava"):
            search_notes("ZZTAINT")
            refused = json.loads(save_note("digest", "ZZTAINT ava private", author="ava"))
    finally:
        identity.reset_agent_identity(token)
    assert queued["status"] == "pending"
    pending = fresh_db.query("SELECT review_visibility, review_owner FROM pending_changes")
    assert pending == [{"review_visibility": "private", "review_owner": "ava"}]
    assert refused["error"].startswith("This turn read notes that only you or your crew")
    topics = [r["topic"] for r in fresh_db.query("SELECT topic FROM notes ORDER BY id")]
    assert "digest" not in topics
