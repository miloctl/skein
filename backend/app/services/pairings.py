"""1:1 pairings: who may pull whose 1:1 brief.

A brief gathers one person's recent standups, blockers, questions, tasks and
promises in one place (private_notes.one_on_one_brief). Every row in it is one
the reader could already open, but the gathering is a profile, and it was
open to every strong identity about every teammate, unseen by its subject.
Now a lead pulls a brief only while the subject has accepted the pairing, and
the subject sees each lead and when they last pulled it.

Either side can start one. A lead's proposal waits for the subject. A
subject's offer is their consent, so it is accepted at once. Either side ends
it. The 1:1 journal (private_notes) is the author's alone and needs no pair.
"""

import json
from datetime import UTC, datetime, timedelta

from .. import db
from .notifications import notify
from .users import fold, resolve_teammate

# A subject who declined or ended a pair is not asked again, with a new
# immediate notice, before this many days pass.
REASK_DAYS = 7


def _cutoff() -> str:
    # the db.now() format, so the text comparison orders by time
    return (datetime.now(UTC) - timedelta(days=REASK_DAYS)).isoformat(timespec="seconds")


def _teammate(name: str, actor: str) -> str:
    person = resolve_teammate(name, actor, "person", allow_team=False)
    if not person or fold(person) == fold(actor):
        raise ValueError("Name a teammate other than yourself.")
    return person


def _open_pair(lead: str, subject: str) -> dict | None:
    return db.query_one(
        "SELECT * FROM one_on_one_pairs WHERE lead = ? AND subject = ? AND status <> 'ended'",
        (lead, subject),
    )


def propose(person: str, *, actor: str, role: str) -> dict:
    """`role` is the actor's side: "lead" asks to pull `person`'s brief,
    "subject" lets `person` pull the actor's."""
    if role not in ("lead", "subject"):
        raise ValueError('role must be "lead" or "subject"')
    other = _teammate(person, actor)
    lead, subject = (actor, other) if role == "lead" else (other, actor)
    now = db.now()
    with db.transaction():
        # the open-pair check decides the insert: lock the pair's key first
        # a JSON pair: no separator a roster name can contain can blur it
        db.name_lock(db.LOCK_PAIRING, json.dumps([fold(lead), fold(subject)]))
        existing = _open_pair(lead, subject)
        if existing and (existing["status"] == "accepted" or role == "lead"):
            raise ValueError("A 1:1 pairing between you already exists.")
        if role == "lead" and db.query_one(
            "SELECT 1 FROM one_on_one_pairs WHERE lead = ? AND subject = ? AND status = 'ended'"
            " AND ended_by = subject AND ended_at >= ?",
            (lead, subject, _cutoff()),
        ):
            raise ValueError(
                f"This teammate declined or ended a pairing with you in the last {REASK_DAYS} days."
                " Ask again after that."
            )
        if existing:
            # the subject offers what the lead asked for: that is acceptance
            return accept(int(existing["id"]), actor=actor)
        accepted = role == "subject"
        pid = db.execute(
            "INSERT INTO one_on_one_pairs (lead, subject, status, proposed_by, created_at,"
            " accepted_at) VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (
                lead,
                subject,
                "accepted" if accepted else "proposed",
                actor,
                now,
                now if accepted else None,
            ),
        )
        db.log_activity(actor, "propose_pairing", f"#{pid}")
        notify(
            other,
            f"{actor} lets you prepare 1:1s with them. You can open their brief on the People page."
            if accepted
            else f"{actor} asks to prepare 1:1s with you. Accept or decline on the People page.",
            tier="immediate",
            link="/people",
        )
    return {
        "id": pid,
        "lead": lead,
        "subject": subject,
        "status": "accepted" if accepted else "proposed",
    }


def accept(pair_id: int, *, actor: str) -> dict:
    with db.transaction():
        row = db.query_one("SELECT * FROM one_on_one_pairs WHERE id = ? FOR UPDATE", (pair_id,))
        # only the subject accepts: their consent is what the pair records
        if not row or row["subject"] != actor or row["status"] == "ended":
            raise db.NotFound("pairing not found")
        if row["status"] == "accepted":
            return {"id": pair_id, "status": "accepted"}
        db.execute(
            "UPDATE one_on_one_pairs SET status = 'accepted', accepted_at = ? WHERE id = ?",
            (db.now(), pair_id),
        )
        db.log_activity(actor, "accept_pairing", f"#{pair_id}")
        notify(
            row["lead"],
            f"{actor} accepted your 1:1 pairing. You can open their brief on the People page.",
            tier="immediate",
            link="/people",
        )
    return {"id": pair_id, "status": "accepted"}


def end(pair_id: int, *, actor: str) -> dict:
    """Either side ends a pair, and a subject declines a proposal the same
    way."""
    with db.transaction():
        row = db.query_one("SELECT * FROM one_on_one_pairs WHERE id = ? FOR UPDATE", (pair_id,))
        if not row or actor not in (row["lead"], row["subject"]) or row["status"] == "ended":
            raise db.NotFound("pairing not found")
        db.execute(
            "UPDATE one_on_one_pairs SET status = 'ended', ended_at = ?, ended_by = ? WHERE id = ?",
            (db.now(), actor, pair_id),
        )
        db.log_activity(actor, "end_pairing", f"#{pair_id}")
        other = row["subject"] if actor == row["lead"] else row["lead"]
        notify(
            other,
            f"{actor} ended your 1:1 pairing."
            if row["status"] == "accepted"
            else f"{actor} declined the 1:1 pairing.",
            tier="immediate",
            link="/people",
        )
    return {"id": pair_id, "status": "ended"}


def end_all_for(person: str) -> int:
    """End every open pair `person` is in. A deactivated account neither
    leads nor is led: its accepted consent must not keep its brief open."""
    return db.execute_rowcount(
        "UPDATE one_on_one_pairs SET status = 'ended', ended_at = ?, ended_by = 'system'"
        " WHERE status <> 'ended' AND (lead = ? OR subject = ?)",
        (db.now(), person, person),
    )


def list_pairs(person: str) -> dict:
    """The pairs `person` is in, both sides. A lead sees the subject and the
    status. A subject also sees when each lead last pulled their brief."""
    rows = db.query(
        "SELECT id, lead, subject, status, proposed_by, created_at, accepted_at, last_brief_at"
        " FROM one_on_one_pairs WHERE (lead = ? OR subject = ?) AND status <> 'ended'"
        " ORDER BY id",
        (person, person),
    )
    leading = [
        {k: r[k] for k in ("id", "subject", "status", "proposed_by", "accepted_at")}
        for r in rows
        if r["lead"] == person
    ]
    subject_of = [
        {k: r[k] for k in ("id", "lead", "status", "proposed_by", "accepted_at", "last_brief_at")}
        for r in rows
        if r["subject"] == person
    ]
    return {"leading": leading, "subject_of": subject_of}


def record_brief(lead: str, subject: str) -> None:
    """Refuse a brief the subject never accepted, and stamp the pull the
    subject sees. Your own brief needs no pair."""
    # exact names: a legacy fold-duplicate ("Mira" and "mira", which rename
    # exists to clean up) is another account and needs a pairing
    if lead == subject:
        return
    with db.transaction():
        n = db.execute_rowcount(
            "UPDATE one_on_one_pairs SET last_brief_at = ?"
            " WHERE lead = ? AND subject = ? AND status = 'accepted'",
            (db.now(), lead, subject),
        )
    if not n:
        # no name in the refusal: the path parameter is the caller's input
        raise PermissionError(
            "No accepted 1:1 pairing covers this person. Ask them to accept one on the People page."
        )
