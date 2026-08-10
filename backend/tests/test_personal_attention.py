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


def test_attention_count_matches_the_page(client):
    """The tab title and My Day's header are read side by side — one on a tab,
    one on the page that tab opens. They come from different queries, so every
    arm of `attention_count` mirrors an arm of `_attention`, cap included."""
    from app.services import collab, promises, review

    # one of every personal kind, plus two shared rows that must not count
    client.post("/api/questions", json={"question": "Who owns infra?", "assigned_to": "tester"})
    client.post("/api/blockers", json={"title": "Stuck", "owner": "tester"})
    promises.add_promise("ship it", to_whom="acme", due_date=_soon(), actor="tester")
    review.propose_change(
        "note", "create", {"topic": "t", "content": "c"}, actor="agent-x", requested_by="tester"
    )
    client.post("/api/intake", json={"title": "somebody else's queue"})
    review.propose_change("note", "create", {"topic": "u", "content": "d"}, actor="agent-x")

    # six stale decisions, to catch a count that does not honor _attention's cap
    for i in range(6):
        collab.record_decision(
            f"call {i}", "we did", review_by="2020-01-01", decided_by="tester", actor="tester"
        )
    collab.sweep_stale_decisions()

    assert (
        client.get("/api/attention").json()["yours"]
        == client.get("/api/briefing").json()["attention_total"]
    )


def test_committed_work_leads_the_day(client):
    """The weekly ritual produces a commitment and a priority-and-date sort
    ignores it: an unplanned high-priority row outranks the work the reader
    told the team they would do.

    `high`, not `urgent`: urgent means "drop what you are doing" and correctly
    sorts above the plan (test_urgent_work_is_not_buried_under_the_plan). This
    pins the ordinary case, which is every other priority."""
    from app import db

    iso = db.today().isocalendar()
    week = f"{iso.year}-W{iso.week:02d}"

    loud = client.post(
        "/api/tasks",
        json={"title": "unplanned but high", "assignee": "tester", "priority": "high"},
    ).json()
    quiet = client.post(
        "/api/tasks",
        json={"title": "what I committed to", "assignee": "tester", "priority": "low"},
    ).json()
    client.patch(f"/api/tasks/{quiet['id']}", json={"committed_week": week})

    tasks = client.get("/api/briefing").json()["your_work"]["tasks"]
    assert [t["id"] for t in tasks][:2] == [quiet["id"], loud["id"]]


def test_urgent_work_is_not_buried_under_the_plan(client):
    """The commitment leads the day, but it is not the FIRST key. `urgent` is
    the word this team reserves for "drop what you are doing", and a Monday
    plan capped at five per person would otherwise put it at position six."""
    for i in range(3):
        t = client.post("/api/tasks", json={"title": f"planned {i}", "assignee": "tester"}).json()
        client.patch(f"/api/tasks/{t['id']}", json={"committed_week": _this_week()})
    urgent = client.post(
        "/api/tasks",
        json={"title": "the plan changed", "assignee": "tester", "priority": "urgent"},
    ).json()

    tasks = client.get("/api/briefing").json()["your_work"]["tasks"]
    assert tasks[0]["id"] == urgent["id"]
    # and the committed work still leads everything that is neither
    assert all(t["committed_week"] == _this_week() for t in tasks[1:4])


def _this_week() -> str:
    from app import db

    iso = db.today().isocalendar()
    return f"{iso.year}-W{iso.week:02d}"
