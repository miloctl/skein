"""What a playbook said would happen, against what did.

Playbooks never learned. The template said six weeks, every engagement ran
nine, and nothing carried that back to the YAML — because by the time an
engagement closes the plan it started with is gone. Milestones have moved,
tasks have been added and deleted, and a cancelled ritual leaves no row at
all.
"""

from datetime import date, timedelta

from app import db
from app.services import engagements, playbooks, review, schedule, scope, work


def _born(name: str = "Alpha rollout", slug: str = "incident") -> dict:
    """Named, not "whichever sorts first" — adding a playbook must not quietly
    re-point fifteen tests at a different plan."""
    return playbooks.instantiate(slug, name, lead="ava", actor="ava")


def test_the_plan_is_snapshot_at_kickoff(client):
    made = _born()
    plan = playbooks.snapshot_for(made["engagement"]["id"])
    assert plan["milestones"] and plan["tasks"]
    # ids, not only titles: a task renamed between kickoff and close is the
    # same task, and a title-keyed diff calls it one removed and one added
    assert {m["id"] for m in plan["milestones"]} == {m["id"] for m in made["milestones"]}
    assert {t["id"] for t in plan["tasks"]} == {t["id"] for t in made["tasks"]}


def test_an_engagement_made_by_hand_has_no_snapshot(client):
    eng = engagements.create_engagement(name="Hand made", actor="ava")
    assert playbooks.snapshot_for(eng["id"]) == {}
    assert playbooks.close_out_diff(eng["id"]) == {}


def test_the_diff_sees_a_slipped_milestone(client):
    made = _born()
    mil = made["milestones"][0]
    moved = (db.today() + timedelta(days=90)).isoformat()
    work.update_milestone(mil["id"], due_date=moved, actor="ava")

    diff = playbooks.close_out_diff(made["engagement"]["id"])
    slip = next(s for s in diff["slipped"] if s["title"] == mil["title"])
    assert slip["days"] > 0
    assert slip["to"] == moved


def test_the_diff_sees_work_the_playbook_never_named(client):
    """`milestone_id=`, NOT `engagement_id=` — that is how _instantiate and the
    UI both attach a task, and `work.create_task` stores `engagement_id or
    None`. An engagement_id-only query matched nothing, so this clause (the
    only one that names titles to add to the YAML) silently never fired."""
    made = _born()
    eid = made["engagement"]["id"]
    work.create_task(
        "chase the vendor contract", milestone_id=made["milestones"][0]["id"], actor="ava"
    )
    assert (
        db.query_one("SELECT engagement_id FROM tasks WHERE title = 'chase the vendor contract'")[
            "engagement_id"
        ]
        is None
    ), "the fixture stopped exercising the link path it exists for"
    assert "chase the vendor contract" in playbooks.close_out_diff(eid)["added_tasks"]


def test_a_renamed_planned_task_is_not_new_work(client):
    """Keyed on id, like the snapshot. A title-keyed filter reports a renamed
    planned task as work outside the playbook and recommends adding it to the
    YAML it is already in."""
    made = _born()
    work.update_task(made["tasks"][0]["id"], title="renamed by the team", actor="ava")
    assert playbooks.close_out_diff(made["engagement"]["id"])["added_tasks"] == []


def test_the_diff_sees_a_ritual_that_never_happened(client):
    """A cancelled event is DELETED (services/schedule.py::cancel_event), so a
    missing id is the only evidence the ceremony did not happen."""
    made = _born()
    assert made["events"], "the incident playbook lost its rituals — this test pins nothing"
    evt = made["events"][0]
    schedule.cancel_event(evt["id"], actor="ava")
    diff = playbooks.close_out_diff(made["engagement"]["id"])
    assert evt["title"] in diff["skipped_rituals"]


