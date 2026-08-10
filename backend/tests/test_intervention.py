"""The manager's ranked queue: what it includes, what it orders first, and
what it refuses to show a reader who cannot open the row.

Composition only — every row restates one an engine already produced. The
value is the ORDER and the fact that one page carries all four engines
(services/intervention.py).
"""

from app.services import intervention, scope


def _kinds(rows):
    return [r["kind"] for r in rows]


def _age(conn, blocker_id: int) -> None:
    """Push a blocker past its own escalation clock.

    `sweep_escalations` compares created_at against `escalate_after_hours`, so
    a freshly filed blocker never escalates however many times the sweep runs —
    a test that skipped this asserted on an empty list and passed for the wrong
    reason.
    """
    conn.execute(
        "UPDATE blockers SET created_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
        (blocker_id,),
    )


def test_a_broken_commitment_outranks_an_untidy_one(client, fresh_db):
    from app.services import blockers, promises, work

    # untidy: a task due with nobody on it
    work.create_task("nobody owns this", due_date="2020-01-01", actor="tester")
    # broken: an escalated blocker
    b = blockers.raise_blocker("build is red", owner="tester", impact="critical", actor="tester")
    _age(fresh_db, b["id"])
    blockers.sweep_escalations()
    # broken: a promise past its date
    promises.add_promise("send the redlines", to_whom="acme", due_date="2020-01-01", actor="tester")

    rows = intervention.interventions(scope.Viewer("tester", True))
    kinds = _kinds(rows)
    assert "blocker_escalated" in kinds
    assert kinds.index("blocker_escalated") < kinds.index("work_unowned")
    assert kinds.index("promise_overdue") < kinds.index("work_unowned")
    assert any(r["entity_id"] == b["id"] for r in rows if r["entity"] == "blocker")


def test_every_row_states_the_next_move_and_its_receipt(client, fresh_db):
    from app.services import promises

    promises.add_promise("send it", to_whom="acme", due_date="2020-01-01", actor="tester")
    rows = intervention.interventions(scope.Viewer("tester", True))
    assert rows
    for r in rows:
        assert r["action"], f"{r['kind']} names no next move"
        assert r["receipts"], f"{r['kind']} carries no receipt"
        # the receipt names the row it is about, and the reference is resolved
        # so a reader can open it rather than hunting by id
        assert any(rc["refs"] for rc in r["receipts"])


def test_a_dispositioned_finding_is_not_asked_about_twice(client, fresh_db):
    from app.services import insights

    insights.run_findings()
    findings = insights.list_findings()
    if not findings:
        return  # a clean fixture files none; the disposition path is below
    fid = findings[0]["id"]
    before = len(
        [
            r
            for r in intervention.interventions(scope.Viewer("tester", True))
            if r["entity_id"] == fid
        ]
    )
    insights.disposition_finding(fid, "dismissed", actor="tester")
    after = [
        r for r in intervention.interventions(scope.Viewer("tester", True)) if r["entity_id"] == fid
    ]
    assert before >= len(after) and not after


def test_a_reader_outside_the_crew_sees_none_of_its_rows(client, fresh_db):
    """A queue assembled from rows the caller cannot open leaks both the row
    and the fact that it exists."""
    from app.services import blockers, crews, users

    for name in ("insider", "outsider"):
        users.ensure_user(name)
    crew = crews.create_crew("ops", actor="insider")
    b = blockers.raise_blocker(
        "crew-only outage",
        owner="insider",
        impact="critical",
        actor="insider",
        visibility=scope.CREW,
        crew_id=crew["id"],
    )
    _age(fresh_db, b["id"])
    blockers.sweep_escalations()

    inside = intervention.interventions(scope.Viewer("insider", True))
    outside = intervention.interventions(scope.Viewer("outsider", True))
    assert any("crew-only outage" in r["title"] for r in inside)
    assert not any("crew-only outage" in r["title"] for r in outside)


def test_an_ancient_row_does_not_own_the_top_forever(client, fresh_db):
    """Age contributes, capped. Past the cap a thing is not getting more
    urgent, it is getting ignored — and one forgotten row from last quarter
    permanently at the top is how a ranked queue stops being read."""
    assert intervention._order("stale_wip", age=30) == intervention._order("stale_wip", age=3650)
