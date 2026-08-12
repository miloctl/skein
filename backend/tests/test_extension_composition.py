"""The public application-composition contract used by workplace packages."""

import json
from dataclasses import FrozenInstanceError, replace
from threading import Event, Thread, current_thread
from time import sleep

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.extensions import (
    AppSettings,
    ExtensionRegistry,
    ExtensionValidationError,
    IdentityContribution,
    JobContribution,
    LifecycleContext,
    LifecycleContribution,
    PolicyContribution,
    PolicyDecision,
    PolicyEffect,
    PolicyInput,
    PolicyResource,
    PolicySubject,
    RouteContribution,
    RouteOperationContribution,
    ServiceIdentityContribution,
    SkeinModule,
    SpecialistContribution,
)
from app.main import app as default_app
from app.main import create_app


def _router(module_id: str = "acme.workplace") -> APIRouter:
    router = APIRouter(prefix=f"/api/extensions/{module_id}")

    @router.get("/ping")
    def ping():
        return {"module": module_id}

    return router


def _routes(name: str, router: APIRouter) -> RouteContribution:
    operations = tuple(
        RouteOperationContribution(
            method,
            route.path,
            f"{name}.{method.lower()}",
            PolicyResource("extension-route"),
            "read" if method == "GET" else "write",
            "low" if method == "GET" else "medium",
        )
        for route in router.routes
        for method in route.methods
    )
    return RouteContribution(name, router, operations)


def _module(**changes) -> SkeinModule:
    values = {
        "module_id": "acme.workplace",
        "version": "1.2.0",
        "extension_api": "1.0",
        "minimum_core": "0.2.0",
        "maximum_core_exclusive": "0.3.0",
        "routes": (_routes("acme.workplace.routes", _router()),),
    }
    values.update(changes)
    return SkeinModule(**values)


def _paths(routes) -> set[str]:
    found = set()
    for route in routes:
        path = getattr(route, "path", "")
        if path:
            found.add(path)
        nested = getattr(route, "original_router", None)
        if nested is not None:
            found.update(_paths(nested.routes))
    return found


def test_default_entry_point_and_factory_have_the_same_paths():
    built = create_app()
    assert _paths(built.routes) == _paths(default_app.routes)
    assert built is not default_app
    assert built.state.skein_registry is not default_app.state.skein_registry


def test_a_private_router_is_composed_without_mutating_the_default_app(fresh_db):
    built = create_app(modules=(_module(),))
    with TestClient(built, headers={"X-User": "tester"}) as client:
        response = client.get("/api/extensions/acme.workplace/ping")
    assert response.json() == {"module": "acme.workplace"}
    assert "/api/extensions/acme.workplace/ping" not in _paths(default_app.routes)


def test_lifecycle_and_catch_up_job_use_the_composed_registry(fresh_db):
    events: list[str] = []
    contexts = []

    def startup(context):
        contexts.append(context)
        events.append("startup")

    def shutdown(_context):
        events.append("shutdown")

    module = _module(
        jobs=(
            JobContribution(
                "acme.workplace.sync",
                lambda _context: events.append("job") or {"synced": 1},
                service_identity="acme-sync",
                policy_action="acme.sync",
                effect="write",
                risk="medium",
                catch_up=True,
            ),
        ),
        service_identities=(
            ServiceIdentityContribution(
                "acme.workplace.sync-identity",
                "acme-sync",
            ),
        ),
        lifecycle=(LifecycleContribution("acme.workplace.lifecycle", startup, shutdown),),
    )
    settings = replace(AppSettings.from_config(), scheduler_enabled=False)
    with TestClient(create_app(settings, (module,)), headers={"X-User": "tester"}) as client:
        names = {item["job"] for item in client.get("/health").json()["jobs"]}
        assert events[:2] == ["startup", "job"]
        assert "acme.workplace.sync" in names
    assert events[-1] == "shutdown"
    assert contexts[0].core_version
    assert not hasattr(contexts[0], "app")
    assert not hasattr(contexts[0], "settings")