def test_closing_drafts_the_lesson_as_a_proposal(client):
    """Never a direct write. The diff is arithmetic, but the lesson it implies
    is a judgment only somebody who ran the engagement can make."""
    made = _born()
    eid = made["engagement"]["id"]
    work.update_milestone(
        made["milestones"][0]["id"],
        due_date=(db.today() + timedelta(days=90)).isoformat(),
        actor="ava",
    )
    before = len(engagements.list_lessons())

    engagements.update_engagement(eid, status="closed", conclusion="achieved", actor="ava")

    assert len(engagements.list_lessons()) == before, "a lesson was written without a verdict"
    pending = [p for p in review.list_changes("pending") if p["entity"] == "lesson"]
    assert pending, "closing a playbook-born engagement drafted nothing"
    payload = pending[-1]["payload"]
    # the exact number, computed from the snapshot rather than guessed: the
    # playbook's own due_after_days decides what "moved to today+90" slipped by
    planned = playbooks.snapshot_for(eid)["milestones"][0]["due_date"]
    days = (db.today() + timedelta(days=90) - date.fromisoformat(planned)).days
    assert f"the largest by {days} days" in payload["lesson"]
    assert payload["project_class"] == "incident"


def test_failed_optional_lesson_rolls_back_its_whole_unit(client, monkeypatch):
    """The engagement close stays valid when the optional lesson fails.

    The proposal and its notice must not survive the caught failure.
    """
    made = _born()
    eid = made["engagement"]["id"]
    work.update_milestone(
        made["milestones"][0]["id"],
        due_date=(db.today() + timedelta(days=90)).isoformat(),
        actor="ava",
    )
    original_log_activity = db.log_activity

    def fail_after_proposal(actor, action, detail="", **kwargs):
        if action == "playbook_closeout":
            raise RuntimeError("forced close-out audit failure")
        return original_log_activity(actor, action, detail, **kwargs)

    monkeypatch.setattr(db, "log_activity", fail_after_proposal)

    result = engagements.update_engagement(eid, status="closed", conclusion="partial", actor="ava")

    assert result["lesson_proposal_id"] == 0
    assert db.query_one("SELECT status FROM engagements WHERE id = ?", (eid,))["status"] == "closed"
    assert not db.query_one(
        "SELECT id FROM pending_changes WHERE entity = 'lesson' AND payload LIKE ?",
        (f'%"engagement_id": {eid},%',),
    )
    assert not db.query_one(
        "SELECT id FROM notifications WHERE message LIKE 'Review needed:%Close-out lesson%'"
    )
    assert not db.query_one(
        "SELECT id FROM activity WHERE action = 'propose_change' AND detail LIKE '%lesson%'"
    )


def test_an_engagement_that_went_to_plan_drafts_nothing(client):
    """ "It went to plan" teaches the next reader nothing and costs a reviewer
    a verdict. Every planned task done, no date moved, no ritual dropped."""
    made = _born()
    for t in made["tasks"]:
        work.update_task(t["id"], status="done", actor="ava")
    engagements.update_engagement(
        made["engagement"]["id"], status="closed", conclusion="achieved", actor="ava"
    )
    assert not [p for p in review.list_changes("pending") if p["entity"] == "lesson"]


def test_a_draft_with_no_action_in_it_is_not_filed(client):
    """An abandoned engagement leaves every planned task unfinished. A lesson
    that says so and nothing else restates the engagement's own conclusion,
    and every draft costs a verdict from the team's scarcest resource."""
    made = _born()
    engagements.update_engagement(
        made["engagement"]["id"], status="closed", conclusion="stopped", actor="ava"
    )
    assert not [p for p in review.list_changes("pending") if p["entity"] == "lesson"]


