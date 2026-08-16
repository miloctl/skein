"""Reading an artifact back.

Every generator here — the handoff, the week rituals, the daily digest, the
exec readout — wrote a markdown file and a row pointing at it, and nothing
could read one back: `list_artifacts` hands out a server-side path, which no
browser can open. The bodies were reachable only by shelling into the
container.
"""

from pathlib import Path

import pytest

from app import config, db
from app.services import blockers, collab, handoff, review, scope, work


def _readout(client) -> dict:
    client.post("/api/portfolio/readout")
    return next(a for a in client.get("/api/artifacts").json() if a["kind"] == "readout")


def test_reads_the_body_of_a_listed_artifact(client):
    art = _readout(client)
    body = client.get(f"/api/artifacts/{art['id']}").json()
    assert body["id"] == art["id"]
    assert body["kind"] == "readout"
    # the file's own text, not the row: a reader gets what the generator wrote
    assert body["markdown"].strip()
    assert body["markdown"] == Path(art["path"]).read_text()
    assert isinstance(body["threads"], list)


def test_artifact_body_returns_only_current_readable_typed_threads(client):
    task = work.create_task("Readable thread", actor="tester")
    blocker = blockers.raise_blocker("Readable blocker", actor="tester")
    proposal = review.propose_change(
        "task",
        "create",
        {"title": "Proposed thread"},
        actor="scout",
        notify_team=False,
    )
    hidden = work.create_task(
        "Private thread",
        actor="tester",
        visibility=scope.PRIVATE,
    )

    root = Path(config.DATA_DIR) / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "typed-thread-test.md"
    path.write_text(
        f"# Report\n\nTask #{task['id']} waits on blocker #{blocker['id']}. "
        f"Task #{task['id']} appears twice. Proposal #{proposal['id']} is pending.\n"
        f"Task #{hidden['id']} is private. "
        "Decision #99999 is absent. Bare #9, sprint #5, and 'question #8' stay text.",
        encoding="utf-8",
    )
    aid = db.execute(
        "INSERT INTO artifacts (kind, title, path, created_by, created_at)"
        " VALUES ('digest', 'Typed threads', ?, 'tester', ?) RETURNING id",
        (str(path), db.now()),
    )

    body = client.get(f"/api/artifacts/{aid}").json()
    assert body["threads"] == [
        {"entity": "task", "id": task["id"]},
        {"entity": "blocker", "id": blocker["id"]},
        {"entity": "proposal", "id": proposal["id"]},
    ]


def test_artifact_threads_follow_each_destination_policy(client, monkeypatch):
    from app.extensions.policy import PolicyDecision, PolicyEffect
    from app.routes import api

    task = work.create_task("Denied task thread", actor="tester")
    blocker = blockers.raise_blocker("Readable blocker thread", actor="tester")
    decision = collab.record_decision(
        "Denied decision thread",
        "Keep the destination closed",
        review_by="2030-01-01",
        category="charter",
        actor="tester",
    )
    proposal = review.propose_change(
        "task",
        "create",
        {"title": "Denied proposal thread"},
        actor="scout",
        notify_team=False,
    )

    root = Path(config.DATA_DIR) / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "policy-thread-test.md"
    path.write_text(
        f"Task #{task['id']}. Blocker #{blocker['id']}. "
        f"Decision #{decision['id']}. Proposal #{proposal['id']}.",
        encoding="utf-8",
    )
    aid = db.execute(
        "INSERT INTO artifacts (kind, title, path, created_by, created_at)"
        " VALUES ('digest', 'Policy threads', ?, 'tester', ?) RETURNING id",
        (str(path), db.now()),
    )

    def destination_decision(_request, _subject, action, *_args, **_kwargs):
        effect = (
            PolicyEffect.DENY
            if action
            in {
                "skein.rest.get.tasks",
                "skein.rest.get.decisions",
                "skein.rest.get.review",
            }
            else PolicyEffect.PERMIT
        )
        return PolicyDecision(effect)

    monkeypatch.setattr(api, "decide", destination_decision)
    body = client.get(f"/api/artifacts/{aid}").json()
    assert body["threads"] == [{"entity": "blocker", "id": blocker["id"]}]


def test_artifact_threads_apply_row_level_policy_not_only_route_level(client, monkeypatch):
    """A rule can permit the route and deny one row. Denying by action alone
    is always caught by the cached route decision, so this fixture is the only
    thing that exercises the row-level half of the thread filter."""
    from app.extensions.policy import PolicyDecision, PolicyEffect
    from app.routes import api

    kept = work.create_task("Kept task thread", actor="tester")
    denied = work.create_task("Row-denied task thread", actor="tester")

    root = Path(config.DATA_DIR) / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "row-policy-thread-test.md"
    path.write_text(
        f"Task #{kept['id']} stays. Task #{denied['id']} is row-denied.",
        encoding="utf-8",
    )
    aid = db.execute(
        "INSERT INTO artifacts (kind, title, path, created_by, created_at)"
        " VALUES ('digest', 'Row policy threads', ?, 'tester', ?) RETURNING id",
        (str(path), db.now()),
    )

    def row_decision(_request, _subject, action, resource_type, **kwargs):
        effect = (
            PolicyEffect.DENY
            if resource_type == "task" and kwargs.get("resource_id") == str(denied["id"])
            else PolicyEffect.PERMIT
        )
        return PolicyDecision(effect)

    monkeypatch.setattr(api, "decide", row_decision)
    body = client.get(f"/api/artifacts/{aid}").json()
    assert body["threads"] == [{"entity": "task", "id": kept["id"]}]