def test_configured_mcp_identity_collision_disables_only_mcp(fresh_db, caplog):
    module = _module(
        routes=(),
        service_identities=(
            ServiceIdentityContribution(
                "acme.workplace.mcp-collision",
                "acme-mcp-agent",
            ),
        ),
    )
    settings = replace(
        AppSettings.from_config(),
        scheduler_enabled=False,
        mcp_user="acme-mcp-agent",
    )

    with TestClient(create_app(settings, (module,))) as client:
        assert client.get("/health").status_code == 200
    assert "The REST API is unaffected" in caplog.text
    assert fresh_db.query_one("SELECT kind FROM users WHERE name = 'acme-mcp-agent'") == {
        "kind": "agent"
    }


def test_existing_human_disables_api_mcp_but_keeps_rest_available(fresh_db, caplog):
    from app.services import users

    users.ensure_user("mira")
    settings = replace(AppSettings.from_config(), mcp_user="mira")

    with TestClient(create_app(settings=settings)) as client:
        assert client.get("/health").status_code == 200
    assert "already owned by a human identity" in caplog.text
    assert "The REST API is unaffected" in caplog.text
    assert fresh_db.query_one("SELECT kind FROM users WHERE name = 'mira'") == {"kind": "human"}


def test_existing_human_stops_standalone_mcp_before_it_runs(fresh_db, monkeypatch):
    from app import mcp_server
    from app.services import users

    users.ensure_user("mira")
    ran: list[bool] = []
    monkeypatch.setattr(mcp_server, "ACTOR", "mira")
    monkeypatch.setattr(mcp_server.mcp, "run", lambda: ran.append(True))

    with pytest.raises(SystemExit) as raised:
        mcp_server.main()
    assert raised.value.code == 1
    assert not ran
    assert fresh_db.query_one("SELECT kind FROM users WHERE name = 'mira'") == {"kind": "human"}


def test_started_lifecycle_is_stopped_if_a_later_startup_fails(fresh_db):
    events: list[str] = []

    def fail(_context):
        events.append("fail")
        raise RuntimeError("startup failed")

    module = _module(
        lifecycle=(
            LifecycleContribution(
                "acme.workplace.first",
                lambda _context: events.append("start"),
                lambda _context: events.append("stop"),
            ),
            LifecycleContribution("acme.workplace.second", fail),
        )
    )
    with (
        pytest.raises(RuntimeError, match="startup failed"),
        TestClient(create_app(modules=(module,))),
    ):
        pass
    assert events == ["start", "fail", "stop"]


def test_started_lifecycle_is_stopped_if_core_startup_later_fails(fresh_db, monkeypatch):
    events: list[str] = []
    module = _module(
        lifecycle=(
            LifecycleContribution(
                "acme.workplace.lifecycle",
                lambda _context: events.append("start"),
                lambda _context: events.append("stop"),
            ),
        )
    )

    def fail_telemetry():
        raise RuntimeError("telemetry startup failed")

    monkeypatch.setattr("app.main.setup_telemetry", fail_telemetry)
    with (
        pytest.raises(RuntimeError, match="telemetry startup failed"),
        TestClient(create_app(modules=(module,))),
    ):
        pass
    assert events == ["start", "stop"]


def test_faulty_shutdown_does_not_skip_other_extension_cleanup(fresh_db):
    events: list[str] = []

    def faulty(_context):
        events.append("faulty")
        raise RuntimeError("shutdown failed")

    module = _module(
        lifecycle=(
            LifecycleContribution(
                "acme.workplace.first",
                lambda _context: events.append("start-first"),
                lambda _context: events.append("stop-first"),
            ),
            LifecycleContribution(
                "acme.workplace.second",
                lambda _context: events.append("start-second"),
                faulty,
            ),
        )
    )
    with TestClient(create_app(modules=(module,))):
        pass
    assert events == ["start-first", "start-second", "faulty", "stop-first"]


def test_lifecycle_context_is_part_of_the_public_extension_api():
    assert LifecycleContext(core_version="0.2.0").core_version == "0.2.0"


def test_settings_and_registry_are_immutable():
    settings = AppSettings.from_config()
    registry = ExtensionRegistry.build((_module(),))
    with pytest.raises(FrozenInstanceError):
        settings.auth_mode = "oidc"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        registry.modules = ()  # type: ignore[misc]


