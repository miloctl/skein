"""Identity carried into a deferred operation: review resume, a persona-driven
chat turn, a delegation that mints an agent, and the `<person>-mcp` door.

Fixtures come from the run-2 audit probes (records
backend/app/extensions/registry.py:refresh-subject:*, backend/app/routes/api.py:
execute-extension-review:*, backend/app/tools/portfolio.py:delegation-party-door:*,
skein/review/delegation-approval-mints-*, backend/app/mcp_server.py:_remote_actor:*,
backend/app/services/users.py:refuse_mcp_suffix:*, skein/mcp-server/read-worklog-*).
"""

import asyncio
import json

import pytest
from conftest import _strong, _turn
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from app import config
from app.extensions import (
    IdentityContribution,
    PolicyContribution,
    PolicyDecision,
    PolicyEffect,
    PolicySubject,
    SkeinModule,
    ToolContribution,
    WorkflowActionContribution,
)
from app.extensions.policy import PolicyInput
from app.extensions.registry import ExtensionRegistry
from app.extensions.tools import ToolCallContext, execute_tool
from app.main import create_app
from app.services import scope

# ---- review resume: a weak requester keeps an empty group set -------------


def _directory_module(*, rule):
    def resolver(name):
        return {"active": True, "groups": ["managers"] if name == "dana" else []}

    def mapper(_name, groups, _strong):
        return {"capabilities": ("acme.approve",) if "managers" in groups else ()}

    class In(BaseModel):
        model_config = ConfigDict(extra="forbid")
        target: str

    class Out(BaseModel):
        ok: bool

    calls: list[str] = []

    def handler(_context, request):
        calls.append(request.target)
        return {"ok": True}

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.7.0",
        identities=(
            IdentityContribution(
                "acme.workplace.directory", mapper, resolver=resolver, resolves_groups=True
            ),
        ),
        policies=(PolicyContribution("acme.workplace.rules", rule),),
        tools=(
            ToolContribution(
                name="acme.workplace.update",
                version="1.0.0",
                model_name="acme_update",
                description="Update one item.",
                handler=handler,
                input_schema=In,
                output_schema=Out,
                effect="write",
                risk="medium",
                policy_action="acme.update",
                allowed_agents=("agent",),
                timeout_seconds=1,
            ),
        ),
    )
    return module, calls


def _manager_gate(request: PolicyInput):
    if request.action == "acme.update":
        if "acme.approve" in request.subject.capabilities:
            return None
        return PolicyDecision(
            PolicyEffect.REVIEW,
            ("A manager must approve.",),
            approver_groups=("managers",),
            approver_capabilities=("acme.approve",),
        )
    return None


def test_refresh_subject_never_adds_groups_to_a_weak_name(fresh_db):
    from app.services import users

    users.ensure_user("dana")
    module, _calls = _directory_module(rule=_manager_gate)
    registry = ExtensionRegistry.build((module,))
    weak = registry.refresh_subject(PolicySubject("dana", strong=False, source="trusted-header"))
    assert weak.groups == () and "acme.approve" not in weak.capabilities
    strong = registry.refresh_subject(PolicySubject("dana", strong=True, source="oidc"))
    assert strong.groups == ("managers",) and "acme.approve" in strong.capabilities


def test_weak_requester_cannot_lift_a_manager_gated_review_at_resume(fresh_db):
    from app.services import users

    for name in ("dana", "bob", "mallory"):
        users.ensure_user(name)
    module, calls = _directory_module(rule=_manager_gate)
    with TestClient(create_app(modules=(module,))) as c:
        registry = c.app.state.skein_registry
        tool = registry.tool("acme.workplace.update")
        for filer in ("mallory", "dana"):
            # what a bare X-User turn files: no groups, strong False
            filed = asyncio.run(
                execute_tool(
                    tool,
                    {"target": f"from-{filer}"},
                    ToolCallContext(
                        PolicySubject(filer, strong=False, source="trusted-header"), "agent"
                    ),
                    registry.policy_engine,
                )
            )
            assert filed.status == "review_required", (filer, filed)
            # a groupless weak reviewer is refused for both filers: the
            # requirement stored at filing time stands at resume, even though
            # the directory maps dana to the approving group
            verdict = c.post(
                f"/api/review/{filed.review_id}/approve",
                json={"note": ""},
                headers={"X-User": "bob"},
            )
            assert verdict.status_code == 403, (filer, verdict.text)
    assert calls == []


# ---- workflow resume re-decides playbook.create ---------------------------

GOVERNED = """\
schema_version: 1
name: Governed delivery
project_class: regulated
milestones:
  - title: Prepare
workflow:
  - type: approval
    name: release-approval
    action: atlas.release.approve
    resource_type: release
    risk: high
  - type: action
    name: atlas.workplace.notify-manager
    input:
      target: delivery
"""


