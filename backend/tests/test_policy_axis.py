"""The workplace project-policy axis on routes whose first path literal names
no entity (policy_context._ROUTE_ENTITIES): the generic gate judges them on an
empty resource, so each handler decides for itself. Every test here composes
one rule that denies project class `regulated`, checks a sibling control that
already honors it, and pins that the route now honors it too.

Fixtures come from the run-2 audit probes (~/security-audit-skill/skein/run-2,
records skein/digest/*, skein/composites/*, skein/policy-*/*, skein/chat/*,
skein/private/*, skein/artifacts/*, skein/admin-export/*, skein/rest-policy/*).
"""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta

from conftest import _strong, _turn
from fastapi.testclient import TestClient

from app import config, db
from app.extensions import (
    PolicyContribution,
    PolicyDecision,
    PolicyEffect,
    PolicyInput,
    PolicyResource,
    PolicySubject,
    SkeinModule,
)
from app.extensions.policy import policy_input_data, reset_policy_engine, set_policy_engine
from app.extensions.registry import ExtensionRegistry
from app.main import create_app
from app.services import scope

CANARY = "REGULATED CANARY"
PARTY = "Outside Party Co"


def _deny_regulated(request: PolicyInput):
    if request.resource.project_type == "regulated":
        return PolicyDecision(PolicyEffect.DENY, ("Regulated work is closed.",))
    return None


def _deny_regulated_for(name: str):
    def rule(request: PolicyInput):
        if request.subject.name == name and request.resource.project_type == "regulated":
            return PolicyDecision(PolicyEffect.DENY, ("Regulated work is closed.",))
        return None

    return rule


def _module(rule=_deny_regulated):
    return SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.7.0",
        policies=(PolicyContribution("acme.workplace.regulated", rule),),
    )


def _app(rule=_deny_regulated):
    return create_app(modules=(_module(rule),))


def _due(days=3):
    return (date.today() + timedelta(days=days)).isoformat()


def _engagements(actor="mira"):
    from app.services import engagements, users

    users.ensure_human_identity(actor)
    std = engagements.create_engagement("Standard work", project_class="standard", actor=actor)
    reg = engagements.create_engagement(
        f"Regulated {CANARY}", project_class="regulated", actor=actor
    )
    return std["id"], reg["id"]


def _ritual_artifacts():
    return [int(r["id"]) for r in db.query("SELECT id FROM artifacts WHERE kind = 'ritual'")]


def test_composite_routes_fail_closed_under_a_project_rule(fresh_db):
    from app.services import promises

    std, reg = _engagements()
    promises.add_promise("standard promise", PARTY, _due(), std, actor="mira")
    promises.add_promise(f"promise {CANARY}", PARTY, _due(), reg, actor="mira")
    with TestClient(_app(), headers={"X-User": "mira"}) as c:
        control = c.get("/api/promises")
        assert control.status_code == 200 and CANARY not in control.text
        assert c.get("/api/findings").status_code == 403
        for method, path in (
            ("GET", "/api/stakeholders"),
            ("POST", "/api/rituals/week-close"),
            ("POST", "/api/rituals/week-open"),
            ("POST", "/api/findings/run"),
            ("GET", "/api/insights"),
            ("POST", "/api/digest"),
            ("GET", "/api/activity"),
            ("GET", "/api/activity/feed"),
        ):
            response = c.request(method, path)
            assert response.status_code == 403, (path, response.text)
            assert CANARY not in response.text
    # a refused ritual stores no artifact
    assert _ritual_artifacts() == []


def test_composite_routes_answer_with_no_workplace_rule(fresh_db):
    from app.services import promises

    std, _reg = _engagements()
    promises.add_promise("standard promise", PARTY, _due(), std, actor="mira")
    # a composed rule, even a permissive one, closes the free-text composites
    # (ProjectionPolicy.allows_unclassified); the row-shaped ones stay open
    with TestClient(create_app(), headers={"X-User": "mira"}) as c:
        assert c.get("/api/stakeholders").status_code == 200
        assert c.get("/api/insights").status_code == 200
        assert c.get("/api/activity/feed").status_code == 200
        assert c.post("/api/rituals/week-close").status_code == 200
        assert c.get("/api/usage").status_code == 200
    with TestClient(_app(lambda request: None), headers={"X-User": "mira"}) as c:
        assert c.get("/api/stakeholders").status_code == 200
        assert c.get("/api/usage").status_code == 200


