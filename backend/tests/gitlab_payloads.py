"""GitLab webhook payloads for the tests, UNVERIFIED.

Built from GitLab's documented webhook shapes and kaneo's types
(apps/api/src/plugins/gitlab/webhooks/push.ts, merge-request-opened.ts,
utils/payload.ts), not captured from a running instance: an owner-approved
exception to the CLAUDE.md fixture rule (docs/intent/gitlab-forge.md, D14).
The ROADMAP row "Captured GitLab webhook fixtures" replaces them with
deliveries copied from the internal instance's Recent events. Until then, a
field GitLab names differently shows first in production, as an ignored
delivery.
"""

HOST = "gitlab.example.test"
WEB_URL = f"https://{HOST}/team/app"
SHA = "da1560886d4f094c3e6c9ef40349f7d38b5d27d7"
ZEROS = "0" * 40

EVENTS = {
    "push": "Push Hook",
    "merge_request": "Merge Request Hook",
    "pipeline": "Pipeline Hook",
}


def _project(default_branch: str = "main") -> dict:
    return {
        "id": 15,
        "name": "app",
        "web_url": WEB_URL,
        "path_with_namespace": "team/app",
        "default_branch": default_branch,
        "git_ssh_url": f"git@{HOST}:team/app.git",
        "git_http_url": f"{WEB_URL}.git",
    }


def push(branch: str, *, username: str = "mira", after: str = SHA, ref: str = "") -> dict:
    return {
        "object_kind": "push",
        "event_name": "push",
        "before": "95790bf891e76fee5e1747ab589903a6a1f80f22",
        "after": after,
        "ref": ref or f"refs/heads/{branch}",
        "checkout_sha": after,
        "user_id": 4,
        "user_name": "Mira",
        "user_username": username,
        "project_id": 15,
        "project": _project(),
        "repository": {
            "name": "app",
            "url": f"git@{HOST}:team/app.git",
            "homepage": WEB_URL,
        },
        "commits": [
            {"id": after, "message": "Fix the login form\n", "url": f"{WEB_URL}/-/commit/{after}"}
        ],
        "total_commits_count": 1,
    }


def merge_request(
    action: str,
    *,
    source_branch: str,
    title: str = "Fix the login form",
    description: str | None = "",
    username: str = "mira",
    iid: int = 7,
) -> dict:
    return {
        "object_kind": "merge_request",
        "event_type": "merge_request",
        "user": {"id": 4, "name": "Mira", "username": username},
        "project": _project(),
        "object_attributes": {
            "id": 99,
            "iid": iid,
            "action": action,
            "state": {"merge": "merged", "close": "closed"}.get(action, "opened"),
            "source_branch": source_branch,
            "target_branch": "main",
            "title": title,
            "description": description,
            "draft": False,
            "url": f"{WEB_URL}/-/merge_requests/{iid}",
        },
        "labels": [],
    }


def pipeline(
    status: str,
    *,
    ref: str = "main",
    tag: bool = False,
    default_branch: str = "main",
    pipeline_id: int = 31,
) -> dict:
    return {
        "object_kind": "pipeline",
        "object_attributes": {
            "id": pipeline_id,
            "iid": 3,
            "ref": ref,
            "tag": tag,
            "sha": SHA,
            "source": "push",
            "status": status,
            "detailed_status": {"success": "passed", "failed": "failed"}.get(status, status),
            "url": f"{WEB_URL}/-/pipelines/{pipeline_id}",
        },
        "user": {"id": 4, "name": "Mira", "username": "mira"},
        "project": _project(default_branch),
        "commit": {"id": SHA, "message": "Fix the login form\n"},
        "builds": [],
    }
