import pytest

from app import db
from app.services import oidc_identities, users

ISSUER = "https://idp.example.invalid"


def test_oidc_subject_stays_bound_when_the_display_claim_changes(fresh_db):
    first = oidc_identities.resolve(ISSUER, "subject-a", "alice")
    second = oidc_identities.resolve(ISSUER, "subject-a", "alice-renamed")

    assert second["id"] == first["id"]
    assert second["name"] == "alice"
    assert db.query_one(
        "SELECT display_name FROM oidc_identities WHERE issuer = ? AND subject = ?",
        (ISSUER, "subject-a"),
    ) == {"display_name": "alice-renamed"}


def test_a_new_subject_cannot_inherit_an_existing_name(fresh_db):
    first = oidc_identities.resolve(ISSUER, "subject-a", "alice")

    with pytest.raises(ValueError, match="already exists"):
        oidc_identities.resolve(ISSUER, "subject-b", "alice")

    assert oidc_identities.resolve(ISSUER, "subject-a", "alice")["id"] == first["id"]


def test_an_existing_user_needs_an_explicit_subject_binding(fresh_db):
    existing = users.ensure_human_identity("mira")
    with pytest.raises(ValueError, match="bind the existing user"):
        oidc_identities.resolve(ISSUER, "mira-subject", "mira")

    oidc_identities.bind_existing(ISSUER, "mira-subject", "mira", actor="operator")
    assert oidc_identities.resolve(ISSUER, "mira-subject", "mira")["id"] == existing["id"]


def test_merge_refuses_two_subjects_from_the_same_issuer(fresh_db):
    oidc_identities.resolve(ISSUER, "subject-a", "alice")
    oidc_identities.resolve(ISSUER, "subject-b", "bob")

    with pytest.raises(db.Conflict, match="different OIDC subjects"):
        users.rename_user("alice", "bob", actor="alice", expected_merge=True)
