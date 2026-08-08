"""GET /api/tasks/{id} — the side peek's read.

Every reference to a task in the product (My Day attention items, /ask
citations, activity rows) named a row and linked to the top of a
thirteen-section page. This endpoint is what gives them somewhere to land, so
its scope behavior has to match every other addressed read."""

from app.services import scope, work


def test_it_carries_the_joined_names(fresh_db):
    """The peek states the task's place in the portfolio. Fetching the
    milestone and engagement separately would be three requests for one panel."""
    ms = work.create_milestone("Cutover", actor="tester")
    task = work.create_task("flip the DNS", milestone_id=ms["id"], actor="tester")

    got = work.get_task(task["id"])
    assert got["milestone_title"] == "Cutover"
    assert got["title"] == "flip the DNS"


def test_a_missing_task_raises_missing(fresh_db):
    try:
        work.get_task(9999)
        raise AssertionError("expected a refusal")
    except ValueError as exc:
        assert "9999" in str(exc)


def test_an_unreadable_task_answers_exactly_like_an_absent_one(fresh_db):
    """Task ids are sequential integers. If "not yours" and "does not exist"
    answered differently, a caller could walk the ids and learn which exist —
    the attack services/scope.py::Viewer names."""
    private = work.create_task("salary planning", actor="ava", visibility="private")

    absent, unreadable = None, None
    try:
        work.get_task(9999, scope.Viewer.for_actor("tester"))
    except ValueError as exc:
        absent = str(exc)
    try:
        work.get_task(private["id"], scope.Viewer.for_actor("tester"))
    except ValueError as exc:
        unreadable = str(exc).replace(str(private["id"]), "9999")
    assert absent == unreadable


def test_the_route_answers_404_for_both(client):
    """Same rule at the HTTP boundary, where a 403 would leak existence.

    BOTH cases, at the boundary: the unreadable one is the case that leaks,
    and testing only the absent id passes even if the route stops scoping."""
    private = work.create_task("salary planning", actor="ava", visibility="private")
    absent = client.get("/api/tasks/9999")
    unreadable = client.get(f"/api/tasks/{private['id']}")
    assert absent.status_code == unreadable.status_code == 404
    # and the same sentence, with only the id differing
    assert absent.json()["detail"].replace("9999", "X") == unreadable.json()["detail"].replace(
        str(private["id"]), "X"
    )


def test_the_author_still_reads_their_own_private_task(fresh_db):
    """The peek must not hide a row from the one person it belongs to."""
    private = work.create_task("salary planning", actor="ava", visibility="private")
    got = work.get_task(private["id"], scope.Viewer.for_actor("ava"))
    assert got["title"] == "salary planning"


def test_a_private_milestone_title_never_rides_a_readable_task(fresh_db):
    """The join is the leak path list_tasks_joined already records: filtered
    only on the task, a private milestone's title travels beside a workspace
    task, inside a panel built to be read."""
    # written by ava, who can read both. create_task already refuses a
    # milestone the ACTOR cannot see, so the leak this pins is at READ time:
    # a workspace task whose parent is private, fetched by somebody else.
    ms = work.create_milestone("Layoff planning", actor="ava", visibility="private")
    task = work.create_task("book the room", milestone_id=ms["id"], actor="ava")

    got = work.get_task(task["id"], scope.Viewer.for_actor("tester"))
    assert got["title"] == "book the room"
    assert got["milestone_title"] is None
