"""The write half of the tier: who may set one, and what a child inherits.

Phase 3 gives four surfaces a picker — tasks, notes, decisions, standups —
plus the blocker a standup forks and the worklog a task carries. Every other
table has the columns and defaults to workspace, so the filter is uniform
without 16 pickers landing at once (docs/VISIBILITY.md).
"""

from datetime import UTC, datetime, timedelta

import pytest

from app import db
from app.services import blockers, collab, crews, delegation, scope, users, work


def _crew(owner="ava", name="Platform"):
    users.ensure_user(owner)
    return crews.create_crew(name, actor=owner)["id"]


def test_a_write_defaults_to_workspace(fresh_db):
    """Migration 004 defaults every column, so a caller that says nothing
    behaves exactly as it did before the tier existed."""
    users.ensure_user("ava")
    tid = work.create_task(title="unscoped", actor="ava")["id"]
    row = fresh_db.query_one("SELECT visibility, crew_id FROM tasks WHERE id = ?", (tid,))
    assert row == {"visibility": "workspace", "crew_id": None}


def test_a_crew_write_needs_membership(fresh_db):
    """assert_writable runs inside the insert's transaction — a caller who is
    not in the crew cannot scope a row into it."""
    users.ensure_user("ava")
    users.ensure_user("bo")
    cid = _crew()
    tid = work.create_task(title="ours", actor="ava", visibility="crew", crew_id=cid)["id"]
    assert fresh_db.query_one("SELECT crew_id FROM tasks WHERE id = ?", (tid,))["crew_id"] == cid

    with pytest.raises(db.NotFound):
        work.create_task(title="theirs", actor="bo", visibility="crew", crew_id=cid)
    # and the refused write left no row
    assert fresh_db.query_one("SELECT COUNT(*) AS n FROM tasks")["n"] == 1


def test_the_crew_check_and_the_insert_are_one_transaction(fresh_db):
    """Bare, assert_writable opens its own connection, so a membership change
    between the check and the insert still scopes the row. The refusal must
    roll the whole write back, ledger row included."""
    users.ensure_user("ava")
    _crew()
    before = fresh_db.query_one("SELECT COUNT(*) AS n FROM activity")["n"]
    with pytest.raises(db.NotFound):
        collab.save_note("topic", "body", author="ava", actor="ava", visibility="crew", crew_id=999)
    assert fresh_db.query_one("SELECT COUNT(*) AS n FROM notes")["n"] == 0
    assert fresh_db.query_one("SELECT COUNT(*) AS n FROM activity")["n"] == before


def test_a_private_row_reaches_its_author_and_nobody_else(client, fresh_db):
    """private is the author and nobody else — not even a crew they are in."""
    users.ensure_user("ava")
    users.ensure_user("bo")
    cid = _crew()
    crews.add_member(cid, "bo", actor="ava")
    ava, bo = _key("ava"), _key("bo")
    client.post("/api/tasks", json={"title": "mine alone", "visibility": "private"}, headers=ava)
    client.post("/api/tasks", json={"title": "open"}, headers=ava)

    assert {t["title"] for t in client.get("/api/tasks", headers=ava).json()} == {
        "mine alone",
        "open",
    }
    assert {t["title"] for t in client.get("/api/tasks", headers=bo).json()} == {"open"}


def test_a_private_row_is_never_indexed(fresh_db):
    """search.index_record looks the tier up itself rather than trusting 20
    call sites to pass one. Deleting the row later does not take back what
    /ask, semantic search and the MCP tool already served."""
    from app.services import search

    users.ensure_user("ava")
    collab.save_note(
        "secrets", "the vendor number is 240k", author="ava", actor="ava", visibility="private"
    )
    collab.save_note("public", "the vendor number is public", author="ava", actor="ava")
    hits = search.search("vendor")
    assert [h["title"] for h in hits] == ["public"]


