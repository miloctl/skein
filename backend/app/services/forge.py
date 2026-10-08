"""Signed forge events share one policy, receipt and task-application path."""

import hashlib
import json
import re
from urllib.parse import quote, urlsplit

from .. import db
from . import ci, scope, work

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
# so a run of n spaces after "task" backtracks n+1 ways at every position -
# 31 seconds of blocked event loop for an 80 KB title, measured.
_TEXT = re.compile(
    r"\b(?:clos(?:e|es|ed)|fix(?:es|ed)?|resolv(?:e|es|ed))[ \t]+task[ \t]?#?[ \t]?(\d{1,9})\b",
    re.ASCII | re.IGNORECASE,
)
# The line `skein pr-body` writes (cli/skein_cli.py). _TEXT needs a space
# after the verb, so it cannot read `Closes-Task: #42`. A line of its own, in
# the body only: `Refs-Task` names a task without closing it, and a Push Hook
# commit message is never read, because every commit on a task branch
# carries a Refs-Task trailer (prepare-commit-msg, COMMIT_MSG_HOOK) and most
# of them are work in progress. Fixed-width separators for the backtracking
# reason _TEXT gives.
_TRAILER = re.compile(r"^Closes-Task:[ \t]?#?(\d{1,9})\b", re.MULTILINE | re.IGNORECASE | re.ASCII)
# the payload is already bounded by MAX_FORGE_BODY, and _TEXT is linear, so
# these are correctness bounds only: a branch name or title longer than this
# is not a ref anyone typed. The BODY is scanned whole - a closing verb at the
# end of a long pull request description is exactly the case that must work.
_SCAN = {"branch": 200, "title": 400}


def match_task(branch: str = "", title: str = "", body: str = "") -> int | None:
    """Branch name first: it is the only field a person cannot retitle later."""
    for pattern, text, cap in (
        (_BRANCH, branch, _SCAN["branch"]),
        # before any prose: `skein pr-body` puts the task description above
        # this line and the commit subjects below it, and a "fixed task 7"
        # there must not close task 7 in place of the task the line names
        (_TRAILER, body, None),
        (_TEXT, title, _SCAN["title"]),
        (_TEXT, body, None),
    ):
        found = pattern.search((text or "")[:cap] if cap else (text or ""))
        if found:
            return int(found.group(1))
    return None


def _reachable_task(task_id: int) -> dict | None:
    """The task row, or None when it is private. A private task has one
    reader, its author; every other reply on this path, a 404 from
    assert_editable, "already done", a policy refusal, told the secret holder
    that a sequential id is someone's private task and what state it is in.
    A crew task keeps moving: its branch names are the crew's ordinary work,
    and the forge secret is held by the repository's own administrators."""
    return db.query_one(
        "SELECT status, delegated_agent, forge_url FROM tasks WHERE id = ? AND visibility <> ?",
        (task_id, scope.PRIVATE),
    )


def _clean_url(url: str) -> str:
    """A forge-supplied URL reaches an href and any future renderer that is
    not React (a digest, the CLI). Only bounded http(s) with no
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
    # U+2028, then the 31 C1 controls and every bidi override - and
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
    # else entirely - `https://git.example@evil.test/x`
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
    write one that reads as a person's own click - hence the constant actor.
    And `forge` is a system actor, which the feed shows to EVERY viewer, so
    naming the pusher in the detail would walk person-level data past the
    anti-surveillance rule that hides a teammate's own rows (docs/INSIGHTS.md
    - person-level data plans the future, it never judges the past). The task
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
    task = _reachable_task(task_id)
    if not task:
        return {"ignored": f"task #{task_id} not found"}
    status = TRANSITIONS[kind]
    url = _clean_url(url)
    # a push to a merged task's branch must not reopen finished work - the
    # forge reports activity, and activity on a done task is not a regression
    if task["status"] == "done" and status != "done":
        return {"task_id": task_id, "ignored": "task is already done"}
    # delegated work closes on the sponsor's verdict, never on a side channel:
    # whoever merged the pull request is not necessarily the sponsor, and
    # submit_for_acceptance is the only path that records a verdict
    if status == "done" and task["delegated_agent"]:
        return {"task_id": task_id, "ignored": "task is delegated - the sponsor accepts it"}
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
# raised AttributeError here - and main.py maps no AttributeError to a 4xx, so
# a signed caller turned one wrong-typed field into a 500. A wrong-typed field
# coerces to empty and the payload reads as "not an event that moves work".
def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _str(value) -> str:
    if not isinstance(value, str):
        return ""
    # a lone surrogate is valid JSON and invalid UTF-8: quote() and the
    # database raise on it, and the codec message repeats the input back
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return ""
    return value


# A GitLab project path, as GitLab itself allows it.
_PROJECT_PATH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", re.ASCII)


def _ref_name(value: str) -> str:
    """A branch name fit for a blocker title, or "".

    The title reaches the hash-chained ledger as `forge`, which every feed
    shows and nobody can correct, so a token holder must not write a sentence
    there: no whitespace, control or format characters (isprintable() is
    False for all but the ASCII space)."""
    return value if 0 < len(value) <= 200 and value.isprintable() and " " not in value else ""


GITLAB_IGNORED = (
    "Skein acts only on branch pushes, merge requests that open, reopen or merge,"
    " and default-branch pipelines that pass or fail"
)


def parse_gitea(event: str, payload: dict) -> dict | None:
    """Map a Gitea webhook to the generic shape. None means "not an event
    that moves work" - a comment, a label, a draft, a closed-unmerged pull
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
        # the branch - the push that starts the task. The branch page is
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


