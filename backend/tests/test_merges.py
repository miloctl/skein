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
