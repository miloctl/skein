"""Publication retries consume only a gated GitHub release artifact."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "validate_release_run", ROOT / "scripts/validate_release_run.py"
)
assert SPEC and SPEC.loader
validate_release_run = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validate_release_run)

SHA = "a" * 40


def _responses(
    *,
    failed_job: str = "",
    skipped_publisher: str = "",
    incomplete_publisher: str = "",
    expired: bool = False,
):
    jobs = [
        {
            "name": name,
            "status": "completed",
            "conclusion": "failure" if name == failed_job else "success",
        }
        for name in sorted(validate_release_run.REQUIRED_JOBS)
    ]
    jobs.extend(
        {
            "name": name,
            "status": "in_progress" if name == incomplete_publisher else "completed",
            "conclusion": (
                None
                if name == incomplete_publisher
                else "skipped"
                if name == skipped_publisher
                else "success"
            ),
        }
        for name in sorted(validate_release_run.PUBLISH_JOBS)
    )

    def get_json(url: str, _token: str):
        if "/jobs?" in url:
            return {"jobs": jobs}
        if "/artifacts?" in url:
            return {
                "artifacts": [
                    {"name": "release-packages", "expired": expired},
                ]
            }
        return {
            "name": "ci",
            "path": ".github/workflows/ci.yml",
            "event": "push",
            "head_branch": "main",
            "head_sha": SHA,
        }

    return get_json


def test_release_retry_accepts_the_gated_artifact(monkeypatch):
    monkeypatch.setattr(validate_release_run, "_get_json", _responses())

    assert (
        validate_release_run.validate_release_run(
            "https://api.github.test", "miloctl/skein", "12345", "token"
        )
        == SHA
    )


def test_release_retry_refuses_a_run_with_a_failed_gate(monkeypatch):
    monkeypatch.setattr(
        validate_release_run, "_get_json", _responses(failed_job="extension-contracts")
    )

    with pytest.raises(validate_release_run.ValidationError, match="release gate"):
        validate_release_run.validate_release_run(
            "https://api.github.test", "miloctl/skein", "12345", "token"
        )


def test_release_retry_refuses_a_nonrelease_run(monkeypatch):
    monkeypatch.setattr(
        validate_release_run,
        "_get_json",
        _responses(skipped_publisher="publish-npm"),
    )

    with pytest.raises(validate_release_run.ValidationError, match="every publisher"):
        validate_release_run.validate_release_run(
            "https://api.github.test", "miloctl/skein", "12345", "token"
        )


def test_release_retry_refuses_an_incomplete_publisher(monkeypatch):
    monkeypatch.setattr(
        validate_release_run,
        "_get_json",
        _responses(incomplete_publisher="publish-pypi"),
    )

    with pytest.raises(validate_release_run.ValidationError, match="every publisher"):
        validate_release_run.validate_release_run(
            "https://api.github.test", "miloctl/skein", "12345", "token"
        )


def test_release_retry_refuses_an_insecure_api_url():
    with pytest.raises(validate_release_run.ValidationError, match="must use HTTPS"):
        validate_release_run.validate_release_run(
            "http://api.github.test", "miloctl/skein", "12345", "token"
        )


def test_release_retry_refuses_an_expired_artifact(monkeypatch):
    monkeypatch.setattr(validate_release_run, "_get_json", _responses(expired=True))

    with pytest.raises(validate_release_run.ValidationError, match="no usable"):
        validate_release_run.validate_release_run(
            "https://api.github.test", "miloctl/skein", "12345", "token"
        )
