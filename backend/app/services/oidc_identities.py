"""Stable OIDC issuer/subject bindings for human roster identities."""

import hashlib

from .. import db
from . import users


def _validate(issuer: str, subject: str, display_name: str) -> None:
    if not issuer or not issuer.isprintable():
        raise ValueError("The OIDC issuer is invalid.")
    if not subject or len(subject) > 255 or not subject.isprintable():
        raise ValueError("The OIDC subject is invalid.")
    if not display_name or len(display_name) > 64 or not display_name.isprintable():
        raise ValueError("The OIDC user name is invalid.")


def _subject_key(issuer: str, subject: str) -> str:
    return hashlib.sha256(f"{issuer}\0{subject}".encode()).hexdigest()


def _bound(issuer: str, subject: str) -> dict | None:
    return db.query_one(
        "SELECT users.*, oidc_identities.display_name"
        " FROM oidc_identities JOIN users ON users.id = oidc_identities.user_id"
        " WHERE oidc_identities.issuer = ? AND oidc_identities.subject = ?",
        (issuer, subject),
    )


def resolve(issuer: str, subject: str, display_name: str) -> dict:
    """Resolve one verified OIDC subject without reusing an unbound roster row."""
    _validate(issuer, subject, display_name)
    with db.transaction():
        # The subject lock comes before the roster-name lock on every bind path.
        # A CLI bind and a first sign-in deadlock if either path reverses this pair.
        db.name_lock(db.LOCK_OIDC_IDENTITY, _subject_key(issuer, subject))
        bound = _bound(issuer, subject)
        if bound is not None:
            if bound["kind"] != "human":
                raise RuntimeError("an OIDC identity points to a non-human roster row")
            if bound["display_name"] != display_name:
                db.execute(
                    "UPDATE oidc_identities SET display_name = ? WHERE issuer = ? AND subject = ?",
                    (display_name, issuer, subject),
                )
                db.log_activity(
                    bound["name"],
                    "oidc_profile_updated",
                    f"{bound['name']}: OIDC profile updated",
                )
            return bound

        db.name_lock(db.LOCK_IDENTITY, users.fold(display_name))
        existing = db.query_one("SELECT * FROM users WHERE name = ?", (display_name,))
        if existing is not None:
            if existing["kind"] != "human":
                raise ValueError(
                    "The OIDC user name belongs to an agent identity."
                    " Set SKEIN_OIDC_USERNAME_CLAIM to a claim that gives each person one name."
                )
            raise ValueError(
                "The Skein user name already exists without this OIDC subject binding."
                " Ask an administrator to bind the existing user."
            )
        human = users.ensure_human_identity(display_name)
        db.execute(
            "INSERT INTO oidc_identities"
            " (issuer, subject, user_id, display_name, origin, created_by, created_at)"
            " VALUES (?, ?, ?, ?, 'human', ?, ?)",
            (issuer, subject, human["id"], display_name, human["name"], db.now()),
        )
        db.log_activity(
            human["name"],
            "oidc_identity_bound",
            f"{human['name']}: OIDC identity bound",
        )
        return human


def bind_existing(issuer: str, subject: str, name: str, *, actor: str = "system") -> dict:
    """Bind one existing human row during an explicit deployment migration."""
    _validate(issuer, subject, name)
    with db.transaction():
        db.name_lock(db.LOCK_OIDC_IDENTITY, _subject_key(issuer, subject))
        db.name_lock(db.LOCK_IDENTITY, users.fold(name))
        subject_owner = _bound(issuer, subject)
        human = db.query_one("SELECT * FROM users WHERE name = ?", (name,))
        if human is None or human["kind"] != "human":
            raise ValueError("The OIDC binding target is not an existing human user.")
        if subject_owner is not None:
            if subject_owner["id"] != human["id"]:
                raise ValueError("The OIDC subject is already bound to a different user.")
            return subject_owner
        if db.query_one(
            "SELECT 1 FROM oidc_identities WHERE issuer = ? AND user_id = ?",
            (issuer, human["id"]),
        ):
            raise ValueError("The user already has a different OIDC subject binding.")
        db.execute(
            "INSERT INTO oidc_identities"
            " (issuer, subject, user_id, display_name, origin, created_by, created_at)"
            " VALUES (?, ?, ?, ?, 'human', ?, ?)",
            (issuer, subject, human["id"], name, actor, db.now()),
        )
        db.log_activity(actor, "oidc_identity_bound", f"{name}: OIDC identity bound")
        return human
