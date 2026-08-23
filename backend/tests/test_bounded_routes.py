"""The bounded-input census, as a ratchet.

`docs/CORRECTIONS.md` rule 5 names three bounds and only the PATCH-vs-create
parity check was enforced by a test. A review found real gaps behind the other
two, and the gaps were found by hand — which means the next one gets found by
hand too, or not at all.

This walks the route table and requires every MUTATING route to either call
`ratelimit.check` in its own body or carry a row in EXEMPT with a reason. The
list below is today's reality, not an aspiration: it exists so a NEW unmetered
route cannot ship quietly, and it is meant to shrink. Two directions are
enforced, because a stale exemption is how a list like this rots:

  - a mutating route in neither place fails
  - an exemption for a route that now HAS a check, or that no longer exists,
    fails too

A CALL to `ratelimit.check` in the handler body, matched in the parse tree
rather than as a substring, and not anywhere in the call graph: the guard has
to be visible where a reviewer reads the route, and a comment naming the
function must not satisfy the ratchet. A route capped inside its service is
exempted here and says so.
"""

import ast
import inspect
import textwrap

from app.main import app

MUTATING = {"POST", "PATCH", "PUT", "DELETE"}


def _calls_the_check(fn) -> bool:
    """A CALL to ratelimit.check in the handler body, found in the parse tree.

    Not a substring search: `# ratelimit.check happens in the service` would
    satisfy that, and a ratchet nothing can satisfy by accident is the whole
    point of this file.
    """
    try:
        src = textwrap.dedent(inspect.getsource(fn))
    except (OSError, TypeError):  # pragma: no cover — a C-level endpoint
        return False
    try:
        tree = ast.parse(src)
    except SyntaxError:  # pragma: no cover
        return False
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "check"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "ratelimit"
        for node in ast.walk(tree)
    )


def _routes():
    """Every leaf route, with its final path.

    FastAPI wraps an included router in `_IncludedRouter` rather than
    flattening it into `app.routes`, so a walk that only reads `app.routes`
    sees the four doc endpoints and nothing else — and a census that finds
    zero routes passes while covering nothing.
    """

    def walk(routes):
        for r in routes:
            included = getattr(r, "original_router", None)
            if included is not None:
                yield from walk(included.routes)
                continue
            yield r

    for r in walk(app.routes):
        verbs = (getattr(r, "methods", set()) or set()) & MUTATING
        if not verbs or not getattr(r, "endpoint", None):
            continue
        for verb in sorted(verbs):
            yield f"{verb} {r.path}", r.endpoint


# route -> why it carries no per-user cap. A reason, never a shrug: an absence
# with no comment reads as an oversight to the next reader.
EXEMPT: dict[str, str] = {
    # --- judged by a human, one row at a time, and CAS-claimed ---
    "POST /api/review/{change_id}/approve": "one verdict per proposal, claimed by CAS",
    "POST /api/review/{change_id}/reject": "one verdict per proposal, claimed by CAS",
    "POST /api/review/approve-batch": "bounded by the pending queue it drains",
    "POST /api/review/seen": "idempotent timestamp, no row created",
    "POST /api/notifications/read": "idempotent flag flip on rows the caller owns",
    # --- edits of a row that already exists: no growth, and the parity test
    # bounds the field sizes ---
    "PATCH /api/blockers/{blocker_id}": "edits one existing row",
    "PATCH /api/engagements/{engagement_id}": "edits one existing row",
    "PATCH /api/intake/{request_id}": "edits one existing row",
    "PATCH /api/milestones/{milestone_id}": "edits one existing row",
    "PATCH /api/promises/{promise_id}": "edits one existing row",
    "POST /api/blockers/{blocker_id}/resolve": "terminal flip on one existing row",
    "POST /api/promises/{promise_id}/status": "terminal flip on one existing row",
    "POST /api/decisions/{decision_id}/reconfirm": "flips one existing row",
    "POST /api/intake/{request_id}/score": "scores one existing row",
    "POST /api/intake/{request_id}/disposition": "terminal verdict on one existing row",
    "POST /api/findings/{finding_id}/disposition": "verdict on one existing finding",
    "POST /api/findings/{finding_id}/convert": "converts one existing finding",
    "POST /api/week/plan": "sets the commitment line for one week",
    # --- one row per person, overwritten in place ---
    "POST /api/users/growth-interests": "one row per person, overwritten",
    "POST /api/users/theme": "one row per person, overwritten",
    # --- administrators only; a flooding administrator owns the deployment ---
    "POST /api/admin/keys/revoke-all": "AdminUser kill switch",
    "POST /api/users/theme/default": "AdminUser, one row for the whole team",
    "POST /api/users/{name}/active": "AdminUser roster edit",
    "POST /api/users/{name}/rename": "AdminUser roster edit",
    "POST /api/agents/authority": "humans only, one row per (agent, entity)",
    "DELETE /api/keys/{key_id}": "revokes one key the caller can already see",
    "POST /api/keys": "needs an existing key to mint another",
    # --- signature-verified integrations, metered as one caller each ---
    "POST /api/webhooks/ci": "HMAC-verified; files one deduped blocker per run",
    "POST /api/slack/command": "HMAC-verified, and Slack rate-limits its own slash commands",
    # --- capped elsewhere, deliberately ---
    "POST /api/decisions/{decision_id}/supersede": "the write path is capped in the create route",
    # --- known cost, no cap yet: these are the census's own open rows and the
    # reason it is a ratchet rather than a one-time sweep ---
    "POST /api/findings/run": "UNCAPPED: a full rule-engine sweep on demand",
    "POST /api/context-pack/publish": "UNCAPPED: rebuilds and versions a pack",
    "POST /api/playbooks/instantiate": "UNCAPPED: writes an engagement, milestones and tasks",
    "POST /api/intake/{request_id}/what-if": "UNCAPPED: read-only projection, no write",
}


