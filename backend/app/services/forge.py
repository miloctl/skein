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
# The text fallback needs a CLOSING VERB, not just the word "task". "Blocked
# by task 2 until Friday" names a task it must never close, and `\b…\s+task`
# is also what stops `subtask 3` and `multitask 5` from matching. A bare `#42`
# is excluded for its own reason: that is how the forge numbers ITS issues, so
# matching it would close Skein task 42 for a PR about Gitea issue 42.
#
# Separators are fixed-width ([ \t]?), never `\s*`: `\s*#?\s*` is ambiguous,
# so a run of n spaces after "task" backtracks n+1 ways at every position —
# 31 seconds of blocked event loop for an 80 KB title, measured.
_TEXT = re.compile(
    r"\b(?:clos(?:e|es|ed)|fix(?:es|ed)?|resolv(?:e|es|ed))[ \t]+task[ \t]?#?[ \t]?(\d{1,9})\b",
    re.ASCII | re.IGNORECASE,
)
# a pull request body is free text from the forge — scan a bounded prefix, the
# way every REST write path caps its fields
_SCAN = {"branch": 200, "title": 400, "body": 4000}


def match_task(branch: str = "", title: str = "", body: str = "") -> int | None:
    """Branch name first: it is the only field a person cannot retitle later."""
    for pattern, text, cap in (
        (_BRANCH, branch, _SCAN["branch"]),
        (_TEXT, title, _SCAN["title"]),
        (_TEXT, body, _SCAN["body"]),
    ):
        found = pattern.search((text or "")[:cap])
        if found:
            return int(found.group(1))
    return None


def _clean_url(url: str) -> str:
    """A forge-supplied URL reaches an href and any future renderer that is
    not React (a digest, a Slack card, the CLI). Only http(s) survives here,
    bounded, so no renderer has to remember."""
    url = (url or "").strip()[:2000]
    return url if url.startswith(("https://", "http://")) else ""


def forge_event(
    kind: str,
    branch: str = "",
    title: str = "",
    body: str = "",
    url: str = "",
    *,
    pushed_by: str = "",
) -> dict:
    """The actor is ALWAYS 'forge', never the teammate the login names.

    activity rows are hash-chained and can never be corrected, so a caller
    holding the shared secret must not be able to write one that reads as a
    person's own click. Whoever pushed is named in the detail instead: it is
    the same information, as data rather than as identity."""
    if kind not in TRANSITIONS:
        raise ValueError(f"kind must be one of {tuple(TRANSITIONS)}")
    task_id = match_task(branch, title, body)
    if task_id is None:
        return {"ignored": "no task reference in the branch name, title, or body"}
    task = db.query_one(
        "SELECT status, delegated_agent, forge_url FROM tasks WHERE id = ?", (task_id,)
    )
    if not task:
        return {"ignored": f"task #{task_id} not found"}
    status = TRANSITIONS[kind]
    url = _clean_url(url)
    # a push to a merged task's branch must not reopen finished work — the
    # forge reports activity, and activity on a done task is not a regression
    if task["status"] == "done" and status != "done":
        return {"task_id": task_id, "ignored": "task is already done"}
    # delegated work closes on the sponsor's verdict, never on a side channel:
    # whoever merged the pull request is not necessarily the sponsor, and
    # submit_for_acceptance is the only path that records a verdict
    if status == "done" and task["delegated_agent"]:
        return {"task_id": task_id, "ignored": "task is delegated — the sponsor accepts it"}
    # every push to an open branch re-delivers the same event. Comparing the
    # STORED url, not just "was a url sent", is what keeps a busy repo from
    # appending an unprunable hash-chained row per push.
    if task["status"] == status and url in ("", task["forge_url"]):
        return {"task_id": task_id, "ignored": f"task is already {status}"}
    work.update_task(
        task_id,
        status="" if task["status"] == status else status,
        forge_url=url,
        actor="forge",
        note=f" (pushed by {pushed_by})" if pushed_by else " (from the forge)",
    )
    return {"task_id": task_id, "status": status, "url": url}


def parse_gitea(event: str, payload: dict) -> dict | None:
    """Map a Gitea webhook to the generic shape. None means "not an event
    that moves work" — a comment, a label, a draft, a closed-unmerged pull
    request. Returning None is the honest answer, not an error."""
    sender = (payload.get("sender") or {}).get("login") or ""
    repo = (payload.get("repository") or {}).get("html_url") or ""
    if event == "push":
        ref = payload.get("ref") or ""
        if not ref.startswith("refs/heads/"):
            return None  # a tag or a note, not a branch
        branch = ref[len("refs/heads/") :]
        # Gitea fills compare_url only when a branch ALREADY existed: the push
        # that creates `task/42-fix` — the one that starts the task — sends the
        # bare instance root, which would link `code ↗` at the forge home page.
        # The branch page is the better link anyway.
        compare = payload.get("compare_url") or ""
        if repo and not compare.startswith(f"{repo}/"):
            compare = f"{repo}/src/branch/{branch}"
        return {
            "kind": "branch_push",
            "branch": branch,
            "url": compare,
            "pushed_by": (payload.get("pusher") or {}).get("login") or sender,
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
            "pushed_by": sender or (pr.get("user") or {}).get("login") or "",
        }
    return None


def resolve_pusher(login: str) -> str:
    """The teammate a forge login names, for the ledger DETAIL — never for the
    actor (see forge_event). An agent name resolves to nothing: the ledger
    must not read as though an agent acted, and an agent's moves belong to the
    gated tool surface."""
    from .users import is_agent

    if login:
        row = db.query_one(
            "SELECT name FROM users WHERE lower(name) = lower(?) AND active = 1", (login,)
        )
        if row and not is_agent(row["name"]):
            return row["name"]
    return ""