def test_usage_diff_and_event_brief_honor_the_project_rule(fresh_db):
    from app.services import promises, review, schedule, usage, work

    std, reg = _engagements("mallory")
    promises.add_promise("standard promise", PARTY, _due(2), std, actor="mallory")
    promises.add_promise(f"promise {CANARY}", PARTY, _due(2), reg, actor="mallory")
    event = schedule.schedule_event(
        "meeting",
        (datetime.now(UTC) + timedelta(days=1)).isoformat(timespec="seconds"),
        attendees=PARTY,
        actor="mallory",
    )["id"]
    usage.record_chat_usage("t-std", "chief-of-staff", "mock", 10, 5, engagement_id=std)
    usage.record_chat_usage("t-reg", "chief-of-staff", "mock", 10, 5, engagement_id=reg)
    std_task = work.create_task("standard task", engagement_id=std, actor="mallory")["id"]
    reg_task = work.create_task(f"task {CANARY}", engagement_id=reg, actor="mallory")["id"]

    def context(task_id, project_type):
        return {
            "input": policy_input_data(
                PolicyInput(
                    PolicySubject("agent", kind="agent"),
                    "task.update",
                    PolicyResource(
                        "task", str(task_id), project_type=project_type, classification="workspace"
                    ),
                    "agent",
                )
            )
        }

    std_change = review.propose_change(
        "task",
        "update",
        {"title": "std proposal"},
        summary="std proposal",
        entity_id=std_task,
        actor="agent",
        notify_team=False,
        policy_context=context(std_task, "standard"),
    )["id"]
    reg_change = review.propose_change(
        "task",
        "update",
        {"title": "reg proposal"},
        summary="reg proposal",
        entity_id=reg_task,
        actor="agent",
        notify_team=False,
        policy_context=context(reg_task, "regulated"),
    )["id"]
    with TestClient(_app(), headers={"X-User": "mallory"}) as c:
        listed = c.get("/api/review")
        assert listed.status_code == 200 and "reg proposal" not in listed.text
        assert c.get(f"/api/review/{std_change}/diff").json()["diff"]["current"] == {
            "title": "standard task"
        }
        # the diff of a proposal the queue withholds answers no diff
        assert c.get(f"/api/review/{reg_change}/diff").json() == {"id": reg_change, "diff": None}
        assert c.get("/api/usage").status_code == 403
        assert c.get(f"/api/events/{event}/stakeholders").status_code == 403