def test_a_generated_report_does_not_thread_references_inside_titles(client):
    """The fixture body comes from a real generator, not a hand-written file:
    week_open interpolates task titles, and an unquoted title that says
    "decision #N" minted a thread to a row the report never referenced."""
    from app.services import collab, rituals, users

    users.ensure_user("ava")
    decision = collab.record_decision(
        "Unreferenced decision",
        "The report must not link this row",
        actor="tester",
    )
    task = work.create_task(
        f"Bob's chase of decision #{decision['id']} approval",
        assignee="ava",
        due_date=db.today().isoformat(),
        actor="tester",
    )

    out = rituals.week_open(actor="tester", force=True)
    body = client.get(f"/api/artifacts/{out['artifact_id']}").json()
    threads = body["threads"]
    assert {"entity": "task", "id": task["id"]} in threads
    assert not any(t["entity"] == "decision" for t in threads)


def test_a_ritual_hands_back_an_id_that_reads_back(client):
    """The whole point of returning artifact_id: the ritual's own response is
    the only place the id is knowable without re-listing every artifact and
    matching on the title."""
    out = client.post("/api/rituals/week-close").json()
    body = client.get(f"/api/artifacts/{out['artifact_id']}").json()
    assert body["markdown"] == out["markdown"]


def test_an_already_run_ritual_returns_the_existing_artifact(client):
    first = client.post("/api/rituals/week-close?force=true").json()
    repeat = client.post("/api/rituals/week-close").json()
    assert repeat == {
        "week": first["week"],
        "skipped": "already ran this week",
        "artifact_id": first["artifact_id"],
    }


def test_a_concurrent_repeat_waits_for_the_ritual_report(monkeypatch, fresh_db):
    """`fresh_db` because this claims a FIXED week (2035-W01) and job_runs
    keeps the claim: without the reset the test passes once per database and
    every later run finds the week already claimed, never enters the patched
    run, and fails on `entered.wait`."""
    from datetime import date
    from threading import Event, Thread

    from app.services import rituals

    monkeypatch.setattr(rituals.db, "today", lambda: date(2035, 1, 5))
    original = rituals._week_close_run
    entered = Event()
    release = Event()
    outputs: list[dict] = []
    errors: list[Exception] = []

    def slow_run(today, week, actor):
        entered.set()
        assert release.wait(5)
        return original(today, week, actor)

    def run():
        try:
            outputs.append(rituals.week_close(actor="tester"))
        except Exception as exc:  # pragma: no cover — asserted empty below
            errors.append(exc)

    monkeypatch.setattr(rituals, "_week_close_run", slow_run)
    first = Thread(target=run)
    second = Thread(target=run)
    first.start()
    assert entered.wait(5)
    second.start()
    assert second.is_alive(), "the repeat did not wait for the claim transaction"
    release.set()
    first.join(5)
    second.join(5)

    assert errors == []
    assert len(outputs) == 2
    assert {row["artifact_id"] for row in outputs} == {outputs[0]["artifact_id"]}
    assert sum("skipped" in row for row in outputs) == 1


def test_a_repeat_still_skips_when_the_report_is_gone(client):
    """The claim outlives its artifact whenever the row is deleted. Raising
    there answers the manual button AND every scheduler retry with a 500 for
    the rest of the claimed week."""
    first = client.post("/api/rituals/week-close").json()
    db.execute("DELETE FROM artifacts WHERE id = ?", (first["artifact_id"],))

    repeat = client.post("/api/rituals/week-close")
    assert repeat.status_code == 200
    assert repeat.json()["skipped"] == "already ran this week"
    assert repeat.json()["artifact_id"] is None


def test_a_later_day_in_the_same_week_links_the_existing_ritual(client, monkeypatch):
    from datetime import date

    from app.services import rituals

    day = [date(2028, 1, 3)]
    monkeypatch.setattr(rituals.db, "today", lambda: day[0])
    first = client.post("/api/rituals/week-close").json()

    day[0] = date(2028, 1, 5)
    repeat = client.post("/api/rituals/week-close").json()
    assert repeat["skipped"] == "already ran this week"
    assert repeat["artifact_id"] == first["artifact_id"]


