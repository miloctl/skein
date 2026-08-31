"""Validate the green ci run a publication or finalization names."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, NamedTuple

# The gates a publishable run must have passed. `release-guard` and the two
# publisher jobs left ci.yml when publication moved to publish-release.yml, so
# requiring them here would reject every run this validator now exists to
# accept. Publication is no longer proved by a job status at all: finalize
# compares the registry bytes against this run's artifact, which is a stronger
# claim than "a publisher job ran" ever was.
REQUIRED_JOBS = {
    "packages",
    "backend",
    "frontend",
    "extension-contracts",
    "e2e",
}


class ValidationError(ValueError):
    pass


class ReleaseRun(NamedTuple):
    artifact_run_id: str
    artifact_id: int
    artifact_digest: str
    release_sha: str


def _get_json(url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(  # noqa: S310 — the caller accepts only HTTPS
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            value = json.load(response)
    except (OSError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        raise ValidationError("GitHub did not return usable release-run data.") from exc
    if not isinstance(value, dict):
        raise ValidationError("GitHub returned an invalid release-run response.")
    return value


def validate_release_run(
    api_url: str,
    repository: str,
    run_id: str,
    token: str,
) -> ReleaseRun:
    if not run_id.isascii() or not run_id.isdigit():
        raise ValidationError("The release run ID must contain digits only.")
    parsed = urllib.parse.urlparse(api_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValidationError("The GitHub API URL must use HTTPS.")

    base = f"{api_url.rstrip('/')}/repos/{repository}/actions/runs/{run_id}"
    run = _get_json(base, token)
    if (
        run.get("name") != "ci"
        or run.get("path") != ".github/workflows/ci.yml"
        or run.get("event") != "push"
        or run.get("head_branch") != "main"
    ):
        raise ValidationError("The selected run is not a main-branch release run.")

    head_sha = str(run.get("head_sha") or "")
    if len(head_sha) != 40 or any(char not in "0123456789abcdef" for char in head_sha):
        raise ValidationError("The selected run has an invalid commit SHA.")

    jobs = _get_json(f"{base}/jobs?filter=all&per_page=100", token).get("jobs")
    if not isinstance(jobs, list):
        raise ValidationError("GitHub returned an invalid release-job list.")
    package_jobs = [job for job in jobs if isinstance(job, dict) and job.get("name") == "packages"]
    if len(package_jobs) != 1:
        raise ValidationError("The selected run has more than one packages job attempt.")
    states = {
        str(job.get("name")): (job.get("status"), job.get("conclusion"))
        for job in jobs
        if isinstance(job, dict)
    }
    failed = sorted(name for name in REQUIRED_JOBS if states.get(name) != ("completed", "success"))
    if failed:
        raise ValidationError("The selected run did not pass every release gate.")

    artifacts = _get_json(f"{base}/artifacts?per_page=100", token).get("artifacts")
    if not isinstance(artifacts, list):
        raise ValidationError("GitHub returned an invalid release-artifact list.")
    matches = [
        artifact
        for artifact in artifacts
        if isinstance(artifact, dict) and artifact.get("name") == "release-packages"
    ]
    if len(matches) != 1 or matches[0].get("expired") is not False:
        raise ValidationError("The selected run has no usable release-packages artifact.")
    artifact = matches[0]
    artifact_id = artifact.get("id")
    if not isinstance(artifact_id, int) or artifact_id <= 0:
        raise ValidationError("The selected artifact has an invalid artifact ID.")
    workflow_run = artifact.get("workflow_run")
    if isinstance(workflow_run, dict) and workflow_run.get("head_sha") not in (None, head_sha):
        raise ValidationError("The selected artifact belongs to another commit SHA.")
    digest = str(artifact.get("digest") or "")
    if digest and not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValidationError("The selected artifact has an invalid digest.")

    return ReleaseRun(run_id, artifact_id, digest, head_sha)


def main() -> int:
    try:
        # RETRY_RUN_ID was the retry path's name for it, and that path is gone.
        # Both callers (publish-release, finalize-release) set RELEASE_RUN_ID.
        run_id = os.environ["RELEASE_RUN_ID"]
        release = validate_release_run(
            os.environ.get("GITHUB_API_URL", "https://api.github.com"),
            os.environ["GITHUB_REPOSITORY"],
            run_id,
            os.environ["GITHUB_TOKEN"],
        )
        output = Path(os.environ["GITHUB_OUTPUT"])
        with output.open("a", encoding="utf-8") as stream:
            stream.write(f"artifact_run_id={release.artifact_run_id}\n")
            stream.write(f"artifact_id={release.artifact_id}\n")
            stream.write(f"artifact_digest={release.artifact_digest}\n")
            stream.write(f"release_sha={release.release_sha}\n")
        return 0
    except (KeyError, ValidationError) as exc:
        message = str(exc) if isinstance(exc, ValidationError) else "A release-run input is absent."
        print(f"validate-release-run: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
