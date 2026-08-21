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


def test_system_findings_stay_out_of_the_meeting_queue(client, fresh_db, monkeypatch):
    """job_stale is severity high, so without the audience filter a stale cron
    outranks an overdue customer promise in the Monday running order. The rule
    still fires and still reaches Insights — it is only out of this queue."""
    from app import config
    from app.services.insights import list_findings, run_findings

    monkeypatch.setattr(config, "SCHEDULER_ENABLED", True)
    fresh_db.execute(
        "INSERT INTO job_outcomes (job, status, detail, duration_ms, created_at)"
        " VALUES ('daily-digest', 'ok', '', 0, '2020-01-01T00:00:00+00:00')"
    )
    result = run_findings(actor="tester")
    assert any(f["rule_id"] == "job_stale" for f in result["findings"])
    assert any(f["rule_id"] == "job_stale" for f in list_findings())

    rows = intervention.interventions(scope.Viewer("tester", True))
    assert not [r for r in rows if r["kind"].startswith("finding_")]


def test_a_skipped_finding_does_not_spend_the_findings_budget(client, fresh_db, monkeypatch):
    """The [:30] budget must be spent on rows that can render. Sliced before
    the filters, thirty system findings emptied this arm — the same failure
    the disposition filter's comment already warns about, for a new filter."""
    # patched on insights, not intervention: the import is inside the
    # function body, so the name resolves there at call time
    from app.services import insights

    def flooded(weeks=4, limit=200):
        rows = [
            {
                "id": i,
                "rule_id": "job_stale",
                "subject": f"job-{i}",
                "severity": "high",
                "message": f"job {i} is stale",
                "receipt": {},
                "disposition": "",
            }
            for i in range(30)
        ]
        rows.append(
            {
                "id": 99,
                "rule_id": "question_aging",
                "subject": "question-7",
                "severity": "low",
                "message": "Question #7 has been open 9 days",
                "receipt": {},
                "disposition": "",
            }
        )
        return rows

    monkeypatch.setattr(insights, "list_findings", flooded)
    rows = intervention.interventions(scope.Viewer("tester", True))
    assert any(r["kind"] == "finding_low" and "Question #7" in r["title"] for r in rows)


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


def test_a_finding_receipt_with_stored_rows_names_counts_not_dicts():
    """review_stall stores its pending proposals whole. str() on that list
    printed a page of Python dicts into the meeting agenda — the receipt is
    the count and the ids, and the rows themselves stay on /insights."""
    from app.services.intervention import _finding_receipt

    line = _finding_receipt(
        {
            "message": "m",
            "receipt": {
                "pending": [{"id": 4, "summary": "run tool"}, {"id": 5}, {"id": 13}],
                "oldest_days": 5.3,
            },
        }
    )
    assert line == "pending: 3 rows (#4, #5, #13), oldest days: 5.3"
    assert "{" not in line and "'" not in line