def test_a_private_row_reaches_the_index_table_itself(fresh_db):
    """The assertion above goes through search(), which drops the hit at READ
    time. That makes the two protections mask each other: neutering
    search._is_private so private rows are indexed leaves the whole suite
    green, and the body is then in search_index and has been handed to the
    embedding endpoint (search._maybe_embed, a third party). Deleting the row
    afterwards does not take either back. This asserts on the TABLE.
    """
    users.ensure_user("ava")
    collab.save_note(
        "secrets", "the vendor number is 240k", author="ava", actor="ava", visibility="private"
    )
    collab.save_note("public", "the vendor number is public", author="ava", actor="ava")
    indexed = db.query("SELECT title, body FROM search_index")
    assert [r["title"] for r in indexed] == ["public"]
    assert not [r for r in indexed if "240k" in (r["body"] or "")]


def test_a_crew_row_is_indexed_and_then_withheld_from_a_non_member(fresh_db):
    """The other half of the same masking pair. A crew row IS indexed — only
    the private tier is kept out of the table — so search.visible_hits is the
    only thing standing between it and a non-member. Neutering that function
    left 1169 tests green while search(), /ask and the MCP tool all served a
    crew note to anybody.
    """
    from app.services import crews, search

    users.ensure_user("ava")
    users.ensure_user("bo")
    cid = crews.create_crew("Platform", actor="ava")["id"]
    collab.save_note(
        "vendor", "ZZCREWZZ terms", author="ava", actor="ava", visibility="crew", crew_id=cid
    )
    assert db.query("SELECT entity_id FROM search_index WHERE body LIKE '%ZZCREWZZ%'")
    assert [h["title"] for h in search.search("ZZCREWZZ", viewer=scope.Viewer("ava", True))] == [
        "vendor"
    ]
    assert search.search("ZZCREWZZ", viewer=scope.Viewer("bo", True)) == []
    assert search.search("ZZCREWZZ") == []


def test_demoting_a_record_to_private_removes_it_from_the_index(fresh_db):
    """A record indexed while workspace must not stay searchable after it
    becomes private."""
    from app.services import search

    users.ensure_user("ava")
    n = collab.save_note("terms", "renegotiation window", author="ava", actor="ava")
    assert search.search("renegotiation")
    db.execute("UPDATE notes SET visibility = 'private' WHERE id = ?", (n["id"],))
    collab.update_note(n["id"], topic="terms", content="renegotiation window", actor="ava")
    assert search.search("renegotiation") == []


def test_a_private_row_stays_out_of_the_export(fresh_db):
    """The export is JSON on disk and the mirror copies it off-box, so no
    downstream check can take a private row back."""
    import json as j

    from app.services import admin

    users.ensure_user("ava")
    collab.save_note("secrets", "vendor terms", author="ava", actor="ava", visibility="private")
    collab.save_note("public", "open terms", author="ava", actor="ava")
    out = admin.export()
    dump = j.loads(__import__("pathlib").Path(out["path"]).read_text())
    assert [n["topic"] for n in dump["notes"]] == ["public"]


def test_a_private_body_never_enters_the_hash_chained_ledger(fresh_db):
    """A migration may not rewrite a row carrying a seq, so a body written
    here is written for good — no delete, no redaction, no later tier change
    takes it back."""
    users.ensure_user("ava")
    work.create_task(title="acquire NewCo for 40m", actor="ava", visibility="private")
    rows = fresh_db.query("SELECT detail FROM activity WHERE action = 'create_task'")
    assert rows and all("NewCo" not in r["detail"] for r in rows)
    # a workspace row still carries its title, which is what the feed reads
    work.create_task(title="ordinary work", actor="ava")
    assert any(
        "ordinary work" in r["detail"]
        for r in fresh_db.query("SELECT detail FROM activity WHERE action = 'create_task'")
    )