def test_share_and_finding_writes_are_judged_on_the_project(fresh_db):
    from app.services import blockers, insights, intake, promises, work

    _std, reg = _engagements()
    private_task = work.create_task(
        f"private task {CANARY}", engagement_id=reg, actor="mira", visibility=scope.PRIVATE
    )["id"]
    parent = work.create_task("parent", engagement_id=reg, actor="mira")["id"]
    private_blocker = blockers.raise_blocker(
        "private blocker", owner="mira", task_id=parent, actor="mira", visibility=scope.PRIVATE
    )["id"]
    private_promise = promises.add_promise(
        "private promise", PARTY, _due(), reg, actor="mira", visibility=scope.PRIVATE
    )["id"]
    private_intake = intake.submit_request(
        "private intake",
        "detail",
        requester=PARTY,
        project_class="regulated",
        actor="mira",
        visibility=scope.PRIVATE,
    )["id"]
    promises.add_promise(f"finding promise {CANARY}", PARTY, _due(), reg, actor="mira")
    fired = insights.run_findings(actor="scheduler")["findings"]
    finding = next(f["id"] for f in fired if CANARY in f["message"])
    with TestClient(_app()) as c:
        mira = _strong(c, "mira")
        assert (
            c.patch(
                f"/api/tasks/{private_task}", json={"priority": "high"}, headers=mira
            ).status_code
            == 403
        )
        for kind, row_id in (
            ("tasks", private_task),
            ("blockers", private_blocker),
            ("promises", private_promise),
            ("requests", private_intake),
        ):
            shared = c.post(f"/api/share/{kind}/{row_id}", headers=mira)
            assert shared.status_code == 403, (kind, shared.text)
        weak = {"X-User": "mallory"}
        assert c.get("/api/findings", headers=weak).status_code == 403
        assert (
            c.post(
                f"/api/findings/{finding}/convert", json={"kind": "task"}, headers=weak
            ).status_code
            == 403
        )
        assert (
            c.post(
                f"/api/findings/{finding}/disposition",
                json={"disposition": "dismissed"},
                headers=weak,
            ).status_code
            == 403
        )
    for table, row_id in (
        ("tasks", private_task),
        ("blockers", private_blocker),
        ("promises", private_promise),
        ("intake_requests", private_intake),
    ):
        row = fresh_db.query_one(f"SELECT visibility FROM {table} WHERE id = ?", (row_id,))  # noqa: S608 — test literal
        assert row["visibility"] == scope.PRIVATE, table
    assert fresh_db.query_one("SELECT COUNT(*) AS n FROM finding_dispositions")["n"] == 0
    assert (
        fresh_db.query_one("SELECT COUNT(*) AS n FROM tasks WHERE source_finding_id IS NOT NULL")[
            "n"
        ]
        == 0
    )


def test_share_still_widens_a_permitted_row(fresh_db):
    from app.services import work

    std, _reg = _engagements()
    task = work.create_task(
        "private std", engagement_id=std, actor="mira", visibility=scope.PRIVATE
    )["id"]
    with TestClient(_app()) as c:
        assert (
            c.post(f"/api/share/tasks/{task}", headers=_strong(c, "mira")).json()["visibility"]
            == "workspace"
        )


def test_week_plan_decides_for_a_weak_owners_private_task(fresh_db):
    from app.services import weekly, work

    std, reg = _engagements()
    std_task = work.create_task(
        "std private", engagement_id=std, actor="mira", visibility=scope.PRIVATE
    )["id"]
    reg_task = work.create_task(
        "reg private", engagement_id=reg, actor="mira", visibility=scope.PRIVATE
    )["id"]
    with TestClient(_app()) as c:
        weak = {"X-User": "mira"}
        assert (
            c.post(
                "/api/week/plan", json={"task_ids": [reg_task]}, headers=_strong(c, "mira")
            ).status_code
            == 403
        )
        # the weak owner's own private regulated task used to skip decide() and
        # still be written; it is judged on the write path now
        assert (
            c.post(
                "/api/week/plan", json={"task_ids": [reg_task, std_task]}, headers=weak
            ).status_code
            == 403
        )
        committed = c.post("/api/week/plan", json={"task_ids": [std_task]}, headers=weak)
        assert committed.status_code == 200 and committed.json()["committed"] == 1
    rows = {
        r["id"]: r["committed_week"] for r in fresh_db.query("SELECT id, committed_week FROM tasks")
    }
    assert rows[reg_task] is None and rows[std_task] == weekly.current_week()


