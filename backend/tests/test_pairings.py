"""Only a lead the subject accepted pulls a 1:1 brief (services/pairings.py)."""

from conftest import _strong

from app import db
from app.services import pairings, users


def _brief(client, headers, person):
    return client.get(f"/api/private/brief/{person}", headers=headers)


def test_a_brief_needs_the_subjects_accepted_pairing(client, fresh_db):
    """Any strong identity pulled any teammate's brief, and the subject was
    never told."""
    for name in ("ava", "bob", "cy"):
        users.ensure_user(name)
    ava, bob, cy = (_strong(client, n) for n in ("ava", "bob", "cy"))
    assert _brief(client, ava, "bob").status_code == 403
    assert _brief(client, ava, "ava").status_code == 200  # your own needs no pair

    asked = client.post("/api/private/pairs", json={"person": "bob", "role": "lead"}, headers=ava)
    assert asked.status_code == 200 and asked.json()["status"] == "proposed"
    assert (
        client.post(
            "/api/private/pairs", json={"person": "bob", "role": "lead"}, headers=ava
        ).status_code
        == 400
    )
    assert db.query_one(
        'SELECT 1 FROM notifications WHERE "user" = ? AND message LIKE ?', ("bob", "ava asks%")
    )
    assert _brief(client, ava, "bob").status_code == 403
    pid = asked.json()["id"]
    # only the subject accepts, and nobody outside the pair ends it
    assert client.post(f"/api/private/pairs/{pid}/accept", headers=ava).status_code == 404
    assert client.post(f"/api/private/pairs/{pid}/end", headers=cy).status_code == 404
    assert client.post(f"/api/private/pairs/{pid}/accept", headers=bob).status_code == 200
    assert _brief(client, ava, "bob").status_code == 200
    seen = client.get("/api/private/pairs", headers=bob).json()["subject_of"]
    assert [(p["lead"], bool(p["last_brief_at"])) for p in seen] == [("ava", True)]
    assert client.get("/api/private/pairs", headers=cy).json() == {"leading": [], "subject_of": []}
    assert client.post(f"/api/private/pairs/{pid}/end", headers=bob).status_code == 200
    assert _brief(client, ava, "bob").status_code == 403

    # a subject's offer is their consent
    offered = client.post(
        "/api/private/pairs", json={"person": "cy", "role": "subject"}, headers=bob
    )
    assert offered.json()["status"] == "accepted"
    assert _brief(client, cy, "bob").status_code == 200
    assert client.get("/api/private/pairs", headers={"X-User": "cy"}).status_code in (401, 403)


def test_a_merge_folds_pairings(fresh_db):
    """Two halves of one person paired with one teammate are one pair, and a
    pair between the two halves would pair a person with themselves."""
    for name in ("ava", "ava2", "bob"):
        users.ensure_user(name)
    pairings.propose("bob", actor="ava", role="lead")
    pairings.propose("bob", actor="ava2", role="lead")
    pairings.propose("ava2", actor="ava", role="subject")
    users.rename_user("ava", "ava2", actor="ops", expected_merge=True)
    rows = db.query("SELECT lead, subject FROM one_on_one_pairs WHERE status <> 'ended'")
    assert rows == [{"lead": "ava2", "subject": "bob"}]
