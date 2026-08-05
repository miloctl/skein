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
    import pytest

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


def test_the_author_renaming_themselves_does_move_it(fresh_db):
    from app.services import private_notes, users

    users.ensure_user("alice")
    private_notes.add_note("alice", "bob", "my own note", kind="note")

    result = users.rename_user("alice", "alice-2", actor="alice")

    assert result["private_notes_moved"] is True
    assert [n["body"] for n in private_notes.list_notes("alice-2", "bob")] == ["my own note"]


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


def test_slack_refuses_to_write_as_an_agent_identity(client, fresh_db, monkeypatch):
    """deps.py refuses an agent identity on REST because agent rows carry trust
    scores and gate levels, and writes as them sidestep the review gate. Slack
    took user_name as the actor with no such check, and suppressed the clash."""
    import hashlib
    import hmac
    import time

    from app import config
    from app.services import users

    users.ensure_user("agent", kind="agent")
    monkeypatch.setattr(config, "SLACK_SIGNING_SECRET", "shhh")

    body = "text=todo%3A+exfiltrate+the+roster&user_name=agent"
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(b"shhh", f"v0:{ts}:{body}".encode(), hashlib.sha256).hexdigest()
    r = client.post(
        "/api/slack/command",
        content=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": sig,
        },
    )
    assert r.status_code == 200
    # loose on wording (an STE pass may reword it), strict on the outcome
    assert "agent" in r.json()["text"].lower()
    # and nothing was written as that identity
    from app.services import work

    assert [t for t in work.list_tasks() if t["created_by"] == "agent"] == []


@pytest.mark.parametrize("name", ["dana", "slack-user"])
def test_slack_still_writes_for_ordinary_people(client, fresh_db, monkeypatch, name):
    import hashlib
    import hmac
    import time

    from app import config

    monkeypatch.setattr(config, "SLACK_SIGNING_SECRET", "shhh")
    # a body that WRITES — /help writes nothing, so it could not prove the
    # ordinary-person path still works
    body = f"text=todo%3A+ordinary+person+capture&user_name={name}"
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(b"shhh", f"v0:{ts}:{body}".encode(), hashlib.sha256).hexdigest()
    r = client.post(
        "/api/slack/command",
        content=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": sig,
        },
    )
    assert r.status_code == 200
    assert "is an agent identity" not in r.json()["text"]
    # is_agent() is False for a user that does not EXIST, so asserting it alone
    # passed even if ensure_user never ran. Assert the row, and the write.
    from app.services import work

    row = fresh_db.query_one("SELECT kind FROM users WHERE name = ?", (name,))
    assert row is not None and row["kind"] == "human"
    assert [t for t in work.list_tasks() if t["created_by"] == name]
