"""'void': the terminal state for a task that never should have existed.

docs/CORRECTIONS.md rule 2 says records that carry history get a terminal
state, not a delete — and 'done' could not carry this meaning, because done
feeds throughput, cycle time and kept-%. Before this the only escape for a
mistyped capture was marking it done, which polluted every metric it touched.
"""


def _mk(client, title="oops, typed twice", **extra):
    return client.post("/api/tasks", json={"title": title, **extra}).json()["id"]


def test_void_leaves_every_list_and_metric(client, fresh_db):
    from app.services import weekly

    tid = _mk(client, due_date="2020-01-01")
    weekly.apply_plan(weekly.current_week(), [tid], actor="tester")
    assert client.patch(f"/api/tasks/{tid}", json={"status": "void"}).status_code == 200

    # neither Browse slice: not open work, and not finished work
    browse = client.get("/api/tasks/browse").json()
    assert tid not in [t["id"] for t in browse["open"] + browse["done"]]
    # not the commitment line: counting it against kept-% punishes the correction
    week = client.get("/api/week").json()
    assert tid not in [t["id"] for t in week["tasks"]]
    # not My Day's due-soon list
    due = client.get("/api/briefing").json()["your_work"]["due_soon"]
    assert tid not in [t["id"] for t in due]
    # not search — a voided task must never be cited
    hits = client.get("/api/search?q=typed").json()
    assert not any(h["entity"] == "task" and h["entity_id"] == tid for h in hits)
    # no completed_at: void is not done, so flow metrics never count it
    assert client.get(f"/api/tasks/{tid}").json()["completed_at"] is None


def test_void_stays_readable_and_restores(client, fresh_db):
    tid = _mk(client)
    client.patch(f"/api/tasks/{tid}", json={"status": "void"})
    # the row answers at its own address — void is a state, not a delete
    assert client.get(f"/api/tasks/{tid}").json()["status"] == "void"

    client.patch(f"/api/tasks/{tid}", json={"status": "todo"})
    browse = client.get("/api/tasks/browse").json()
    assert tid in [t["id"] for t in browse["open"]]
    # restored work is searchable again
    hits = client.get("/api/search?q=typed").json()
    assert any(h["entity"] == "task" and h["entity_id"] == tid for h in hits)


def test_a_wait_on_a_voided_task_is_satisfied(client, fresh_db):
    """A task that never should have existed blocks nothing — the wait must
    stop yellowing the moment the target is voided, like done and resolved
    (portfolio._WAIT_SATISFIED)."""
    from app.services.portfolio import _satisfied_targets

    target = _mk(client, title="the phantom dependency")
    waiter = _mk(client, title="real work")
    client.patch(f"/api/tasks/{waiter}", json={"waiting_on": f"task:{target}"})
    assert _satisfied_targets([{"waiting_on_type": "task", "waiting_on_id": target}]) == set()

    client.patch(f"/api/tasks/{target}", json={"status": "void"})
    assert _satisfied_targets([{"waiting_on_type": "task", "waiting_on_id": target}]) == {
        ("task", target)
    }


def test_void_is_refused_on_a_delegated_task(client, fresh_db):
    """Voiding ends a delegation with no verdict and no trust signal, and
    strands the acceptance proposal — the sponsor ends the delegation first."""
    from app.services import delegation

    tid = _mk(client, title="delegated work")
    delegation.delegate_task(tid, "scout", "tester", actor="tester")
    r = client.patch(f"/api/tasks/{tid}", json={"status": "void"})
    assert r.status_code == 400
    assert "end the delegation first" in r.json()["detail"]
