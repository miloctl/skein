"""The Monday cockpit (services/planning.py).

Composition only, so what is worth pinning is not the arithmetic — every
number has its own tests already — but the two properties composition gets
wrong: that the ORDER is preserved, and that the viewer reaches every part."""

from app import db
from app.services import collab, planning, scope, work


def test_it_carries_the_meeting_in_order(fresh_db):
    """The keys are the running order. Reversed, the manager commits the week
    before seeing whether the last one landed, which is the mistake the
    ritual exists to prevent."""
    got = list(planning.cockpit())
    assert got.index("last_week") < got.index("week")
    assert got.index("week") < got.index("intake")
    assert got.index("intake") < got.index("stale_decisions")


def test_the_viewer_reaches_the_scoped_parts(fresh_db):
    """A cockpit that read the workspace tier with a manager's name on it
    would be the one page where crew work leaks — it composes six reads, and
    a viewer dropped in any one of them is invisible in the other five.

    The decision must actually BE stale, and the assertion must be
    two-sided: an earlier version asserted only absence against a list that
    was empty for both viewers, so it passed with the viewer argument
    deleted."""
    from datetime import timedelta

    yesterday = (db.today() - timedelta(days=1)).isoformat()
    collab.record_decision(
        "private one",
        "keep it private",
        actor="ava",
        review_by=yesterday,
        visibility="private",
    )
    collab.record_decision("workspace one", "everyone sees this", actor="ava", review_by=yesterday)
    collab.sweep_stale_decisions()

    author = planning.cockpit(scope.Viewer.for_actor("ava"))["stale_decisions"]
    other = planning.cockpit(scope.Viewer.for_actor("tester"))["stale_decisions"]
    # the list is non-empty for BOTH, so absence below means filtered, not empty
    assert {d["title"] for d in author} == {"private one", "workspace one"}
    assert {d["title"] for d in other} == {"workspace one"}


def test_last_week_carryover_is_only_unfinished_work(fresh_db):
    """The carryover is the part a kept-% cannot show. Including done tasks
    would make every week look like it rolled forward."""
    done = work.create_task("shipped", actor="tester")
    open_ = work.create_task("still going", actor="tester")
    last = planning.weekly.current_week(-1)
    work.update_task(done["id"], committed_week=last, actor="tester")
    work.update_task(open_["id"], committed_week=last, actor="tester")
    work.update_task(done["id"], status="done", actor="tester")

    carry = planning.cockpit()["last_week"]["carryover"]
    titles = [t["title"] for t in carry]
    assert "still going" in titles
    assert "shipped" not in titles


def test_the_route_answers(client):
    body = client.get("/api/planning").json()
    assert body["week"]["week"]
    assert "capacity_ahead" in body


def test_the_weeks_ahead_are_bounded(fresh_db):
    """A model or a query string supplies this. Unbounded it is one query per
    week, forever."""
    assert len(planning.cockpit(ahead_weeks=999)["capacity_ahead"]) == 26
    assert len(planning.cockpit(ahead_weeks=0)["capacity_ahead"]) == 1


def test_a_private_absence_reason_never_leaves_the_workspace_tier(fresh_db):
    """Proven by mutation to be uncovered: deleting the mask in
    portfolio.py::capacity_ahead leaked every private absence REASON through
    GET /api/planning to every CurrentUser, and 90 tests across five files
    stayed green.

    services/absences.py::away_today makes the same split and states it: the
    unavailability is the honest core, the reason is not. A private `focus` or
    `oncall` window that announces itself by name is the leak that rule exists
    to stop — and adding this function to _UNFILTERED_READS switched the
    automated scanner off for it, so this test IS the enforcement now.
    """
    from datetime import timedelta

    from app.services import absences, portfolio

    monday = db.today() - timedelta(days=db.today().weekday())
    absences.add_absence(
        "ava",
        monday.isoformat(),
        (monday + timedelta(days=3)).isoformat(),
        kind="oncall",
        actor="ava",
        visibility="private",
    )
    absences.add_absence(
        "mira",
        monday.isoformat(),
        (monday + timedelta(days=3)).isoformat(),
        kind="pto",
        actor="mira",
    )

    weeks = portfolio.capacity_ahead(1, scope.Viewer.for_actor("someone-else"))
    away = {a["person"]: a["kind"] for a in weeks[0]["away"]}
    # the person and the dates travel — that is the honest core
    assert set(away) == {"ava", "mira"}
    # the REASON does not, on a scoped row
    assert away["ava"] == "away"
    assert away["mira"] == "pto"


def test_a_short_allocation_still_loads_its_week(fresh_db):
    """Overlap, not containment. The comment says containment would hide every
    allocation shorter than seven days, and only a sub-week window can tell
    the two predicates apart — a length check cannot."""
    from datetime import timedelta

    from app.services import engagements, portfolio
    from app.services.users import ensure_user

    ensure_user("dana", kind="human")
    eng = engagements.create_engagement("Atlas", actor="tester")
    monday = db.today() - timedelta(days=db.today().weekday())
    engagements.allocate(
        "dana",
        eng["id"],
        60,
        starts_on=(monday + timedelta(days=1)).isoformat(),
        ends_on=(monday + timedelta(days=3)).isoformat(),
        actor="tester",
    )
    people = portfolio.capacity_ahead(1)[0]["people"]
    assert [p["person"] for p in people] == ["dana"]


def test_person_names_never_leave_through_an_egressing_caller(fresh_db):
    """The anti-surveillance rule allows person-level data for planning the
    future, never for judging the past. flow_metrics judges the past, so the
    two callers whose output LEAVES — the exec readout artifact and the agent
    tool, whose reply is text somebody pastes elsewhere — take the aggregated
    shape. /portfolio keeps the names: it is a planning surface with a viewer.

    A mutation proved this class of leak uncovered once already (the absence
    kind in capacity_ahead), so it is pinned rather than trusted."""
    import json

    from app.services import portfolio, readout, work
    from app.services.users import ensure_user
    from app.tools.portfolio import get_flow_metrics

    ensure_user("ava", kind="human")
    t = work.create_task("long runner", assignee="ava", actor="tester")
    work.update_task(t["id"], status="in_progress", actor="tester")

    # the planning surface still names people
    assert [w["person"] for w in portfolio.flow_metrics()["wip_by_person"]] == ["ava"]

    # the agent tool does not, and its stale list carries no assignee column
    tool = json.loads(get_flow_metrics())
    assert tool["wip_by_person"] == []
    assert tool["wip_total"] == 1
    assert all("assignee" not in row for row in tool["stale_wip"])
    assert "ava" not in json.dumps(tool)

    # nor does the artifact built to be forwarded
    md = readout.exec_readout(actor="tester")["markdown"]
    assert "1 task in progress across 1 person" in md
    assert "ava" not in md
