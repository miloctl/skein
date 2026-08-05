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
    skein my-day
    skein tasks
    skein tasks done 12
    skein blockers add "staging db down" --impact high
    skein search cutover
    skein week draft          # weekly commitment line
    skein commitments         # open promises; settle: skein commitments settle 3 kept
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
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG_PATH = Path(os.path.expanduser("~/.config/skein/config.json"))


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
    url = (
        cfg.get("url")
        or os.getenv("SKEIN_URL")
        or os.getenv("SKEIN_API_URL")
        or "http://localhost:8000"
    ).rstrip("/")
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
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
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
        sys.exit(f"error: cannot reach {url} ({exc.reason}) — run `skein config --url ...`")


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
    out = api("POST", "/api/capture", {"text": " ".join(args.text)})
    # the stored kind is the API contract; `promise` is the word the product
    # uses for it (docs/LEXICON.md)
    shown = {"commitment": "promise"}.get(out["kind"], out["kind"])
    print(f"captured as {shown} #{out['id']}")


def cmd_standup(args):
    api(
        "POST",
        "/api/standups",
        {"yesterday": args.yesterday, "today": args.today, "blockers": args.blockers},
    )
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
            " (question/blocker/decision/commitment/task/note) to gate regressions"
        )
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


def cmd_commitments(args):
    if args.action == "settle":
        api("POST", f"/api/commitments/{args.id}/status", {"status": args.status})
        print(f"promise #{args.id} {args.status}")
        return
    rows = api("GET", "/api/commitments" + ("" if args.all else "?status=open"))
    for c in rows:
        due = f" due {c['due_date']}" if c["due_date"] else ""
        who = f" → {c['to_whom']}" if c["to_whom"] else ""
        print(f"[{c['status']}] #{c['id']} {c['promise']}{who}{due} ({c['audience']})")
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


def cmd_install_hooks(args):
    git_dir = subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True, text=True)
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
    c.set_defaults(fn=cmd_standup)

    c = sub.add_parser("my-day", help="your briefing")
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

    c = sub.add_parser("commitments", help="open promises / settle one")
    c.add_argument("action", nargs="?", choices=["list", "settle"], default="list")
    c.add_argument("id", nargs="?", type=int, help="promise id (for settle)")
    c.add_argument(
        "status", nargs="?", choices=["kept", "missed", "withdrawn"], help="verdict (for settle)"
    )
    c.add_argument("--all", action="store_true", help="include settled promises")
    c.set_defaults(fn=cmd_commitments)

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
    c.add_argument(
        "--write", metavar="PATH", help="write to a file (e.g. AGENTS.md) instead of stdout"
    )
    c.set_defaults(fn=cmd_context)

    c = sub.add_parser("week", help="weekly commitment line: show / draft / commit")
    c.add_argument("action", nargs="?", choices=["show", "draft", "commit"], default="show")
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
    if args.cmd == "tasks" and args.action == "delegate" and not (args.id and args.agent):
        p.error("tasks delegate requires: id agent")
    if (
        args.cmd == "commitments"
        and args.action == "settle"
        and (args.id is None or args.status is None)
    ):
        p.error("commitments settle requires an id and kept|missed|withdrawn")
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


if __name__ == "__main__":
    main()
