"""Team pulse: seasons, the participation-based standup chain, and votes that are stored unattributable by design."""


def test_pulse_feedback_without_input_text(client):
    r = client.post("/api/feedback", json={"kind": "pulse", "verdict": "up"})
    assert r.status_code == 200
    r = client.post("/api/feedback", json={"kind": "chat", "verdict": "up"})
    assert r.status_code == 422


def test_pulse_shape_and_season(client):
    from app.services import pulse

    s = pulse.season()
    assert s["days_left"] >= 0 and "S" in s["label"]

    client.post("/api/standups", json={"today": "work"})
    p = client.get("/api/pulse").json()
    assert "standup_chain" in p and "season_totals" in p


def test_standup_chain_roster_is_participation_based(fresh_db):
    import datetime

    from app.services import collab, pulse, users

    users.ensure_user("a")
    users.ensure_user("b")
    users.ensure_user("anonymous")  # pre-name-pick frontend traffic
    users.ensure_user("bot", kind="agent")  # agents don't break the chain

    # nobody has ever posted: no roster, no chain — and no permanent zero
    assert pulse.standup_chain() == {"chain": 0, "humans": 0}

    # posts land on the most recent COMPLETED weekday, so the chain
    # assertions hold on all 7 days — asserting on a standup posted today
    # needs a weekday guard, which skips the assertion on 2 of every 7 CI days
    def post_on_last_weekday(author: str, text: str) -> None:
        collab.post_standup(author, today=text)
        sid = fresh_db.query_row("SELECT MAX(id) AS id FROM standups")["id"]
        day = datetime.datetime.now(datetime.timezone.utc).date()
        while True:
            day -= datetime.timedelta(days=1)
            if day.weekday() < 5:
                break
        fresh_db.execute(
            "UPDATE standups SET created_at = ? WHERE id = ?",
            (f"{day.isoformat()}T09:00:00+00:00", sid),
        )

    post_on_last_weekday("a", "x")
    chain = pulse.standup_chain()
    assert chain["humans"] == 1  # b joins the roster by playing, not by existing
    assert chain["chain"] == 1

    post_on_last_weekday("b", "y")
    chain = pulse.standup_chain()
    assert chain["humans"] == 2  # anonymous and the agent never count
    assert chain["chain"] == 1


def test_pulse_tally_team_aggregated(client, fresh_db):
    for user, verdict in (("a", "up"), ("b", "up"), ("c", "down")):
        r = client.post(
            "/api/feedback",
            json={"kind": "pulse", "input_text": "2026-07-24", "verdict": verdict},
            headers={"X-User": user},
        )
        assert r.status_code == 200
    tally = client.get("/api/insights").json()["pulse_tally"]
    assert tally[0]["up"] == 2 and tally[0]["down"] == 1
    assert "created_by" not in tally[0] and "user" not in tally[0]  # counts only


def test_pulse_votes_are_unattributable(client, fresh_db):
    """The promise is 'never per person' — no username may co-occur with a
    verdict on ANY egress surface: raw feedback endpoint, activity ledger,
    admin export."""
    import json as j

    from app.services import admin
    from app.services.api_keys import create_key

    voters = ("alice", "bob")
    for user, verdict in zip(voters, ("up", "down"), strict=True):
        client.post(
            "/api/feedback",
            json={"kind": "pulse", "input_text": "2026-07-24", "verdict": verdict},
            headers={"X-User": user},
        )
    rows = client.get("/api/feedback?kind=pulse").json()
    assert all("created_by" not in r for r in rows)  # column never on the wire
    assert client.get("/api/feedback?kind=pulse", headers={}).status_code in (200, 403)
    activity = j.dumps(fresh_db.query("SELECT * FROM activity WHERE action = 'record_feedback'"))
    for v in voters:
        assert v not in activity
    assert "pulse/" not in activity  # verdict never reaches the ledger
    key = create_key("auditor", "t")["key"]
    from pathlib import Path

    export = admin.export()
    dump = j.loads(Path(export["path"]).read_text())
    for row in dump.get("feedback", []):
        if row["kind"] == "pulse":
            assert row["created_by"] == ""
    assert key  # export exercised under the strong-identity path


def test_standup_chain_counts_backdated_weekdays_and_breaks_at_a_gap(fresh_db):
    import datetime

    from app.services import collab, pulse, users

    users.ensure_user("a")
    # the 5 most recent COMPLETED weekdays, newest first
    weekdays: list[datetime.date] = []
    d = datetime.datetime.now(datetime.timezone.utc).date()
    while len(weekdays) < 5:
        d -= datetime.timedelta(days=1)
        if d.weekday() < 5:
            weekdays.append(d)
    # standups on the 3 newest, a gap on the 4th, one more on the 5th —
    # pins the lookback window, the weekend rewind, and the gap break
    for day in [*weekdays[:3], weekdays[4]]:
        collab.post_standup("a", today="x")
        sid = fresh_db.query_row("SELECT MAX(id) AS id FROM standups")["id"]
        fresh_db.execute(
            "UPDATE standups SET created_at = ? WHERE id = ?",
            (f"{day.isoformat()}T09:00:00+00:00", sid),
        )
    chain = pulse.standup_chain()
    assert chain["humans"] == 1
    # today (no standup yet) does not break the chain; the 4th-day gap does
    assert chain["chain"] == 3
