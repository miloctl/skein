"""Reading an artifact back.

Every generator here — the handoff, the week rituals, the daily digest, the
exec readout — wrote a markdown file and a row pointing at it, and nothing
could read one back: `list_artifacts` hands out a server-side path, which no
browser can open. The bodies were reachable only by shelling into the
container.
"""

from pathlib import Path

import pytest

from app import db
from app.services import handoff, scope


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


def test_a_concurrent_repeat_waits_for_the_ritual_report(monkeypatch):
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