def parse_gitlab(kind: str, payload: dict) -> dict | None:
    """Map a GitLab webhook to the generic shape. `kind` is the payload's
    object_kind, which the route has checked against the event header. None
    means "not an event that moves work", as in parse_gitea."""
    web_url = _str(_dict(payload.get("project")).get("web_url"))
    if kind == "push":
        ref = _str(payload.get("ref"))
        after = _str(payload.get("after"))
        # a branch deletion is a push whose `after` is all zeros: 40 of them,
        # or 64 in a SHA-256 repository
        if not ref.startswith("refs/heads/") or (after and not after.strip("0")):
            return None
        branch = ref[len("refs/heads/") :]
        # the branch page, for the reason parse_gitea gives
        url = f"{web_url}/-/tree/{quote(branch, safe='/')}" if web_url else ""
        return {
            "kind": "branch_push",
            "branch": branch,
            "url": url,
            "login": _str(payload.get("user_username")),
        }
    if kind == "merge_request":
        request = _dict(payload.get("object_attributes"))
        action = _str(request.get("action"))
        if action in ("open", "reopen"):
            mapped = "pr_opened"
        elif action == "merge":
            mapped = "pr_merged"
        else:
            # close, update (the Push Hook already said it), approvals
            return None
        return {
            "kind": mapped,
            "branch": _str(request.get("source_branch")),
            "title": _str(request.get("title")),
            "body": _str(request.get("description")),
            "url": _str(request.get("url")),
            "login": _str(_dict(payload.get("user")).get("username")),
        }
    if kind == "pipeline":
        run = _dict(payload.get("object_attributes"))
        # `manual`: the run stopped at a blocking manual job, and no job
        # failed. A branch with a deploy gate ends every run this way, so
        # without this a red blocker there never resolves. Running, pending, canceled and
        # skipped say nothing about the branch.
        status = {"failed": "failure", "success": "success", "manual": "success"}.get(
            _str(run.get("status"))
        )
        # A merge request pipeline carries the SOURCE branch as its `ref`, so a
        # fork's `main` would read as this project's main. A child pipeline
        # reports its parent's branch, so a green parent after a red child
        # would resolve the blocker the child filed. A tag pipeline's ref is a tag.
        if (
            status is None
            or run.get("tag")
            or payload.get("merge_request")
            or _str(run.get("source")) in ("merge_request_event", "parent_pipeline")
        ):
            return None
        project = _dict(payload.get("project"))
        repo = _str(project.get("path_with_namespace"))
        repo = repo if _PROJECT_PATH.fullmatch(repo) else ""
        branch = _ref_name(_str(run.get("ref")))
        default_branch = _ref_name(_str(project.get("default_branch")))
        # here, not only in ci.ci_event: an event that files nothing must not
        # spend the shared rate bucket (routes/webhooks.py::gitlab_webhook)
        if (
            not repo
            or not branch
            or (branch not in ci.DEFAULT_BRANCHES and branch != default_branch)
        ):
            return None
        run_id = run.get("id")
        fallback = f"{web_url}/-/pipelines/{run_id}" if web_url and isinstance(run_id, int) else ""
        return {
            "kind": "pipeline",
            "repo": repo,
            "branch": branch,
            "status": status,
            "run_url": _clean_url(_str(run.get("url"))) or _clean_url(fallback),
            "default_branch": default_branch,
        }
    return None


