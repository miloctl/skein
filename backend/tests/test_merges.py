"""A merge both accounts agree to moves what only the source could read
(services/merges.py). An administrator's merge still cannot."""

import pytest
from conftest import _strong

from app import db
from app.services import collab, memory, merges, private_notes, users


def _personal(name):
    """Data only `name` can read, one of each kind a merge must carry."""
    collab.save_note("mine", "ZZPRIVATEROWZZ", author=name, actor=name, visibility="private")
    memory.remember("ZZMEMORYZZ", user=name, actor=name)
    private_notes.add_note(name, "bob", "ZZJOURNALZZ")
    row = db.query_one("SELECT id FROM users WHERE name = ?", (name,))
    db.execute(
        "INSERT INTO oidc_identities (issuer, subject, user_id, display_name, created_by,"
        " created_at) VALUES ('https://idp', 'sub-2', ?, ?, 'ops', ?)",
        (row["id"], name, db.now()),
    )


def test_a_consented_merge_moves_what_only_the_source_could_read(client, fresh_db):
    """An account holding private data could not be merged at all, so a
    person with a duplicate account had no path to one account."""
    for name in ("ava", "ava2", "bob"):
        users.ensure_user(name)
    _personal("ava2")
    with pytest.raises(ValueError, match=r"only (its owner|they) can"):
        users.rename_user("ava2", "ava", actor="ops", expected_merge=True)

    source, target, bob = (_strong(client, n) for n in ("ava2", "ava", "bob"))
    asked = client.post("/api/merge-requests", json={"target": "ava"}, headers=source)
    assert asked.status_code == 200
    assert (
        client.post("/api/merge-requests", json={"target": "ava"}, headers=source).status_code
        == 400
    )
    assert (
        client.get("/api/merge-requests", headers=target).json()["incoming"][0]["source"] == "ava2"
    )
    rid = asked.json()["id"]
    # only the target confirms, and a weak name cannot
    assert client.post(f"/api/merge-requests/{rid}/confirm", headers=bob).status_code == 404
    assert client.post(f"/api/merge-requests/{rid}/confirm", headers=source).status_code == 404
    assert client.post(
        f"/api/merge-requests/{rid}/confirm", headers={"X-User": "ava"}
    ).status_code in (401, 403)
    assert client.post(f"/api/merge-requests/{rid}/confirm", headers=target).status_code == 200

    assert not db.query_one("SELECT 1 FROM users WHERE name = 'ava2'")
    assert (
        db.query_one("SELECT author FROM notes WHERE content = 'ZZPRIVATEROWZZ'")["author"] == "ava"
    )
    assert (
        db.query_one("SELECT \"user\" FROM memories WHERE content = 'ZZMEMORYZZ'")["user"] == "ava"
    )
    assert [n["body"] for n in private_notes.list_notes("ava")] == ["ZZJOURNALZZ"]
    ava_id = db.query_one("SELECT id FROM users WHERE name = 'ava'")["id"]
    assert (
        db.query_one("SELECT user_id FROM oidc_identities WHERE subject = 'sub-2'")["user_id"]
        == ava_id
    )
    # nobody tells the target about a merge they confirmed themselves
    assert not db.query_one(
        "SELECT 1 FROM notifications WHERE \"user\" = 'ava' AND message LIKE '%merged%'"
    )


def test_either_account_can_stop_a_merge_request(fresh_db):
    for name in ("ava", "ava2", "cy"):
        users.ensure_user(name)
    first = merges.request("ava", actor="ava2")
    assert merges.settle(first["id"], actor="ava2")["status"] == "cancelled"
    second = merges.request("ava", actor="ava2")
    with pytest.raises(db.NotFound):
        merges.settle(second["id"], actor="cy")
    assert merges.settle(second["id"], actor="ava")["status"] == "declined"
    assert db.query_one(
        "SELECT 1 FROM notifications WHERE \"user\" = 'ava2' AND message LIKE 'ava declined%'"
    )
    with pytest.raises(db.NotFound):
        merges.confirm(second["id"], actor="ava")


