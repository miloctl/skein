#!/usr/bin/env python3
"""skein — the team platform from your terminal. Stdlib only.
(Installed as both `skein` and the legacy `strands` alias.)

Setup:
    skein config --url http://localhost:8000 --key sk-strands-... [--user you]
    (create a key: curl -X POST $URL/api/keys -H 'X-User: you' -d '{"label":"cli"}')

Examples:
    strands capture "todo: ship the API"
    strands standup --today "auth flow" --blockers "waiting on vendor"
    strands my-day
    strands tasks
    strands tasks done 12
    strands blockers add "staging db down" --impact high
    strands search cutover
    strands week draft        # weekly commitment line
    strands eval              # replay capture classifier vs feedback corpus
    strands context --write AGENTS.md
    strands install-hooks     # git post-commit: Closes-Task: #N trailers
"""

import argparse
import json
import os
import subprocess
import sys
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG_PATH = Path(os.path.expanduser("~/.config/strands/config.json"))


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


def api(method: str, path: str, body: dict | None = None) -> dict | list:
    cfg = load_config()
    url = (cfg.get("url") or os.getenv("STRANDS_URL") or "http://localhost:8000").rstrip("/")
    headers = {"Content-Type": "application/json", "X-Client": "cli"}
    key = cfg.get("key") or os.getenv("STRANDS_API_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if cfg.get("user"):
        headers["X-User"] = cfg["user"]
    req = urllib.request.Request(
        url + path, method=method, headers=headers,
        data=json.dumps(body).encode() if body is not None else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("detail", str(exc))
        except Exception:
            detail = str(exc)
        sys.exit(f"error: {detail}")
    except urllib.error.URLError as exc:
        sys.exit(f"error: cannot reach {url} ({exc.reason}) — run `strands config --url ...`")


def cmd_config(args):
    cfg = load_config()
    if args.key == "-":  # prompt: keeps the key out of argv/shell history
        import getpass

        args.key = getpass.getpass("API key (sk-strands-…): ").strip()
    for field in ("url", "key", "user"):
        value = getattr(args, field)
        if value:
            cfg[field] = value
    save_config(cfg)
    shown = {**cfg, "key": (cfg.get("key", "")[:16] + "…") if cfg.get("key") else ""}
    print(json.dumps(shown, indent=1))


def cmd_capture(args):
    out = api("POST", "/api/capture", {"text": " ".join(args.text)})
    print(f"captured as {out['kind']} #{out['id']}")


def cmd_standup(args):
    api("POST", "/api/standups", {"yesterday": args.yesterday, "today": args.today,
                                  "blockers": args.blockers})
    print("standup posted" + (" (blocker auto-filed)" if args.blockers else ""))


def cmd_my_day(args):
    b = api("GET", "/api/briefing")
    n = b["needs_you"]
    print(f"# My Day — {b['user']}, {b['date']}\n")
    for q in n["open_questions"]:
        print(f"  ? #{q['id']} {q['question']} (from {q['asked_by']})")
    for bl in n["your_blockers"]:
        print(f"  ⛔ #{bl['id']} {bl['title']} [{bl['impact']}/{bl['status']}]")
    for nt in n.get("notifications", []):
        print(f"  🔔 {nt['message']}")
    if n["pending_reviews"]:
        print(f"  📥 {len(n['pending_reviews'])} pending review(s)")
    if n["intake_to_triage"]:
        print(f"  📨 {len(n['intake_to_triage'])} intake request(s) to triage")
    print("\n## Your tasks")
    for t in b["your_work"]["tasks"] or []:
        print(f"  [{t['priority']}/{t['status']}] #{t['id']} {t['title']}")
    if not b["your_work"]["tasks"]:
        print("  (none)")
    for e in b["team"]["todays_events"]:
        print(f"  📅 {e['starts_at']}: {e['title']}")


def cmd_tasks(args):
    if args.action == "done":
        api("PATCH", f"/api/tasks/{args.id}", {"status": "done"})
        print(f"task #{args.id} done")
        return
    for t in api("GET", "/api/tasks"):
        if t["status"] == "done" and not args.all:
            continue
        print(f"[{t['priority']}/{t['status']}] #{t['id']} {t['title']}"
              + (f" (@{t['assignee']})" if t["assignee"] else ""))


def cmd_blockers(args):
    if args.action == "add":
        out = api("POST", "/api/blockers", {"title": " ".join(args.title),
                                            "impact": args.impact})
        print(f"blocker #{out['id']} filed (escalates after {out['escalate_after_hours']}h)")
    elif args.action == "resolve":
        api("POST", f"/api/blockers/{args.id}/resolve",
            {"resolution": args.resolution or "resolved via CLI"})
        print(f"blocker #{args.id} resolved")
    else:
        for b in api("GET", "/api/blockers"):
            print(f"[{b['impact']}/{b['status']}] #{b['id']} {b['title']}"
                  + (f" (@{b['owner']})" if b["owner"] else " (unowned)"))


def cmd_search(args):
    hits = api("GET", "/api/search?q=" + urllib.parse.quote(" ".join(args.query)))
    for h in hits:
        snippet = re.sub(r"</?b>", "", h["snippet"])
        print(f"[{h['entity']} #{h['entity_id']}] {h['title']} — {snippet}")
    if not hits:
        print("no matches")


def cmd_eval(args):
    out = api("GET", "/api/eval/capture")
    if not out["cases"]:
        print("no labeled capture feedback yet — POST /api/feedback with"
              " kind=capture, verdict=up|corrected to build the corpus")
        return
    print(f"capture classifier: {out['passed']}/{out['cases']} passed"
          f" (accuracy {out['accuracy']})")
    for m in out["mismatches"]:
        print(f"  ✗ #{m['id']} {m['input']!r}: expected {m['expected']},"
              f" got {m['predicted']}")
    if out["mismatches"]:
        sys.exit(1)


def cmd_context(args):
    pack = api("GET", "/api/context-pack")
    if args.write:
        Path(args.write).write_text(pack["content"] + "\n")
        print(f"wrote context pack v{pack['version']} to {args.write}")
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


HOOK = """#!/bin/sh
# strands git hook: close tasks referenced by Closes-Task: #N trailers
strands sync-commit || true
"""


def cmd_install_hooks(args):
    git_dir = subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True,
                             text=True)
    if git_dir.returncode != 0:
        sys.exit("error: not a git repository")
    hook_path = Path(git_dir.stdout.strip()) / "hooks" / "post-commit"
    hook_path.parent.mkdir(exist_ok=True)
    hook_path.write_text(HOOK)
    hook_path.chmod(0o755)
    print(f"installed {hook_path}")
    print('commits with "Closes-Task: #12" (or Refs-Task) now sync to the platform')


TRAILER = re.compile(r"^(Closes|Refs)-Task:\s*#?(\d+)", re.I | re.M)


def cmd_sync_commit(args):
    msg = subprocess.run(["git", "log", "-1", "--format=%B%n%H"], capture_output=True,
                         text=True).stdout
    sha = msg.strip().splitlines()[-1][:10] if msg.strip() else "unknown"
    matches = TRAILER.findall(msg)
    if not matches:
        return
    for verb, task_id in matches:
        if verb.lower() == "closes":
            api("PATCH", f"/api/tasks/{task_id}", {"status": "done"})
            print(f"strands: task #{task_id} closed by commit {sha}")
        api("POST", "/api/notes", {
            "topic": f"commit-{sha}",
            "content": f"Commit {sha} {verb.lower()} task #{task_id}",
        })


def main():
    p = argparse.ArgumentParser(prog="skein", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("config", help="set server url / api key / user")
    c.add_argument("--url")
    c.add_argument("--key", nargs="?", const="-",
                   help="API key; omit the value to be prompted (keeps it out of shell history)")
    c.add_argument("--user")
    c.set_defaults(fn=cmd_config)

    c = sub.add_parser("capture", help="quick-capture text (auto-routed)")
    c.add_argument("text", nargs="+")
    c.set_defaults(fn=cmd_capture)

    c = sub.add_parser("standup", help="post a standup")
    c.add_argument("--yesterday", default=""); c.add_argument("--today", default="")
    c.add_argument("--blockers", default="")
    c.set_defaults(fn=cmd_standup)

    c = sub.add_parser("my-day", help="your briefing")
    c.set_defaults(fn=cmd_my_day)

    c = sub.add_parser("tasks", help="list tasks / mark done")
    c.add_argument("action", nargs="?", choices=["list", "done"], default="list")
    c.add_argument("id", nargs="?", type=int)
    c.add_argument("--all", action="store_true", help="include done tasks")
    c.set_defaults(fn=cmd_tasks)

    c = sub.add_parser("blockers", help="list / add / resolve blockers")
    c.add_argument("action", nargs="?", choices=["list", "add", "resolve"], default="list")
    c.add_argument("title", nargs="*", help="title (for add) or nothing")
    c.add_argument("--impact", default="medium",
                   choices=["low", "medium", "high", "critical"])
    c.add_argument("--id", type=int, dest="id")
    c.add_argument("--resolution", default="")
    c.set_defaults(fn=cmd_blockers)

    c = sub.add_parser("search", help="full-text search the workspace")
    c.add_argument("query", nargs="+")
    c.set_defaults(fn=cmd_search)

    c = sub.add_parser("eval", help="replay the capture classifier against its"
                       " labeled feedback corpus (exit 1 on regressions)")
    c.set_defaults(fn=cmd_eval)

    c = sub.add_parser("context", help="print the team context pack (org-brain)")
    c.add_argument("--write", metavar="PATH",
                   help="write to a file (e.g. AGENTS.md) instead of stdout")
    c.set_defaults(fn=cmd_context)

    c = sub.add_parser("week", help="weekly commitment line: show / draft / commit")
    c.add_argument("action", nargs="?", choices=["show", "draft", "commit"],
                   default="show")
    c.add_argument("ids", nargs="*", type=int, help="task ids (for commit)")
    c.set_defaults(fn=cmd_week)

    c = sub.add_parser("install-hooks", help="install the git post-commit trailer hook")
    c.set_defaults(fn=cmd_install_hooks)

    c = sub.add_parser("sync-commit", help="(hook) sync HEAD commit trailers")
    c.set_defaults(fn=cmd_sync_commit)

    args = p.parse_args()
    if args.cmd == "blockers" and args.action == "resolve" and not args.id:
        p.error("blockers resolve requires --id")
    if args.cmd == "blockers" and args.action == "add" and not args.title:
        p.error("blockers add requires a title")
    if args.cmd == "tasks" and args.action == "done" and not args.id:
        p.error("tasks done requires an id")
    args.fn(args)


if __name__ == "__main__":
    main()