def test_slip_is_measured_from_what_HAPPENED_not_from_replanning(client):
    """The motivating case: a team that never re-dates a milestone and lands
    weeks late. Comparing the due date now against the due date at kickoff
    sees nothing there — it rewards good date hygiene with a lesson and bad
    date hygiene with silence."""
    made = _born()
    mil = made["milestones"][0]
    planned = playbooks.snapshot_for(made["engagement"]["id"])["milestones"][0]["due_date"]
    late = (date.fromisoformat(planned) + timedelta(days=30)).isoformat()
    # done, late, and the due date never touched
    work.update_milestone(mil["id"], status="done", actor="ava")
    # a full timestamp, which is what db.now() stores — a bare date passes only
    # because of the [:10] slice and would not catch a change to that slice
    db.execute(
        "UPDATE milestones SET completed_at = ? WHERE id = ?", (f"{late}T14:03:00+00:00", mil["id"])
    )

    diff = playbooks.close_out_diff(made["engagement"]["id"])
    slip = next(s for s in diff["slipped"] if s["title"] == mil["title"])
    assert slip["days"] == 30
    assert slip["basis"] == "finished"
    assert "landed late" in playbooks._variance_lesson(diff, "Alpha")[0]


def test_an_engagement_with_no_snapshot_closes_exactly_as_before(client):
    eng = engagements.create_engagement(name="Hand made", actor="ava")
    engagements.update_engagement(eng["id"], status="closed", conclusion="achieved", actor="ava")
    assert not [p for p in review.list_changes("pending") if p["entity"] == "lesson"]
    assert db.query_one("SELECT status FROM engagements WHERE id = ?", (eng["id"],))["status"] == (
        "closed"
    )


def test_a_missing_snapshot_file_is_not_a_500_at_close(client):
    """data/artifacts/ is gitignored, so a restore-from-backup brings the
    database back without the files. A close must not fail on that."""
    from pathlib import Path

    made = _born()
    eid = made["engagement"]["id"]
    row = db.query_one(
        "SELECT path FROM artifacts WHERE engagement_id = ? AND kind = 'plan-snapshot'", (eid,)
    )
    Path(row["path"]).unlink()
    assert playbooks.snapshot_for(eid) == {}
    engagements.update_engagement(eid, status="closed", conclusion="achieved", actor="ava")
    assert db.query_one("SELECT status FROM engagements WHERE id = ?", (eid,))["status"] == "closed"


def test_the_drafted_lesson_computes_its_own_agreement(client):
    """This text lands in a kickoff note the NEXT team reads cold. "1 task were
    added" is the sentence a reader stops trusting the number in."""
    made = _born()
    eid = made["engagement"]["id"]
    work.create_task("chase the vendor", engagement_id=eid, actor="ava")
    one = playbooks._variance_lesson(playbooks.close_out_diff(eid), "Alpha")[0]
    assert "1 task outside the playbook was added" in one

    work.create_task("second extra", engagement_id=eid, actor="ava")
    two = playbooks._variance_lesson(playbooks.close_out_diff(eid), "Alpha")[0]
    assert "2 tasks outside the playbook were added" in two
    # sentences, not a semicolon chain — the wording standard bans the semicolon
    assert ";" not in two


def test_an_approved_lesson_reaches_the_next_kickoff(client):
    """The whole point of R6. A lesson the reviewer approves has to arrive at
    the next engagement of the same class, or the loop dead-ends at a row
    nobody reads."""
    made = _born("Incident one")
    eid = made["engagement"]["id"]
    work.update_milestone(
        made["milestones"][0]["id"],
        due_date=(db.today() + timedelta(days=21)).isoformat(),
        actor="ava",
    )
    engagements.update_engagement(eid, status="closed", conclusion="partial", actor="ava")
    prop = [p for p in review.list_changes("pending") if p["entity"] == "lesson"][-1]
    review.approve_change(prop["id"], actor="ava")

    _born("Incident two")
    note = db.query_one("SELECT * FROM notes WHERE topic = ?", ("kickoff-lessons-Incident two",))
    assert note, "the next kickoff got no lessons note"
    assert "Incident one ran against" in note["content"]


