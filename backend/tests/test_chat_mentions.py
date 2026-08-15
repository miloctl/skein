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


def _last_saved(client, thread):
    return client.get(f"/api/chats/{thread}/messages").json()[-1]["content"]


def _unread(fresh_db, person):
    rows = fresh_db.query(
        'SELECT message FROM notifications WHERE "user" = ? AND read_at IS NULL', (person,)
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
    # a real token that reaches the roster and misses — "@ noon" never becomes
    # a token at all, so it would pass without the roster lookup ever running
    assert turn_guard.unnotified("ping @nobody-on-this-roster", wrote=False) is None


def test_a_self_mention_is_not_a_miss(client):
    """scan drops the actor — a self-mention is not directed attention — so
    reporting one tells the author to file something that notifies nobody."""
    users.ensure_user("tester")
    assert turn_guard.unnotified("remind @tester to check it", wrote=False, actor="tester") is None


def test_a_mention_inside_code_is_not_a_mention(client):
    """Chat is where shell and YAML get pasted. `curl -H "X-User: @mira"` is an
    argument, and warning about it teaches the reader to distrust the receipt."""
    users.ensure_user("mira")
    fenced = 'why does this fail?\n```\ncurl -H "X-User: @mira" /api/x\n```'
    assert turn_guard.unnotified(fenced, wrote=False) is None
    assert turn_guard.unnotified("run `@mira` inline", wrote=False) is None


def test_many_mentions_are_capped(client):
    for n in ("aa", "bb", "cc", "dd", "ee"):
        users.ensure_user(n)
    note = turn_guard.unnotified("@aa @bb @cc @dd @ee", wrote=False)
    assert note is not None
    assert note["entity"] == "aa, bb, cc and 2 more"


def test_the_invoked_persona_is_not_reported_unreached(client):
    """A leading @slug IS the delivery for that name — warning about it would
    contradict the answer the reader is looking at."""
    users.ensure_user("backend-architect", kind="agent")
    # the message MUST contain the mention, or this passes with `invoked`
    # deleted: names_in returns nothing and the guard is silent either way
    assert turn_guard.unnotified("@backend-architect what breaks?", wrote=False) is not None
    assert (
        turn_guard.unnotified(
            "@backend-architect what breaks?", wrote=False, invoked="backend-architect"
        )
        is None
    )


def test_a_specialist_mention_offers_the_ask_route(client):
    """A filed row reaches an agent too, so the capture prefix alone is not
    wrong — it answers "ask @slug about tomorrow" with instructions for filing
    a task. The specialist can answer instead, and only it can."""
    users.ensure_user("backend-architect", kind="agent")
    users.ensure_user("mira")
    spec = turn_guard.unnotified("ask @backend-architect about tomorrow", wrote=False)
    person = turn_guard.unnotified("ask @mira about tomorrow", wrote=False)
    assert spec is not None and person is not None
    assert "start the message with `@` and its name" in spec["detail"]
    assert "start the message with `@` and its name" not in person["detail"]
    # both keep the filing route: a specialist reads a filed row through
    # my_agent_inbox, so dropping it would remove a real way to reach one
    assert "todo:" in spec["detail"] and "todo:" in person["detail"]


def test_the_invoked_specialist_is_not_offered_as_a_route(client):
    """`invoked` is filtered out of `named`, and the advice must filter it the
    same way — otherwise a turn answered BY the specialist tells the reader to
    go and ask that specialist."""
    users.ensure_user("backend-architect", kind="agent")
    users.ensure_user("mira")
    out = turn_guard.unnotified(
        "@backend-architect ask @mira too", wrote=False, invoked="backend-architect"
    )
    assert out is not None and out["entity"] == "mira"
    assert "start the message with `@` and its name" not in out["detail"]


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
    # the literal string services/personas.py raises: an invented one asserts
    # the absence of text the backend never emits
    assert "no persona" not in out
    # the saved turn either warned about the unreached mention or filed a row
    # that reaches mira — one of the two must hold, in _receipt_line's exact
    # casing ("Not notified" never matched, so this line asserted nothing)
    saved = _last_saved(client, "cm-4")
    assert "not notified: mira" in saved or "wrote " in saved


def test_a_bare_at_slug_with_no_message_is_ordinary_text(client):
    out = _read_chat(client, "@growth-mentor", thread="cm-5")
    assert "Growth Mentor" not in out
    # without this the test passes when the rewrite fires on an empty rest and
    # the /as branch answers with its usage line, which also lacks the masthead
    assert "Usage: `/as" not in out


def test_punctuation_after_the_slug_still_invokes(client):
    """A comma or a newline after the slug is ordinary composer typing, and
    partition(" ") saw neither — the specialist was not invoked AND the guard
    then warned that it had not been notified."""
    assert "Growth Mentor" in _read_chat(client, "@growth-mentor, plan a goal?", thread="cm-6")
    assert "Growth Mentor" in _read_chat(client, "@growth-mentor\nplan a goal?", thread="cm-7")


def test_the_keyless_capture_carries_the_human_author(client, fresh_db):
    """MockAgent transcribes the human's own words. Attributing the payload
    to the agent identity filed Ava's question as one the agent asked, and
    the mention notification named the agent instead of her."""
    users.ensure_user("mira")
    _read_chat(client, "q: @mira can you check the export job?", thread="cm-author")
    question = fresh_db.query_one("SELECT asked_by, created_by FROM questions ORDER BY id DESC")
    assert question["asked_by"] == "tester"
    assert question["created_by"] == "tester"
    assert any("tester mentioned you" in m for m in _unread(fresh_db, "mira"))