def test_extension_compatibility_uses_the_installed_package_version():
    from importlib.metadata import version

    from app.extensions import SKEIN_CORE_VERSION

    assert version("skein") == SKEIN_CORE_VERSION


def test_factory_settings_control_auth_health_and_docs(fresh_db):
    settings = replace(
        AppSettings.from_config(),
        auth_mode="api-key",
        auth_error="",
        api_token="",
        docs_enabled=False,
        scheduler_enabled=False,
    )
    with TestClient(create_app(settings), headers={"X-User": "self-asserted"}) as client:
        assert client.get("/api/tasks").status_code == 401
        assert client.get("/health").json()["auth_mode"] == "api-key"
        assert client.get("/docs").status_code == 404


@pytest.mark.parametrize(
    ("module", "message"),
    [
        (_module(extension_api="2.0"), "extension API"),
        (_module(minimum_core="9.0.0"), "supports core versions"),
        (
            _module(routes=(_routes("acme.workplace.routes", _router("wrong.namespace")),)),
            "must be under",
        ),
        (
            _module(
                jobs=(
                    JobContribution(
                        "sync",
                        lambda _context: None,
                        service_identity="acme-sync",
                        policy_action="acme.sync",
                        effect="write",
                        risk="medium",
                    ),
                )
            ),
            "must start with",
        ),
    ],
)
def test_invalid_module_contracts_fail_before_startup(module, message):
    with pytest.raises(ExtensionValidationError, match=message):
        create_app(modules=(module,))


def test_private_route_without_domain_policy_metadata_is_rejected():
    router = _router()
    module = _module(
        routes=(RouteContribution("acme.workplace.routes", router),),
    )

    with pytest.raises(ExtensionValidationError, match="missing policy"):
        ExtensionRegistry.build((module,))


def test_duplicate_route_operation_policies_are_rejected():
    router = _router()
    contribution = _routes("acme.workplace.routes", router)
    module = _module(
        routes=(
            replace(
                contribution,
                operations=(contribution.operations[0], contribution.operations[0]),
            ),
        )
    )

    with pytest.raises(ExtensionValidationError, match="duplicate operation policies"):
        ExtensionRegistry.build((module,))


def test_duplicate_ids_contributions_and_cycles_are_rejected():
    with pytest.raises(ExtensionValidationError, match="duplicate module id"):
        ExtensionRegistry.build((_module(), _module()))

    duplicate = _module(
        routes=(
            _routes("acme.workplace.routes", _router()),
            _routes("acme.workplace.routes", _router()),
        )
    )
    with pytest.raises(ExtensionValidationError, match="duplicate route"):
        ExtensionRegistry.build((duplicate,))

    collision = _module(
        routes=(
            _routes("acme.workplace.first", _router()),
            _routes("acme.workplace.second", _router()),
        )
    )
    with pytest.raises(ExtensionValidationError, match="route collision"):
        ExtensionRegistry.build((collision,))

    left = _module(module_id="acme.left", requires=("acme.right",), routes=())
    right = _module(module_id="acme.right", requires=("acme.left",), routes=())
    with pytest.raises(ExtensionValidationError, match="dependency cycle"):
        ExtensionRegistry.build((left, right))


def test_specialist_and_service_machine_identities_cannot_share_a_subject():
    shared = "acme.workplace.operator"
    module = _module(
        routes=(),
        service_identities=(
            ServiceIdentityContribution("acme.workplace.operator-service", shared),
        ),
        specialists=(
            SpecialistContribution(
                name=shared,
                version="1.0.0",
                display_name="Operator",
                description="A specialist identity.",
                system_prompt="Operate within policy.",
            ),
        ),
    )

    with pytest.raises(ExtensionValidationError, match="both a specialist and a service"):
        ExtensionRegistry.build((module,))


@pytest.mark.parametrize(
    "subject",
    ["system", "Scheduler", "forge", "agent", "anonymous", "ci", "mcp", "team"],
)
def test_private_modules_cannot_claim_reserved_core_machine_subjects(subject):
    module = _module(
        routes=(),
        service_identities=(
            ServiceIdentityContribution("acme.workplace.reserved-service", subject),
        ),
    )

    with pytest.raises(ExtensionValidationError, match="reserved core subject"):
        ExtensionRegistry.build((module,))