def test_the_snapshot_is_not_a_report(client):
    """Work → Reports renders every row it gets through a MARKDOWN reader and
    its subtitle promises digests and briefs. A raw JSON plan at the top of
    that page is machinery leaking onto a reader's surface."""
    from app.services import handoff

    made = _born()
    eid = made["engagement"]["id"]
    assert db.query_one(
        "SELECT id FROM artifacts WHERE engagement_id = ? AND kind = 'plan-snapshot'", (eid,)
    ), "the snapshot row must still exist — close_out_diff reads it"
    assert not [a for a in handoff.list_artifacts() if a["kind"] == "plan-snapshot"]
    assert not [
        a for a in handoff.list_artifacts(engagement_id=eid) if a["kind"] == "plan-snapshot"
    ]


def test_a_plan_the_caller_cannot_fully_read_yields_no_diff(client):
    """A hidden row is indistinguishable from a deleted one. Reporting it as
    dropped would tell a crew their finished work was abandoned, so the whole
    diff is refused rather than answered from half a plan."""
    made = _born()
    eid = made["engagement"]["id"]
    db.execute(
        "UPDATE milestones SET visibility = 'private' WHERE id = ?", (made["milestones"][0]["id"],)
    )
    assert playbooks.close_out_diff(eid, scope.Viewer("someone-else", True)) == {}


def test_a_hidden_task_or_ritual_refuses_the_whole_diff(client):
    """The titles in a diff come from the SNAPSHOT, which passed through no
    filter. Reporting a hidden row as dropped both publishes it and says the
    opposite of the truth — the task exists, the meeting happened."""
    made = _born()
    eid = made["engagement"]["id"]
    other = scope.Viewer("someone-else", True)

    db.execute("UPDATE tasks SET visibility = 'private' WHERE id = ?", (made["tasks"][0]["id"],))
    assert playbooks.close_out_diff(eid, other) == {}
    db.execute("UPDATE tasks SET visibility = 'workspace' WHERE id = ?", (made["tasks"][0]["id"],))

    db.execute("UPDATE events SET visibility = 'private' WHERE id = ?", (made["events"][0]["id"],))
    assert playbooks.close_out_diff(eid, other) == {}


def test_a_deleted_row_is_still_reported(client):
    """The refusal above must not swallow the real signal: a task actually
    deleted, and a ritual actually cancelled, are what the diff exists for."""
    made = _born()
    eid = made["engagement"]["id"]
    db.execute("DELETE FROM tasks WHERE id = ?", (made["tasks"][0]["id"],))
    schedule.cancel_event(made["events"][0]["id"], actor="ava")
    diff = playbooks.close_out_diff(eid)
    assert made["tasks"][0]["title"] in diff["dropped_tasks"]
    assert made["events"][0]["title"] in diff["skipped_rituals"]


def test_a_scoped_engagement_drafts_no_lesson_and_says_so(client):
    """close_out_diff reads with NOBODY for the lesson path, so a draft
    assembled from crew rows would need a privilege the reviewer lacks. The
    panel has to know, or it promises a lesson nobody gets."""
    made = _born()
    eid = made["engagement"]["id"]
    work.update_milestone(
        made["milestones"][0]["id"],
        due_date=(db.today() + timedelta(days=90)).isoformat(),
        actor="ava",
    )
    # Direct SQL creates the legacy invalid audience relationship that the
    # close-out reader must still handle. The supported milestone writer now
    # refuses this state.
    db.execute("UPDATE engagements SET visibility = 'private' WHERE id = ?", (eid,))
    assert playbooks.close_out_diff(eid, scope.Viewer("ava", True))["drafts_lesson"] is False
    engagements.update_engagement(eid, status="closed", conclusion="partial", actor="ava")
    assert not [p for p in review.list_changes("pending") if p["entity"] == "lesson"]


