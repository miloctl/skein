"""Identity boundaries, each demonstrated open against a throwaway database
before the fix. The theme is one rule with three holes in it: an identity may act only as
itself, and a surface that writes on someone's behalf must prove which
someone.
"""

import pytest


def test_a_rename_by_someone_else_cannot_touch_the_private_journal(fresh_db):
    """Every keyholder can rename any roster row — the trusted-network model makes
    them all admins over TEAM data. Moving the private half too let anyone
    merge another person's row into their own name and inherit their 1:1 notes
    and fb: journal, the one dataset teammates are promised they cannot read.

    Refused rather than half-completed: rename DELETES the old roster row, so
    a third-party rename that skipped the private half would strand the notes
    with no supported recovery — the author cannot re-run it as themselves."""

    from app import db
    from app.services import private_notes, users

    users.ensure_user("alice")
    users.ensure_user("mallory")
    private_notes.add_note("alice", "bob", "bob is coasting", kind="feedback")

    with pytest.raises(ValueError, match="private 1:1 notes"):
        users.rename_user("alice", "mallory", actor="mallory")

    # refused BEFORE any mutation — no partial write
    assert db.query_one("SELECT 1 FROM users WHERE name = 'alice'") is not None
    assert private_notes.list_notes("mallory", "bob") == []
    assert [n["body"] for n in private_notes.list_notes("alice", "bob")] == ["bob is coasting"]


def test_an_author_with_no_private_notes_is_still_renameable_by_an_admin(fresh_db):
    """The legitimate 'Mira vs mira' cleanup must keep working."""
    from app.services import users

    users.ensure_user("mira")
    out = users.rename_user("mira", "Mira", actor="ops")
    assert out["new"] == "Mira" and out["private_notes_moved"] is False


def test_a_rename_that_cannot_move_the_journal_keeps_the_old_name(fresh_db, monkeypatch):
    """The roster and the private journal move in one transaction. Committed
    apart, a failure between them left the author's notes under a name no
    roster row holds, readable by the next person to claim it."""
    from app import db
    from app.services import private_notes, users

    users.ensure_user("alice")
    private_notes.add_note("alice", "bob", "my own note", kind="note")

    def fail(old, new):
        raise RuntimeError("private schema unavailable")

    monkeypatch.setattr(private_notes, "rename_author", fail)
    with pytest.raises(RuntimeError):
        users.rename_user("alice", "alicia", actor="alice")
    assert db.query_one("SELECT 1 FROM users WHERE name = 'alice'") is not None
    assert db.query_one("SELECT 1 FROM users WHERE name = 'alicia'") is None


def test_the_author_renaming_themselves_does_move_it(fresh_db):
    from app.services import private_notes, users

    users.ensure_user("alice")
    private_notes.add_note("alice", "bob", "my own note", kind="note")

    result = users.rename_user("alice", "alice-2", actor="alice")

    assert result["private_notes_moved"] is True
    assert [n["body"] for n in private_notes.list_notes("alice-2", "bob")] == ["my own note"]


def test_a_self_directed_merge_onto_a_colleague_is_refused(fresh_db):
    """A merge moves the caller's API keys onto the target row. Self-directed,
    that made any keyholder into any colleague: mallory's unchanged key then
    authenticated as victim, with victim's private journal renamed along."""
    from app.services import api_keys, private_notes, users

    users.ensure_user("mallory")
    users.ensure_user("victim")
    private_notes.add_note("victim", "bob", "victim's private feedback", kind="feedback")
    key = api_keys.create_key("mallory")["key"]

    with pytest.raises(ValueError, match="cannot be self-directed"):
        users.rename_user("mallory", "victim", actor="mallory")

    assert api_keys.verify_key(key) == "mallory"
    assert [n["body"] for n in private_notes.list_notes("victim", "bob")] == [
        "victim's private feedback"
    ]


def test_an_agent_identity_can_be_freed_by_rename(fresh_db):
    """SKEIN_MCP_USER is operator-supplied and the obvious thing to type is
    your own name, which reserves it as an AGENT identity — refused on REST and
    on every private surface. This is the documented recovery."""
    from app import db
    from app.services import users

    users.ensure_user("mario", kind="agent")  # the trap
    assert users.is_agent("mario")

    users.rename_user("mario", "mario-mcp", actor="operator")

    assert db.query_one("SELECT 1 FROM users WHERE name = 'mario'") is None
    users.ensure_user("mario")  # the human can claim it again
    assert not users.is_agent("mario")


def test_ensure_user_refuses_to_flip_a_human_into_an_agent(fresh_db):
    """The upgrade case: an existing human row must survive a boot that
    reserves the same name for MCP."""
    from app.services import users

    users.ensure_user("mario")
    users.ensure_user("mario", kind="agent")  # what main.py does at startup
    assert not users.is_agent("mario")


def test_a_merge_cannot_carry_a_teammate_into_the_caller(fresh_db):
    """Merged into the caller's own account, a teammate's history, files and
    chats became the caller's, and the teammate's key signed in as the caller."""
    from app.services import users

    users.ensure_user("boss")
    users.ensure_user("alice")
    with pytest.raises(ValueError, match="your own account"):
        users.rename_user("alice", "boss", actor="boss", expected_merge=True)
    assert users.is_active("alice")


def test_a_merge_cannot_hand_single_owner_data_to_another_person(fresh_db):
    from app.services import memory, users, work

    for name in ("alice", "bob", "carol"):
        users.ensure_user(name)
    work.create_task("alice private", actor="alice", visibility="private")
    with pytest.raises(ValueError, match="only its owner can read"):
        users.rename_user("alice", "bob", actor="carol", expected_merge=True)
    fresh_db.execute("DELETE FROM tasks")
    memory.remember("alice only", user="alice", actor="alice")
    with pytest.raises(ValueError, match="only its owner can read"):
        users.rename_user("alice", "bob", actor="carol", expected_merge=True)


def test_a_merged_account_key_stops_signing_in(fresh_db):
    """The merge moved the source's API keys to the target, so the source's
    key authenticated as the target account."""
    from app.services import api_keys, users

    users.ensure_user("dana")
    users.ensure_user("dana-alt")
    key = api_keys.create_key("dana", label="old")["key"]
    users.rename_user("dana", "dana-alt", actor="ops", expected_merge=True)
    assert api_keys.verify_key(key) is None


def test_a_freed_name_cannot_be_claimed_by_a_new_person(client, fresh_db):
    """The ledger is never renamed, so a new `ava` read the earlier ava's
    ledger rows as her own history, and owned her private proposals."""
    from app.services import review, scope, users

    users.ensure_user("ava")
    users.ensure_user("bosun", kind="agent")
    proposal = review.propose_change(
        "note",
        "create",
        {"topic": "t", "content": "ava only"},
        "note",
        actor="bosun",
        requested_by="ava",
        review_visibility=scope.PRIVATE,
        review_owner="ava",
    )
    users.rename_user("ava", "ava.smith", actor="ops")
    with pytest.raises(ValueError, match="history from an earlier account"):
        users.ensure_user("ava")
    assert (
        client.post(
            "/api/notes", json={"topic": "t", "content": "c"}, headers={"X-User": "Ava"}
        ).status_code
        == 403
    )
    owner = fresh_db.query_one(
        "SELECT review_owner FROM pending_changes WHERE id = ?", (proposal["id"],)
    )
    assert owner["review_owner"] == "ava.smith"
