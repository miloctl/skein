"""The write half of the tier: who may set one, and what a child inherits.

Every REST create body accepts a tier; two UI surfaces offer a picker (quick
capture and the standup card), and the rest inherit — the blocker a standup
forks, the worklog a task carries. Every table has the columns and defaults to
workspace, so the FILTER is uniform even where the picker is not
(docs/VISIBILITY.md).
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
    with pytest.raises(ValueError, match="means one reader"):
        work.create_task(title="x", assignee="bo", actor="ava", visibility="private")


def test_an_unknown_tier_is_refused(fresh_db):
    users.ensure_user("ava")
    with pytest.raises(ValueError, match="visibility must be"):
        work.create_task(title="x", actor="ava", visibility="secret")


def test_a_crew_tier_without_a_crew_is_refused(fresh_db):
    users.ensure_user("ava")
    with pytest.raises(ValueError, match="Pick the crew"):
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
    scoped_eng = engagements.create_engagement(
        f"{secret} engagement", actor="ava", visibility="crew", crew_id=cid
    )
    # ALLOCATED, or capacity and allocation_conflicts read as empty and every
    # assertion below passes whatever their tier lock does. The engagement NAME
    # travels through GROUP_CONCAT on four REST surfaces, the exec readout file
    # and the team_capacity tool, and this seed not allocating is the reason
    # that shipped.
    # OVER 100 in total, because allocation_conflicts only reports a person
    # above that line — at 60% the surface is empty and the assertion passes
    # whatever the tier lock does. This is the shape that hid the leak.
    open_eng = engagements.create_engagement("open work", actor="ava")
    engagements.allocate("ava", scoped_eng["id"], 80, actor="ava")
    engagements.allocate("ava", open_eng["id"], 80, actor="ava")
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


@pytest.mark.parametrize(
    ("parent_tier", "parent_crew", "child_tier", "child_crew", "allowed"),
    [
        (scope.WORKSPACE, None, scope.WORKSPACE, None, True),
        (scope.WORKSPACE, None, scope.CREW, 1, True),
        (scope.WORKSPACE, None, scope.PRIVATE, None, True),
        (scope.CREW, 1, scope.WORKSPACE, None, False),
        (scope.CREW, 1, scope.CREW, 1, True),
        (scope.CREW, 1, scope.CREW, 2, False),
        (scope.CREW, 1, scope.PRIVATE, None, True),
        (scope.PRIVATE, None, scope.WORKSPACE, None, False),
        (scope.PRIVATE, None, scope.CREW, 1, False),
        (scope.PRIVATE, None, scope.PRIVATE, None, True),
    ],
)
def test_task_relationship_audience_must_fit_inside_its_parent(
    parent_tier, parent_crew, child_tier, child_crew, allowed
):
    assert scope.relationship_contains(parent_tier, parent_crew, child_tier, child_crew) is allowed


def test_shared_task_service_refuses_links_that_publish_narrow_parent_ids(fresh_db):
    from app.services import engagements

    users.ensure_user("mira")
    private_engagement = engagements.create_engagement(
        "Private parent", actor="mira", visibility=scope.PRIVATE
    )["id"]
    private_milestone = work.create_milestone(
        "Private milestone", actor="mira", visibility=scope.PRIVATE
    )["id"]

    for links in (
        {"engagement_id": private_engagement},
        {"milestone_id": private_milestone},
    ):
        with pytest.raises(ValueError, match="cannot be visible to more people"):
            work.create_task("Published child", actor="mira", **links)

    private_child = work.create_task(
        "Private child",
        actor="mira",
        visibility=scope.PRIVATE,
        engagement_id=private_engagement,
    )
    assert private_child["id"] > 0

    workspace_child = work.create_task("Unlinked child", actor="mira")["id"]
    with pytest.raises(ValueError, match="cannot be visible to more people"):
        work.update_task(workspace_child, engagement_id=private_engagement, actor="mira")
    assert fresh_db.query_one(
        "SELECT engagement_id FROM tasks WHERE id = ?", (workspace_child,)
    ) == {"engagement_id": None}


def test_task_reads_redact_legacy_relationship_ids_the_viewer_cannot_read(fresh_db):
    from app.services import engagements

    users.ensure_user("mira")
    engagement = engagements.create_engagement(
        "Private parent", actor="mira", visibility=scope.PRIVATE
    )["id"]
    milestone = work.create_milestone("Private milestone", actor="mira", visibility=scope.PRIVATE)[
        "id"
    ]
    direct = work.create_task("Legacy direct", actor="mira")["id"]
    through_milestone = work.create_task("Legacy milestone", actor="mira")["id"]
    fresh_db.execute("UPDATE tasks SET engagement_id = ? WHERE id = ?", (engagement, direct))
    fresh_db.execute(
        "UPDATE tasks SET milestone_id = ? WHERE id = ?", (milestone, through_milestone)
    )

    assert work.get_task(direct)["engagement_id"] is None
    assert work.get_task(through_milestone)["milestone_id"] is None
    listed = {row["id"]: row for row in work.list_tasks()}
    joined = {row["id"]: row for row in work.list_tasks_joined()}
    assert listed[direct]["engagement_id"] is None
    assert listed[through_milestone]["milestone_id"] is None
    assert joined[direct]["engagement_id"] is None
    assert joined[through_milestone]["milestone_id"] is None

    owner = scope.Viewer("mira", True)
    assert work.get_task(direct, owner)["engagement_id"] == engagement
    assert work.get_task(through_milestone, owner)["milestone_id"] == milestone


def test_task_links_must_resolve_to_one_engagement(fresh_db):
    from app.services import engagements

    users.ensure_user("mira")
    direct = engagements.create_engagement("Direct project", actor="mira")["id"]
    engagements.create_engagement("Milestone project", actor="mira")
    milestone = work.create_milestone(
        "Milestone gate",
        project="Milestone project",
        actor="mira",
    )["id"]

    with pytest.raises(ValueError, match="must belong to the same engagement"):
        work.create_task(
            "Conflicting create",
            actor="mira",
            engagement_id=direct,
            milestone_id=milestone,
        )

    task = work.create_task("Conflicting update", actor="mira", engagement_id=direct)["id"]
    with pytest.raises(ValueError, match="must belong to the same engagement"):
        work.update_task(task, milestone_id=milestone, actor="mira")
    assert fresh_db.query_one(
        "SELECT engagement_id, milestone_id FROM tasks WHERE id = ?", (task,)
    ) == {"engagement_id": direct, "milestone_id": None}


def test_milestone_relationships_cannot_publish_narrow_engagement_ids(fresh_db):
    from app.services import engagements

    users.ensure_user("mira")
    private_engagement = engagements.create_engagement(
        "Private milestone parent",
        actor="mira",
        visibility=scope.PRIVATE,
    )["id"]

    with pytest.raises(ValueError, match="milestone cannot be visible to more people"):
        work.create_milestone(
            "Published milestone",
            project="Private milestone parent",
            actor="mira",
        )

    milestone = work.create_milestone("Unlinked milestone", actor="mira")["id"]
    with pytest.raises(ValueError, match="milestone cannot be visible to more people"):
        work.update_milestone(milestone, engagement_id=private_engagement, actor="mira")

    fresh_db.execute(
        "UPDATE milestones SET engagement_id = ? WHERE id = ?",
        (private_engagement, milestone),
    )
    outsider = work.list_milestones()
    owner = work.list_milestones(viewer=scope.Viewer("mira", True))
    assert outsider[0]["engagement_id"] is None
    assert owner[0]["engagement_id"] == private_engagement


def test_milestone_relink_preserves_linked_task_project_coherence(fresh_db):
    from app.services import engagements

    first = engagements.create_engagement("First project")["id"]
    second = engagements.create_engagement("Second project")["id"]
    milestone = work.create_milestone("Shared gate", project="First project")["id"]
    task = work.create_task(
        "Dual-linked task",
        engagement_id=first,
        milestone_id=milestone,
    )["id"]

    with pytest.raises(ValueError, match="must belong to the same engagement"):
        work.update_milestone(milestone, engagement_id=second)

    assert fresh_db.query_one(
        "SELECT engagement_id FROM milestones WHERE id = ?", (milestone,)
    ) == {"engagement_id": first}
    assert fresh_db.query_one(
        "SELECT engagement_id, milestone_id FROM tasks WHERE id = ?", (task,)
    ) == {"engagement_id": first, "milestone_id": milestone}


def test_engagement_auto_adoption_skips_a_conflicting_orphan_milestone(fresh_db):
    from app.services import engagements

    direct = engagements.create_engagement("Existing direct project")["id"]
    milestone = work.create_milestone("Future gate", project="Future project")["id"]
    work.create_task(
        "Existing direct link",
        engagement_id=direct,
        milestone_id=milestone,
    )

    engagements.create_engagement("Future project")

    assert fresh_db.query_one(
        "SELECT engagement_id FROM milestones WHERE id = ?", (milestone,)
    ) == {"engagement_id": None}


def test_waiting_relationships_cannot_publish_narrow_work_ids(fresh_db):
    from app.services import promises

    users.ensure_user("mira")
    private_task = work.create_task("Private dependency", actor="mira", visibility=scope.PRIVATE)[
        "id"
    ]
    private_blocker = blockers.raise_blocker(
        "Private blocker", actor="mira", visibility=scope.PRIVATE
    )["id"]
    private_promise = promises.add_promise(
        "Private promise", actor="mira", visibility=scope.PRIVATE
    )["id"]

    for kind, target in (
        ("task", private_task),
        ("blocker", private_blocker),
        ("promise", private_promise),
    ):
        child = work.create_task(f"Workspace waits on {kind}", actor="mira")["id"]
        with pytest.raises(ValueError, match="cannot be visible to more people"):
            work.update_task(child, waiting_on=f"{kind}:{target}", actor="mira")

        # Protect data that a release before the containment rule could store.
        fresh_db.execute(
            "UPDATE tasks SET waiting_on_type = ?, waiting_on_id = ? WHERE id = ?",
            (kind, target, child),
        )
        assert work.get_task(child)["waiting_on_id"] is None
        listed = next(row for row in work.list_tasks() if row["id"] == child)
        joined = next(row for row in work.list_tasks_joined() if row["id"] == child)
        assert (listed["waiting_on_type"], listed["waiting_on_id"]) == (None, None)
        assert (joined["waiting_on_type"], joined["waiting_on_id"]) == (None, None)

    private_child = work.create_task("Private waiting child", actor="mira", visibility="private")
    work.update_task(private_child["id"], waiting_on=f"blocker:{private_blocker}", actor="mira")
    owner = scope.Viewer("mira", True)
    assert work.get_task(private_child["id"], owner)["waiting_on_id"] == private_blocker


def test_composed_task_views_redact_legacy_private_waiting_ids(fresh_db):
    from app.services import (
        briefing,
        context_pack,
        engagement_brief,
        engagements,
        portfolio,
        weekly,
    )

    users.ensure_user("mira")
    users.ensure_user("noah")
    engagement = engagements.create_engagement("Visible delivery", actor="mira")["id"]
    private_blocker = blockers.raise_blocker(
        "Private blocker",
        actor="mira",
        visibility=scope.PRIVATE,
    )["id"]
    task = work.create_task(
        "Legacy published wait",
        actor="mira",
        assignee="noah",
        due_date=db.today().isoformat(),
        engagement_id=engagement,
    )["id"]
    week = weekly.current_week()
    fresh_db.execute(
        "UPDATE tasks SET waiting_on_type = 'blocker', waiting_on_id = ?, committed_week = ?"
        " WHERE id = ?",
        (private_blocker, week, task),
    )
    viewer = scope.Viewer("noah", True)

    day = briefing.my_day("noah", viewer)["your_work"]
    day_tasks = [*day["tasks"], *day["due_soon"]]
    assert day_tasks
    assert all((row["waiting_on_type"], row["waiting_on_id"]) == (None, None) for row in day_tasks)

    brief = engagement_brief.brief(engagement, viewer)
    brief_task = next(row for row in brief["tasks"] if row["id"] == task)
    assert (brief_task["waiting_on_type"], brief_task["waiting_on_id"]) == (None, None)

    pack = context_pack.build_engagement_pack(engagement, viewer)
    assert f"waiting on blocker #{private_blocker}" not in pack

    health = next(row for row in portfolio.engagement_health(viewer) if row["id"] == engagement)
    assert not any(f"blocker #{private_blocker}" in receipt for receipt in health["receipts"])

    week_task = next(row for row in weekly.week_view(week)["tasks"] if row["id"] == task)
    assert (week_task["waiting_on_type"], week_task["waiting_on_id"]) == (None, None)


def test_a_create_body_exposes_the_tier_its_service_accepts():
    """A create form that omits `visibility` files at the workspace tier no
    matter what the caller chose, silently. Five did — absence, promise,
    question, intake and lesson — while the nine beside them carried the pair,
    so the omission read as a decision somebody had made.

    The invariant is a RELATION, not a list: if the service function a POST
    route calls takes `visibility`, the body that feeds it must offer it. An
    exemption list here would be one more inventory to forget, which is the
    failure it exists to prevent.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    tree = ast.parse((root / "routes" / "api.py").read_text())
    fields = {
        n.name: {t.target.id for t in n.body if isinstance(t, ast.AnnAssign)}
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef)
    }
    # which service functions take a tier
    takes_tier: dict[tuple[str, str], bool] = {}
    for path in (root / "services").glob("*.py"):
        mod = ast.parse(path.read_text())
        for fn in mod.body:
            if isinstance(fn, ast.FunctionDef):
                args = fn.args.args + fn.args.kwonlyargs
                takes_tier[path.stem, fn.name] = any(a.arg == "visibility" for a in args)

    missing = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        if not any(
            isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "post"
            for d in fn.decorator_list
        ):
            continue
        body_models = [getattr(a.annotation, "id", "") for a in fn.args.args]
        model = next((m for m in body_models if m in fields), "")
        if not model:
            continue
        for call in ast.walk(fn):
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                continue
            owner = getattr(call.func.value, "id", "")
            if takes_tier.get((owner, call.func.attr)) and "visibility" not in fields[model]:
                missing.append(f"{fn.name} -> {owner}.{call.func.attr} (body {model})")
    assert not missing, (
        "these POST bodies feed a service that accepts a tier, and do not"
        f" offer one — so the caller's choice is discarded: {sorted(set(missing))}"
    )
