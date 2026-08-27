"""Validate a prior GitHub release run before retrying publication."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

PUBLISH_JOBS = {"publish-pypi", "publish-npm"}
REQUIRED_JOBS = {
    "packages",
    "backend",
    "frontend",
    "extension-contracts",
    "e2e",
    "release-guard",
}


class ValidationError(ValueError):
    pass


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
) -> str:
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

    jobs = _get_json(f"{base}/jobs?filter=latest&per_page=100", token).get("jobs")
    if not isinstance(jobs, list):
        raise ValidationError("GitHub returned an invalid release-job list.")
    states = {
        str(job.get("name")): (job.get("status"), job.get("conclusion"))
        for job in jobs
        if isinstance(job, dict)
    }
    failed = sorted(name for name in REQUIRED_JOBS if states.get(name) != ("completed", "success"))
    if failed:
        raise ValidationError("The selected run did not pass every release gate.")
    if any(
        states.get(name, (None, None))[0] != "completed"
        or states.get(name, (None, None))[1] in (None, "", "skipped")
        for name in PUBLISH_JOBS
    ):
        raise ValidationError("The selected run did not attempt every publisher.")

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

    return head_sha


def main() -> int:
    try:
        head_sha = validate_release_run(
            os.environ.get("GITHUB_API_URL", "https://api.github.com"),
            os.environ["GITHUB_REPOSITORY"],
            os.environ["RETRY_RUN_ID"],
            os.environ["GITHUB_TOKEN"],
        )
        output = Path(os.environ["GITHUB_OUTPUT"])
        with output.open("a", encoding="utf-8") as stream:
            stream.write(f"artifact_run_id={os.environ['RETRY_RUN_ID']}\n")
            stream.write(f"release_sha={head_sha}\n")
        return 0
    except (KeyError, ValidationError) as exc:
        message = str(exc) if isinstance(exc, ValidationError) else "A release-run input is absent."
        print(f"validate-release-run: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