def test_chat_links_and_room_detail_are_judged_on_the_engagement(fresh_db):
    from app.services import chat_threads, users

    users.ensure_human_identity("alice")
    users.ensure_human_identity("bob")
    std, reg = _engagements("alice")
    chat_threads.claim_thread("solo-strong", "alice")
    chat_threads.claim_thread("solo-weak", "alice")
    with TestClient(_app()) as c:
        alice, bob = _strong(c, "alice"), _strong(c, "bob")
        assert c.get(f"/api/engagements/{reg}/brief", headers=alice).status_code == 403
        assert (
            c.patch(
                "/api/chats/solo-strong", json={"engagement_id": reg}, headers=alice
            ).status_code
            == 403
        )
        assert (
            c.patch(
                "/api/chats/solo-weak", json={"engagement_id": reg}, headers={"X-User": "alice"}
            ).status_code
            == 403
        )
        assert (
            c.patch("/api/chats/solo-strong", json={"engagement_id": std}, headers=alice).json()[
                "engagement_id"
            ]
            == std
        )
        room = c.post("/api/shared-chats", json={"title": "room"}, headers=alice).json()["id"]
        assert (
            c.patch(
                f"/api/shared-chats/{room}", json={"engagement_id": reg}, headers=alice
            ).status_code
            == 403
        )
        # a link made before the rule applied is reported as no link, never by name
        fresh_db.execute("UPDATE chat_threads SET engagement_id = ? WHERE id = ?", (reg, room))
        invitation = c.post(
            f"/api/shared-chats/{room}/invitations",
            json={"person": "bob", "share_history": True},
            headers=alice,
        ).json()["id"]
        assert (
            c.post(f"/api/shared-chats/invitations/{invitation}/accept", headers=bob).status_code
            == 200
        )
        detail = c.get(f"/api/shared-chats/{room}", headers=bob).json()
        assert detail["engagement_id"] is None and detail["engagement_name"] == ""
        assert CANARY not in json.dumps(detail)
    assert {
        r["engagement_id"]
        for r in fresh_db.query("SELECT engagement_id FROM chat_threads WHERE kind = 'solo'")
    } == {std, None}


def test_one_on_one_brief_filters_the_leads_denied_project(fresh_db):
    from app.services import blockers, pairings, promises, users, work

    users.ensure_user("ava")
    std, reg = _engagements("bob")
    reg_task = work.create_task("reg task", assignee="bob", engagement_id=reg, actor="bob")["id"]
    work.update_task(reg_task, status="in_progress", actor="bob")
    std_task = work.create_task("std task", assignee="bob", engagement_id=std, actor="bob")["id"]
    work.update_task(std_task, status="in_progress", actor="bob")
    blocked = work.create_task("blocked", assignee="bob", engagement_id=reg, actor="bob")["id"]
    blockers.raise_blocker("reg blocker", owner="bob", task_id=blocked, actor="bob")
    promises.add_promise("reg promise", engagement_id=reg, actor="bob")
    pairings.accept(pairings.propose("bob", actor="ava", role="lead")["id"], actor="bob")
    with TestClient(_app(_deny_regulated_for("ava"))) as c:
        ava = _strong(c, "ava")
        assert [t["id"] for t in c.get("/api/tasks?status=", headers=ava).json()] == [std_task]
        brief = c.get("/api/private/brief/bob", headers=ava).json()
    assert [t["id"] for t in brief["in_progress"]] == [std_task]
    assert brief["open_blockers"] == [] and brief["promises_made"] == []


def _run_tool(delegate, arguments, subject):
    from app.agents.core_tools import GovernedCoreTool

    wrapper = GovernedCoreTool(delegate)

    async def run():
        return [
            event
            async for event in wrapper._stream(
                {"toolUseId": "t", "input": arguments}, {}, subject, "agent", ""
            )
        ]

    last = asyncio.run(run())[-1]
    result = dict(last.get("tool_result") or last)
    return result, "".join(part.get("text", "") for part in result.get("content", []))


