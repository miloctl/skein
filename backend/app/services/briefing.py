"""My Day / attention inbox: pure SQL, answers "what changed and what needs me?"
in one call. Items are grouped by the kind of judgment required (decide /
unblock / commit / review / notice) and each carries a "why you're seeing
this" reason. An LLM narrative can be layered on top later (see digest.py)."""

import re
from datetime import datetime, timedelta, timezone

from .. import db


# groups, in display order: decide (what needs a call), unblock (what's
# stuck), commit (what you promised), review (what awaits your verdict),
# notice (worth knowing) — the frontend renders them in this order
def _attention(user: str, needs: dict, today: str, week: str) -> list[dict]:
    items = []
    for q in needs["open_questions"]:
        items.append(
            {
                "kind": "question",
                "ref_id": q["id"],
                "group": "unblock",
                "label": f"question #{q['id']}: {q['question'][:80]}",
                "reason": "assigned to you and still open — someone is waiting on the answer",
                "link": "/dashboard",
            }
        )
    for b in needs["your_blockers"]:
        items.append(
            {
                "kind": "blocker",
                "ref_id": b["id"],
                "group": "unblock",
                "label": f"blocker #{b['id']}: {b['title']}",
                "reason": f"you own it (impact {b['impact']}) — it escalates on a clock",
                "link": "/dashboard",
            }
        )
    for p in needs["pending_reviews"]:
        items.append(
            {
                "kind": "proposal",
                "ref_id": p["id"],
                "group": "review",
                "label": f"proposal #{p['id']}: {p['summary']}",
                "reason": f"proposed by {p['proposed_by']} — applies only after a human verdict",
                "link": "/review",
            }
        )
    for r in needs["intake_to_triage"]:
        items.append(
            {
                "kind": "intake",
                "ref_id": r["id"],
                "group": "decide",
                "label": f"intake #{r['id']}: {r['title']}",
                "reason": "awaiting a disposition — the requester sees the reason you give",
                "link": "/intake",
            }
        )
    for d in db.query("SELECT id, title FROM decisions WHERE status = 'stale' ORDER BY id LIMIT 5"):
        items.append(
            {
                "kind": "decision",
                "ref_id": d["id"],
                "group": "decide",
                "label": f"decision #{d['id']}: {d['title']}",
                "reason": "past its review-by date — reconfirm it or supersede it",
                "link": "/charter",
            }
        )
    for c in db.query(
        "SELECT id, promise, due_date, audience FROM commitments WHERE status = 'open'"
        " AND due_date IS NOT NULL AND due_date <= ? ORDER BY due_date",
        (week,),
    ):
        overdue = c["due_date"] < today
        items.append(
            {
                "kind": "commitment",
                "ref_id": c["id"],
                "group": "commit",
                "label": f"commitment #{c['id']}: {c['promise'][:80]}",
                "reason": (
                    f"{'OVERDUE since' if overdue else 'due'} {c['due_date']}"
                    + (" — a promise to the team" if c["audience"] == "team" else "")
                ),
                "link": "/portfolio",
            }
        )
    for n, similar in _coalesce(needs["notifications"])[:5]:
        items.append(
            {
                "kind": "notification",
                "ref_id": n["id"],
                "group": "notice",
                "label": _ellipsize(n["message"], 100)
                + (f" (+{similar} similar)" if similar else ""),
                "reason": (
                    "for the whole team — dismiss when read"
                    if n["user"] == "team"
                    else "for you — dismiss when read"
                ),
                "link": n["link"] or "/",
            }
        )
    return items