def _workflow_module(calls, state):
    class SendIn(BaseModel):
        model_config = ConfigDict(extra="forbid")
        target: str

    class SendOut(BaseModel):
        sent: bool

    def send(_context, request):
        calls.append(request.target)
        return {"sent": True}

    action = WorkflowActionContribution(
        name="atlas.workplace.notify-manager",
        version="1.0.0",
        handler=send,
        input_schema=SendIn,
        output_schema=SendOut,
        effect="write",
        risk="medium",
        policy_action="atlas.notification.send",
    )

    def identity(name, _groups, _strong):
        caps = []
        if name in state["creators"]:
            caps.append("regulated-creator")
        if name == "manager":
            caps.append("delivery-manager")
        return {"capabilities": tuple(caps)}

    def rule(request: PolicyInput):
        if request.action == "playbook.create" and request.resource.project_type == "regulated":
            if state["closed"] or "regulated-creator" not in request.subject.capabilities:
                return PolicyDecision(PolicyEffect.DENY, ("Regulated playbooks are closed.",))
            return None
        if request.action == "atlas.release.approve":
            return PolicyDecision(
                PolicyEffect.REVIEW,
                ("A delivery manager must approve.",),
                approver_capabilities=("delivery-manager",),
            )
        return None

    return SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.7.0",
        workflow_actions=(action,),
        policies=(PolicyContribution("atlas.workplace.rules", rule),),
        identities=(IdentityContribution("atlas.workplace.identity", identity),),
    )


@pytest.mark.parametrize("how", ["capability", "rule"])
def test_workflow_resume_refuses_a_requester_the_create_rule_now_denies(
    fresh_db, tmp_path, monkeypatch, how
):
    from app.services import users

    overlay = tmp_path / "playbooks"
    overlay.mkdir()
    (overlay / "governed.yaml").write_text(GOVERNED)
    monkeypatch.setattr(config, "PLAYBOOKS_OVERLAY", overlay)
    calls: list[str] = []
    state = {"creators": {"mallory"}, "closed": False}
    for name in ("mallory", "manager"):
        users.ensure_user(name)
    with TestClient(create_app(modules=(_workflow_module(calls, state),))) as c:
        queued = c.post(
            "/api/playbooks/instantiate",
            headers={"X-User": "mallory"},
            json={"playbook": "governed", "engagement_name": "Queued while allowed"},
        )
        assert queued.status_code == 200, queued.text
        review_id = queued.json()["workflow"]["review_id"]
        if how == "capability":
            state["creators"].discard("mallory")
        else:
            state["closed"] = True
        approved = c.post(
            f"/api/review/{review_id}/approve",
            headers={"X-User": "manager"},
            json={"note": "Release approval is fine."},
        )
        # the executor's refusal answers as an apply failure (400) and the
        # proposal stays pending; the playbook_policy control path answers 403
        assert approved.status_code in (400, 403), approved.text
        assert "denies this reviewed action" in approved.text
    assert fresh_db.query("SELECT id FROM engagements") == []
    assert calls == []


# ---- the party door in a human-driven turn --------------------------------


def _crew_delegation(fresh_db):
    from app.services import crews, delegation, users, work

    for name in ("ava", "mallory"):
        users.ensure_user(name)
    users.ensure_user("scout", kind="agent")
    crew = crews.create_crew("sec", actor="ava")
    task = work.create_task("crew task", actor="ava", visibility="crew", crew_id=crew["id"])["id"]
    delegation.delegate_task(task, "scout", "ava", actor="ava", mint_authorized=True)
    delegation.report_progress(task, "CREWNOTE rotate the key", actor="scout")
    return task


def test_persona_tools_take_the_requesters_own_read_first(fresh_db, monkeypatch):
    from app.agents import identity
    from app.tools import portfolio

    task = _crew_delegation(fresh_db)
    # a non-member drives the persona that holds the delegation
    with _turn("mallory"):
        token = identity.set_agent_identity("scout")
        try:
            for call in (
                lambda: portfolio.read_worklog(task),
                lambda: portfolio.report_progress(task, "planted"),
                lambda: portfolio.claim_delegated_task(task),
                lambda: portfolio.submit_for_acceptance(task, "done"),
            ):
                out = json.loads(call())
                assert out.get("error") == scope.missing_text("tasks", task), out
        finally:
            identity.reset_agent_identity(token)
    assert (
        fresh_db.query_one("SELECT COUNT(*) AS n FROM task_worklog WHERE note LIKE '%planted%'")[
            "n"
        ]
        == 0
    )
    # the crew steward driving the same persona reads it; the unattended
    # runner (no requester) keeps the party path
    with _turn("ava"):
        token = identity.set_agent_identity("scout")
        try:
            assert "CREWNOTE" in portfolio.read_worklog(task)
        finally:
            identity.reset_agent_identity(token)
    token = identity.set_agent_identity("scout")
    try:
        assert "CREWNOTE" in portfolio.read_worklog(task)
    finally:
        identity.reset_agent_identity(token)


