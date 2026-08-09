"""Promises made TO the team.

The ledger recorded one direction. A manager's week is full of the other one
— "legal will send the redlines Thursday" — and those lived in memory or
nowhere. The ones that go quiet are the ones that hurt, and the person waiting
is usually the person least able to escalate.
"""

from datetime import UTC, datetime, timedelta

from app import db
from app.services import promises


def _overdue(client, text: str, who: str = "legal", days: int = 3) -> int:
    due = (db.today() - timedelta(days=days)).isoformat()
    return promises.add_promise(text, to_whom=who, due_date=due, direction="received", actor="ava")[
        "id"
    ]


def test_awaiting_capture_files_a_received_promise(client):
    got = client.post(
        "/api/capture", json={"text": "awaiting: legal — the redlines by 2026-09-01"}
    ).json()
    assert got["kind"] == "awaiting"
    row = db.query_one("SELECT * FROM promises WHERE id = ?", (got["id"],))
    assert row["direction"] == "received"
    assert row["due_date"] == "2026-09-01"
    # the date and the party are lifted out of the sentence, not left in it
    assert "2026-09-01" not in row["promise"]
    assert row["to_whom"] == "legal"


def test_the_party_can_be_more_than_one_word(client):
    """A vendor is "acme corp", not "acme" — and the outside parties with
    multi-word names are the ones services/stakeholders.py exists to gather.
    A one-word rule dropped `to_whom` on every one of them."""
    got = client.post(
        "/api/capture", json={"text": "awaiting: acme corp — the signed SOW by 2026-09-01"}
    ).json()
    row = db.query_one("SELECT * FROM promises WHERE id = ?", (got["id"],))
    assert row["to_whom"] == "acme corp"
    assert row["promise"] == "the signed SOW"


def test_a_dash_inside_a_sentence_is_not_a_party(client):
    """ "the redlines — soon" is one thought. Splitting it files a promise
    against a party called "the redlines", and the chaser nudges about it."""
    got = client.post("/api/capture", json={"text": "awaiting: the redlines — soon"}).json()
    row = db.query_one("SELECT * FROM promises WHERE id = ?", (got["id"],))
    assert row["to_whom"] == ""
    assert row["promise"] == "the redlines — soon"


def test_a_promise_the_team_made_is_still_the_default(client):
    """Every row that existed before migration 007 means what it meant. The
    default is 'given', so the exec readout and the findings rules read the
    same set they always did."""
    got = client.post("/api/capture", json={"text": "promised: ship the summary"}).json()
    row = db.query_one("SELECT * FROM promises WHERE id = ?", (got["id"],))
    assert row["direction"] == "given"


def test_the_chaser_nudges_the_person_waiting(client):
    pid = _overdue(client, "the signed contract")
    out = promises.chase_received()
    assert out["nudged"] == 1
    assert out["escalated"] == 0
    notes = db.query("SELECT * FROM notifications WHERE user = 'ava'")
    assert any("Still open with legal" in n["message"] for n in notes)
    assert db.query_one("SELECT nudge_count FROM promises WHERE id = ?", (pid,))["nudge_count"] == 1


def test_the_chaser_stays_quiet_inside_a_cycle(client):
    """The job runs hourly so a due date is noticed the day it passes. A nudge
    every hour is a nudge nobody reads."""
    _overdue(client, "the signed contract")
    assert promises.chase_received()["nudged"] == 1
    assert promises.chase_received()["nudged"] == 0


def test_two_silent_cycles_reach_the_team(client):
    """The escalation is the point: the person waiting has chased twice and
    nothing moved, so it stops being their problem alone."""
    pid = _overdue(client, "the signed contract")
    promises.chase_received()
    # a cycle later, still unsettled
    db.execute(
        "UPDATE promises SET last_nudged_at = ? WHERE id = ?",
        ((datetime.now(UTC) - timedelta(hours=promises.NUDGE_CYCLE_HOURS + 1)).isoformat(), pid),
    )
    out = promises.chase_received()
    assert out["escalated"] == 1
    team = db.query("SELECT * FROM notifications WHERE user = 'team'")
    assert any("overdue and unanswered" in n["message"] for n in team)

    # a third cycle, and a fourth: the team hears once
    for _ in range(2):
        db.execute(
            "UPDATE promises SET last_nudged_at = ? WHERE id = ?",
            (
                (datetime.now(UTC) - timedelta(hours=promises.NUDGE_CYCLE_HOURS + 1)).isoformat(),
                pid,
            ),
        )
        assert promises.chase_received()["escalated"] == 0
    assert len(db.query("SELECT * FROM notifications WHERE user = 'team'")) == 1


def test_the_team_escalation_names_nobody(client):
    """`to_whom` is free text and nothing stops it being a teammate. The
    team-wide message reaches every viewer, so naming the party there would
    publish a named person's missed past commitment to the whole roster —
    which is what services/forge.py refuses for the same reason."""
    pid = _overdue(client, "the migration doc", who="dana")
    promises.chase_received()
    db.execute(
        "UPDATE promises SET last_nudged_at = ? WHERE id = ?",
        ((datetime.now(UTC) - timedelta(hours=promises.NUDGE_CYCLE_HOURS + 1)).isoformat(), pid),
    )
    promises.chase_received()

    team = " ".join(n["message"] for n in db.query("SELECT * FROM notifications WHERE user='team'"))
    assert "the migration doc" in team, "the escalation must still say what is overdue"
    assert "dana" not in team

    # the PERSONAL nudge goes to the row's own author and still names the
    # party — that reader is the one chasing, and needs to know who to chase
    mine = " ".join(n["message"] for n in db.query("SELECT * FROM notifications WHERE user='ava'"))
    assert "dana" in mine


