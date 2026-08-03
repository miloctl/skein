"""Git forge events -> task status. A branch named for a task, a pull
request that names it, a merge: the work moves without anyone retyping it.

Inbound only. Skein never writes back to the forge, so there is no echo to
suppress and no token to store — the webhook secret is the whole contract.
"""

import re

from .. import db
from . import work

# Event -> the status it means. A constant, not a table: the rule table
# earns its schema when a SECOND forge exists (docs/ROADMAP.md K1).
TRANSITIONS = {
    "branch_push": "in_progress",
    "pr_opened": "in_progress",
    "pr_merged": "done",
}

# `task/42-fix-login` is what `skein task start 42` writes (ROADMAP D3), plus
# the prefixed forms people type by hand: `feature/task-42`, `fix/task-42-x`.
_BRANCH = re.compile(r"(?:^|/)task[/-](\d{1,9})(?:[-/]|$)", re.ASCII | re.IGNORECASE)
# The text fallback REQUIRES the word "task". A bare `#42` is how the forge
# itself numbers issues and pull requests, so matching it would close Skein
# task 42 because someone's PR mentioned Gitea issue 42.
_TEXT = re.compile(r"task\s*#?\s*(\d{1,9})", re.ASCII | re.IGNORECASE)


def match_task(branch: str = "", title: str = "", body: str = "") -> int | None:
    """Branch name first: it is the only field a person cannot retitle later."""
    for pattern, text in ((_BRANCH, branch), (_TEXT, title), (_TEXT, body)):
        found = pattern.search(text or "")
        if found:
            return int(found.group(1))
    return None


def forge_event(
    kind: str,
    branch: str = "",
    title: str = "",
    body: str = "",
    url: str = "",
    *,
    actor: str = "forge",
) -> dict:
    if kind not in TRANSITIONS:
        raise ValueError(f"kind must be one of {tuple(TRANSITIONS)}")
    task_id = match_task(branch, title, body)
    if task_id is None:
        return {"ignored": "no task reference in the branch name, title, or body"}
    task = db.query_one("SELECT status, delegated_agent FROM tasks WHERE id = ?", (task_id,))
    if not task:
        return {"ignored": f"task #{task_id} not found"}
    status = TRANSITIONS[kind]
    # a push to a merged task's branch must not reopen finished work — the
    # forge reports activity, and activity on a done task is not a regression
    if task["status"] == "done" and status != "done":
        return {"task_id": task_id, "ignored": "task is already done"}
    # delegated work closes on the sponsor's verdict, never on a side channel:
    # whoever merged the pull request is not necessarily the sponsor, and
    # submit_for_acceptance is the only path that records a verdict
    if status == "done" and task["delegated_agent"]:
        return {"task_id": task_id, "ignored": "task is delegated — the sponsor accepts it"}
    if task["status"] == status and not url:
        return {"task_id": task_id, "ignored": f"task is already {status}"}
    work.update_task(
        task_id,
        status="" if task["status"] == status else status,
        forge_url=url,
        actor=actor,
        origin="agent",
    )
    return {"task_id": task_id, "status": status, "url": url}


def parse_gitea(event: str, payload: dict) -> dict | None:
    """Map a Gitea webhook to the generic shape. None means "not an event
    that moves work" — a comment, a label, a draft, a closed-unmerged pull
    request. Returning None is the honest answer, not an error."""
    sender = (payload.get("sender") or {}).get("login") or ""
    if event == "push":
        ref = payload.get("ref") or ""
        if not ref.startswith("refs/heads/"):
            return None  # a tag or a note, not a branch
        return {
            "kind": "branch_push",
            "branch": ref[len("refs/heads/") :],
            "url": payload.get("compare_url") or "",
            "actor": (payload.get("pusher") or {}).get("login") or sender,
        }
    if event == "pull_request":
        pr = payload.get("pull_request") or {}
        action = payload.get("action") or ""
        if action in ("opened", "reopened"):
            kind = "pr_opened"
        elif action == "closed" and pr.get("merged"):
            kind = "pr_merged"
        else:
            return None
        return {
            "kind": kind,
            "branch": (pr.get("head") or {}).get("ref") or "",
            "title": pr.get("title") or "",
            "body": pr.get("body") or "",
            "url": pr.get("html_url") or "",
            "actor": sender or (pr.get("user") or {}).get("login") or "",
        }
    return None


def resolve_actor(login: str) -> str:
    """A forge login that names a teammate attributes the move to them; every
    other login becomes 'forge'. Agent identities never match: an agent that
    could act through the webhook would reach the human write path with the
    review gate behind it."""
    from .users import is_agent

    if login:
        row = db.query_one(
            "SELECT name FROM users WHERE lower(name) = lower(?) AND active = 1", (login,)
        )
        if row and not is_agent(row["name"]):
            return row["name"]
    return "forge"
