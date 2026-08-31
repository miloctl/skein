"""Publication and finalization consume only a gated GitHub release artifact."""

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
    expired: bool = False,
    artifact_id: object = 987,
    artifact_sha: str = SHA,
    duplicate_packages: bool = False,
):
    jobs = [
        {
            "name": name,
            "status": "completed",
            "conclusion": "failure" if name == failed_job else "success",
        }
        for name in sorted(validate_release_run.REQUIRED_JOBS)
    ]
    if duplicate_packages:
        jobs.append({"name": "packages", "status": "completed", "conclusion": "success"})

    def get_json(url: str, _token: str):
        if "/jobs?" in url:
            return {"jobs": jobs}
        if "/artifacts?" in url:
            return {
                "artifacts": [
                    {
                        "id": artifact_id,
                        "name": "release-packages",
                        "expired": expired,
                        "digest": "sha256:" + "b" * 64,
                        "workflow_run": {"head_sha": artifact_sha},
                    },
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


def test_release_run_accepts_the_gated_artifact(monkeypatch):
    monkeypatch.setattr(validate_release_run, "_get_json", _responses())

    release = validate_release_run.validate_release_run(
        "https://api.github.test", "miloctl/skein", "12345", "token"
    )
    assert release.release_sha == SHA
    assert release.artifact_id == 987
    assert release.artifact_digest == "sha256:" + "b" * 64


def test_release_run_refuses_a_run_with_a_failed_gate(monkeypatch):
    monkeypatch.setattr(
        validate_release_run, "_get_json", _responses(failed_job="extension-contracts")
    )

    with pytest.raises(validate_release_run.ValidationError, match="release gate"):
        validate_release_run.validate_release_run(
            "https://api.github.test", "miloctl/skein", "12345", "token"
        )


def test_release_run_refuses_an_insecure_api_url():
    with pytest.raises(validate_release_run.ValidationError, match="must use HTTPS"):
        validate_release_run.validate_release_run(
            "http://api.github.test", "miloctl/skein", "12345", "token"
        )


def test_release_run_refuses_an_expired_artifact(monkeypatch):
    monkeypatch.setattr(validate_release_run, "_get_json", _responses(expired=True))

    with pytest.raises(validate_release_run.ValidationError, match="no usable"):
        validate_release_run.validate_release_run(
            "https://api.github.test", "miloctl/skein", "12345", "token"
        )


def test_release_run_refuses_an_invalid_artifact_id(monkeypatch):
    monkeypatch.setattr(validate_release_run, "_get_json", _responses(artifact_id="bad"))
    with pytest.raises(validate_release_run.ValidationError, match="artifact ID"):
        validate_release_run.validate_release_run(
            "https://api.github.test", "miloctl/skein", "12345", "token"
        )


def test_release_run_refuses_an_artifact_from_another_sha(monkeypatch):
    monkeypatch.setattr(validate_release_run, "_get_json", _responses(artifact_sha="c" * 40))
    with pytest.raises(validate_release_run.ValidationError, match="commit SHA"):
        validate_release_run.validate_release_run(
            "https://api.github.test", "miloctl/skein", "12345", "token"
        )


def test_release_run_refuses_a_rebuilt_packages_job(monkeypatch):
    monkeypatch.setattr(validate_release_run, "_get_json", _responses(duplicate_packages=True))
    with pytest.raises(validate_release_run.ValidationError, match="packages job"):
        validate_release_run.validate_release_run(
            "https://api.github.test", "miloctl/skein", "12345", "token"
        )