def _namespace(*parts: str) -> str:
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


def apply_delivery(
    registry,
    event: str,
    payload: dict,
    delivery: str,
    body_digest: str,
    *,
    provider: str = "gitea",
) -> dict:
    """Redelivery comes back through the same webhook, never a replay writer.

    `provider` is the forge (gitea or gitlab), never a model provider. It
    picks the parser, and it keys the receipt namespace, so one repository
    URL under two forges keeps two sets of receipts."""
    from ..extensions.fastapi import enforce_decision
    from ..extensions.policy import PolicyInput, PolicyResource
    from .policy_context import existing, hold_resource

    if provider == "gitlab":
        repository = _str(_dict(payload.get("project")).get("web_url"))
        mapped = parse_gitlab(event, payload)
    else:
        repository = _str(_dict(payload.get("repository")).get("html_url"))
        mapped = parse_gitea(event, payload)
    namespace = _namespace(provider, repository.lower())
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
            if db.query_one(
                "SELECT 1 FROM job_runs WHERE job = 'forge-delivery' AND run_key = ?", (delivery,)
            ):
                return {"ignored": "this delivery was already applied"}
        if mapped is None:
            return {
                "ignored": GITLAB_IGNORED
                if provider == "gitlab"
                else "only push and pull_request events move work"
            }
        # Gitea redelivery changes the UUID, not the signed bytes. The bigint
        # lock space cannot collide with db.name_lock's two-int keys: nesting
        # another name_lock here can invert receipt/task lock order on a hash collision.
        fingerprint = _namespace(namespace, event, body_digest)
        lock_key = int.from_bytes(bytes.fromhex(fingerprint)[:8], signed=True)
        db.query("SELECT pg_advisory_xact_lock(?::bigint)", (lock_key,))
        if db.query_one(
            "SELECT 1 FROM forge_receipts WHERE namespace = ? AND event = ? AND payload_sha256 = ? LIMIT 1",
            (namespace, event, body_digest),
        ):
            result = {"ignored": "this delivery was already applied"}
        elif mapped["kind"] == "pipeline":
            # the CI door's own action, so one workplace rule governs both
            # doors (routes/webhooks.py::ci_webhook). A DENY raises inside this
            # transaction: no blocker, no receipt, and a resend can retry.
            enforce_decision(
                registry.policy_engine.decide(
                    PolicyInput(
                        registry.service_subject("forge"),
                        "skein.integration.ci",
                        PolicyResource(
                            "integration",
                            "ci",
                            attributes={"repository": mapped["repo"], "provider": provider},
                        ),
                        "forge",
                        tool="forge.webhook",
                        tool_effect="write",
                        tool_risk="high",
                    )
                )
            )
            # `forge`, a system actor: every feed shows the row, and it names
            # no person. A red build is a fact about the branch, so there is
            # no login check. ci_event nests its own name_lock after the two
            # above, the order the fingerprint comment warns about: a hash
            # collision with another delivery's receipt key can deadlock, and
            # PostgreSQL then aborts one side, which answers 503
            # (db.BUSY_ERRORS) and a resend applies it.
            result = ci.ci_event(
                mapped["repo"],
                mapped["branch"],
                mapped["status"],
                mapped["run_url"],
                actor="forge",
                default_branch=mapped["default_branch"],
            )
        else:
            task_id = match_task(
                mapped.get("branch", ""), mapped.get("title", ""), mapped.get("body", "")
            )
            # absent to policy as well: a DENY on a private task answered 403
            # where a nonexistent id answers 200 (_reachable_task)
            if task_id and not _reachable_task(task_id):
                task_id = None
            if task_id:
                hold_resource("task", task_id)
                domain = {
                    **existing("task", task_id),
                    "repository": repository,
                    "provider": provider,
                }
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
    this refusal and nothing else - it never reaches the ledger, so the feed
    cannot become a record of who pushed what (see forge_event).

    Asks whether ANY row with this name is an agent, never "what kind is the
    first row". `users.name` is case-sensitively unique, so `Scout` the human
    and `scout` the agent coexist - and a plain lookup returns whichever sorts
    first, which would let a human's chosen capitalization disarm the refusal
    for the agent."""
    from .users import is_agent

    return is_agent(login)
