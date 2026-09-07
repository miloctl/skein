"""Signed forge events share one policy, receipt and task-application path."""

import hashlib
import json
import re
from urllib.parse import quote, urlsplit
from uuid import UUID

from .. import config, db
from . import work

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
# the payload is already bounded by MAX_FORGE_BODY, and _TEXT is linear, so
# these are correctness bounds only: a branch name or title longer than this
# is not a ref anyone typed. The BODY is scanned whole — a closing verb at the
# end of a long pull request description is exactly the case that must work.
_SCAN = {"branch": 200, "title": 400}


def match_task(branch: str = "", title: str = "", body: str = "") -> int | None:
    """Branch name first: it is the only field a person cannot retitle later."""
    for pattern, text, cap in (
        (_BRANCH, branch, _SCAN["branch"]),
        (_TEXT, title, _SCAN["title"]),
        (_TEXT, body, None),
    ):
        found = pattern.search((text or "")[:cap] if cap else (text or ""))
        if found:
            return int(found.group(1))
    return None


def _clean_url(url: str) -> str:
    """A forge-supplied URL reaches an href and any future renderer that is
    not React (a digest, a Slack card, the CLI). Only bounded http(s) with no
    embedded credentials and no control characters survives here, so no
    renderer has to remember. Scheme alone is not enough: `https://ok/" onx="`
    passes a scheme check and breaks the first renderer that builds markup by
    hand."""
    url = (url or "").strip()[:2000]
    # schemes are case-insensitive; a forge configured with an uppercase base
    # URL would otherwise lose every link
    if not url.lower().startswith(("https://", "http://")):
        return ""
    # ALLOWLIST. Every blocklist here has leaked: it missed form feed, then
    # U+2028, then the 31 C1 controls and every bidi override — and
    # `https://good.test/‮gpj.exe` renders reversed in an href. RFC 3986
    # has no non-ASCII characters, so anything else belongs percent-encoded.
    # 0x21-0x7E: isprintable() is True for U+0020, and a space ends an
    # unquoted attribute exactly the way the form feed did
    if not all(0x21 <= ord(c) <= 0x7E for c in url):
        return ""
    if any(c in url for c in "\"'<>`"):
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    # a netloc carrying credentials renders as a convincing link to somewhere
    # else entirely — `https://git.example@evil.test/x`
    if "@" in parts.netloc or not parts.hostname:
        return ""
    return url


def forge_event(
    kind: str,
    branch: str = "",
    title: str = "",
    body: str = "",
    url: str = "",
    login: str = "",
    *,
    actor: str = "forge",
) -> dict:
    """The actor is the registered forge service, and the pusher is named nowhere.

    Two rules meet here. activity rows are hash-chained and can never be
    corrected, so a caller holding the shared secret must not be able to
    write one that reads as a person's own click — hence the constant actor.
    And `forge` is a system actor, which the feed shows to EVERY viewer, so
    naming the pusher in the detail would walk person-level data past the
    anti-surveillance rule that hides a teammate's own rows (docs/INSIGHTS.md
    — person-level data plans the future, it never judges the past). The task
    row already says what moved; who pushed is the forge's business."""
    if kind not in TRANSITIONS:
        raise ValueError(f"kind must be one of {tuple(TRANSITIONS)}")
    # REFUSE, never merely un-name. An agent pushing `task/42-x` would reach
    # work.update_task through the human path with the review gate behind it,
    # and dropping the login from the ledger would hide that, not stop it.
    # An agent's writes belong to the gated tool surface.
    if is_agent_login(login):
        return {"ignored": "an agent identity moves work through its gated tools, not the forge"}
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
    # The TRANSITION is the write, not the link. Once a task is in the status
    # an event means, nothing more is recorded: the ordinary push → open PR →
    # push-review-fixes cycle alternates between the branch page and the PR
    # link, and a URL-keyed guard writes a permanent hash-chained row on every
    # hop. A caller-supplied string must never decide whether we write.
    if task["status"] == status:
        return {"task_id": task_id, "ignored": f"task is already {status}"}
    work.update_task(
        task_id,
        status=status,
        # the link is carried by the transition that earns it, so a merge
        # leaves the PR and a first push leaves the branch page
        forge_url=url,
        actor=actor,
        origin="forge",
        note=" (from the forge)",
    )
    return {"task_id": task_id, "status": status, "url": url}


# _dict/_str, on EVERY nested read below: the route only guards the top level
# (webhooks.py refuses a non-dict payload), so `{"ref": 1}` or `"pusher": "x"`
# raised AttributeError here — and main.py maps no AttributeError to a 4xx, so
# a signed caller turned one wrong-typed field into a 500. A wrong-typed field
# coerces to empty and the payload reads as "not an event that moves work".
def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _str(value) -> str:
    return value if isinstance(value, str) else ""