@pytest.mark.parametrize(
    "subject",
    [
        "code-reviewer",
        "CODE-REVIEWER",
    ],
)
def test_composed_machine_identities_cannot_claim_stock_persona_names(fresh_db, subject):
    module = _module(
        routes=(),
        service_identities=(
            ServiceIdentityContribution("acme.workplace.persona-service", subject),
        ),
    )

    with (
        pytest.raises(RuntimeError, match="machine identity ownership conflict"),
        TestClient(create_app(modules=(module,))),
    ):
        pass


@pytest.mark.parametrize(
    "subject",
    ["code-reviewer", "CODE-REVIEWER", "system", "agent", "anonymous", "ci", "mcp"],
)
def test_api_mcp_collision_disables_only_mcp(fresh_db, caplog, subject):
    settings = replace(AppSettings.from_config(), mcp_user=subject)

    with TestClient(create_app(settings=settings)) as client:
        assert client.get("/health").status_code == 200
    assert "The REST API is unaffected" in caplog.text
    if subject != "agent":
        assert fresh_db.query_one("SELECT 1 FROM users WHERE name = ?", (subject,)) is None


@pytest.mark.parametrize("subject", ["code-reviewer", "system", "agent", "anonymous", "ci", "mcp"])
def test_standalone_mcp_actor_cannot_claim_a_reserved_identity(fresh_db, monkeypatch, subject):
    from app import mcp_server

    monkeypatch.setattr(mcp_server, "ACTOR", subject)
    monkeypatch.setattr(mcp_server.mcp, "run", lambda: None)

    with pytest.raises(SystemExit) as raised:
        mcp_server.main()
    assert raised.value.code == 1
    assert fresh_db.query_one("SELECT 1 FROM users WHERE name = ?", (subject,)) is None


def test_machine_identity_reservation_serializes_a_concurrent_human_claim(fresh_db, monkeypatch):
    """A human cannot land between the machine collision check and insert."""
    from app.services import users

    machine_checked = Event()
    human_attempted = Event()
    human_finished = Event()
    original_refuse_fold_collision = users.refuse_fold_collision
    results: dict[str, dict] = {}
    errors: list[BaseException] = []

    def pause_machine_after_collision_check(name: str, *, ignore: str = "") -> None:
        original_refuse_fold_collision(name, ignore=ignore)
        if current_thread().name == "machine-reservation":
            machine_checked.set()
            assert human_attempted.wait(timeout=2)
            # BEGIN IMMEDIATE keeps the competing insert blocked until the
            # machine reservation commits.
            sleep(0.05)
            assert not human_finished.is_set()

    monkeypatch.setattr(users, "refuse_fold_collision", pause_machine_after_collision_check)

    def reserve_machine() -> None:
        try:
            results["machine"] = users.ensure_agent_identity("race-owner")
        except BaseException as exc:  # pragma: no cover - reported by the assertions below
            errors.append(exc)

    def claim_human() -> None:
        try:
            assert machine_checked.wait(timeout=2)
            human_attempted.set()
            results["human"] = users.ensure_user("race-owner")
        except BaseException as exc:  # pragma: no cover - reported by the assertions below
            errors.append(exc)
        finally:
            human_finished.set()

    machine = Thread(target=reserve_machine, name="machine-reservation")
    human = Thread(target=claim_human, name="human-claim")
    machine.start()
    human.start()
    machine.join(timeout=3)
    human.join(timeout=3)

    assert not machine.is_alive() and not human.is_alive()
    assert errors == []
    assert results["machine"]["kind"] == "agent"
    assert results["human"]["kind"] == "agent"
    assert fresh_db.query_row("SELECT kind FROM users WHERE name = ?", ("race-owner",)) == {
        "kind": "agent"
    }