def test_artifact_reader_judges_engagement_less_composites(fresh_db):
    from app.services import blockers, documents, promises, work
    from app.tools.files import read_artifact

    _std, reg = _engagements("alice")
    task = work.create_task("reg task", engagement_id=reg, actor="alice")["id"]
    blocker = blockers.raise_blocker(f"blocker {CANARY}", task_id=task, actor="alice")["id"]
    fresh_db.execute("UPDATE blockers SET status = 'escalated' WHERE id = ?", (blocker,))
    promises.add_promise(f"promise {CANARY}", PARTY, _due(5), reg, actor="alice")
    rule = _deny_regulated_for("mallory")
    # the producer runs before the rule reaches its action: an active rule
    # closes every free-text composite for everyone (allows_unclassified)
    with TestClient(create_app(), headers={"X-User": "alice"}) as alice:
        readout = alice.post("/api/portfolio/readout")
        assert readout.status_code == 200 and CANARY in readout.json()["markdown"]
        readout_id = readout.json()["artifact_id"]
    loose = documents.create_document(
        "loose", f"text naming {CANARY}", actor="alice", engagement_id=0
    )["id"]
    linked = documents.create_document("linked", "about it", actor="alice", engagement_id=reg)["id"]
    assert (
        fresh_db.query_one("SELECT engagement_id FROM artifacts WHERE id = ?", (readout_id,))[
            "engagement_id"
        ]
        is None
    )
    app = _app(rule)
    with TestClient(app, headers=_strong(name="mallory")) as mallory:
        assert mallory.post("/api/portfolio/readout").status_code == 403
        assert mallory.get(f"/api/artifacts/{linked}").status_code == 403
        listed = {a["id"] for a in mallory.get("/api/artifacts").json()}
        paged = {a["id"] for a in mallory.get("/api/artifacts/page").json()["items"]}
        assert not {readout_id, loose, linked} & (listed | paged)
        assert mallory.get(f"/api/artifacts/{readout_id}").status_code == 403
        assert mallory.get(f"/api/artifacts/{loose}").status_code == 403
    # with no workplace rule the composite is readable as before
    with TestClient(create_app(), headers={"X-User": "alice"}) as permitted:
        assert permitted.get(f"/api/artifacts/{readout_id}").status_code == 200
        assert readout_id in {a["id"] for a in permitted.get("/api/artifacts").json()}
    token = set_policy_engine(ExtensionRegistry.build((_module(rule),)).policy_engine)
    try:
        # a turn sets the policy subject the tool body reads (conftest._turn,
        # as routes/chat.py does); the wrapper's own subject is the argument
        with _turn("mallory"):
            for artifact_id in (readout_id, loose):
                result, text = _run_tool(
                    read_artifact,
                    {"artifact_id": artifact_id},
                    PolicySubject("mallory", strong=True),
                )
                assert CANARY not in text, artifact_id
                assert result.get("status") != "success" or "denied" in text
    finally:
        reset_policy_engine(token)


def test_admin_export_fails_closed_for_a_denied_reader(fresh_db, monkeypatch):
    from app.services import users, work

    monkeypatch.setattr(config, "ADMINS", frozenset({"alice"}))
    users.ensure_user("alice")
    std, reg = _engagements("alice")
    work.create_task("std task", engagement_id=std, actor="alice")
    work.create_task(f"task {CANARY}", engagement_id=reg, actor="alice")

    def deny_reads(request: PolicyInput):
        if (
            request.action.startswith("skein.rest.get.")
            and request.resource.project_type == "regulated"
        ):
            return PolicyDecision(PolicyEffect.DENY, ("closed",))
        return None

    with TestClient(_app(deny_reads)) as c:
        alice = _strong(c, "alice")
        assert reg not in {r["id"] for r in c.get("/api/engagements", headers=alice).json()}
        assert c.get("/api/admin/export", headers=alice).status_code == 403
        assert c.get("/api/admin/export/download", headers=alice).status_code == 403
    assert list((config.DATA_DIR / "exports").glob("export-*.json")) == []
    with TestClient(create_app()) as c:
        download = c.get("/api/admin/export/download", headers=_strong(c, "alice"))
        assert download.status_code == 200 and CANARY in download.text