def test_reopening_and_closing_again_files_one_lesson(client):
    """Two approvable copies of one lesson is two verdicts and two lines in
    the next kickoff note."""
    made = _born()
    eid = made["engagement"]["id"]
    work.update_milestone(
        made["milestones"][0]["id"],
        due_date=(db.today() + timedelta(days=90)).isoformat(),
        actor="ava",
    )
    engagements.update_engagement(eid, status="closed", conclusion="partial", actor="ava")
    engagements.update_engagement(eid, status="active", actor="ava")
    engagements.update_engagement(eid, status="closed", conclusion="partial", actor="ava")
    assert len([p for p in review.list_changes("pending") if p["entity"] == "lesson"]) == 1


def test_a_snapshot_of_the_wrong_shape_is_no_snapshot(client):
    """The file carries no version field, so the first change to the plan
    format would otherwise turn every older engagement's close-out into a
    permanent 500."""
    from pathlib import Path

    made = _born()
    eid = made["engagement"]["id"]
    path = Path(
        db.query_one(
            "SELECT path FROM artifacts WHERE engagement_id = ? AND kind = 'plan-snapshot'", (eid,)
        )["path"]
    )
    for junk in ("[1,2]", "123", '{"playbook": "incident"}', "not json at all"):
        path.write_text(junk, encoding="utf-8")
        assert playbooks.snapshot_for(eid) == {}, junk
        assert playbooks.close_out_diff(eid) == {}, junk


def test_a_truncated_list_never_contradicts_the_count_beside_it(client):
    """Any string carrying a number has to be exact. A bare [:3] prints three
    names next to the word "seven"."""
    made = _born()
    eid = made["engagement"]["id"]
    for i in range(7):
        work.create_task(f"extra {i}", milestone_id=made["milestones"][0]["id"], actor="ava")
    fix = playbooks._variance_lesson(playbooks.close_out_diff(eid), "Alpha")[1]
    assert "and 4 more" in fix


def test_a_milestone_delivered_early_is_not_slip(client):
    """A finish date settles the question either way. Reported as slip, the
    drafted lesson pads the playbook for work that came in ahead — the
    headline feature teaching the template the opposite of what happened."""
    made = _born()
    mil = made["milestones"][0]
    planned = playbooks.snapshot_for(made["engagement"]["id"])["milestones"][0]["due_date"]
    # the team moved the date out, then delivered before the ORIGINAL date
    work.update_milestone(
        mil["id"],
        due_date=(date.fromisoformat(planned) + timedelta(days=9)).isoformat(),
        status="done",
        actor="ava",
    )
    db.execute(
        "UPDATE milestones SET completed_at = ? WHERE id = ?",
        (
            f"{(date.fromisoformat(planned) - timedelta(days=6)).isoformat()}T09:00:00+00:00",
            mil["id"],
        ),
    )
    diff = playbooks.close_out_diff(made["engagement"]["id"])
    assert not [s for s in diff["slipped"] if s["title"] == mil["title"]], diff["slipped"]


def test_the_panel_does_not_promise_a_lesson_it_will_not_file(client):
    """The close-out control says "closing drafts a lesson from this". With
    the flag gated on tier alone it said that for a diff with no fixable
    variance, filed nothing, and sent the reader to an empty queue."""
    made = _born()
    eid = made["engagement"]["id"]
    # unfinished work only — real variance, but nothing to change in the YAML
    assert playbooks.close_out_diff(eid)["drafts_lesson"] is False
    engagements.update_engagement(eid, status="closed", conclusion="partial", actor="ava")
    assert not [p for p in review.list_changes("pending") if p["entity"] == "lesson"]

    made2 = _born("Second one")
    work.update_milestone(
        made2["milestones"][0]["id"],
        due_date=(db.today() + timedelta(days=90)).isoformat(),
        actor="ava",
    )
    assert playbooks.close_out_diff(made2["engagement"]["id"])["drafts_lesson"] is True