def test_composed_machine_identities_cannot_claim_overlay_persona_or_flock_names(
    fresh_db, tmp_path, monkeypatch
):
    from app import config, mcp_server
    from app.services import personas

    persona_dir = tmp_path / "personas"
    persona_dir.mkdir()
    (persona_dir / "atlas-overlay.md").write_text("not parsed for identity ownership\n")
    monkeypatch.setattr(config, "PERSONAS_OVERLAY", persona_dir)

    flock_dir = tmp_path / "flocks"
    flock_dir.mkdir()
    members = sorted(personas.bench_slugs() - {"atlas-overlay"})[:2]
    (flock_dir / "atlas-flock.yaml").write_text(
        "schema_version: 1\n"
        "name: Atlas flock\n"
        "description: Test overlay identity ownership.\n"
        f"members:\n  - {members[0]}\n  - {members[1]}\n"
        "synthesis: false\n"
    )
    monkeypatch.setattr(config, "FLOCKS_OVERLAY", flock_dir)
    monkeypatch.setattr(mcp_server.mcp, "run", lambda: None)

    for subject in ("atlas-overlay", "atlas-flock"):
        module = _module(
            routes=(),
            service_identities=(
                ServiceIdentityContribution("acme.workplace.overlay-service", subject),
            ),
        )
        with (
            pytest.raises(RuntimeError, match="machine identity ownership conflict"),
            TestClient(create_app(modules=(module,))),
        ):
            pass

        settings = replace(AppSettings.from_config(), mcp_user=subject)
        with TestClient(create_app(settings=settings)) as client:
            assert client.get("/health").status_code == 200

        monkeypatch.setattr(mcp_server, "ACTOR", subject)
        with pytest.raises(SystemExit) as raised:
            mcp_server.main()
        assert raised.value.code == 1
        assert fresh_db.query_one("SELECT 1 FROM users WHERE name = ?", (subject,)) is None


def test_dependencies_are_ordered_independently_of_input_order():
    base = _module(module_id="acme.base", routes=())
    child = _module(module_id="acme.child", requires=("acme.base",), routes=())
    registry = ExtensionRegistry.build((child, base))
    assert [module.module_id for module in registry.modules] == ["acme.base", "acme.child"]


def test_job_trigger_input_is_copied_and_read_only():
    trigger = {"trigger": "interval", "hours": 1}
    job = JobContribution(
        "acme.workplace.sync",
        lambda _context: None,
        service_identity="acme-sync",
        policy_action="acme.sync",
        effect="write",
        risk="medium",
        trigger=trigger,
    )
    trigger["hours"] = 99
    assert job.trigger["hours"] == 1
    with pytest.raises(TypeError):
        job.trigger["hours"] = 2  # type: ignore[index]


def test_extension_job_claims_one_run_per_time_window(fresh_db):
    calls: list[str] = []
    module = _module(
        jobs=(
            JobContribution(
                "acme.workplace.sync",
                lambda context: calls.append(context.run_id) or {"synced": 1},
                service_identity="acme-sync",
                policy_action="acme.sync",
                effect="write",
                risk="medium",
                period_hours=1,
            ),
        ),
        service_identities=(
            ServiceIdentityContribution(
                "acme.workplace.sync-identity",
                "acme-sync",
            ),
        ),
    )
    from app.main import _job_specs

    registry = ExtensionRegistry.build((module,))
    spec = next(
        item
        for item in _job_specs(registry, AppSettings.from_config())
        if item.name == "acme.workplace.sync"
    )
    assert spec.fn() == {"synced": 1}
    assert spec.fn()["skipped"] == "this job run is already claimed"
    assert len(calls) == 1


def test_timed_out_write_job_reports_unknown_completion(fresh_db):
    finished = Event()

    def slow_write(_context):
        sleep(0.05)
        finished.set()
        return {"synced": 1}

    module = _module(
        jobs=(
            JobContribution(
                "acme.workplace.slow-sync",
                slow_write,
                service_identity="acme-sync",
                policy_action="acme.sync",
                effect="write",
                risk="medium",
                timeout_seconds=0.001,
            ),
        ),
        service_identities=(
            ServiceIdentityContribution(
                "acme.workplace.sync-identity",
                "acme-sync",
            ),
        ),
    )
    from app.main import _job_specs

    registry = ExtensionRegistry.build((module,))
    spec = next(
        item
        for item in _job_specs(registry, AppSettings.from_config())
        if item.name == "acme.workplace.slow-sync"
    )
    assert spec.fn() == {"status": "error", "error_code": "COMPLETION_UNKNOWN"}
    assert finished.wait(1)