def test_artifact_pages_reach_every_report(client, fresh_db):
    expected = []
    for i in range(75):
        expected.append(
            fresh_db.execute(
                "INSERT INTO artifacts (kind, title, path, created_by, created_at)"
                " VALUES ('digest', ?, ?, 'scheduler', ?) RETURNING id",
                (f"cursor report {i}", f"/tmp/cursor-{i}.md", fresh_db.now()),
            )
        )

    seen = []
    before = 0
    while True:
        suffix = f"?before={before}" if before else ""
        page = client.get(f"/api/artifacts/page{suffix}").json()
        seen.extend(row["id"] for row in page["items"] if row["title"].startswith("cursor report"))
        if page["next_before"] is None:
            break
        before = page["next_before"]

    assert seen == sorted(expected, reverse=True)
    assert len(seen) == len(set(seen)) == 75
    assert client.get("/api/artifacts/page?before=-1").status_code == 422
    assert isinstance(client.get("/api/artifacts").json(), list)


def test_artifact_pages_scan_past_rows_that_workplace_policy_denies(client, fresh_db, monkeypatch):
    from app.extensions.policy import PolicyDecision, PolicyEffect
    from app.routes import api

    ids = []
    for i in range(75):
        ids.append(
            fresh_db.execute(
                "INSERT INTO artifacts (kind, title, path, created_by, created_at)"
                " VALUES ('digest', ?, ?, 'scheduler', ?) RETURNING id",
                (f"policy report {i}", f"/tmp/policy-{i}.md", fresh_db.now()),
            )
        )
    cutoff = sorted(ids, reverse=True)[49]

    def decision(*args, **kwargs):
        effect = (
            PolicyEffect.DENY
            if int(kwargs.get("resource_id") or 0) >= cutoff
            else PolicyEffect.PERMIT
        )
        return PolicyDecision(effect, ())

    monkeypatch.setattr(api, "decide", decision)
    page = client.get("/api/artifacts/page").json()
    visible = [row["id"] for row in page["items"] if row["title"].startswith("policy report")]
    assert visible == sorted(ids, reverse=True)[50:]
    assert page["next_before"] is None
    compatible = [
        row["id"]
        for row in client.get("/api/artifacts").json()
        if row["title"].startswith("policy report")
    ]
    assert compatible == visible


def test_a_forced_rerun_reuses_its_row_instead_of_filing_a_second(client):
    """_write_artifact upserts on the path, so a same-day rerun overwrites the
    file. Returning a NEW id there would hand the reader an artifact that does
    not exist; returning a STALE one would point at the overwritten body."""
    # ?force=true, because the route no longer forces by default: the weekly
    # claim is what stops a second click re-notifying the whole roster
    first = client.post("/api/rituals/week-close?force=true").json()
    second = client.post("/api/rituals/week-close?force=true").json()
    assert first["artifact_id"] == second["artifact_id"]
    # by title, not by kind: `week_open` files a ritual row of its own (the
    # startup catch-up runs one), and counting every ritual would pass here
    # whether or not the rerun added a second close-out
    rows = [a for a in client.get("/api/artifacts").json() if "close-out" in a["title"]]
    assert len(rows) == 1
    assert (
        client.get(f"/api/artifacts/{second['artifact_id']}").json()["markdown"]
        == (second["markdown"])
    )


def test_absent_artifact_and_unreadable_artifact_read_alike(client):
    """The 404 must not answer "does #N exist" — ids are sequential."""
    art = _readout(client)
    gone = art["id"] + 999
    absent = client.get(f"/api/artifacts/{gone}")
    assert absent.status_code == 404
    assert absent.json() == {"detail": f"no artifact #{gone}"}

    # the same row, now unreadable: same status, and the SAME sentence the
    # absent case produces for this id — nothing distinguishes the two
    db.execute("UPDATE artifacts SET visibility = 'private' WHERE id = ?", (art["id"],))
    hidden = client.get(f"/api/artifacts/{art['id']}")
    assert hidden.status_code == 404
    assert hidden.json() == {"detail": f"no artifact #{art['id']}"}


def test_a_path_outside_the_artifacts_dir_is_refused(client):
    """`path` is a stored string and this call turns one into a file read. A
    crafted row must not reach the environment file or the private notes DB
    that services/private_notes.py keeps out of every other surface."""
    art = _readout(client)
    db.execute("UPDATE artifacts SET path = ? WHERE id = ?", ("/etc/hostname", art["id"]))
    assert client.get(f"/api/artifacts/{art['id']}").status_code == 404
    with pytest.raises(db.NotFound):
        handoff.read_artifact(art["id"], scope.Viewer("tester", True))


def test_a_row_whose_file_vanished_is_our_fault_not_the_callers(client):
    """A restored database beside an empty data volume. Nothing the caller sent
    can produce this, so it stays a 500 and lands in the error rate — as a 404
    it would say "no such artifact" about a row the reader can see listed, and
    no operator would ever be paged (CLAUDE.md, error classification)."""
    art = _readout(client)
    db.execute(
        "UPDATE artifacts SET path = ? WHERE id = ?",
        (art["path"] + ".gone", art["id"]),
    )
    with pytest.raises(RuntimeError, match="no file on disk"):
        handoff.read_artifact(art["id"], scope.Viewer("tester", True))
