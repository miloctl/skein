"""References in a meeting's agenda: a person's words, so an apostrophe is a
word and not a quoted title, and each reference links only for a reader who
may open the record it names."""

import re
from pathlib import Path

from conftest import authored_repo_root
from fastapi.testclient import TestClient

from app.services import refs


def _agenda_event(client, agenda):
    r = client.post(
        "/api/events", json={"title": "planning", "starts_at": "2026-10-02T10:00", "agenda": agenda}
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_an_apostrophe_does_not_hide_a_reference(client, fresh_db):
    from app.services import blockers, collab, work

    q = collab.ask_question("who owns staging?", "tester", actor="tester")["id"]
    b = blockers.raise_blocker("vendor key", actor="tester")["id"]
    t = work.create_task("draft the plan", actor="tester")["id"]
    eid = _agenda_event(
        # both apostrophes on one line: a generated frame's quote pairs
        # never span a newline (refs._QUOTED)
        client,
        f"Discuss question #{q} and Mira's blocker #{b}, and don't skip task #{t}.",
    )
    got = client.get(f"/api/events/{eid}").json()["agenda_refs"]
    assert [(r["entity"], r["id"], r["title"]) for r in got] == [
        ("question", q, "who owns staging?"),
        ("blocker", b, "vendor key"),
        ("task", t, "draft the plan"),
    ]


def test_a_reference_to_a_hidden_record_is_plain_text(client, fresh_db):
    from app.services import collab, users

    users.ensure_user("ana")
    hidden = collab.ask_question("ana's secret?", "ana", actor="ana", visibility="private")["id"]
    eid = _agenda_event(client, f"Raise question #{hidden}")
    body = client.get(f"/api/events/{eid}")
    assert body.json()["agenda_refs"] == [] and "ana's secret" not in body.text


def test_an_agenda_reference_honors_the_project_rule(fresh_db):
    from test_policy_axis import CANARY, _app, _engagements

    from app.services import schedule, work

    std, reg = _engagements("mallory")
    ok = work.create_task("standard", engagement_id=std, actor="mallory")["id"]
    denied = work.create_task(f"task {CANARY}", engagement_id=reg, actor="mallory")["id"]
    eid = schedule.schedule_event(
        "review", "2026-10-02T10:00", agenda=f"task #{ok} then task #{denied}", actor="mallory"
    )["id"]
    with TestClient(_app(), headers={"X-User": "mallory"}) as c:
        body = c.get(f"/api/events/{eid}")
        assert [r["id"] for r in body.json()["agenda_refs"]] == [ok]
        assert CANARY not in body.text


def test_every_target_has_a_page_in_the_frontend():
    """services/refs.py and frontend/lib/entity-ref.ts each say the other
    keeps step. A target with no href renders as plain text: the server
    parses the reference and the page links it nowhere."""
    source = (authored_repo_root(Path(__file__)) / "frontend/lib/entity-ref.ts").read_text()
    table = source[source.index("const HREF") : source.index("};", source.index("const HREF"))]
    hrefs = set(re.findall(r"^\s+(\w+): \(", table, re.MULTILINE))
    assert set(refs.TARGETS) <= hrefs, set(refs.TARGETS) - hrefs