def _ellipsize(text: str, limit: int) -> str:
    """Cut at a word boundary with an ellipsis where one exists; a single
    space-free run (URL, token) hard-cuts at the limit instead."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ·—-")
    return cut + "…"


def _coalesce(notifications: list[dict]) -> list[tuple[dict, int]]:
    """Stack near-duplicates ("claude ingested meeting notes: …" × 3) into one
    entry with a count; dismissing it surfaces the next on reload. Short
    prefixes stay separate — "🚢 Shipped: A" and "🚢 Shipped: B" are distinct
    events, not duplicates."""
    grouped: dict[str, list[dict]] = {}
    for n in notifications:
        prefix = n["message"].split(":", 1)[0]
        key = (
            (n["link"] or "") + "|" + prefix
            if ":" in n["message"] and len(prefix) >= 15
            else f"solo|{n['id']}"
        )
        grouped.setdefault(key, []).append(n)
    return [(g[0], len(g) - 1) for g in grouped.values()]


_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _standup_suggestion(user: str, since: str) -> str:
    """Derive "yesterday" from what actually happened instead of asking for
    it — the minimum daily ask is one 'today' line plus blockers if any."""
    rows = db.query(
        "SELECT action, detail FROM activity WHERE actor = ? AND created_at >= ?"
        " AND action NOT IN ('delete_chat', 'rename_chat', 'move_chat', 'request_key')"
        " ORDER BY id DESC LIMIT 6",
        (user, since),
    )
    parts = []
    for r in rows[:3]:
        detail = _UUID_RE.sub("…", str(r["detail"] or "")).strip()
        parts.append(f"{str(r['action']).replace('_', ' ')} {detail}".strip()[:60])
    return "; ".join(parts)


def _human_digest(rows: list[dict]) -> list[dict]:
    """The "Since yesterday" card is for teammates, not operators: drop
    chat-housekeeping rows (coalesced to one line per actor) and never show
    raw UUIDs in a human digest. Scans every input row (the query caps at 40)
    so the tidy tally is honest, then caps the combined output at 20.
    NOTE: emits synthetic rows (string id "tidy-<actor>", empty created_at) —
    consumers must not parse ids as ints or sort by created_at."""
    out: list[dict] = []
    tidied: dict[str, int] = {}
    for r in rows:
        if r["action"] in ("delete_chat", "rename_chat", "move_chat"):
            tidied[r["actor"]] = tidied.get(r["actor"], 0) + 1
            continue
        detail = _UUID_RE.sub("…", str(r["detail"] or "")).strip()
        out.append({**r, "detail": detail})
    for actor, n in tidied.items():
        out.append(
            {
                "id": f"tidy-{actor}",
                "actor": actor,
                "action": "tidied",
                "detail": f"{n} chat{'s' if n > 1 else ''}",
                "created_at": "",
            }
        )
    return out[:20]


def my_day(user: str) -> dict:
    # UTC dates to match db.now() timestamps on the rows
    utc_today = datetime.now(timezone.utc).date()
    today = utc_today.isoformat()
    week = (utc_today + timedelta(days=7)).isoformat()
    yesterday = (utc_today - timedelta(days=1)).isoformat()

    needs_you = {
        "open_questions": db.query(
            "SELECT * FROM questions WHERE status = 'open' AND assigned_to = ? ORDER BY id",
            (user,),
        ),
        # LIMITed: a bulk ingest can legitimately file hundreds of proposals,
        # and this payload rides the hottest page — the count carries the rest
        "pending_reviews": db.query(
            "SELECT id, entity, action, summary, proposed_by, created_at"
            " FROM pending_changes WHERE status = 'pending' ORDER BY id LIMIT 50"
        ),
        "your_blockers": db.query(
            "SELECT * FROM blockers WHERE status != 'resolved' AND owner = ? ORDER BY created_at",
            (user,),
        ),
        "intake_to_triage": db.query(
            "SELECT id, title, requester, status, score FROM intake_requests"
            " WHERE status IN ('submitted', 'scored') ORDER BY score DESC LIMIT 10"
        ),
        "notifications": db.query(
            "SELECT * FROM notifications WHERE user IN (?, 'team') AND read_at IS NULL"
            " ORDER BY id DESC LIMIT 20",
            (user,),
        ),
    }
    pending_total = db.query_one(
        "SELECT COUNT(*) AS n FROM pending_changes WHERE status = 'pending'"
    )
    return {
        "user": user,
        "date": today,
        "needs_you": needs_you,
        # honest total alongside the LIMITed list — the header must not read
        # "50 things need you" while the nav badge says 300
        "pending_reviews_total": pending_total["n"] if pending_total else 0,
        "attention": _attention(user, needs_you, today, week),
        "your_work": {
            "tasks": db.query(
                "SELECT * FROM tasks WHERE assignee = ? AND status IN ('todo', 'in_progress', 'blocked')"
                " ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1"
                " WHEN 'medium' THEN 2 ELSE 3 END, due_date IS NULL, due_date",
                (user,),
            ),
            "due_soon": db.query(
                "SELECT * FROM tasks WHERE status != 'done' AND due_date IS NOT NULL"
                " AND due_date <= ? AND assignee IN (?, '') ORDER BY due_date",
                (week, user),
            ),
            "standup_suggestion": _standup_suggestion(user, yesterday),
        },
        "team": {
            "recently_shipped": db.query(
                "SELECT id, name, closed_at FROM engagements WHERE status = 'closed'"
                " AND closed_at >= ?",
                ((utc_today - timedelta(days=2)).isoformat(),),
            ),
            "escalated_blockers": db.query(
                "SELECT * FROM blockers WHERE status = 'escalated' ORDER BY created_at"
            ),
            "todays_events": db.query(
                "SELECT * FROM events WHERE starts_at >= ? AND starts_at < ? ORDER BY starts_at",
                (today, (utc_today + timedelta(days=1)).isoformat()),
            ),
            "recent_activity": _human_digest(
                db.query(
                    "SELECT * FROM activity WHERE created_at >= ? ORDER BY id DESC LIMIT 40",
                    (yesterday,),
                )
            ),
        },
    }


def attention_count(user: str) -> int:
    """Nav badge on Inbox. Counts ONLY what actually lives there — proposals
    awaiting a verdict and requests awaiting triage. Blockers, questions, and
    commitments render on My Day; counting them here made the badge promise
    things the destination doesn't show (a 3 that lands on an empty page)."""
    row = db.query_one(
        "SELECT"
        " (SELECT COUNT(*) FROM pending_changes WHERE status = 'pending')"
        " + (SELECT MIN(COUNT(*), 10) FROM intake_requests"
        "    WHERE status IN ('submitted', 'scored'))"
        " AS n"
    )
    return row["n"] if row else 0