def test_body_engagement_id_cannot_pick_the_project_the_gate_judges(fresh_db):
    from app.services import memory, promises, schedule

    std, reg = _engagements("alice")
    promise = promises.add_promise("regulated promise", "Legal", _due(), reg, actor="alice")["id"]
    event = schedule.schedule_event("review", "2026-10-01T10:00", engagement_id=reg, actor="alice")[
        "id"
    ]
    note = memory.remember("regulated note", engagement_id=reg, actor="alice")["id"]
    with TestClient(_app(_deny_regulated_for("mallory"))) as c:
        weak = {"X-User": "mallory"}
        assert (
            c.patch(
                f"/api/promises/{promise}", json={"promise": "edited"}, headers=weak
            ).status_code
            == 403
        )
        # the gate runs before body validation and judges the row's own
        # project first, so the stray field never picks the project
        assert (
            c.patch(
                f"/api/promises/{promise}",
                json={"promise": "x", "engagement_id": std},
                headers=weak,
            ).status_code
            == 403
        )
        assert (
            c.post(
                f"/api/promises/{promise}/status",
                json={"status": "kept", "engagement_id": 999999},
                headers=_strong(c, "mallory"),
            ).status_code
            == 403
        )
        # a handler that ignores the field is judged on the row's own project first
        assert (
            c.post(
                f"/api/events/{event}/outcome",
                json={"outcome": "recorded", "engagement_id": 999999},
                headers=weak,
            ).status_code
            == 403
        )
        assert (
            c.request(
                "DELETE", f"/api/memories/{note}", json={"engagement_id": std}, headers=weak
            ).status_code
            == 403
        )
    assert (
        fresh_db.query_one("SELECT status FROM promises WHERE id = ?", (promise,))["status"]
        != "kept"
    )
    assert fresh_db.query_one("SELECT id FROM memories WHERE id = ?", (note,)) is not None


