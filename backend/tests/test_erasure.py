"""A departed person's private data: kept for the grace period, then erased.

Deactivation kept solo chats, uploads, private rows, addressed memories,
notifications and the 1:1 journal forever, readable by nobody and removable
by nobody."""

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import _strong

from app import db
from app.services import erasure, notifications, private_notes, review, scope, users


def _backdate_deactivation(name: str, days: int) -> None:
    then = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
    db.execute("UPDATE users SET deactivated_at = ? WHERE name = ?", (then, name))


def test_the_erase_order_names_every_scoped_table():
    """A scoped table missing from the order keeps its private rows through
    an erasure, and the summary still counts them."""
    assert set(erasure._ORDER) == set(scope.CLASSIFIED)


def _departing_person(client) -> dict:
    """What a person leaves behind, made through the paths that make it."""
    leaver = _strong(client, "leaver")
    made: dict = {}
    made["standup"] = client.post(
        "/api/standups", json={"yesterday": "private plans", "today": "t"}, headers=leaver
    ).json()["id"]
    made["shared_standup"] = client.post(
        "/api/standups",
        json={"yesterday": "shared work", "today": "t", "visibility": "workspace"},
        headers=leaver,
    ).json()["id"]
    engagement = client.post(
        "/api/engagements", json={"name": "Side project", "visibility": "private"}, headers=leaver
    ).json()["id"]
    made["engagement"] = engagement
    made["task"] = client.post(
        "/api/tasks",
        json={"title": "private task", "visibility": "private", "engagement_id": engagement},
        headers=leaver,
    ).json()["id"]
    made["note"] = client.post(
        "/api/notes",
        json={"topic": "diary", "content": "private note", "visibility": "private"},
        headers=leaver,
    ).json()["id"]
    upload = client.post(
        "/api/files",
        files={"file": ("cv.md", io.BytesIO(b"my resume"), "text/plain")},
        headers=leaver,
    ).json()["id"]
    made["upload_path"] = Path(
        db.query_one("SELECT path FROM artifacts WHERE id = ?", (upload,))["path"]
    )
    assert (
        client.post(
            "/api/chat", json={"thread_id": "leaver-chat", "message": "/help"}, headers=leaver
        ).status_code
        == 200
    )
    from app.services import memory

    memory.remember("prefers mornings", user="leaver", actor="leaver")
    private_notes.add_note("leaver", "ava", "ava wants to lead")
    private_notes.add_note("ava", "leaver", "leaver asked for a raise")
    notifications.notify("leaver", "Your key expires soon.")
    made["proposal"] = review.propose_change(
        "note",
        "create",
        {"topic": "t", "content": "agent draft", "author": "leaver", "visibility": "private"},
        summary="agent draft",
        actor="scout",
        requested_by="leaver",
        review_visibility=scope.PRIVATE,
        review_owner="leaver",
    )["id"]
    users.set_growth_interests("leaver", "staff engineer", actor="leaver")
    return made


def test_a_deactivated_person_is_erased_after_the_grace_period(client, fresh_db):
    made = _departing_person(client)
    before = erasure.holdings("leaver")
    assert before["standups"] == before["tasks"] == before["engagements"] == 1
    assert before["solo_chats"] == before["memories"] == before["journal_notes"] == 1

    deactivated = users.set_active("leaver", False, actor="ava")
    assert deactivated["erase_on"]
    assert erasure.erase_due() == {"erased": 0}  # inside the grace period
    _backdate_deactivation("leaver", erasure.GRACE_DAYS)

    assert erasure.erase_due() == {"erased": 1}
    assert not any(erasure.holdings("leaver").values())
    assert not made["upload_path"].exists()
    assert not fresh_db.query("SELECT 1 FROM chat_messages WHERE thread_id = 'leaver-chat'")
    assert not fresh_db.query("SELECT 1 FROM search_index WHERE entity = 'memory'")
    person = fresh_db.query_row("SELECT * FROM users WHERE name = 'leaver'")
    assert person["erased_at"] and person["growth_interests"] == ""
    # shared records stay, with the name; another author's note about them stays
    assert fresh_db.query_one(
        "SELECT 1 FROM standups WHERE id = ? AND author = 'leaver'", (made["shared_standup"],)
    )
    assert private_notes.author_note_count("ava") == 1
    # the ledger records how much, never what
    ledger = fresh_db.query_row(
        "SELECT actor, detail FROM activity WHERE action = 'erase_private_data'"
    )
    assert ledger["actor"] == "scheduler"
    assert "private" not in ledger["detail"] and "resume" not in ledger["detail"]
    assert not fresh_db.query("SELECT 1 FROM activity WHERE action = 'delete_chat'")
    # a second run finds nothing to do
    assert erasure.erase_due() == {"erased": 0}


def test_reactivation_inside_the_grace_period_keeps_everything(client, fresh_db):
    _departing_person(client)
    users.set_active("leaver", False, actor="ava")
    _backdate_deactivation("leaver", erasure.GRACE_DAYS + 5)
    users.set_active("leaver", True, actor="ava")
    # the clock starts again at the second deactivation, not at the first
    users.set_active("leaver", False, actor="ava")
    assert erasure.erase_due() == {"erased": 0}
    assert erasure.holdings("leaver")["standups"] == 1


def test_an_active_account_is_never_erased(client, fresh_db):
    _departing_person(client)
    with pytest.raises(ValueError, match="deactivated"):
        erasure.erase("leaver")
    assert erasure.holdings("leaver")["standups"] == 1


def test_the_roster_shows_an_administrator_when_the_erase_runs(client, fresh_db):
    admin = _strong(client)
    users.ensure_human_identity("leaver")
    deactivated = users.set_active("leaver", False, actor="tester")
    rows = {r["name"]: r for r in client.get("/api/users?all=1", headers=admin).json()}
    assert rows["leaver"]["erase_on"] == deactivated["erase_on"]
    assert rows["leaver"]["erased"] is False
    assert all("erase_on" not in row for row in rows.values() if row["active"])
    assert json.dumps(rows["leaver"]).count("leaver") == 1