def parse_gitea(event: str, payload: dict) -> dict | None:
    """Map a Gitea webhook to the generic shape. None means "not an event
    that moves work" — a comment, a label, a draft, a closed-unmerged pull
    request. Returning None is the honest answer, not an error."""
    sender = _str(_dict(payload.get("sender")).get("login"))
    repo = _str(_dict(payload.get("repository")).get("html_url"))
    if event == "push":
        ref = _str(payload.get("ref"))
        if not ref.startswith("refs/heads/"):
            return None  # a tag or a note, not a branch
        branch = ref[len("refs/heads/") :]
        # NEVER compare_url. It names two shas, so it points at one diff
        # rather than at the work, and it is empty on the push that CREATES
        # the branch — the push that starts the task. The branch page is
        # stable and outlives every commit on it. quote() because a ref may
        # carry characters that break a URL.
        url = f"{repo}/src/branch/{quote(branch, safe='/')}" if repo else ""
        login = _str(_dict(payload.get("pusher")).get("login")) or sender
        return {"kind": "branch_push", "branch": branch, "url": url, "login": login}
    if event == "pull_request":
        pr = _dict(payload.get("pull_request"))
        action = _str(payload.get("action"))
        if action in ("opened", "reopened"):
            kind = "pr_opened"
        elif action == "closed" and pr.get("merged"):
            kind = "pr_merged"
        else:
            return None
        return {
            "kind": kind,
            "branch": _str(_dict(pr.get("head")).get("ref")),
            "title": _str(pr.get("title")),
            "body": _str(pr.get("body")),
            "url": _str(pr.get("html_url")),
            "login": sender or _str(_dict(pr.get("user")).get("login")),
        }
    return None


def github_namespace(repository: str, hook_id: int) -> str:
    return _namespace("github", config.GITHUB_API_URL, repository.lower(), str(hook_id))


def _namespace(*parts: str) -> str:
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


def github_guid(value: str) -> str:
    try:
        parsed = str(UUID(value))
        if value.lower() != parsed:
            raise ValueError
        return parsed
    except (ValueError, AttributeError):
        raise ValueError(
            "The GitHub delivery ID is not valid. Send the original delivery headers."
        ) from None


def github_web_base() -> str:
    api = urlsplit(config.GITHUB_API_URL)
    return "https://github.com" if api.netloc == "api.github.com" else f"https://{api.netloc}"


def github_hook(payload: dict, hook_id: str) -> dict:
    if config.GITHUB_CONFIG_ERROR:
        raise PermissionError(
            "GitHub webhooks are not configured. Check the deployment configuration."
        )
    repository = _dict(payload.get("repository")).get("full_name")
    if (
        not isinstance(repository, str)
        or not hook_id.isascii()
        or not hook_id.isdecimal()
        or len(hook_id) > 19
    ):
        raise ValueError(
            "The GitHub repository or hook ID is not valid. Send the original payload and headers."
        )
    for item in config.GITHUB_HOOKS:
        if repository.lower() == item["repository"] and int(hook_id) == item["hook_id"]:
            html_url = _str(_dict(payload.get("repository")).get("html_url"))
            if html_url.lower().rstrip("/") != f"{github_web_base()}/{repository}".lower():
                raise ValueError(
                    "The repository URL does not match GitHub configuration. Send the original payload."
                )
            return item
    raise PermissionError(
        "The GitHub repository and hook are not allowed. Check the deployment allowlist."
    )


def parse_github(event: str, payload: dict, repository: str) -> dict | None:
    if event not in ("push", "pull_request"):
        return None
    bad = "The GitHub payload has invalid fields. Send the original event payload."
    sender = _dict(payload.get("sender")).get("login")
    if not isinstance(sender, str) or not sender or len(sender) > 100:
        raise ValueError(bad)
    repo_url = f"{github_web_base()}/{repository}"
    if event == "push":
        ref = payload.get("ref")
        if (
            not isinstance(ref, str)
            or type(payload.get("deleted")) is not bool
            or "pull_request" in payload
            or "action" in payload
        ):
            raise ValueError(bad)
        if payload["deleted"] or not ref.startswith("refs/heads/"):
            return None
        branch = ref.removeprefix("refs/heads/")
        return {
            "kind": "branch_push",
            "branch": branch,
            "url": f"{repo_url}/tree/{quote(branch, safe='/')}",
            "login": sender,
        }
    pr = _dict(payload.get("pull_request"))
    action = payload.get("action")
    if not isinstance(action, str) or "ref" in payload or not pr:
        raise ValueError(bad)
    if action not in ("opened", "reopened", "closed"):
        return None
    pr_branch = _dict(pr.get("head")).get("ref")
    if (
        type(pr.get("merged")) is not bool
        or type(pr.get("draft")) is not bool
        or not isinstance(pr_branch, str)
        or not isinstance(pr.get("title"), str)
        or (pr.get("body") is not None and not isinstance(pr["body"], str))
        or not isinstance(pr.get("html_url"), str)
    ):
        raise ValueError(bad)
    if pr["draft"] or (action == "closed" and not pr["merged"]):
        return None
    return {
        "kind": "pr_merged" if action == "closed" else "pr_opened",
        "branch": pr_branch,
        "title": pr["title"],
        "body": pr.get("body") or "",
        "url": pr["html_url"],
        "login": sender,
    }