def test_async_background_handlers_are_rejected_during_composition():
    async def async_job(_context):
        return {"synced": 1}

    module = _module(
        jobs=(
            JobContribution(
                "acme.workplace.sync",
                async_job,
                service_identity="acme-sync",
                policy_action="acme.sync",
                effect="write",
                risk="medium",
            ),
        ),
        service_identities=(
            ServiceIdentityContribution(
                "acme.workplace.sync-identity",
                "acme-sync",
            ),
        ),
    )
    with pytest.raises(ExtensionValidationError, match="synchronous handler"):
        ExtensionRegistry.build((module,))


def test_inbound_mcp_uses_core_dependencies_identity_and_workplace_policy(fresh_db, monkeypatch):
    from app import mcp_server
    from app.extensions.policy import current_policy_engine, current_policy_subject

    observed = {}

    def deny_private_action(request):
        if request.action in (
            "acme.workplace.private-read",
            "skein.mcp.tasks.read",
            "skein.mcp.delegation.progress",
        ):
            return PolicyDecision(PolicyEffect.DENY, ("workplace MCP policy",))
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.3.0",
        requires=("skein.core",),
        policies=(PolicyContribution("acme.workplace.mcp-policy", deny_private_action),),
        identities=(
            IdentityContribution(
                "acme.workplace.mcp-identity",
                lambda _name, _groups, _authenticated: {
                    "roles": ("integration",),
                    "capabilities": ("acme.mcp",),
                },
            ),
        ),
    )

    def run():
        subject = current_policy_subject()
        observed["subject"] = subject
        observed["decision"] = current_policy_engine().decide(
            PolicyInput(
                subject=subject,
                action="acme.workplace.private-read",
                resource=PolicyResource("integration"),
                origin="mcp",
                agent=subject.name,
            )
        )
        observed["read"] = json.loads(mcp_server.list_tasks())
        observed["write"] = json.loads(mcp_server.report_progress(999, "must not land"))

    monkeypatch.setattr(mcp_server, "ACTOR", "acme-mcp")
    monkeypatch.setattr(mcp_server.mcp, "run", run)
    mcp_server.main((module,))

    assert observed["subject"].roles == ("integration",)
    assert observed["subject"].capabilities == ("acme.mcp",)
    assert observed["decision"].effect == PolicyEffect.DENY
    assert observed["read"]["policy_effect"] == "deny"
    assert observed["write"]["policy_effect"] == "deny"
    assert fresh_db.query_one("SELECT 1 AS present FROM task_worklog") is None


def test_inbound_mcp_delegation_uses_authoritative_crew_task_context(fresh_db, monkeypatch):
    from app import mcp_server
    from app.extensions.policy import (
        reset_policy_engine,
        reset_policy_subject,
        set_policy_engine,
        set_policy_subject,
    )
    from app.services import crews, delegation, users, work

    users.ensure_user("sponsor")
    crew = crews.create_crew("Delivery", actor="sponsor")
    task = work.create_task(
        "Crew delivery",
        actor="sponsor",
        visibility="crew",
        crew_id=crew["id"],
    )
    delegation.delegate_task(task["id"], "crew-agent", "sponsor", actor="sponsor")
    observed: list[PolicyResource] = []

    def deny_crew_claim(request: PolicyInput):
        if request.action == "skein.mcp.delegation.claim":
            observed.append(request.resource)
            if request.resource.classification == "crew":
                return PolicyDecision(PolicyEffect.DENY)
        return None

    module = _module(
        routes=(),
        policies=(PolicyContribution("acme.workplace.crew-policy", deny_crew_claim),),
    )
    registry = ExtensionRegistry.build((module,))
    monkeypatch.setattr(mcp_server, "ACTOR", "crew-agent")
    engine_token = set_policy_engine(registry.policy_engine)
    subject_token = set_policy_subject(PolicySubject("crew-agent", kind="agent"))
    try:
        result = json.loads(mcp_server.claim_delegated_task(task["id"]))
    finally:
        reset_policy_subject(subject_token)
        reset_policy_engine(engine_token)

    assert result["policy_effect"] == "deny"
    assert observed[0].classification == "crew"
    assert fresh_db.query_one("SELECT status FROM tasks WHERE id = ?", (task["id"],)) == {
        "status": "todo"
    }
