"""@mentions in chat: a leading @slug invokes the bench, an @person that
reaches nobody says so instead of going quiet."""

import pytest

from app.agents import turn_guard
from app.services import mentions, users


@pytest.fixture(autouse=True)
def _no_slack(monkeypatch):
    from app.services import notifications

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)


class _Quiet:
    """An agent turn that answers and writes nothing — the case the warning
    exists for. Unreachable through MockAgent, which smart-captures every
    freeform message and therefore always files something."""

    event_loop_metrics = None

    async def stream_async(self, message):
        yield {"data": "Noted."}


def _read_chat(client, message, thread):
    with client.stream("POST", "/api/chat", json={"thread_id": thread, "message": message}) as resp:
        assert resp.status_code == 200
        return resp.read().decode()


def _unread(fresh_db, person):
    rows = fresh_db.query(
        "SELECT message FROM notifications WHERE user = ? AND read_at IS NULL", (person,)
    )
    return [r["message"] for r in rows]


def test_names_in_splits_people_from_the_bench(client):
    users.ensure_user("mira")
    users.ensure_user("backend-architect", kind="agent")
    people, agents = mentions.names_in("@mira and @backend-architect, thoughts?")
    assert people == ["mira"]
    assert agents == ["backend-architect"]


def test_names_in_uses_the_same_parse_as_scan(client):
    """A surface that reports what a mention will do must not run a second
    parser. These are cases scan() already pins: sentence-final punctuation
    binds into the token, and an ssh target is not a mention."""
    users.ensure_user("mira")
    assert mentions.names_in("thanks @mira.")[0] == ["mira"]
    assert mentions.names_in("run ssh root@mira tonight")[0] == []


def test_a_turn_that_wrote_something_stays_quiet(client):
    users.ensure_user("mira")
    assert turn_guard.unnotified("ping @mira", wrote=True) is None


def test_an_unknown_name_is_not_warned_about(client):
    # only roster names resolve, so a stray @word must not produce a receipt
    assert turn_guard.unnotified("email me @ noon", wrote=False) is None


def test_the_invoked_persona_is_not_reported_unreached(client):
    """A leading @slug IS the delivery for that name — warning about it would
    contradict the answer the reader is looking at."""
    users.ensure_user("backend-architect", kind="agent")
    assert turn_guard.unnotified("what breaks?", wrote=False, invoked="backend-architect") is None


def test_a_silent_turn_reports_who_it_did_not_reach(client, monkeypatch):
    """The wiring: an agent that answers without filing must not leave the
    mention silent."""
    users.ensure_user("mira")
    monkeypatch.setattr("app.routes.chat.build_agent", lambda *a, **k: _Quiet())
    out = _read_chat(client, "can @mira look at the export job?", thread="cm-1")
    # the wire frame carries the fields; the rendered label lives in the
    # transcript and in runtime-provider.tsx::receiptLine
    assert '"kind": "unnotified"' in out
    assert '"entity": "mira"' in out
    saved = client.get("/api/chats/cm-1/messages").json()[-1]["content"]
    assert "not notified: mira" in saved


def test_the_keyless_path_delivers_because_it_files(client, fresh_db):
    """No warning on mock, and none is wanted: MockAgent captures the message,
    so the note carries the @name and mira is notified for real."""
    users.ensure_user("mira")
    out = _read_chat(client, "note: ask @mira about the export job", thread="cm-2")
    assert "not notified" not in out.lower()
    assert any("mentioned you" in m for m in _unread(fresh_db, "mira"))


def test_a_leading_at_slug_answers_as_that_persona(client):
    out = _read_chat(client, "@growth-mentor how do I plan a learning goal?", thread="cm-3")
    assert "Growth Mentor" in out  # the masthead the /as path renders


def test_a_leading_at_person_is_not_an_invocation(client):
    """@mira is a mention, not a bench slug: the turn stays an ordinary chat
    turn rather than answering as a persona or refusing an unknown slug."""
    users.ensure_user("mira")
    out = _read_chat(client, "@mira please review the export job", thread="cm-4")
    assert "is not defined" not in out
    assert "Usage: `/as" not in out


def test_a_bare_at_slug_with_no_message_is_ordinary_text(client):
    out = _read_chat(client, "@growth-mentor", thread="cm-5")
    assert "Growth Mentor" not in out