def test_a_private_row_cannot_be_handed_to_anyone(fresh_db):
    """There is nobody who could read it, so an assignee is a person given
    work that does not exist for them."""
    users.ensure_user("ava")
    users.ensure_user("bo")
    with pytest.raises(ValueError, match="readable by nobody else"):
        work.create_task(title="x", assignee="bo", actor="ava", visibility="private")


def test_an_unknown_tier_is_refused(fresh_db):
    users.ensure_user("ava")
    with pytest.raises(ValueError, match="visibility must be"):
        work.create_task(title="x", actor="ava", visibility="secret")


def test_a_crew_tier_without_a_crew_is_refused(fresh_db):
    users.ensure_user("ava")
    with pytest.raises(ValueError, match="pick the crew"):
        work.create_task(title="x", actor="ava", visibility="crew")


def test_a_standups_blocker_child_inherits_its_tier(fresh_db):
    """The crossing that leaks on day one otherwise: post_standup lifts its
    blockers text into a NEW blocker row, and a workspace child publishes what
    the standup was scoped to hide — into the digest, the readout and FTS."""
    users.ensure_user("ava")
    cid = _crew()
    collab.post_standup(
        "ava",
        today="shipped",
        blockers="vendor will not sign",
        actor="ava",
        visibility="crew",
        crew_id=cid,
    )
    row = fresh_db.query_one("SELECT visibility, crew_id, title FROM blockers")
    assert row["visibility"] == "crew" and row["crew_id"] == cid
    assert "vendor" in row["title"]


def test_a_worklog_inherits_its_task(fresh_db):
    """A worklog note is the task's own text. report_progress reads the task
    already, so the tier rides a query that runs either way."""
    users.ensure_user("ava")
    users.ensure_user("scout", kind="agent")
    cid = _crew()
    tid = work.create_task(title="scoped", actor="ava", visibility="crew", crew_id=cid)["id"]
    delegation.delegate_task(tid, "scout", "ava", actor="ava")
    delegation.claim_task(tid, actor="scout")
    delegation.report_progress(tid, "pulled 4 of 6 pages", actor="scout")
    row = fresh_db.query_one("SELECT visibility, crew_id FROM task_worklog")
    assert row == {"visibility": "crew", "crew_id": cid}


def test_a_worklog_on_a_workspace_task_stays_workspace(fresh_db):
    users.ensure_user("ava")
    users.ensure_user("scout", kind="agent")
    tid = work.create_task(title="open work", actor="ava")["id"]
    delegation.delegate_task(tid, "scout", "ava", actor="ava")
    delegation.claim_task(tid, actor="scout")
    delegation.report_progress(tid, "note", actor="scout")
    assert fresh_db.query_one("SELECT visibility FROM task_worklog")["visibility"] == "workspace"


def test_an_agent_never_chooses_a_tier(fresh_db):
    """A visibility argument on a tool is a decision with no human in it, and
    review.approve_change splats a payload straight into the handler — a tier
    in that payload would apply with a reviewer's name on it. No tool passes
    one, so every agent write lands at workspace."""
    import inspect

    from app.tools import ALL_TOOLS

    for tool in ALL_TOOLS:
        fn = getattr(tool, "fn", tool)
        params = inspect.signature(fn).parameters
        assert "visibility" not in params, getattr(fn, "__name__", tool)
        assert "crew_id" not in params, getattr(fn, "__name__", tool)


def test_the_review_registry_never_receives_a_tier(fresh_db):
    """approve_change calls fn(**payload). Every registry handler would raise
    TypeError on an unexpected key, and review.py catches it and resets the
    proposal to pending — so a tier-carrying payload boomerangs forever."""
    from app.services import capture, review

    # capture.plan is the payload the MCP and chat capture paths propose
    for text in ("q: is this ok?", "todo: ship it", "blocked on vendor", "note: a thing"):
        _kind, _entity, payload = capture.plan(text, actor="ava")
        assert "visibility" not in payload and "crew_id" not in payload, text

    # and no registry handler would survive one if it arrived
    import inspect

    for entity, actions in review._registry().items():
        for action, fn in actions.items():
            params = inspect.signature(fn).parameters
            takes = {"visibility", "crew_id"} & set(params)
            if takes:
                # a handler that DOES take the tier must take it keyword-only,
                # or a proposal payload can set it positionally
                for name in takes:
                    assert params[name].kind is inspect.Parameter.KEYWORD_ONLY, (
                        f"{entity}.{action} takes {name} positionally"
                    )