# ---- minting an agent identity needs a proven credential ------------------


def test_delegation_mints_an_agent_only_for_a_strong_principal(fresh_db, monkeypatch):
    from app.services import delegation, users, work

    monkeypatch.setattr(config, "AGENT_REVIEW", False)
    users.ensure_user("mira")
    task = work.create_task("workspace task", actor="mira")["id"]
    with pytest.raises(ValueError, match="strong identity"):
        delegation.delegate_task(task, "casey", "mira", actor="mira")
    assert fresh_db.query_one("SELECT 1 FROM users WHERE name = 'casey'") is None
    from app.tools import portfolio

    with _turn("mallory", strong=False):
        out = json.loads(portfolio.delegate_task(task, "casey", "mira"))
        assert "strong identity" in json.dumps(out)
    assert fresh_db.query_one("SELECT 1 FROM users WHERE name = 'casey'") is None
    with _turn("mira", strong=True):
        out = json.loads(portfolio.delegate_task(task, "casey", "mira"))
        assert "error" not in out, out
    assert fresh_db.query_one("SELECT kind FROM users WHERE name = 'casey'")["kind"] == "agent"


def test_a_weak_verdict_does_not_mint_the_agent(fresh_db, monkeypatch):
    from app.services import review, users, work

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    users.ensure_user("mira")
    task = work.create_task("workspace task", actor="mira")["id"]
    change = review.propose_change(
        "delegation",
        "create",
        {"task_id": task, "agent": "dana", "sponsor": "mira"},
        summary=f"delegate task #{task} to dana",
        actor="agent",
        notify_team=False,
        requested_by="mallory",
    )
    with TestClient(create_app()) as c:
        weak = c.post(f"/api/review/{change['id']}/approve", json={}, headers={"X-User": "mallory"})
        assert weak.status_code in (400, 403), weak.text
        assert fresh_db.query_one("SELECT 1 FROM users WHERE name = 'dana'") is None
        strong = c.post(f"/api/review/{change['id']}/approve", json={}, headers=_strong(c, "mira"))
        assert strong.status_code == 200, strong.text
    assert fresh_db.query_one("SELECT kind FROM users WHERE name = 'dana'")["kind"] == "agent"


# ---- the `<person>-mcp` door ----------------------------------------------


def test_the_mcp_suffix_is_reserved_on_the_folded_name(fresh_db):
    from app.services import delegation, users, work

    users.ensure_user("ava")
    users.ensure_user("alice")
    with pytest.raises(ValueError, match="reserved"):
        users.ensure_human_identity("ava-mcp­")
    with pytest.raises(ValueError, match="reserved"):
        users.ensure_agent_identity("Ava-MCP")
    task = work.create_task("t", actor="alice")["id"]
    with pytest.raises(ValueError, match="reserved"):
        delegation.delegate_task(task, "ava-mcp", "alice", actor="alice", mint_authorized=True)
    assert fresh_db.query("SELECT name FROM users WHERE name ILIKE '%mcp%'") == []
    # the person's own door still mints its row
    from app.mcp_server import _remote_actor

    assert _remote_actor("ava") == "ava-mcp"
    row = fresh_db.query_one("SELECT identity_owner FROM users WHERE name = 'ava-mcp'")
    assert row["identity_owner"] == "mcp"


def test_a_long_person_name_fails_closed_at_the_mcp_door(fresh_db):
    from app.mcp_server import _remote_actor
    from app.services import users

    long_name = ("dave" + "x" * 100)[:62]
    users.ensure_user(long_name)
    with pytest.raises(ValueError):
        _remote_actor(long_name)
    assert fresh_db.query("SELECT name FROM users WHERE kind = 'agent'") == []
    short = ("bob" + "x" * 100)[:60]
    users.ensure_user(short)
    assert _remote_actor(short) == short + "-mcp"


def test_a_generic_row_under_the_suffix_is_not_the_persons_door(fresh_db):
    from app.mcp_server import _remote_actor
    from app.services import users

    users.ensure_user("bob")
    # a row minted before the suffix was reserved (owner "agent", not "mcp")
    fresh_db.execute(
        "INSERT INTO users (name, kind, active, identity_owner, created_at)"
        " VALUES ('bob-mcp', 'agent', 1, 'agent', ?)",
        (fresh_db.now(),),
    )
    with pytest.raises(ValueError):
        _remote_actor("bob")
