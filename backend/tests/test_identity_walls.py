"""Identity boundaries that a security audit found open.

Each case here was demonstrated against a throwaway database before the fix.
The theme is one rule with three holes in it: an identity may act only as
itself, and a surface that writes on someone's behalf must prove which
someone.
"""

import pytest


def test_a_rename_by_someone_else_never_moves_the_private_journal(fresh_db):
    """Every keyholder can rename any roster row — the trusted-LAN model makes
    them all admins over TEAM data. Moving the private half too let anyone
    merge another person's row into their own name and inherit their 1:1 notes
    and fb: journal, the one dataset teammates are promised they cannot read."""
    from app.services import private_notes, users

    users.ensure_user("alice")
    users.ensure_user("mallory")
    private_notes.add_note("alice", "bob", "bob is coasting", kind="feedback")

    result = users.rename_user("alice", "mallory", actor="mallory")

    assert result["private_notes_moved"] is False
    assert private_notes.list_notes("mallory", "bob") == []
    # fails CLOSED: the rows keep the old author, so they are unreachable
    # rather than readable by the wrong person
    assert [n["body"] for n in private_notes.list_notes("alice", "bob")] == ["bob is coasting"]


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
    assert "agent identity" in r.json()["text"]
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
    body = f"text=%2Fhelp&user_name={name}"
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
    assert "agent identity" not in r.json()["text"]