def test_resolve_write_returns_none_not_zero_for_workspace(fresh_db):
    """crew_id is a nullable FK. Storing 0 would point at no crew while
    reading as set, and the filter tests `crew_id IN (...)`."""
    assert scope.resolve_write("workspace", 0, actor="ava") == ("workspace", None)
    assert scope.resolve_write("", 0, actor="ava") == ("workspace", None)


def test_inherit_defaults_closed_for_a_missing_parent(fresh_db):
    assert scope.inherit(None) == ("workspace", None)
    assert scope.inherit({"visibility": "crew", "crew_id": 7}) == ("crew", 7)


def test_every_classified_table_has_both_columns(client, fresh_db):
    """The filter emits `visibility` AND `crew_id`. A table with one but not
    the other passes every crew-less test and 500s for the first crew member."""
    for table in scope.CLASSIFIED:
        cols = {c["name"] for c in fresh_db.query(f"PRAGMA table_info({table})")}
        assert {"visibility", "crew_id"} <= cols, table


def test_the_blocker_a_crew_standup_forks_is_not_indexed_as_workspace(fresh_db):
    """Belt and braces on the inheritance: the child's row and its FTS entry
    must agree, or search leaks what the tier hid."""
    users.ensure_user("ava")
    cid = _crew()
    collab.post_standup(
        "ava", blockers="secret vendor issue", actor="ava", visibility="crew", crew_id=cid
    )
    bid = fresh_db.query_one("SELECT id FROM blockers")["id"]
    # a viewer in the crew sees it; NOBODY does not, which is the filter
    assert blockers.list_blockers(viewer=scope.Viewer("ava", True))[0]["crew_id"] == cid
    assert blockers.list_blockers() == []
    assert (
        fresh_db.query_one("SELECT visibility FROM blockers WHERE id = ?", (bid,))["visibility"]
        == "crew"
    )


def _key(owner):
    from app.services.api_keys import create_key

    users.ensure_user(owner)
    return {"Authorization": f"Bearer {create_key(owner, 'k')['key']}"}


def test_a_crew_row_reaches_only_its_crew_over_http(client, fresh_db):
    """The whole point, end to end: a crew task is invisible to a teammate
    outside the crew, on every surface that lists tasks."""
    users.ensure_user("ava")
    users.ensure_user("bo")
    users.ensure_user("cass")
    cid = _crew()
    crews.add_member(cid, "bo", actor="ava")

    ava, bo, cass = _key("ava"), _key("bo"), _key("cass")
    assert (
        client.post(
            "/api/tasks",
            json={"title": "crew work", "visibility": "crew", "crew_id": cid},
            headers=ava,
        ).status_code
        == 200
    )
    assert client.post("/api/tasks", json={"title": "open work"}, headers=ava).status_code == 200

    def titles(hdr):
        return {t["title"] for t in client.get("/api/tasks", headers=hdr).json()}

    assert titles(ava) == {"crew work", "open work"}
    assert titles(bo) == {"crew work", "open work"}
    assert titles(cass) == {"open work"}


def test_a_weak_caller_reads_only_the_workspace_tier(client, fresh_db):
    """The enforcement bar. In trusted-header mode X-User is whatever the
    caller typed, so a scoped row is never handed to one."""
    users.ensure_user("ava")
    cid = _crew()
    ava = _key("ava")
    client.post(
        "/api/tasks", json={"title": "crew work", "visibility": "crew", "crew_id": cid}, headers=ava
    )
    client.post("/api/tasks", json={"title": "open work"}, headers=ava)

    weak = client.get("/api/tasks", headers={"X-User": "ava"}).json()
    assert {t["title"] for t in weak} == {"open work"}


