"""My Day's promise to its reader: a row under "Needs you" names them.

Every list here was workspace-wide before. A promise somebody else made, a
decision somebody else took, and a proposal nobody assigned all rendered under
a heading that said the reader had to act — so the reader learned that the
heading does not mean what it says, and the product's daily habit went with it.
"""


def _soon() -> str:
    """Inside My Day's seven-day promise window, in the TEAM's day — a fixed
    date drifts out of the window and the test starts passing for the wrong
    reason (CLAUDE.md: a date far from the window edge pins nothing)."""
    from datetime import timedelta

    from app import db

    return (db.today() + timedelta(days=2)).isoformat()


def _audience(briefing: dict, kind: str) -> list[str]:
    return [a.get("audience") for a in briefing["attention"] if a["kind"] == kind]


def test_another_persons_promise_is_not_your_commitment(client):
    from app.services import promises

    promises.add_promise("ship the redlines", to_whom="acme", due_date=_soon(), actor="mira")
    b = client.get("/api/briefing").json()  # tester, not mira
    assert not any(a["kind"] == "promise" for a in b["attention"])

    promises.add_promise("ship the schema", to_whom="acme", due_date=_soon(), actor="tester")
    b = client.get("/api/briefing").json()
    assert [a["ref_id"] for a in b["attention"] if a["kind"] == "promise"]
    assert _audience(b, "promise") == ["you"]


def test_another_persons_stale_decision_is_not_yours_to_reconfirm(client, fresh_db):
    from app.services import collab

    collab.record_decision(
        "Use Postgres", "we use postgres", review_by="2020-01-01", decided_by="mira", actor="mira"
    )
    mine = collab.record_decision(
        "Use SQLite", "we use sqlite", review_by="2020-01-01", decided_by="tester", actor="tester"
    )
    collab.sweep_stale_decisions()

    b = client.get("/api/briefing").json()
    decisions = [a for a in b["attention"] if a["kind"] == "decision"]
    assert [d["ref_id"] for d in decisions] == [mine["id"]]
    # anchored at the row, not the bare page: /charter defaults to the charter
    # category, and this decision has none
    assert decisions[0]["link"] == f"/charter#charter-entry-{mine['id']}"


def test_a_shared_queue_says_it_is_shared(client):
    from app.services import review

    client.post("/api/intake", json={"title": "Need a thing"})
    review.propose_change("note", "create", {"topic": "t", "content": "c"}, actor="agent-x")
    b = client.get("/api/briefing").json()
    assert _audience(b, "intake") == ["team"]
    assert _audience(b, "proposal") == ["team"]


def test_a_proposal_you_asked_for_is_addressed_to_you(client):
    from app.services import review

    review.propose_change(
        "note", "create", {"topic": "t", "content": "c"}, actor="agent-x", requested_by="tester"
    )
    b = client.get("/api/briefing").json()
    assert _audience(b, "proposal") == ["you"]


def test_a_team_notification_survives_one_readers_dismissal(client, fresh_db):
    """A 'team' row is ONE record. Before `notification_reads`, the first
    person to press dismiss cleared the announcement for the whole roster."""
    from app.services import notifications

    notifications.notify("team", "the build is red", tier="digest")

    seen = notifications.list_notifications("tester")
    assert any("the build is red" in n["message"] for n in seen)
    notifications.mark_read("tester", seen[0]["id"])
    assert not any(
        "the build is red" in n["message"] for n in notifications.list_notifications("tester")
    )

    # mira never dismissed it
    assert any("the build is red" in n["message"] for n in notifications.list_notifications("mira"))