def test_a_renegotiated_due_date_starts_the_chase_over(client):
    """Moving the date IS the answer the nudge asked for. Carrying the old
    count forward sends the next chase straight to the team escalation."""
    pid = _overdue(client, "the signed contract")
    promises.chase_received()
    new_due = (db.today() - timedelta(days=1)).isoformat()
    promises.edit_promise(pid, due_date=new_due, actor="ava")
    row = db.query_one("SELECT * FROM promises WHERE id = ?", (pid,))
    assert row["nudge_count"] == 0
    assert row["last_nudged_at"] is None
    assert promises.chase_received()["escalated"] == 0


def test_an_agent_recorded_promise_is_chased_to_the_team(client):
    """An agent reads no notifications and cannot chase anybody, so a nudge
    addressed to one lands nowhere. rituals.py::week_open routes the same
    case the same way."""
    from app.services import users

    users.ensure_user("scout", kind="agent")
    due = (db.today() - timedelta(days=3)).isoformat()
    promises.add_promise(
        "the redlines", to_whom="legal", due_date=due, direction="received", actor="scout"
    )
    assert promises.chase_received()["nudged"] == 1
    assert not db.query("SELECT * FROM notifications WHERE user = 'scout'")
    assert db.query("SELECT * FROM notifications WHERE user = 'team'")


def test_a_settled_promise_is_not_chased(client):
    pid = _overdue(client, "the signed contract")
    promises.update_promise(pid, "kept", actor="ava")
    assert promises.chase_received()["nudged"] == 0


def test_a_received_promise_with_no_date_is_recorded_and_never_nudged(client):
    """The chaser runs on the due date. Recording one without a date is still
    worth doing — it is on the list — but there is nothing to be late for."""
    promises.add_promise("the redlines", to_whom="legal", direction="received", actor="ava")
    assert promises.chase_received()["nudged"] == 0


def test_a_promise_the_team_made_is_never_chased_by_this_rule(client):
    """`given` promises have their own reader (the exec readout and the
    week-close ritual). Chasing them here would nudge the person who OWES the
    promise about their own work, twice."""
    due = (db.today() - timedelta(days=3)).isoformat()
    promises.add_promise("we will ship it", to_whom="Acme", due_date=due, actor="ava")
    assert promises.chase_received()["nudged"] == 0


def test_a_scoped_promise_is_chased_but_never_escalated_to_the_team(client):
    """The personal nudge goes to the row's own author and leaks nothing at
    any tier. The escalation is a team-wide message quoting the promise text,
    so a crew or private row must never reach it — and the author must still
    be chased."""
    pid = _overdue(client, "the confidential redlines")
    db.execute("UPDATE promises SET visibility = 'private' WHERE id = ?", (pid,))
    promises.chase_received()
    db.execute(
        "UPDATE promises SET last_nudged_at = ? WHERE id = ?",
        ((datetime.now(UTC) - timedelta(hours=promises.NUDGE_CYCLE_HOURS + 1)).isoformat(), pid),
    )
    out = promises.chase_received()

    assert out["nudged"] == 1  # the author is still chased
    assert out["escalated"] == 0
    team = db.query("SELECT message FROM notifications WHERE user = 'team'")
    assert not any("confidential" in n["message"] for n in team)


def test_a_received_promise_never_reads_as_one_the_team_made(client):
    """`direction` defaults every OLD row to 'given', but a NEW received row
    also defaults to audience='external' — so every reader that meant "what we
    owe" had to learn the difference. The readout is the one that matters most:
    it leaves, and a promise made TO the team listed under our external
    promises tells a stakeholder the opposite of the truth."""
    from app.services import readout, rituals, schedule

    _overdue(client, "the redlines Acme owes us", who="Acme")
    # a due date inside the readout's 14-day window, or the section it belongs
    # in is empty and the assertion below proves nothing
    soon = (db.today() + timedelta(days=3)).isoformat()
    promises.add_promise("we will ship the summary", to_whom="Acme", due_date=soon, actor="ava")

    doc = readout.exec_readout(actor="ava")["markdown"]
    assert "we will ship the summary" in doc
    assert "redlines Acme owes us" not in doc

    close = rituals.week_close(actor="ava", force=True)["markdown"]
    assert "redlines Acme owes us" not in close

    # the calendar renders inside somebody's mail client beside real meetings,
    # so the two directions cannot share a word
    ics = schedule.ics_feed()
    assert "awaiting: the redlines Acme owes us" in ics
    assert "promised: the redlines Acme owes us" not in ics


def test_my_day_shows_what_you_owe_not_what_you_are_owed(client):
    _overdue(client, "the redlines Acme owes us", who="Acme")
    day = client.get("/api/briefing").json()
    labels = " ".join(str(i.get("label", "")) for i in day["attention"])
    assert "redlines Acme owes us" not in labels