def test_the_author_reads_their_own_scoped_row_after_leaving_the_crew(client, fresh_db):
    """Leaving a crew revokes the crew's rows, not the ones you wrote. The
    author disjunct is what makes that true."""
    users.ensure_user("ava")
    users.ensure_user("bo")
    cid = _crew()
    crews.add_member(cid, "bo", actor="ava")
    bo = _key("bo")
    client.post(
        "/api/tasks",
        json={"title": "bo's crew task", "visibility": "crew", "crew_id": cid},
        headers=bo,
    )
    crews.remove_member(cid, "bo", actor="ava")
    assert {t["title"] for t in client.get("/api/tasks", headers=bo).json()} == {"bo's crew task"}


def test_a_note_keyword_search_does_not_leak_past_its_or(client, fresh_db):
    """`topic LIKE ? OR content LIKE ? AND {frag}` binds AND tighter than OR,
    so every topic match came back whatever its tier."""
    users.ensure_user("ava")
    users.ensure_user("cass")
    cid = _crew()
    ava, cass = _key("ava"), _key("cass")
    client.post(
        "/api/notes",
        json={"topic": "vendor", "content": "secret terms", "visibility": "crew", "crew_id": cid},
        headers=ava,
    )
    client.post("/api/notes", json={"topic": "vendor", "content": "public terms"}, headers=ava)

    out = client.get("/api/notes", params={"q": "vendor"}, headers=cass).json()
    assert [n["content"] for n in out] == ["public terms"]


def test_the_browse_join_keeps_tasks_with_no_milestone(client, fresh_db):
    """The filter is on the LEFT JOIN's driving side. On the nullable side in
    WHERE it turns the join INNER and drops every task with no milestone."""
    users.ensure_user("ava")
    ava = _key("ava")
    client.post("/api/tasks", json={"title": "no milestone"}, headers=ava)
    mid = work.create_milestone(title="M1", actor="ava")["id"]
    client.post("/api/tasks", json={"title": "has milestone", "milestone_id": mid}, headers=ava)
    out = client.get("/api/tasks", headers=ava).json()
    assert {t["title"] for t in out} == {"no milestone", "has milestone"}


def _seed_every_scoped_kind(cid):
    """One scoped row of every kind an egress surface reads, so a lock that
    is removed shows up as a leak rather than as an empty list."""
    secret = "ZZSECRETZZ"
    # relative, not a literal date: every findings rule has a window, and a
    # row that falls outside it makes the surface return [] — which passes the
    # "secret not in output" assertion no matter what the tier filter does
    soon = (datetime.now(UTC).date() + timedelta(days=2)).isoformat()
    # committed to THIS week: weekly.week_view filters on committed_week, and an
    # uncommitted task makes that surface empty whatever its tier lock says
    now = datetime.now(UTC).date().isocalendar()
    tid = work.create_task(title=f"{secret} task", actor="ava", visibility="crew", crew_id=cid)[
        "id"
    ]
    work.update_task(tid, committed_week=f"{now.year}-W{now.week:02d}", actor="ava")
    collab.record_decision(
        f"{secret} call", "x", decided_by="ava", actor="ava", visibility="crew", crew_id=cid
    )
    collab.ask_question(f"{secret} question", "ava", actor="ava", visibility="crew", crew_id=cid)
    collab.save_note(
        "conventions", f"{secret} note", author="ava", actor="ava", visibility="crew", crew_id=cid
    )
    b = blockers.raise_blocker(
        title=f"{secret} blocker",
        owner="ava",
        impact="critical",
        actor="ava",
        visibility="crew",
        crew_id=cid,
    )
    db.execute("UPDATE blockers SET status = 'escalated' WHERE id = ?", (b["id"],))
    from app.services import intake, promises

    promises.add_promise(
        f"{secret} promise", due_date=soon, actor="ava", visibility="crew", crew_id=cid
    )
    intake.submit_request(
        f"{secret} request", detail="x", actor="ava", visibility="crew", crew_id=cid
    )
    from app.services import absences, engagements, memory, schedule

    # the calendar feed renders inside a mail client, so events and milestones
    # reach further than any other surface here
    schedule.schedule_event(
        f"{secret} event", soon + "T10:00", actor="ava", visibility="crew", crew_id=cid
    )
    engagements.create_engagement(
        f"{secret} engagement", actor="ava", visibility="crew", crew_id=cid
    )
    work.create_milestone(
        f"{secret} milestone",
        project=f"{secret} engagement",
        due_date=soon,
        actor="ava",
        visibility="crew",
        crew_id=cid,
    )
    memory.remember(
        f"{secret} memory", topic="t", user="ava", actor="ava", visibility="crew", crew_id=cid
    )
    absences.add_absence(
        "ava", soon, soon, note=secret, actor="ava", visibility="crew", crew_id=cid
    )
    return secret