def apply_delivery(
    registry,
    provider: str,
    event: str,
    payload: dict,
    delivery: str,
    body_digest: str,
    hook_id: str = "",
) -> dict:
    """Redelivery comes back through this same signed webhook, never a replay writer."""
    from ..extensions.fastapi import enforce_decision
    from ..extensions.policy import PolicyInput, PolicyResource
    from .policy_context import existing, hold_resource

    if provider == "github":
        hook = github_hook(payload, hook_id)
        delivery = github_guid(delivery)
        namespace = github_namespace(hook["repository"], hook["hook_id"])
        mapped = parse_github(event, payload, hook["repository"])
        repository = hook["repository"]
    else:
        repository = _str(_dict(payload.get("repository")).get("html_url"))
        # Provider headers are outside the HMAC. Relabeling authenticated
        # GitHub bytes as Gitea must not walk around the GitHub allowlist.
        try:
            github_source = urlsplit(repository).hostname in {
                "github.com",
                urlsplit(github_web_base()).hostname,
            }
        except ValueError:
            github_source = False
        if repository and github_source:
            raise ValueError(
                "The webhook headers do not match the repository provider. Send the original headers."
            )
        namespace = _namespace("gitea", repository.lower())
        mapped = parse_gitea(event, payload)
    if mapped is None:
        return {"ignored": "only push and pull_request events move work"}
    with db.transaction():
        # Receipt locks precede resource locks on every forge path. An insert
        # outside this transaction can lose an event before its task commits.
        if delivery:
            db.name_lock(4_216_030, namespace + delivery)
            receipt = db.query_one(
                "SELECT event, payload_sha256 FROM forge_receipts WHERE namespace = ? AND delivery_id = ?",
                (namespace, delivery),
            )
            if receipt:
                if receipt["event"] != event or receipt["payload_sha256"] != body_digest:
                    raise ValueError(
                        "The delivery ID names a different payload. Send the original delivery."
                    )
                return {"ignored": "this delivery was already applied"}
            # Pre-028 Gitea receipts have no repository namespace. Keep their
            # conservative suppression, but never mint another unscoped key.
            if provider == "gitea" and db.query_one(
                "SELECT 1 FROM job_runs WHERE job = 'forge-delivery' AND run_key = ?", (delivery,)
            ):
                return {"ignored": "this delivery was already applied"}
        task_id = match_task(
            mapped.get("branch", ""), mapped.get("title", ""), mapped.get("body", "")
        )
        if task_id:
            hold_resource("task", task_id)
            domain = {**existing("task", task_id), "repository": repository, "provider": provider}
            enforce_decision(
                registry.policy_engine.decide(
                    PolicyInput(
                        registry.service_subject("forge"),
                        "skein.integration.forge",
                        PolicyResource(
                            "task",
                            str(task_id),
                            str(domain.get("project_type") or ""),
                            str(domain.get("classification") or ""),
                            domain,
                        ),
                        "forge",
                        tool="forge.webhook",
                        tool_effect="write",
                        tool_risk="high",
                    )
                )
            )
        result = forge_event(**mapped, actor="forge")
        if delivery:
            db.execute(
                "INSERT INTO forge_receipts (namespace, delivery_id, provider, event, payload_sha256, task_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    namespace,
                    delivery,
                    provider,
                    event,
                    body_digest,
                    result.get("task_id") if "status" in result else None,
                    db.now(),
                ),
            )
        return result


def is_agent_login(login: str) -> bool:
    """Does this forge login name an agent on the roster? The name is used for
    this refusal and nothing else — it never reaches the ledger, so the feed
    cannot become a record of who pushed what (see forge_event).

    Asks whether ANY row with this name is an agent, never "what kind is the
    first row". `users.name` is case-sensitively unique, so `Scout` the human
    and `scout` the agent coexist — and a plain lookup returns whichever sorts
    first, which would let a human's chosen capitalization disarm the refusal
    for the agent."""
    from .users import is_agent

    return is_agent(login)
