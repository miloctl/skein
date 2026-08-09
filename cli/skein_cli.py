#!/usr/bin/env python3
"""skein — the team platform from your terminal. Stdlib only.

Setup:
    skein config --url http://localhost:8000 --key sk-skein-... [--user you]
    (first key: whoever runs the box mints it with
       `python -m app.bootstrap_key <you>`; later keys via Settings)
    Env fallbacks: SKEIN_URL / SKEIN_API_URL, SKEIN_API_KEY
    (config file wins).

Examples:
    skein capture "todo: ship the API"
    skein standup --today "auth flow" --blockers "waiting on vendor"
    skein standup --draft --today "auth flow"   # yesterday from your activity
    skein my-day
    skein tasks
    skein tasks done 12
    skein blockers add "staging db down" --impact high
    skein search cutover
    skein week draft          # weekly commitment line
    skein promises         # open promises; settle: skein promises settle 3 kept
    skein absences add mira 2026-08-10 2026-08-14   # PTO by default
    skein review              # pending proposals; approve/reject by id
    skein review approve 12 -m "looks right"
    skein worklog 22          # a delegated task's progress log
    skein inbox scout         # an agent's ambient inbox
    skein tasks delegate 12 scout   # hand a task to an agent (you sponsor it)
    skein answer 7 "p95 at 250ms is the contract number"
    skein eval                # replay capture classifier vs feedback corpus
    skein context --write AGENTS.md
    skein install-hooks       # run inside each work repo you want trailer sync in
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG_PATH = Path(os.path.expanduser("~/.config/skein/config.json"))
# how long `skein attention` trusts its cache. A shell prompt runs this on
# EVERY command, so the network call has to be rare; a minute-old count is
# right often enough and wrong harmlessly.
ATTENTION_TTL_S = 60
# A shell prompt runs this between every command. 15 seconds is the API
# default and is a frozen terminal here: a host that DROPS packets (a VPN gone
# down, a firewall) never refuses the connection, so the caller waits the full
# timeout on every keystroke. One second is longer than a healthy local call
# and short enough to be invisible.
ATTENTION_TIMEOUT_S = 1.0


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 0600 from the first byte — no world-readable window
    fd = os.open(CONFIG_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(cfg, indent=1))


def base_url() -> str:
    cfg = load_config()
    return (
        cfg.get("url")
        or os.getenv("SKEIN_URL")
        or os.getenv("SKEIN_API_URL")
        or "http://localhost:8000"
    ).rstrip("/")


def _request(method: str, path: str, body: dict | None = None, timeout: float = 15) -> dict | list:
    """The bare call. `api` adds the human-facing exit; `api_quiet` does not."""
    cfg = load_config()
    url = base_url()
    headers = {"Content-Type": "application/json", "X-Client": "cli"}
    key = cfg.get("key") or os.getenv("SKEIN_API_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if cfg.get("user"):
        headers["X-User"] = cfg["user"]
    req = urllib.request.Request(
        url + path,
        method=method,
        headers=headers,
        data=json.dumps(body).encode() if body is not None else None,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def api(method: str, path: str, body: dict | None = None) -> dict | list:
    try:
        return _request(method, path, body)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("detail", str(exc))
            if isinstance(detail, list):  # pydantic 422: readable, not a repr
                detail = "; ".join(
                    f"{'.'.join(str(x) for x in d.get('loc', [])[1:])}: {d.get('msg', d)}"
                    for d in detail
                )
        except Exception:
            detail = str(exc)
        sys.exit(f"error: {detail}")
    except urllib.error.URLError as exc:
        _mark_unreachable()
        sys.exit(f"error: cannot reach {base_url()} ({exc.reason}) — run `skein config --url ...`")


def api_quiet(method: str, path: str, body: dict | None = None, timeout: float = 15):
    """`api` without the sys.exit, for the two callers where a failure must be
    silent: the prompt segment and the outbox flush.

    An HTTPError comes back AS the exception, not as None. The two mean
    opposite things — a 4xx is the server's verdict and will be the same
    verdict forever, while a transport failure is worth retrying — and
    collapsing them made `skein capture` queue a rejected write and promise
    to file it, then retry that row in front of every later capture for good.
    """
    try:
        return _request(method, path, body, timeout=timeout)
    except urllib.error.HTTPError as exc:
        return exc
    except Exception:
        _mark_unreachable()
        return None


# Set when a call in THIS process failed to reach the server at all. The
# outbox flush runs after every command, and against a dead host it paid a
# second full timeout re-sending the row the command had just queued — 30
# seconds for `skein capture`, the command that exists so a thought is never
# lost. A transport failure already answers the only question the flush asks.
_UNREACHABLE = False


def _mark_unreachable() -> None:
    global _UNREACHABLE
    _UNREACHABLE = True


OUTBOX = CONFIG_PATH.parent / "outbox.jsonl"


def _queue(path: str, body: dict) -> None:
    """Park a write for the next successful command.

    AT LEAST ONCE, and deliberately not exactly once: a row leaves the file
    only after the server accepts it, so a crash between the accept and the
    rewrite re-sends it and files a duplicate. That is the safe direction —
    the duplicate is visible in the feed and deletable, where a row dropped on
    a crash is a capture the person believed they made. Server-side dedupe
    needs an idempotency key the capture route reads, which it does not yet;
    that half is the open part of D5 in docs/ROADMAP.md.
    """
    try:
        OUTBOX.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(OUTBOX, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(json.dumps({"path": path, "body": body}) + "\n")
    except OSError as exc:
        # the one write this feature exists to protect. If it cannot be parked
        # either, the text has to reach the person, not a traceback.
        sys.exit(
            f"error: could not save the capture ({exc}). Your text was: {body.get('text', '')}"
        )


def flush_outbox() -> int:
    """Send what the outbox holds, oldest first.

    CLAIMS the file by renaming it first. os.rename is atomic on POSIX, so a
    capture made by another shell mid-flush lands in a fresh outbox.jsonl this
    call never touches — reading and then truncating in place destroyed it.
    Two shells flushing at once each claim a different file rather than both
    sending the same rows.
    """
    if not OUTBOX.exists():
        return 0
    claim = OUTBOX.with_suffix(f".{os.getpid()}.sending")
    try:
        os.rename(OUTBOX, claim)
        lines = claim.read_text().splitlines()
    except OSError:
        return 0  # another shell claimed it, or the file went away
    rows = []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            # a crash mid-append can truncate one line. Dropping it costs one
            # capture; raising wedged every capture behind it, for the life of
            # the file, and said nothing.
            continue
        if isinstance(row, dict) and "path" in row and "body" in row:
            rows.append(row)
    sent, left = 0, []
    for i, row in enumerate(rows):
        got = api_quiet("POST", row["path"], row["body"])
        if isinstance(got, urllib.error.HTTPError):
            # The SERVER's verdict, and it will be the same verdict forever.
            # Retrying it parked every later capture behind a row that could
            # never send. Say what was lost, then drop it.
            text = str(row.get("body", {}).get("text", ""))[:60]
            print(f"a saved capture was refused and dropped: {text}", file=sys.stderr)
            continue
        if got is None:
            left = rows[i:]  # transport failure: keep this row and the rest
            break
        sent += 1
    _merge_back(claim, left)
    return sent


def _merge_back(claim: Path, left: list) -> None:
    """Put unsent rows back at the FRONT of the queue and drop the claim.
    What did not send is older than anything a concurrent shell queued while
    this call ran."""
    try:
        rest = OUTBOX.read_text() if OUTBOX.exists() else ""
    except OSError:
        rest = ""
    body = "".join(json.dumps(r) + "\n" for r in left) + rest
    try:
        if body:
            fd = os.open(OUTBOX, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(body)
        else:
            OUTBOX.unlink(missing_ok=True)
        claim.unlink(missing_ok=True)
    except OSError:
        pass  # the claim file remains and the next flush ignores it


def cmd_config(args):
    cfg = load_config()
    if args.key == "-":  # prompt: keeps the key out of argv/shell history
        import getpass

        args.key = getpass.getpass("API key (sk-skein-…): ").strip()
    for field in ("url", "key", "user"):
        value = getattr(args, field)
        if value:
            cfg[field] = value
    save_config(cfg)
    shown = {**cfg, "key": (cfg.get("key", "")[:16] + "…") if cfg.get("key") else ""}
    print(json.dumps(shown, indent=1))


def cmd_capture(args):
    body = {"text": " ".join(args.text)}
    got = api_quiet("POST", "/api/capture", body)
    if isinstance(got, urllib.error.HTTPError):
        # the server REFUSED it. Queueing a refusal promises a filing that can
        # never happen, and exits 0 on a write that did not land.
        api("POST", "/api/capture", body)
        return
    if got is None:
        # A capture is the one write a person makes mid-thought. Losing it to
        # a dead server or a train tunnel is the failure this whole command
        # exists to prevent, so it is parked rather than refused.
        _queue("/api/capture", body)
        print("saved locally — it files on your next command that reaches the server")
        return
    print(f"captured as {got['kind']} #{got['id']}")


def cmd_standup(args):
    yesterday = args.yesterday
    if args.draft and not yesterday:
        # Own data, to yourself: the same string the web My Day prefills
        # (services/briefing.py::_standup_suggestion), so the terminal user
        # stops paying a tax the browser user does not. An explicit
        # --yesterday always wins; --draft never overwrites what was typed.
        yesterday = api("GET", "/api/briefing")["your_work"].get("standup_suggestion", "")
        if not yesterday:
            print("No activity to draft from. Skein posts the standup with an empty --yesterday.")
        else:
            print(f"drafted yesterday: {yesterday}")
    api(
        "POST",
        "/api/standups",
        {"yesterday": yesterday, "today": args.today, "blockers": args.blockers},
    )
    print("standup posted" + (" (blocker auto-filed)" if args.blockers else ""))


def cmd_my_day(args):
    if args.cached:
        cached = CONFIG_PATH.parent / "my-day.cache"
        try:
            raw = cached.read_text()
        except OSError:
            sys.exit("no cached briefing yet — run `skein my-day` once with the server up")
        age = int((time.time() - cached.stat().st_mtime) // 60)
        # the age is the point: a cached briefing is yesterday's decisions
        # unless it says otherwise
        print(f"(cached {age} minute{'' if age == 1 else 's'} ago)")
        print(raw)
        return
    b = api("GET", "/api/briefing")
    n = b["needs_you"]
    out = [f"# My Day — {b['user']}, {b['date']}\n"]
    for q in n["open_questions"]:
        out.append(f"  ? #{q['id']} {q['question']} (from {q['asked_by']})")
    for bl in n["your_blockers"]:
        out.append(f"  ⛔ #{bl['id']} {bl['title']} [{bl['impact']}/{bl['status']}]")
    for nt in n.get("notifications", []):
        out.append(f"  🔔 {nt['message']}")
    if n["pending_reviews"]:
        out.append(f"  📥 {len(n['pending_reviews'])} pending review(s)")
    if n["intake_to_triage"]:
        out.append(f"  📨 {len(n['intake_to_triage'])} intake request(s) to triage")
    out.append("\n## Your tasks")
    for t in b["your_work"]["tasks"] or []:
        out.append(f"  [{t['priority']}/{t['status']}] #{t['id']} {t['title']}")
    if not b["your_work"]["tasks"]:
        out.append("  (none)")
    for e in b["team"]["todays_events"]:
        out.append(f"  📅 {e['starts_at']}: {e['title']}")
    body = "\n".join(out)
    print(body)
    # cached for --cached. Written only on a SUCCESSFUL fetch, so the cache is
    # always a briefing that really rendered rather than a partial one.
    try:
        cache = CONFIG_PATH.parent / "my-day.cache"
        cache.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(cache, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(body)
    except OSError:
        pass  # a briefing that printed is not a failure because it did not cache


def cmd_tasks(args):
    if args.action == "delegate":
        out = api("POST", f"/api/tasks/{args.id}/delegate", {"agent": args.agent})
        print(f"task #{args.id} delegated to {out['delegated_agent']} (sponsor {out['sponsor']})")
        return
    if args.action == "done":
        api("PATCH", f"/api/tasks/{args.id}", {"status": "done"})
        print(f"task #{args.id} done")
        return
    for t in api("GET", "/api/tasks"):
        if t["status"] == "done" and not args.all:
            continue
        print(
            f"[{t['priority']}/{t['status']}] #{t['id']} {t['title']}"
            + (f" (@{t['assignee']})" if t["assignee"] else "")
        )


def cmd_blockers(args):
    if args.action == "add":
        out = api("POST", "/api/blockers", {"title": " ".join(args.title), "impact": args.impact})
        print(f"blocker #{out['id']} filed (escalates after {out['escalate_after_hours']}h)")
    elif args.action == "resolve":
        api(
            "POST",
            f"/api/blockers/{args.id}/resolve",
            {"resolution": args.resolution or "resolved via CLI"},
        )
        print(f"blocker #{args.id} resolved")
    else:
        for b in api("GET", "/api/blockers"):
            print(
                f"[{b['impact']}/{b['status']}] #{b['id']} {b['title']}"
                + (f" (@{b['owner']})" if b["owner"] else " (unowned)")
            )


def cmd_search(args):
    hits = api("GET", "/api/search?q=" + urllib.parse.quote(" ".join(args.query)))
    for h in hits:
        snippet = re.sub(r"</?b>", "", h["snippet"])
        print(f"[{h['entity']} #{h['entity_id']}] {h['title']} — {snippet}")
    if not hits:
        print("no matches")


def cmd_ask(args):
    """The terminal half of the nav box's `?` prefix. NOT a model call: the
    same FTS index, retried on the separate words when the phrase matches
    nothing, returned as snippets that cite `entity #id`."""
    out = api("GET", "/api/ask?q=" + urllib.parse.quote(" ".join(args.question)))
    for c in out["citations"]:
        snippet = re.sub(r"</?b>", "", c["snippet"])
        print(f"[{c['ref']}] {c['title']} — {snippet}")
    # printed after the citations, never instead of them: the note says the
    # answer is loose, and a reader who saw only the note would think there
    # were no rows at all
    if out.get("note"):
        print(out["note"])


def cmd_attention(args):
    """The count for a shell prompt. It must never block and never raise: a
    prompt that waits on a dead backend freezes the terminal between every
    command, and one that prints a traceback ruins every line.

    The cache gates on its MTIME, not on its contents, so a FAILED call is
    remembered too. Writing only on success left the file untouched, the age
    check never applied, and a dropped-packet host was retried on every
    keystroke — a 15-second stall per prompt, forever.
    """
    cache = CONFIG_PATH.parent / "attention.cache"
    try:
        if time.time() - cache.stat().st_mtime < ATTENTION_TTL_S:
            count = cache.read_text().strip()
            _print_attention(args, count)
            return
    except OSError:
        pass  # absent or unreadable: ask, then stamp whatever comes back

    got = api_quiet("GET", "/api/attention", timeout=ATTENTION_TIMEOUT_S)
    count = str(got.get("count", "")) if isinstance(got, dict) else ""
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(cache, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(count)  # "" stamps the failure, which is the point
    except OSError:
        pass  # a prompt that cannot write its cache still has to render
    _print_attention(args, count)


def _print_attention(args, count: str) -> None:
    if args.porcelain:
        # nothing at all when nothing waits, and nothing when the count is
        # unknown: a prompt segment that renders "0" is noise on every line of
        # a clean day, and one that renders "?" is noise the reader cannot act
        # on. `skein attention` without --porcelain says what happened.
        if count and count != "0":
            print(count)
        return
    if not count:
        # NOT "did not answer": api_quiet returns the HTTPError for anything
        # the server ANSWERED with (401, 429, 5xx), and telling a reader their
        # server is down when it replied sends them to check the wrong thing.
        # `skein my-day` uses `api`, which prints the real cause.
        print("Skein could not give a count. Run `skein my-day` to see why.")
        return
    n = int(count) if count.isdecimal() else 0
    print(f"{count} thing{'' if n == 1 else 's'} waiting on you")


def cmd_eval(args):
    out = api("GET", "/api/eval/capture")
    unscored = out.get("unscored", [])
    if not out["cases"] and not unscored:
        print(
            "no labeled capture feedback yet — POST /api/feedback with"
            " kind=capture, verdict=up|corrected to build the corpus"
        )
        return
    print(f"capture classifier: {out['passed']}/{out['cases']} passed (accuracy {out['accuracy']})")
    for m in out["mismatches"]:
        print(f"  ✗ #{m['id']} {m['input']!r}: expected {m['expected']}, got {m['predicted']}")
    for u in unscored:
        print(f"  ? #{u['id']} unscored (free-text correction): {u['note'][:70]}")
    if not out["cases"] and unscored:
        print(
            "warning: nothing machine-checkable — corrections must be a kind label"
            " (question/blocker/decision/promise/task/note) to gate regressions"
        )
    if out["mismatches"]:
        sys.exit(1)


def cmd_context(args):
    path = "/api/context-pack"
    if args.engagement:
        path += f"?engagement={args.engagement}"
    pack = api("GET", path)
    if args.write:
        Path(args.write).write_text(pack["content"] + "\n")
        # the engagement pack is generated on demand and carries no version
        # (routes/api.py) — printing pack["version"] raised KeyError AFTER the
        # file was already written, so the caller got the file and a traceback
        version = f" v{pack['version']}" if "version" in pack else ""
        print(f"wrote context pack{version} to {args.write}")
    else:
        print(pack["content"])


def cmd_week(args):
    if args.action == "draft":
        draft = api("GET", "/api/week/draft")
        print(f"# Draft plan {draft['week']}")
        for i in draft["items"]:
            print(f"  #{i['task_id']} {i['title']} (@{i['assignee']})")
        if not draft["items"]:
            print("  (nothing to commit)")
        return
    if args.action == "commit":
        if not args.ids:
            sys.exit("error: week commit requires task ids")
        out = api("POST", "/api/week/plan", {"task_ids": args.ids})
        print(f"committed {out['committed']} task(s) to {out['week']}")
        return
    w = api("GET", "/api/week")
    kept = f" — {w['kept_percent']}% done" if w["kept_percent"] is not None else ""
    print(f"# {w['week']}: {w['done']}/{w['committed']} committed tasks done{kept}")
    for t in w["tasks"]:
        mark = "x" if t["status"] == "done" else " "
        print(f"  [{mark}] #{t['id']} {t['title']} (@{t['assignee'] or 'unassigned'})")


def cmd_promises(args):
    if args.action == "settle":
        api("POST", f"/api/promises/{args.id}/status", {"status": args.status})
        print(f"promise #{args.id} {args.status}")
        return
    rows = api("GET", "/api/promises" + ("" if args.all else "?status=open"))
    for c in rows:
        due = f" due {c['due_date']}" if c["due_date"] else ""
        # the arrow carries the direction: what we owe points out, what we are
        # owed points in. Printed direction-blind, the two read identically
        # and a promise the team is WAITING ON looks like one it broke.
        received = c.get("direction") == "received"
        arrow = "←" if received else "→"
        who = f" {arrow} {c['to_whom']}" if c["to_whom"] else ""
        kind = "awaiting" if received else c["audience"]
        print(f"[{c['status']}] #{c['id']} {c['promise']}{who}{due} ({kind})")
    if not rows:
        print('no open promises — capture one with `skein capture "promised: ..."`')


def cmd_absences(args):
    if args.action == "add":
        out = api(
            "POST",
            "/api/absences",
            {
                "person": args.person,
                "starts_on": args.starts_on,
                "ends_on": args.ends_on,
                "kind": args.kind,
                "note": args.note,
            },
        )
        print(f"absence #{out['id']}: {out['person']} {out['kind']}")
        return
    if args.action == "rm":
        out = api("DELETE", f"/api/absences/{args.id}")
        echo = (
            f" ({out['person']}: {out['kind']} {out['starts_on']} \u2192 {out['ends_on']})"
            if out.get("person")  # older servers return only {id, deleted}
            else ""
        )
        print(f"absence #{args.id} removed{echo}")
        return
    rows = api("GET", "/api/absences")
    if rows:
        print("# away (current + upcoming)")
    for a in rows:
        note = f" — {a['note']}" if a["note"] else ""
        print(f"#{a['id']} {a['person']}: {a['kind']} {a['starts_on']} → {a['ends_on']}{note}")
    if not rows:
        print("nobody is scheduled away")


def cmd_review(args):
    keyless = not (load_config().get("key") or os.getenv("SKEIN_API_KEY"))
    if args.action in ("approve", "reject"):
        if keyless and not load_config().get("user"):
            sys.exit(
                "error: configure who you are first (`skein config --user you`"
                " or --key) — an anonymous verdict helps nobody"
            )
        out = api("POST", f"/api/review/{args.id}/{args.action}", {"note": args.note})
        print(f"proposal #{args.id} {out['status']}")
        if keyless:
            print(
                "note: verdict recorded, but without your API key it will"
                " never feed promotion/demotion streaks — `skein config --key`"
            )
        return
    rows = api("GET", "/api/review?status=pending")
    for c in rows:
        sponsor = f" · sponsor {c['sponsor']}" if c.get("sponsor") else ""
        asked = f" · asked by {c['requested_by']}" if c.get("requested_by") else ""
        print(f"#{c['id']} {c['summary']} (by {c['proposed_by']}{asked}{sponsor})")
    if not rows:
        print("review queue is empty")
    elif keyless:
        print(
            "\nnote: no API key configured — verdicts still land (and count in"
            " approval rates), but only key-authenticated ones feed"
            " promotion/demotion streaks"
        )


def cmd_answer(args):
    api("POST", f"/api/questions/{args.id}/answer", {"answer": " ".join(args.text)})
    print(f"question #{args.id} answered")


def cmd_worklog(args):
    rows = api("GET", f"/api/tasks/{args.id}/worklog")
    for w in reversed(rows):  # service returns newest-first; read as a log
        print(f"{w['created_at'][:16]} {w['author']}: {w['note']}")
    if not rows:
        print(f"no worklog entries for task #{args.id}")


def cmd_inbox(args):
    box = api("GET", f"/api/agents/{urllib.parse.quote(args.agent)}/inbox")
    print(f"# {box['agent']}'s inbox")
    for t in box["delegated_tasks"]:
        sponsor = f" (sponsor {t['sponsor']})" if t["sponsor"] else ""
        print(f"  🧵 [{t['priority']}/{t['status']}] #{t['id']} {t['title']}{sponsor}")
    for q in box["open_questions"]:
        print(f"  ? #{q['id']} {q['question']} (from {q['asked_by']})")
    for r in box["rejected_proposals"]:
        print(f"  ✗ proposal #{r['id']} {r['summary']} — {r['review_note'] or 'no note'}")
    for n in box["notifications"]:
        print(f"  🔔 {n['message']}")
    if not any(
        box[k] for k in ("delegated_tasks", "open_questions", "rejected_proposals", "notifications")
    ):
        print("  (empty)")


HOOK = """#!/bin/sh
# skein git hook: close tasks referenced by Closes-Task: #N trailers
skein sync-commit || true
"""

# Reads the task id off a `task/42-...` branch and adds the trailer the
# post-commit hook looks for.
#
# $2 is git's SOURCE: "" for an editor commit, "message" for -m/-F, "template"
# for -t, and "merge"/"squash" for those. Skipping every non-empty source
# skipped `git commit -m`, which is how most commits are written and the case
# where the trailer helps most — nobody sees an editor to add it by hand. Only
# merge and squash are skipped, where the message is assembled from other
# commits and a trailer would claim this commit closed the task.
COMMIT_MSG_HOOK = """#!/bin/sh
case "$2" in merge|squash) exit 0 ;; esac
# symbolic-ref, not rev-parse: on a branch with no commits yet
# (git init, then `skein task start`, then the first commit) rev-parse
# cannot resolve HEAD and the trailer would be missed on exactly the
# commit that starts the work.
branch=$(git symbolic-ref --short -q HEAD) || exit 0
case "$branch" in
  task/*) ;;
  *) exit 0 ;;
esac
# the digits must END the segment or be followed by - or /. Without the
# boundary `task/12abc` claimed task 12, which neither BRANCH_RE nor
# services/forge.py accepts — three definitions, and this was the outlier.
id=$(printf '%s' "$branch" | sed -n -e 's|^task/\\([0-9][0-9]*\\)$|\\1|p' -e 's|^task/\\([0-9][0-9]*\\)[-/].*|\\1|p')
[ -n "$id" ] || exit 0
# -E, not a BRE alternation: `\\|` is a GNU extension that BSD grep reads as
# a literal pipe, so the guard never fired there — and a hand-written
# `Refs-Task: #42` then got a `Closes-Task: #42` appended, turning a commit
# that REFERENCED a task into one that closes it.
grep -qiE '^(Closes|Refs)-Task:' "$1" && exit 0
# REFS, not CLOSES. Every commit on a task branch gets this trailer, and most
# of them are work in progress — `Closes-Task:` here marked the task done and
# stamped completed_at on the first "wip" commit, which then fed cycle time,
# throughput and the interrupt-load finding with a lie. `skein pr-body` emits
# Closes-Task where a merge genuinely ends the work.
printf '\\nRefs-Task: #%s\\n' "$id" >> "$1"
exit 0
"""

# A prompt segment. Runs `skein attention --porcelain`, which never blocks and
# never errors — a prompt that can hang the terminal is worse than no count.
#
# No emoji: this text leaves Skein's own surfaces (CLAUDE.md), and a terminal
# without an emoji font renders a box on every prompt line, in typography we
# do not control. command_timeout is set because starship's default is 500ms
# and a Python cold start plus a cache read sits close to it.
STARSHIP_SNIPPET = """[custom.skein]
command = "skein attention --porcelain"
when = true
command_timeout = 2000
format = "[skein $output]($style) "
style = "yellow"
"""

PS1_SNIPPET = """# skein: the count of what is waiting on you, or nothing at all
__skein_attention() { skein attention --porcelain 2>/dev/null; }
PS1='$(n=$(__skein_attention); [ -n "$n" ] && printf "\\001\\033[33m\\002skein %s \\001\\033[0m\\002" "$n")'"$PS1"
"""


def cmd_install_prompt(args):
    """Prints the snippet rather than editing a shell profile. Same reasoning
    as install-hooks writing one file and saying where: a tool that rewrites
    somebody's rc file has to be trusted to un-rewrite it."""
    print(STARSHIP_SNIPPET if args.shell == "starship" else PS1_SNIPPET)
    where = "~/.config/starship.toml" if args.shell == "starship" else "~/.bashrc"
    print(f"# add the block above to {where}", file=sys.stderr)


def cmd_install_hooks(args):
    git_dir = subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True, text=True)
    if git_dir.returncode != 0:
        sys.exit("error: not a git repository")
    hooks = Path(git_dir.stdout.strip()) / "hooks"
    hooks.mkdir(exist_ok=True)
    wrote = False
    for name, body in (("post-commit", HOOK), ("prepare-commit-msg", COMMIT_MSG_HOOK)):
        path = hooks / name
        # An existing hook is somebody's configuration — commitizen, gitlint
        # and husky all own these two slots — and overwriting it loses work
        # that cannot be recovered. Refuse, name the file, and let the reader
        # decide. --force is the way to say "I know".
        if path.exists() and not getattr(args, "force", False) and body not in path.read_text():
            print(f"kept {path} — a hook is already there. Use --force to replace it.")
            continue
        path.write_text(body)
        path.chmod(0o755)
        wrote = True
        print(f"installed {path}")
    if wrote:
        print('commits with "Closes-Task: #12" (or Refs-Task) now sync to the platform')
        print("on a task/<id>-... branch a Refs-Task trailer is added for you")


TRAILER = re.compile(r"^(Closes|Refs)-Task:\s*#?(\d+)", re.I | re.M)
# the branch shape the forge webhook already understands: a push to
# `task/42-slug` starts task 42 (services/forge.py). Written the same way
# here so the two halves cannot disagree about what a task branch looks like.
BRANCH_RE = re.compile(r"^task/(\d+)(?:-|$)")


def _slug(title: str, words: int = 6) -> str:
    """A branch-safe tail. Lowercase, hyphens, no run of them — git refuses a
    ref with `..`, a trailing dot or a space, and the forge reads only the
    number anyway."""
    parts = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-").split("-")
    # capped: a loose ref is a FILE, and a path component over 255 bytes is
    # ENAMETOOLONG — one pasted URL in a title was enough. The forge reads
    # only the number, so the tail is a convenience.
    return "-".join([p for p in parts if p][:words])[:60].rstrip("-")


def _git(*args: str) -> str:
    # argv, never a shell string, and every caller passes literal git
    # subcommands — the only interpolated value is a branch name built here.
    got = subprocess.run(["git", *args], capture_output=True, text=True)  # noqa: S603
    if got.returncode != 0:
        sys.exit(f"error: git {' '.join(args)} failed — {got.stderr.strip()}")
    return got.stdout.strip()


def cmd_task_start(args):
    """Branch and status in one step. The forge webhook moves the task when
    the branch is PUSHED; this moves it now, so the board is right while the
    work is still local."""
    task = api("GET", f"/api/tasks/{args.task_id}")
    branch = f"task/{args.task_id}-{_slug(task['title'])}".rstrip("-")
    # show-ref --verify refs/heads/…, never rev-parse: rev-parse resolves ANY
    # ref, so a TAG named task/7-x took the "already a branch" path and left
    # HEAD detached — after which the commit hook adds no trailer and pr-body
    # refuses the branch, both silently.
    existing = subprocess.run(  # noqa: S603 — argv, not a shell string; branch is built above
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        capture_output=True,
        text=True,
    )
    _git("checkout", branch) if existing.returncode == 0 else _git("checkout", "-b", branch)
    print(f"on {branch}")
    # after the branch: a failed checkout must not leave the board claiming
    # work that never started
    api("PATCH", f"/api/tasks/{args.task_id}", {"status": "in_progress"})
    print(f"task #{args.task_id} is in progress")


def cmd_pr_body(args):
    """The description for `gh pr create`. Composes what a reviewer needs and
    a branch name cannot carry: the task, its engagement's context, and the
    commits on the branch."""
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    found = BRANCH_RE.match(branch)
    task_id = args.task_id or (int(found.group(1)) if found else 0)
    if not task_id:
        sys.exit(
            f"error: '{branch}' is not a task branch. Pass a task id, or run `skein task start <id>`."
        )
    task = api("GET", f"/api/tasks/{task_id}")
    base = args.base
    commits = _git("log", "--format=- %s", f"{base}..HEAD")
    print(f"## {task['title']}\n")
    if task.get("description"):
        print(task["description"] + "\n")
    print(f"Closes-Task: #{task_id}\n")
    if task.get("engagement_name"):
        print(f"Engagement: {task['engagement_name']}\n")
    unblocks = task.get("unblocks") or []
    if unblocks:
        # a reviewer's queue-order signal, and the one fact a branch name
        # cannot carry: this merge releases somebody else
        print("Merging this unblocks:\n")
        for u in unblocks:
            print(f"- #{u['id']} {u['title']}")
        print()
    print("## Commits\n")
    print(commits or "- (none yet)")


def cmd_sync_commit(args):
    msg = subprocess.run(
        ["git", "log", "-1", "--format=%B%n%H"], capture_output=True, text=True
    ).stdout
    sha = msg.strip().splitlines()[-1][:10] if msg.strip() else "unknown"
    matches = TRAILER.findall(msg)
    if not matches:
        return
    for verb, task_id in matches:
        if verb.lower() == "closes":
            api("PATCH", f"/api/tasks/{task_id}", {"status": "done"})
            print(f"skein: task #{task_id} closed by commit {sha}")
        api(
            "POST",
            "/api/notes",
            {
                "topic": f"commit-{sha}",
                "content": f"Commit {sha} {verb.lower()} task #{task_id}",
            },
        )


def main():
    p = argparse.ArgumentParser(
        prog="skein", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("config", help="set server url / api key / user")
    c.add_argument("--url")
    c.add_argument(
        "--key",
        nargs="?",
        const="-",
        help="API key; omit the value to be prompted (keeps it out of shell history)",
    )
    c.add_argument("--user")
    c.set_defaults(fn=cmd_config)

    c = sub.add_parser("capture", help="quick-capture text (auto-routed)")
    c.add_argument("text", nargs="+")
    c.set_defaults(fn=cmd_capture)

    c = sub.add_parser("standup", help="post a standup")
    c.add_argument("--yesterday", default="")
    c.add_argument("--today", default="")
    c.add_argument("--blockers", default="")
    c.add_argument(
        "--draft",
        action="store_true",
        help="fill --yesterday from your own activity (the web My Day does this)",
    )
    c.set_defaults(fn=cmd_standup)

    c = sub.add_parser("my-day", help="your briefing")
    c.add_argument(
        "--cached", action="store_true", help="print the last briefing without a network call"
    )
    c.set_defaults(fn=cmd_my_day)

    c = sub.add_parser("tasks", help="list / mark done / delegate to an agent")
    c.add_argument("action", nargs="?", choices=["list", "done", "delegate"], default="list")
    c.add_argument("id", nargs="?", type=int)
    c.add_argument("agent", nargs="?", help="agent name (for delegate; you become sponsor)")
    c.add_argument("--all", action="store_true", help="include done tasks")
    c.set_defaults(fn=cmd_tasks)

    c = sub.add_parser("blockers", help="list / add / resolve blockers")
    c.add_argument("action", nargs="?", choices=["list", "add", "resolve"], default="list")
    c.add_argument("title", nargs="*", help="title (for add) or nothing")
    c.add_argument("--impact", default="medium", choices=["low", "medium", "high", "critical"])
    c.add_argument("--id", type=int, dest="id")
    c.add_argument("--resolution", default="")
    c.set_defaults(fn=cmd_blockers)

    c = sub.add_parser("search", help="full-text search the workspace")
    c.add_argument("query", nargs="+")
    c.set_defaults(fn=cmd_search)

    c = sub.add_parser(
        "ask", help="ask the workspace a question — answers cite the rows they came from"
    )
    c.add_argument("question", nargs="+")
    c.set_defaults(fn=cmd_ask)

    c = sub.add_parser("attention", help="how much is waiting on you")
    c.add_argument(
        "--porcelain",
        action="store_true",
        help="the bare number for a shell prompt, and nothing when it is zero",
    )
    c.set_defaults(fn=cmd_attention)

    c = sub.add_parser("promises", help="open promises / settle one")
    c.add_argument("action", nargs="?", choices=["list", "settle"], default="list")
    c.add_argument("id", nargs="?", type=int, help="promise id (for settle)")
    c.add_argument(
        "status", nargs="?", choices=["kept", "missed", "withdrawn"], help="verdict (for settle)"
    )
    c.add_argument("--all", action="store_true", help="include settled promises")
    c.set_defaults(fn=cmd_promises)

    c = sub.add_parser("absences", help="time away: list / add / rm")
    c.add_argument("action", nargs="?", choices=["list", "add", "rm"], default="list")
    c.add_argument("person", nargs="?", help="teammate (for add) or id (for rm)")
    c.add_argument("starts_on", nargs="?", help="YYYY-MM-DD")
    c.add_argument("ends_on", nargs="?", help="YYYY-MM-DD")
    c.add_argument("--kind", default="pto", choices=["pto", "oncall", "focus"])
    c.add_argument("--note", default="")
    c.set_defaults(fn=cmd_absences)

    c = sub.add_parser("review", help="pending proposals: list / approve / reject")
    c.add_argument("action", nargs="?", choices=["list", "approve", "reject"], default="list")
    c.add_argument("id", nargs="?", type=int)
    c.add_argument("-m", "--note", default="", help="verdict note (required for reject)")
    c.set_defaults(fn=cmd_review)

    c = sub.add_parser("answer", help="answer an open question")
    c.add_argument("id", type=int, help="question id")
    c.add_argument("text", nargs="+", help="the answer")
    c.set_defaults(fn=cmd_answer)

    c = sub.add_parser("worklog", help="a delegated task's progress log")
    c.add_argument("id", type=int, help="task id")
    c.set_defaults(fn=cmd_worklog)

    c = sub.add_parser("inbox", help="an agent's ambient inbox")
    c.add_argument("agent", help="agent name (e.g. scout)")
    c.set_defaults(fn=cmd_inbox)

    c = sub.add_parser(
        "eval",
        help="replay the capture classifier against its"
        " labeled feedback corpus (exit 1 on regressions)",
    )
    c.set_defaults(fn=cmd_eval)

    c = sub.add_parser("context", help="print the team context pack (org-brain)")
    c.add_argument("--write", metavar="PATH", help="write to a file (AGENTS.md) instead of stdout")
    c.add_argument(
        "--engagement",
        type=int,
        metavar="ID",
        help="one engagement's scoped pack: cheaper tokens, less noise",
    )
    c.set_defaults(fn=cmd_context)

    c = sub.add_parser("week", help="weekly commitment line: show / draft / commit")
    c.add_argument("action", nargs="?", choices=["show", "draft", "commit"], default="show")
    c.add_argument("ids", nargs="*", type=int, help="task ids (for commit)")
    c.set_defaults(fn=cmd_week)

    c = sub.add_parser("task", help="start work on a task (branch + status)")
    c.add_argument("action", choices=["start"])
    c.add_argument("task_id", type=int)
    c.set_defaults(fn=cmd_task_start)

    c = sub.add_parser("pr-body", help="compose a pull request description for this branch")
    c.add_argument("task_id", nargs="?", type=int, help="defaults to the id in the branch name")
    c.add_argument("--base", default="main", help="branch to list commits against")
    c.set_defaults(fn=cmd_pr_body)

    c = sub.add_parser("install-hooks", help="install the git commit hooks")
    c.add_argument("--force", action="store_true", help="replace a hook that is already there")
    c.set_defaults(fn=cmd_install_hooks)

    c = sub.add_parser("install-prompt", help="print a shell-prompt snippet for the count")
    c.add_argument("--shell", choices=["starship", "bash"], default="starship")
    c.set_defaults(fn=cmd_install_prompt)

    c = sub.add_parser("sync-commit", help="(hook) sync HEAD commit trailers")
    c.set_defaults(fn=cmd_sync_commit)

    args = p.parse_args()
    if args.cmd == "blockers" and args.action == "resolve" and not args.id:
        p.error("blockers resolve requires --id")
    if args.cmd == "blockers" and args.action == "add" and not args.title:
        p.error("blockers add requires a title")
    if args.cmd == "tasks" and args.action == "done" and not args.id:
        p.error("tasks done requires an id")
    if args.cmd == "tasks" and args.action == "delegate" and not (args.id and args.agent):
        p.error("tasks delegate requires: id agent")
    if (
        args.cmd == "promises"
        and args.action == "settle"
        and (args.id is None or args.status is None)
    ):
        p.error("promises settle requires an id and kept|missed|withdrawn")
    if args.cmd == "absences":
        if args.action == "add" and not (args.person and args.starts_on and args.ends_on):
            p.error("absences add requires: person starts_on ends_on")
        if args.action == "rm":
            if not (args.person or "").isdecimal():
                p.error("absences rm requires the absence id")
            args.id = int(args.person)
    if args.cmd == "review" and args.action in ("approve", "reject") and args.id is None:
        p.error(f"review {args.action} requires a proposal id")
    if args.cmd == "review" and args.action == "reject" and not args.note:
        p.error("review reject requires -m — the proposer reads the reason")
    args.fn(args)
    # AFTER the command, not before: the command itself is the proof the
    # server is reachable, and flushing first would make every offline
    # command pay a failed round trip. `attention` is excluded because it
    # runs on every shell prompt and must stay silent and instant.
    if args.cmd != "attention":
        sent = flush_outbox()
        if sent:
            print(f"filed {sent} capture{'' if sent == 1 else 's'} saved earlier")


if __name__ == "__main__":
    main()
