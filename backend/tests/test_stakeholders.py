"""Open threads with people outside the team.

Four tables already recorded an outside name and nothing put them together,
so "what is open with Acme" was answerable only by remembering — and the
answer arrived after the meeting rather than before it.
"""

from app import db
from app.services import promises, schedule, scope, stakeholders, users


def test_it_gathers_one_party_across_the_tables(client):
    promises.add_promise("the redlines", to_whom="Acme", direction="received", actor="ava")
    promises.add_promise("the summary", to_whom="Acme", actor="ava")
    client.post("/api/intake", json={"title": "a new ask"})
    db.execute("UPDATE intake_requests SET requester = 'Acme'")

    threads = stakeholders.open_threads(scope.Viewer("ava", True))
    acme = next(t for t in threads if t["party"] == "Acme")
    kinds = sorted(i["kind"] for i in acme["items"])
    assert kinds == [
        "promise (they owe us)",
        "promise (we owe them)",
        "request awaiting triage",
    ]


def test_a_teammate_is_not_a_stakeholder(client):
    """The roster is the boundary. A teammate's open question is ordinary
    work, and listing it under a vendor heading would be wrong twice."""
    users.ensure_user("mira")
    client.post("/api/questions", json={"question": "who owns infra?"})
    db.execute("UPDATE questions SET asked_by = 'mira'")
    parties = {t["party"] for t in stakeholders.open_threads(scope.Viewer("ava", True))}
    assert "mira" not in parties


def test_a_system_actor_is_not_a_stakeholder(client):
    """The four reserved names appear in these columns whenever a job wrote
    the row. `scheduler` is not somebody to prepare for a meeting with."""
    promises.add_promise("something", to_whom="scheduler", actor="ava")
    parties = {t["party"] for t in stakeholders.open_threads(scope.Viewer("ava", True))}
    assert "scheduler" not in parties


def test_a_settled_thread_drops_off(client):
    pid = promises.add_promise("the redlines", to_whom="Acme", actor="ava")["id"]
    assert stakeholders.open_threads(scope.Viewer("ava", True))
    promises.update_promise(pid, "kept", actor="ava")
    assert stakeholders.open_threads(scope.Viewer("ava", True)) == []


def test_the_brief_is_scoped_to_the_people_in_the_room(client):
    """A list of every open thread with everybody is a report nobody reads.
    The brief is only useful in the hour before you speak to them."""
    promises.add_promise("the redlines", to_whom="Acme", actor="ava")
    promises.add_promise("something else", to_whom="Globex", actor="ava")
    ev = schedule.schedule_event(
        "Acme sync", "2026-09-01T10:00", attendees="Acme, ava", actor="ava"
    )
    brief = stakeholders.brief_for_event(ev["id"], scope.Viewer("ava", True))
    assert [t["party"] for t in brief["threads"]] == ["Acme"]


def test_a_thread_the_reader_cannot_see_is_not_in_the_brief(client):
    """A brief assembled from rows a caller cannot read would leak both the
    row and the fact that the party exists."""
    pid = promises.add_promise("the secret redlines", to_whom="Acme", actor="ava")["id"]
    db.execute("UPDATE promises SET visibility = 'private' WHERE id = ?", (pid,))
    threads = stakeholders.open_threads(scope.Viewer("someone-else", True))
    assert threads == []


def test_a_teammate_written_short_is_still_not_a_stakeholder(client):
    """These columns are free text, so a teammate is written "Dana W." as
    often as in full. An exact fold let that row onto a card headed "Open
    outside the team" with a past-due date beside it."""
    users.ensure_user("Dana Whitfield")
    promises.add_promise("the Q3 forecast", to_whom="Dana W.", direction="received", actor="ava")
    promises.add_promise("the redlines", to_whom="Acme Corp", direction="received", actor="ava")
    parties = {t["party"] for t in stakeholders.open_threads(scope.Viewer("ava", True))}
    assert "Dana W." not in parties
    assert "Acme Corp" in parties, "an unrelated outside party must still be listed"


def test_one_party_written_three_ways_is_one_thread(client):
    """Case is exactly how free-text party names arrive. Three cards each
    claiming one open item is what this module exists to stop."""
    for spelling in ("Acme", "acme", "ACME"):
        promises.add_promise("a thing", to_whom=spelling, direction="received", actor="ava")
    threads = stakeholders.open_threads(scope.Viewer("ava", True))
    assert len(threads) == 1, [t["party"] for t in threads]
    assert len(threads[0]["items"]) == 3


def test_the_brief_survives_a_busy_roster(client):
    """The roster is excluded in SQL, before the cap. Filtered afterwards,
    roster-directed rows ate the whole budget and the brief for the meeting
    you are about to walk into answered "nothing open"."""
    promises.add_promise("the redlines", to_whom="Acme", direction="received", actor="ava")
    ev = schedule.schedule_event(
        "Acme sync", "2026-09-01T10:00", attendees="Acme, ava", actor="ava"
    )
    assert stakeholders.brief_for_event(ev["id"], scope.Viewer("ava", True))["threads"]

    users.ensure_user("mira")
    for i in range(stakeholders._LIMIT + 10):
        promises.add_promise(f"internal {i}", to_whom="mira", actor="ava")
    brief = stakeholders.brief_for_event(ev["id"], scope.Viewer("ava", True))
    assert [t["party"] for t in brief["threads"]] == ["Acme"]
