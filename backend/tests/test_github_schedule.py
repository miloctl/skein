"""Recovery starts in the background without synchronous startup network I/O."""

import threading

from app.main import _start_scheduler
from app.services.jobs import JobSpec


def test_github_recovery_starts_without_waiting_for_its_period(fresh_db):
    recovery = threading.Event()
    ordinary = threading.Event()
    scheduler = _start_scheduler(
        (
            JobSpec("github-recovery", recovery.set, {"trigger": "interval", "minutes": 5}, 5 / 60),
            JobSpec(
                "ordinary-interval", ordinary.set, {"trigger": "interval", "minutes": 5}, 5 / 60
            ),
        )
    )
    try:
        assert recovery.wait(5), "GitHub recovery did not start after scheduler startup"
        assert not ordinary.is_set()
    finally:
        scheduler.shutdown(wait=True)


def test_github_recovery_uses_the_composed_policy_registry(fresh_db, monkeypatch):
    from app.extensions import AppSettings
    from app.main import _job_specs, create_app
    from app.services import github_recovery

    registry = create_app().state.skein_registry
    seen = []

    def run(*, registry):
        seen.append(registry)
        return {"status": "noop"}

    monkeypatch.setattr(github_recovery, "run", run)
    spec = next(
        item
        for item in _job_specs(registry, AppSettings.from_config())
        if item.name == "github-recovery"
    )
    assert spec.catch_up is False
    assert spec.retry_safe is True
    assert spec.fn() == {"status": "noop"}
    assert seen == [registry]