def test_no_egress_surface_carries_a_scoped_row(client, fresh_db):
    """Each of these writes a markdown file, hands text to the model provider,
    posts to Slack, or renders inside a mail client. No column check reaches a
    file on disk, so the tier has to hold at the query."""
    from app.services import (
        context_pack,
        digest,
        engagements,
        handoff,
        insights,
        readout,
        rituals,
        schedule,
        weekly,
    )

    users.ensure_user("ava")
    cid = _crew()
    secret = _seed_every_scoped_kind(cid)
    eng = engagements.create_engagement("E1", actor="ava")

    surfaces = {
        "digest": lambda: digest.build_digest(),
        "readout": lambda: readout.exec_readout(actor="scheduler"),
        "handoff": lambda: handoff.generate_handoff(eng["id"], actor="scheduler"),
        "context pack": lambda: context_pack.build_pack(),
        "engagement pack": lambda: context_pack.build_engagement_pack(eng["id"]),
        "ics feed": lambda: schedule.ics_feed(),
        "findings": lambda: insights.run_findings(actor="scheduler"),
        "week view": lambda: weekly.week_view(),
        "week close": lambda: rituals.week_close(actor="scheduler", force=True),
        "week open": lambda: rituals.week_open(actor="scheduler", force=True),
        "my day (team block)": lambda: __import__("app.services.briefing", fromlist=["x"]).my_day(
            "bo"
        ),
    }
    for name, run in surfaces.items():
        assert secret not in str(run()), f"{name} carries a crew row"


def test_quick_capture_scopes_every_kind_it_routes_to(fresh_db):
    """capture() has seven branches that hand-mirror plan()'s payloads. A
    tier added to one and not the rest applies to some captured kinds and
    silently not to others — the picker would look like it worked."""
    from app.services import capture

    users.ensure_user("ava")
    cid = _crew()
    cases = {
        "q: can we ship?": ("questions", "question"),
        "blocked on the vendor": ("blockers", "blocker"),
        "decision: we use SQLite": ("decisions", "decision"),
        "promised: the summary by friday": ("promises", "promise"),
        "request: a new dashboard": ("intake_requests", "intake"),
        "todo: write the migration": ("tasks", "task"),
        "just a thought worth keeping": ("notes", "note"),
    }
    for text, (table, _kind) in cases.items():
        capture.capture(text, actor="ava", visibility="crew", crew_id=cid)
        row = fresh_db.query_one(
            f"SELECT visibility, crew_id FROM {table} ORDER BY id DESC LIMIT 1"  # noqa: S608 — test table names
        )
        assert row == {"visibility": "crew", "crew_id": cid}, text