def test_every_bare_route_literal_is_accounted_for():
    """A route whose first literal is not a policy entity is judged on an empty
    resource by the generic gate (extensions/fastapi.py). Each such route either
    decides in its handler, or is named here as carrying no project row. A new
    bare route lands in this list on purpose, not by omission."""
    import inspect

    from fastapi.routing import APIRoute

    from app.main import app
    from app.services.policy_context import _ROUTE_ENTITIES

    helpers = (
        "_require_opaque_project_policy",
        "_permitted_collection",
        "_require_resource_policy",
        # the document routes: _require_resource_policy on the artifact, plus
        # the opaque-project check an engagement-less document needs
        "_require_document_policy",
        "_require_engagement_policy",
        "_require_export_policy",
        "_require_verdict_policy",
        # a comment is judged on its thread's parent (routes/api.py::_decide_comment)
        "_held_comment(",
        "ProjectionPolicy(",
        "_engagement_policy(",
        "decide(",
    )
    # every route under these literals reads or writes no project row. A
    # literal whose routes read project rows and decide in their handlers
    # (portfolio, search, attention, ...) is NOT listed: the helper check
    # judges those, and listing them let a new undecided sibling pass.
    no_project_literals = {
        "absences",
        "adoption",
        "agents",
        "auth",
        "calendar.ics",
        "chat",
        "crews",
        "eval",
        "extensions",
        "feedback",
        "field-guide",
        "fieldguide",
        "files",
        "flocks",
        "growth",
        "health",
        "ingest",
        "keys",
        "mcp",
        "mcp-server",
        "merge-requests",
        "my-data",
        "onboarding",
        "personas",
        "playbooks",
        "ready",
        "readiness",
        # no routine route reads a project row, because a routine carries no project
        "routines",
        "settings",
        "standups",
        "theme",
        "users",
        "webhooks",
        "whoami",
    }
    # routes under a literal that ALSO has policy-judged siblings; each one
    # here touches no project row (messages, membership, verdicts, keys, backup)
    no_project_routes = {
        ("GET", "/api/activity/verify"),
        ("GET", "/api/admin/keys"),
        ("POST", "/api/admin/keys/revoke-all"),
        ("POST", "/api/admin/backup"),
        ("GET", "/api/review/stranded"),
        ("POST", "/api/review/seen"),
        ("POST", "/api/notifications/read"),
        ("GET", "/api/chats"),
        ("GET", "/api/chats/folders"),
        ("POST", "/api/chats/folders"),
        ("DELETE", "/api/chats/folders/{name}"),
        ("GET", "/api/chats/{thread_id}/messages"),
        ("GET", "/api/chats/{thread_id}/messages/page"),
        ("DELETE", "/api/chats/{thread_id}"),
        ("POST", "/api/shared-chats"),
        ("GET", "/api/shared-chats"),
        ("GET", "/api/shared-chats/invitations"),
        ("POST", "/api/shared-chats/invitations/{invitation_id}/accept"),
        ("POST", "/api/shared-chats/invitations/{invitation_id}/decline"),
        ("POST", "/api/shared-chats/{thread_id}/agents"),
        ("DELETE", "/api/shared-chats/{thread_id}/agents"),
        ("GET", "/api/shared-chats/{thread_id}/agent-runs"),
        ("GET", "/api/shared-chats/{thread_id}/messages"),
        ("POST", "/api/shared-chats/{thread_id}/messages"),
        ("DELETE", "/api/shared-chats/{thread_id}/messages/{message_id}"),
        ("POST", "/api/shared-chats/{thread_id}/read"),
        ("POST", "/api/shared-chats/{thread_id}/invitations"),
        ("DELETE", "/api/shared-chats/{thread_id}/invitations"),
        ("DELETE", "/api/shared-chats/{thread_id}/members"),
        ("POST", "/api/shared-chats/{thread_id}/members/role"),
        ("POST", "/api/shared-chats/{thread_id}/leave"),
        ("POST", "/api/shared-chats/{thread_id}/archive"),
        ("POST", "/api/shared-chats/{thread_id}/restore"),
        ("GET", "/api/private/notes"),
        ("POST", "/api/private/notes"),
        ("DELETE", "/api/private/notes/{note_id}"),
        ("GET", "/api/private/audit"),
        ("GET", "/api/private/pairs"),
        ("POST", "/api/private/pairs"),
        ("POST", "/api/private/pairs/{pair_id}/accept"),
        ("POST", "/api/private/pairs/{pair_id}/end"),
    }

    def walk(routes):
        for route in routes:
            if isinstance(route, APIRoute):
                yield route
            elif getattr(route, "original_router", None) is not None:
                yield from walk(route.original_router.routes)

    seen = 0
    unaccounted = []
    for route in walk(app.routes):
        if not route.path.startswith("/api/"):
            continue
        seen += 1
        literals = [
            s
            for s in route.path.strip("/").split("/")
            if s and s != "api" and not s.startswith("{")
        ]
        first = literals[0] if literals else ""
        if first in _ROUTE_ENTITIES or first in no_project_literals:
            continue
        if any((method, route.path) in no_project_routes for method in route.methods):
            continue
        if any(helper in inspect.getsource(route.endpoint) for helper in helpers):
            continue
        unaccounted.append(f"{sorted(route.methods)} {route.path}")
    assert seen > 200, seen  # the walk reached the included routers
    assert not unaccounted, unaccounted


def test_a_verdict_is_judged_on_the_target_rows_project(fresh_db):
    """The queue and the diff withheld a denied project's proposal, and the
    verdict route still applied it: the verdict writes the row."""
    from app.services import review, work

    _std, reg = _engagements("mallory")
    reg_task = work.create_task(f"task {CANARY}", engagement_id=reg, actor="mallory")["id"]
    change = review.propose_change(
        "task",
        "update",
        {"title": "renamed by a denied reviewer"},
        summary="reg proposal",
        entity_id=reg_task,
        actor="agent",
        notify_team=False,
    )["id"]
    with TestClient(_app()) as c:
        mallory = _strong(c, "mallory")
        assert c.post(f"/api/review/{change}/approve", json={}, headers=mallory).status_code == 403
        assert c.post(f"/api/review/{change}/reject", json={}, headers=mallory).status_code == 403
        batch = c.post("/api/review/approve-batch", json={"ids": [change]}, headers=mallory)
        assert batch.status_code == 200
        assert all(r.get("status") != "approved" for r in batch.json()["results"]), batch.text
    assert fresh_db.query_one("SELECT title FROM tasks WHERE id = ?", (reg_task,))[
        "title"
    ].endswith(CANARY)
