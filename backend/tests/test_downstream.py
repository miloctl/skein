"""What finishing a task releases.

`waiting_on` recorded what a task is stuck BEHIND and nothing read the other
direction, so the edge cost the person who typed it and paid them nothing
back. Without a downstream view the edges rot, and every synthesis built on
them quietly becomes fiction with receipts.
"""

import itertools

from app.services import planning, scope, work


def _task(client, title: str) -> int:
    return client.post("/api/tasks", json={"title": title}).json()["id"]


def _waits(client, task_id: int, target: str) -> None:
    r = client.patch(f"/api/tasks/{task_id}", json={"waiting_on": target})
    assert r.status_code == 200, r.text


def test_a_task_names_the_work_it_releases(client):
    a = _task(client, "the dependency")
    b = _task(client, "waits on a")
    c = _task(client, "also waits on a")
    _waits(client, b, f"task:{a}")
    _waits(client, c, f"task:{a}")

    got = client.get(f"/api/tasks/{a}").json()
    assert {t["id"] for t in got["unblocks"]} == {b, c}
    assert got["unblocks_total"] == 2
    assert got["depth_capped"] is False


def test_the_chain_counts_past_the_first_hop(client):
    a, b, c = (_task(client, f"t{i}") for i in range(3))
    _waits(client, b, f"task:{a}")
    _waits(client, c, f"task:{b}")

    got = client.get(f"/api/tasks/{a}").json()
    # b directly, c behind it — the direct list stays one hop, the count does not
    assert [t["id"] for t in got["unblocks"]] == [b]
    assert got["unblocks_total"] == 2


def test_a_blocker_edge_is_not_counted_as_released_work(client):
    """`blockers.task_id` names the task the blocker BLOCKS — raise_blocker
    sets that task to 'blocked'. Counting through it claimed that finishing
    the task released other work, while the same blocker was what stopped
    that task from finishing at all. Resolving a blocker is a blocker verb."""
    stuck = _task(client, "the task the blocker stops")
    waiter = _task(client, "waits on the blocker")
    b = client.post(
        "/api/blockers", json={"title": "stuck", "task_id": stuck, "impact": "high"}
    ).json()
    _waits(client, waiter, f"blocker:{b['id']}")

    got = client.get(f"/api/tasks/{stuck}").json()
    assert got["unblocks"] == []
    assert got["unblocks_total"] == 0
    # and the task the blocker names is itself blocked, which is the point
    assert got["status"] == "blocked"


def test_a_finished_waiter_is_not_counted_as_released(client):
    a = _task(client, "the dependency")
    b = _task(client, "already done")
    _waits(client, b, f"task:{a}")
    client.patch(f"/api/tasks/{b}", json={"status": "done"})

    got = client.get(f"/api/tasks/{a}").json()
    assert got["unblocks"] == []
    assert got["unblocks_total"] == 0


def test_a_cycle_terminates_without_claiming_it_was_capped(client):
    """`waiting_on` is free-form: nothing stops A waiting on B waiting on A.
    A cycle is closed by the visited set, not by the depth cap — so the walk
    returns a true count rather than a capped one, and `depth_capped` stays
    false because nothing was left uncounted."""
    a, b = _task(client, "a"), _task(client, "b")
    _waits(client, a, f"task:{b}")
    _waits(client, b, f"task:{a}")

    got = client.get(f"/api/tasks/{a}").json()
    assert got["unblocks_total"] == 1  # b, and then the cycle closes
    assert got["depth_capped"] is False


def test_a_chain_deeper_than_the_cap_says_it_stopped_counting(client):
    """The cap is for depth, not cycles. A silent stop would report a number
    that quietly gave up part way."""
    ids = [_task(client, f"link{i}") for i in range(work._WAIT_DEPTH + 3)]
    for earlier, later in itertools.pairwise(ids):
        _waits(client, later, f"task:{earlier}")

    got = client.get(f"/api/tasks/{ids[0]}").json()
    assert got["depth_capped"] is True
    assert got["unblocks_total"] == work._WAIT_DEPTH


def test_a_chain_of_exactly_the_cap_is_not_called_truncated(client):
    """`truncated` must mean work was left uncounted, not "the walk reached
    ten". The peek renders it as "the chain runs deeper than Skein follows",
    which is a false claim about a chain that was counted in full."""
    ids = [_task(client, f"exact{i}") for i in range(work._WAIT_DEPTH + 1)]
    for earlier, later in itertools.pairwise(ids):
        _waits(client, later, f"task:{earlier}")

    got = client.get(f"/api/tasks/{ids[0]}").json()
    assert got["unblocks_total"] == work._WAIT_DEPTH
    assert got["depth_capped"] is False


def test_the_walk_is_viewer_scoped_at_every_hop(client):
    """A task nobody may read must not be countable through the chain — a bare
    count would leak that it exists."""
    from app import db

    a = _task(client, "the dependency")
    hidden = _task(client, "private waiter")
    _waits(client, hidden, f"task:{a}")
    db.execute("UPDATE tasks SET visibility = 'private' WHERE id = ?", (hidden,))

    got = work.downstream(a, scope.Viewer("someone-else", True))
    assert got["unblocks"] == []
    assert got["unblocks_total"] == 0


def test_the_cockpit_names_the_task_that_releases_the_most(client):
    a, b, c, d = (_task(client, f"t{i}") for i in range(4))
    _waits(client, b, f"task:{a}")
    _waits(client, c, f"task:{a}")
    _waits(client, d, f"task:{b}")  # a releases 3 through the chain, b releases 1

    top = planning.cockpit(scope.Viewer("tester", True))["top_unblocking_move"]
    assert top is not None
    assert top["id"] == a
    assert top["unblocks"] == 3


def test_the_cockpit_names_nothing_when_nothing_waits(client):
    _task(client, "lonely")
    # None, not a zeroed row: "the top unblocking move releases 0 tasks" is a
    # sentence about work that does not exist
    assert planning.cockpit(scope.Viewer("tester", True))["top_unblocking_move"] is None
