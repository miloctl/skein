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
                "reason": f"you own it; impact {b['impact']} — it escalates on a clock",
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
                "link": "/dashboard",
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
    """Cut at a word boundary with an ellipsis — never mid-word ("0 blocke")."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ·—-")
    return cut + "…"


def _coalesce(notifications: list[dict]) -> list[tuple[dict, int]]:
    """Stack near-duplicates ("claude ingested meeting notes: …" × 3) into one
    entry with a count; dismissing it surfaces the next on reload."""
    grouped: dict[str, list[dict]] = {}
    for n in notifications:
        key = (n["link"] or "") + "|" + n["message"].split(":", 1)[0]
        grouped.setdefault(key, []).append(n)
    return [(g[0], len(g) - 1) for g in grouped.values()]


_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _human_digest(rows: list[dict]) -> list[dict]:
    """The "Since yesterday" card is for teammates, not operators: drop
    chat-housekeeping rows (coalesced to one line per actor) and never show
    raw UUIDs in a human digest."""
    out: list[dict] = []
    tidied: dict[str, int] = {}
    for r in rows:
        if r["action"] in ("delete_chat", "rename_chat", "move_chat"):
            tidied[r["actor"]] = tidied.get(r["actor"], 0) + 1
            continue
        detail = _UUID_RE.sub("…", str(r["detail"] or "")).strip()
        out.append({**r, "detail": detail})
        if len(out) >= 20:
            break
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
    return out


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
        "pending_reviews": db.query(
            "SELECT id, entity, action, summary, proposed_by, created_at"
            " FROM pending_changes WHERE status = 'pending' ORDER BY id"
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
    return {
        "user": user,
        "date": today,
        "needs_you": needs_you,
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
                " AND due_date <= ? ORDER BY due_date",
                (week,),
            ),
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
    """Nav badge: the action tiers (decide/unblock/commit/review). notice-tier
    items (unread notifications) inform but must not nag from the nav.
    Caps (MIN) mirror the display limits in _attention/my_day so the badge
    never exceeds what the page can show."""
    today = datetime.now(timezone.utc).date()
    week = (today + timedelta(days=7)).isoformat()
    row = db.query_one(
        "SELECT"
        " (SELECT COUNT(*) FROM questions WHERE status = 'open' AND assigned_to = ?)"
        " + (SELECT COUNT(*) FROM pending_changes WHERE status = 'pending')"
        " + (SELECT COUNT(*) FROM blockers WHERE status != 'resolved' AND owner = ?)"
        " + (SELECT MIN(COUNT(*), 10) FROM intake_requests"
        "    WHERE status IN ('submitted', 'scored'))"
        " + (SELECT MIN(COUNT(*), 5) FROM decisions WHERE status = 'stale')"
        " + (SELECT COUNT(*) FROM commitments WHERE status = 'open'"
        "    AND due_date IS NOT NULL AND due_date <= ?)"
        " AS n",
        (user, user, week),
    )
    return row["n"] if row else 0
