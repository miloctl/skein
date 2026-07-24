"""CI webhook -> blocker register. A red default-branch build is a de facto
team blocker; make it a real one, deduped, and auto-resolve on green."""

from .. import db
from . import blockers

DEFAULT_BRANCHES = ("main", "master")


def ci_event(repo: str, branch: str, status: str, run_url: str = "", *, actor: str = "ci") -> dict:
    if not repo or not branch:
        raise ValueError("repo and branch are required")
    if status not in ("success", "failure"):
        raise ValueError("status must be success or failure")
    if branch not in DEFAULT_BRANCHES:
        return {"ignored": f"branch {branch} is not a default branch"}

    source = f"ci:{repo}:{branch}"
    existing = db.query(
        "SELECT * FROM blockers WHERE source = ? AND status != 'resolved'", (source,)
    )

    if status == "failure":
        if existing:
            return {"deduped": True, "blocker_id": existing[0]["id"]}
        result = blockers.raise_blocker(
            title=f"CI red on {repo}@{branch}",
            detail=f"Failing build: {run_url or 'no run URL provided'}",
            owner="team",
            impact="high",
            source=source,
            actor=actor,
            origin="agent",
        )
        return {"blocker_id": result["id"], "raised": True}

    resolved = [
        blockers.resolve_blocker(
            b["id"], resolution=f"CI green again: {run_url}", actor=actor, origin="agent"
        )["id"]
        for b in existing
    ]
    return {"resolved": resolved}


def parse_github_actions(payload: dict) -> dict | None:
    """Map a GitHub Actions workflow_run webhook to our generic shape."""
    run = payload.get("workflow_run")
    if not run or run.get("status") != "completed":
        return None
    conclusion = run.get("conclusion")
    if conclusion == "success":
        status = "success"
    elif conclusion in ("failure", "timed_out"):
        status = "failure"
    else:  # cancelled, skipped, action_required, neutral, stale — not a red build
        return None
    return {
        "repo": (payload.get("repository") or {}).get("full_name", ""),
        "branch": run.get("head_branch", ""),
        "status": status,
        "run_url": run.get("html_url", ""),
    }
