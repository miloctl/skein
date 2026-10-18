"""A task's links are decided on parent rows that cannot move under the decision.

create_engagement adopts orphan milestones of the same project name, and its
check reads each milestone's tasks first: a milestone whose task already
names another engagement stays an orphan. A task create that lands between
that read and the adopting UPDATE leaves a task whose milestone and
engagement disagree - and its policy decision was made for a milestone with
no project type. The two writes must serialize on the milestone row.
"""

import threading

from app import db
from app.services import engagements, work


def test_a_task_create_cannot_land_inside_a_milestone_adoption(fresh_db, monkeypatch):
    other = engagements.create_engagement("Elsewhere", actor="tester")["id"]
    milestone = work.create_milestone("Launch", project="Apollo", actor="tester")["id"]

    paused, release = threading.Event(), threading.Event()
    real_execute = db.execute

    def pausing_execute(sql, *args, **kwargs):
        if sql.startswith("UPDATE milestones SET engagement_id") and not paused.is_set():
            paused.set()
            release.wait(10)
        return real_execute(sql, *args, **kwargs)

    monkeypatch.setattr(db, "execute", pausing_execute)
    adoption = threading.Thread(
        target=lambda: engagements.create_engagement("Apollo", actor="tester")
    )
    adoption.start()
    assert paused.wait(10), "the adoption never reached its UPDATE"

    outcome = {}

    def create():
        try:
            outcome["task"] = work.create_task(
                "Ship it", milestone_id=milestone, engagement_id=other, actor="tester"
            )
        except ValueError as exc:
            outcome["refused"] = str(exc)

    creator = threading.Thread(target=create)
    creator.start()
    creator.join(1)
    # the adoption holds the milestone, so the create waits for it
    waited = creator.is_alive()
    release.set()
    adoption.join(10)
    creator.join(10)
    assert waited, "the task create did not wait for the adoption"

    adopted = db.query_one("SELECT engagement_id FROM milestones WHERE id = ?", (milestone,))
    assert adopted["engagement_id"] is not None
    assert "task" not in outcome
    assert "same engagement" in outcome["refused"]