def test_every_mutating_route_is_bounded_or_named():
    unbounded = []
    for key, fn in _routes():
        if _calls_the_check(fn) or key in EXEMPT:
            continue
        unbounded.append(key)
    assert not unbounded, (
        "mutating routes with no rate cap and no EXEMPT row:\n  "
        + "\n  ".join(sorted(unbounded))
        + "\n\nAdd ratelimit.check(...) to the handler, or an EXEMPT row saying why not."
    )


def test_the_exemption_list_does_not_rot():
    """An exemption that stopped being true is worse than none: it reads as a
    decision somebody made about the route as it is now."""
    keys = {key for key, _ in _routes()}
    capped, gone = [], []
    for key, fn in _routes():
        if key in EXEMPT and _calls_the_check(fn):
            capped.append(key)
    for key in EXEMPT:
        if key not in keys:
            gone.append(key)
    assert not capped, f"EXEMPT rows for routes that now call the check: {sorted(capped)}"
    assert not gone, f"EXEMPT rows for routes that no longer exist: {sorted(gone)}"


def test_the_agent_door_cannot_write_what_the_rest_door_refuses(client):
    """The service capped nothing, so an agent or MCP caller wrote a title the
    PATCH route then refused to edit — a row the system wrote that its own UI
    cannot fix. Both doors now read the same two constants."""
    from app.services import work

    long_title = "x" * (work.TITLE_LEN + 1)

    # the REST door: refused, and the body never echoes the rejected value
    r = client.post("/api/tasks", json={"title": long_title})
    assert r.status_code == 422
    assert long_title not in r.text

    # the service door, which is what the agent tools and MCP call
    try:
        work.create_task(long_title, actor="scout", origin="agent")
        raise AssertionError("the service accepted a title the REST door refuses")
    except ValueError as e:
        assert str(work.TITLE_LEN) in str(e)

    # and an edit cannot step around the create-time bound either
    t = work.create_task("short enough", actor="scout", origin="agent")
    try:
        work.update_task(t["id"], title=long_title, actor="scout", origin="agent")
        raise AssertionError("update_task accepted a title create_task refuses")
    except ValueError:
        pass


def test_no_producer_stores_a_proposal_that_cannot_be_approved(client):
    """The guard lives in review.propose_change, not in one producer. The agent
    gate and the notes ingester both file proposals, and a check in either one
    alone leaves the other storing rows that can only ever be rejected."""
    from app.services import review, work

    payload = {"title": "ok", "description": "d" * (work.DESCRIPTION_LEN + 1)}
    before = len(client.get("/api/review?status=pending").json())
    try:
        review.propose_change("task", "create", payload, actor="scout", origin="agent")
        raise AssertionError("propose_change stored a proposal that cannot be applied")
    except ValueError as e:
        assert "description" in str(e)
    # and it stored nothing on the way out
    assert len(client.get("/api/review?status=pending").json()) == before


def test_ingest_never_stores_a_proposal_that_cannot_be_approved(client):
    """A proposal is stored now and applied later through the same service the
    REST door uses. A line the service will refuse becomes a row that can only
    ever be rejected, and the reviewer learns why long after the paste."""
    from app.services import work

    long_line = "todo: " + "x" * (work.DESCRIPTION_LEN + 10)
    r = client.post("/api/ingest", json={"text": long_line}).json()
    assert r["proposals"] == []
    # handed back to the person who pasted it, while they still have the text
    assert any(line.startswith("todo: ") for line in r["unclassified"])

    # and every proposal ingest DOES store applies cleanly
    ok = client.post("/api/ingest", json={"text": "todo: write the release note"}).json()
    assert len(ok["proposals"]) == 1
    approved = client.post(f"/api/review/{ok['proposals'][0]['id']}/approve", json={})
    assert approved.status_code == 200


def test_captured_request_cannot_store_a_detail_the_rest_door_refuses(client):
    """Capture accepts 10,000 characters and passed the whole body through as
    the intake detail, which routes/api.py::IntakeIn caps at 4,000 — so a
    captured request stored a row its own edit form refuses."""
    from app.services import intake

    r = client.post("/api/capture", json={"text": "req: " + "y" * (intake.DETAIL_LEN + 10)})
    assert r.status_code == 400
    assert str(intake.DETAIL_LEN) in r.json()["detail"]
    assert "yyyyyyyyyy" not in r.text  # never echoes the rejected value


def test_the_census_actually_walked_the_table():
    """The walk above reads through `_IncludedRouter`. If FastAPI changes that
    shape again, every assertion here passes over an empty list — a census that
    covers nothing looks exactly like a clean one."""
    keys = [key for key, _ in _routes()]
    assert len(keys) > 60, f"only {len(keys)} mutating routes found — the walk is broken"
    assert "POST /api/tasks" in keys