def test_a_merge_request_cannot_outlive_the_credentials_that_filed_it(fresh_db):
    """A request is one strong call, which a stolen key can make. It stayed
    confirmable after revoke-all and deactivation, and its source never heard."""
    from datetime import UTC, datetime, timedelta

    from app.services import api_keys

    for name in ("ava", "ava2", "mal", "cy"):
        users.ensure_user(name)
    filed = merges.request("mal", actor="ava2")
    assert db.query_one(
        "SELECT 1 FROM notifications WHERE \"user\" = 'ava2' AND message LIKE 'A request to merge%'"
    )
    api_keys.revoke_all_keys(actor="ops")
    with pytest.raises(db.NotFound):
        merges.confirm(filed["id"], actor="mal")
    # deactivation cancels it too, and confirm refuses an inactive account
    again = merges.request("mal", actor="ava2")
    users.set_active("ava2", False, actor="ops")
    with pytest.raises(db.NotFound):
        merges.confirm(again["id"], actor="mal")
    users.set_active("ava2", True, actor="ops")
    third = merges.request("mal", actor="ava2")
    db.execute("UPDATE users SET active = 0 WHERE name = 'mal'")
    with pytest.raises(ValueError, match="deactivated"):
        merges.confirm(third["id"], actor="mal")
    db.execute("UPDATE users SET active = 1 WHERE name = 'mal'")
    # a lapsed request cannot be confirmed, and does not block a new one
    old = (datetime.now(UTC) - timedelta(days=merges.EXPIRY_DAYS + 1)).isoformat(timespec="seconds")
    db.execute("UPDATE merge_requests SET created_at = ? WHERE id = ?", (old, third["id"]))
    with pytest.raises(ValueError, match="older than"):
        merges.confirm(third["id"], actor="mal")
    assert merges.list_for("mal")["incoming"] == []
    assert merges.request("cy", actor="ava2")["status"] == "pending"


def test_a_merge_moves_nothing_further_than_its_source_agreed(fresh_db):
    """A merge into an account that had itself asked to merge elsewhere
    carried the first source's data to a third account."""
    for name in ("ava", "ava2", "cy", "dan"):
        users.ensure_user(name)
    onward = merges.request("cy", actor="ava")
    inward = merges.request("ava", actor="ava2")
    merges.confirm(inward["id"], actor="ava")
    assert (
        db.query_one("SELECT status FROM merge_requests WHERE id = ?", (onward["id"],))["status"]
        == "cancelled"
    )
    # a rename of either named account cancels a pending request too
    pending = merges.request("dan", actor="cy")
    users.rename_user("dan", "daniel", actor="dan")
    assert (
        db.query_one("SELECT status FROM merge_requests WHERE id = ?", (pending["id"],))["status"]
        == "cancelled"
    )


def test_same_issuer_accounts_are_refused_when_asked_not_when_confirmed(fresh_db):
    """Refused only at confirm, the request stayed pending and blocked every
    later one from its source."""
    for name in ("ava", "ava2"):
        users.ensure_user(name)
    for name, subject in (("ava", "sub-a"), ("ava2", "sub-b")):
        row = db.query_one("SELECT id FROM users WHERE name = ?", (name,))
        db.execute(
            "INSERT INTO oidc_identities (issuer, subject, user_id, display_name, created_by,"
            " created_at) VALUES ('https://idp', ?, ?, ?, 'ops', ?)",
            (subject, row["id"], name, db.now()),
        )
    with pytest.raises(ValueError, match="same identity provider"):
        merges.request("ava", actor="ava2")
    assert not db.query_one("SELECT 1 FROM merge_requests")


def test_a_consented_merge_moves_the_sources_notifications(fresh_db):
    for name in ("ava", "ava2", "bob"):
        users.ensure_user(name)
    from app.services.notifications import notify

    notify("ava2", "ZZNOTICEZZ", tier="immediate")
    merges.confirm(merges.request("ava", actor="ava2")["id"], actor="ava")
    assert (
        db.query_one("SELECT \"user\" FROM notifications WHERE message = 'ZZNOTICEZZ'")["user"]
        == "ava"
    )
