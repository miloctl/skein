"""The public application-composition contract used by workplace packages."""

from dataclasses import FrozenInstanceError, replace

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.extensions import (
    AppSettings,
    ExtensionRegistry,
    ExtensionValidationError,
    IdentityContribution,
    JobContribution,
    LifecycleContribution,
    PolicyContribution,
    PolicyDecision,
    PolicyEffect,
    PolicyInput,
    PolicyResource,
    RouteContribution,
    SkeinModule,
)
from app.main import app as default_app
from app.main import create_app


def _router(module_id: str = "acme.workplace") -> APIRouter:
    router = APIRouter(prefix=f"/api/extensions/{module_id}")

    @router.get("/ping")
    def ping():
        return {"module": module_id}

    return router


def _module(**changes) -> SkeinModule:
    values = {
        "module_id": "acme.workplace",
        "version": "1.2.0",
        "routes": (RouteContribution("acme.workplace.routes", _router()),),
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

    def startup(_context):
        events.append("startup")

    def shutdown(_context):
        events.append("shutdown")

    module = _module(
        jobs=(
            JobContribution(
                "acme.workplace.sync",
                lambda _context: events.append("job") or {"synced": 1},
                catch_up=True,
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
            _module(
                routes=(RouteContribution("acme.workplace.routes", _router("wrong.namespace")),)
            ),
            "must be under",
        ),
        (
            _module(jobs=(JobContribution("sync", lambda _context: None),)),
            "must start with",
        ),
    ],
)
def test_invalid_module_contracts_fail_before_startup(module, message):
    with pytest.raises(ExtensionValidationError, match=message):
        create_app(modules=(module,))


def test_duplicate_ids_contributions_and_cycles_are_rejected():
    with pytest.raises(ExtensionValidationError, match="duplicate module id"):
        ExtensionRegistry.build((_module(), _module()))

    duplicate = _module(
        routes=(
            RouteContribution("acme.workplace.routes", _router()),
            RouteContribution("acme.workplace.routes", _router()),
        )
    )
    with pytest.raises(ExtensionValidationError, match="duplicate route"):
        ExtensionRegistry.build((duplicate,))

    collision = _module(
        routes=(
            RouteContribution("acme.workplace.first", _router()),
            RouteContribution("acme.workplace.second", _router()),
        )
    )
    with pytest.raises(ExtensionValidationError, match="route collision"):
        ExtensionRegistry.build((collision,))

    left = _module(module_id="acme.left", requires=("acme.right",), routes=())
    right = _module(module_id="acme.right", requires=("acme.left",), routes=())
    with pytest.raises(ExtensionValidationError, match="dependency cycle"):
        ExtensionRegistry.build((left, right))


def test_dependencies_are_ordered_independently_of_input_order():
    base = _module(module_id="acme.base", routes=())
    child = _module(module_id="acme.child", requires=("acme.base",), routes=())
    registry = ExtensionRegistry.build((child, base))
    assert [module.module_id for module in registry.modules] == ["acme.base", "acme.child"]


def test_job_trigger_input_is_copied_and_read_only():
    trigger = {"trigger": "interval", "hours": 1}
    job = JobContribution("acme.workplace.sync", lambda _context: None, trigger)
    trigger["hours"] = 99
    assert job.trigger["hours"] == 1
    with pytest.raises(TypeError):
        job.trigger["hours"] = 2  # type: ignore[index]


def test_inbound_mcp_uses_core_dependencies_identity_and_workplace_policy(fresh_db, monkeypatch):
    from app import mcp_server
    from app.extensions.policy import current_policy_engine, current_policy_subject

    observed = {}

    def deny_private_action(request):
        if request.action == "acme.workplace.private-read":
            return PolicyDecision(PolicyEffect.DENY, ("workplace MCP policy",))
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
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

    monkeypatch.setattr(mcp_server, "ACTOR", "acme-mcp")
    monkeypatch.setattr(mcp_server.mcp, "run", run)
    mcp_server.main((module,))

    assert observed["subject"].roles == ("integration",)
    assert observed["subject"].capabilities == ("acme.mcp",)
    assert observed["decision"].effect == PolicyEffect.DENY
