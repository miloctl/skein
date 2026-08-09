"""My Day / attention inbox: pure SQL, answers "what changed and what needs me?"
in one call. Items are grouped by the kind of judgment required (decide /
unblock / commit / review / notice) and each carries a "why you're seeing
this" reason. An LLM narrative can be layered on top later (see digest.py)."""

import re
from datetime import datetime, timedelta

from .. import db
from . import scope
from .scope import WORKSPACE_ONLY


# groups, in display order: decide (what needs a call), unblock (what's
# stuck), commit (what you promised), review (what awaits your verdict),
# notice (worth knowing) — the frontend renders them in this order
def _attention(user: str, needs: dict, today: str, week: str) -> list[dict]:
    items = []
    for ev in needs.get("meetings_awaiting_outcome", []):
        items.append(
            {
                "kind": "meeting",
                "ref_id": ev["id"],
                "group": "notice",
                "label": f"meeting: {ev['title'][:80]}",
                # the agenda is what makes this answerable: "did this produce
                # anything" is a question about what it was FOR
                "reason": (
                    f"it ran {_when(ev['starts_at'])} and no outcome is recorded"
                    + (f" — agenda: {ev['agenda'][:60]}" if ev["agenda"] else "")
                ),
                # /ingest is where an outcome gets written up. My Day carries
                # its own two buttons for the answer itself (app/page.tsx), so
                # the loop closes without leaving the page.
                "link": "/ingest",
            }
        )
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
                "reason": "awaiting your accept, defer, or decline — the requester sees the reason you give",
                "link": "/intake",
            }
        )
    for d in db.query(
        f"SELECT id, title FROM decisions WHERE status = 'stale' AND {WORKSPACE_ONLY}"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        " ORDER BY id LIMIT 5"
    ):
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
        # direction = 'given': these are YOUR promises. A received one is somebody
        # else's commitment to the team and has its own reader (the cockpit's
        # waiting-on card), so listing it here reads as work you owe.
        f"SELECT id, promise, due_date, audience FROM promises"  # noqa: S608 — scope filters emit only bound marks
        f" WHERE status = 'open' AND direction = 'given' AND {WORKSPACE_ONLY}"
        " AND due_date IS NOT NULL AND due_date <= ? ORDER BY due_date",
        (week,),
    ):
        overdue = c["due_date"] < today
        items.append(
            {
                "kind": "promise",
                "ref_id": c["id"],
                "group": "commit",
                "label": f"promise #{c['id']}: {c['promise'][:80]}",
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


def _when(stamp: str) -> str:
    """A stored naive-UTC stamp as prose. Raw ISO with the `T` in a sentence is
    a machine string in front of a reader."""
    try:
        return datetime.fromisoformat(stamp).strftime("%d %b at %H:%M")
    except ValueError:
        return stamp[:16]


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


def _scoped_recent(user: str, since: str) -> list[dict]:
    from .activity import visible_actor_filter

    actor_sql, params = visible_actor_filter(user)
    return db.query(
        f"SELECT * FROM activity WHERE created_at >= ? AND {actor_sql}"  # noqa: S608 — placeholders
        " ORDER BY COALESCE(seq, 0) DESC, id DESC LIMIT 40",
        (since, *params),
    )


def my_day(user: str, viewer: scope.Viewer = scope.NOBODY) -> dict:
    """`viewer`, not just `user`: these three lists are addressed to a person
    BY NAME, and a name is self-asserted in trusted-header mode. Keyed on the
    name alone, `X-User: ava` with no credential returned Ava's private task
    and blocker titles, which is the one thing docs/VISIBILITY.md decision 3
    refuses. The filter also expires access the moment somebody leaves a crew
    — membership is checked at the write, and this read outlives it.
    """
    from .review import _readable

    # The team's day (config.SKEIN_TZ): due_date and committed_week carry no
    # zone, so "due today" must mean the day the reader is living in.
    local_today = db.today()
    today = local_today.isoformat()
    week = (local_today + timedelta(days=7)).isoformat()
    # created_at is a UTC timestamp, so this bound is an instant, not a date —
    # a bare local date here would start the window at UTC midnight and drop
    # the evening's work west of UTC (db.local_midnight_utc)
    yesterday = db.local_midnight_utc(local_today - timedelta(days=1))

    q_f, q_p = scope.visible_filter(viewer, "questions")
    b_f, b_p = scope.visible_filter(viewer, "blockers")
    t_f, t_p = scope.visible_filter(viewer, "tasks")

    from .schedule import meetings_awaiting_outcome

    needs_you = {
        # meetings that have finished with nothing recorded. Viewer-scoped
        # like every other list here, and a NOTICE rather than a decide: the
        # reader is being told something, not asked to judge it.
        "meetings_awaiting_outcome": meetings_awaiting_outcome(viewer),
        "open_questions": db.query(
            f"SELECT * FROM questions WHERE status = 'open' AND assigned_to = ? AND {q_f}"  # noqa: S608 — scope.visible_filter emits only bound marks
            " ORDER BY id",
            (user, *q_p),
        ),
        # LIMITed: a bulk ingest can legitimately file hundreds of proposals,
        # and this payload rides the hottest page — the count carries the rest.
        #
        # Through review._readable, because `summary` is built by the producer
        # out of the target row's own text. GET /api/review filters this way
        # and this one did not, so the dashboard served the review queue's
        # scoped summaries to every caller — the same leak, one reader over.
        "pending_reviews": _readable(
            db.query(
                "SELECT id, entity, entity_id, action, summary, proposed_by, created_at"
                " FROM pending_changes WHERE status = 'pending' ORDER BY id LIMIT 50"
            ),
            viewer,
        ),
        "your_blockers": db.query(
            f"SELECT * FROM blockers WHERE status != 'resolved' AND owner = ? AND {b_f}"  # noqa: S608 — scope.visible_filter emits only bound marks
            " ORDER BY created_at",
            (user, *b_p),
        ),
        "intake_to_triage": db.query(
            f"SELECT id, title, requester, status, score FROM intake_requests"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
            f" WHERE {WORKSPACE_ONLY} AND status IN ('submitted', 'scored')"
            " ORDER BY score DESC LIMIT 10"
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
                "SELECT * FROM tasks WHERE assignee = ?"  # noqa: S608 — scope.visible_filter emits only bound marks
                f" AND status IN ('todo', 'in_progress', 'blocked') AND {t_f}"
                " ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1"
                " WHEN 'medium' THEN 2 ELSE 3 END, due_date IS NULL, due_date LIMIT 200",
                (user, *t_p),
            ),
            # The tier filter wraps BOTH arms. The unowned arm was already
            # workspace-locked; the named-assignee arm was not, and it is a
            # read that outlives the membership check made at the write — a
            # crew task assigned to somebody stayed on their My Day after they
            # left the crew, with SELECT * carrying title and description.
            #
            # assignee IN (?, '') is deliberate: an unowned task that is due is
            # everyone's business. The '' arm reaches every reader, and
            # assert_readable_by only ever checked a NAMED assignee — so this
            # arm takes the workspace lock, or an unowned crew task lands on
            # the whole roster's My Day. LIMIT is not deliberate: unbounded,
            # a team with thousands of stale overdue rows served every one of
            # them as SELECT * on every dashboard load, for every user.
            # ORDER BY due_date puts the most overdue first, so the cap drops
            # the least urgent. Reads idx_tasks_assignee_due (001_baseline.sql).
            "due_soon": db.query(
                "SELECT * FROM tasks WHERE status != 'done' AND due_date IS NOT NULL"  # noqa: S608 — scope.visible_filter emits only bound marks
                f" AND due_date <= ? AND {t_f}"
                f" AND (assignee = ? OR (assignee = '' AND {WORKSPACE_ONLY}))"
                " ORDER BY due_date LIMIT 50",
                (week, *t_p, user),
            ),
            "standup_suggestion": _standup_suggestion(user, yesterday),
        },
        "team": {
            "recently_shipped": db.query(
                f"SELECT id, name, closed_at FROM engagements WHERE status = 'closed'"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
                f" AND {WORKSPACE_ONLY} AND closed_at >= ?",
                (db.local_midnight_utc(local_today - timedelta(days=2)),),
            ),
            "escalated_blockers": db.query(
                f"SELECT * FROM blockers WHERE status = 'escalated' AND {WORKSPACE_ONLY}"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
                " ORDER BY created_at"
            ),
            "todays_events": db.query(
                f"SELECT * FROM events WHERE starts_at >= ? AND starts_at < ?"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
                f" AND {WORKSPACE_ONLY} ORDER BY starts_at",
                db.local_event_window(local_today),
            ),
            # scoped like /activity: your own strand plus agents and system —
            # My Day must not be the surface where colleagues watch each other
            "recent_activity": _human_digest(_scoped_recent(user, yesterday)),
        },
    }


def attention_count(user: str) -> int:
    """Nav badge on Inbox. Counts ONLY what actually lives there — proposals
    awaiting a verdict and requests awaiting triage. Blockers, questions, and
    promises render on My Day; counting them here made the badge promise
    things the destination doesn't show (a 3 that lands on an empty page)."""
    row = db.query_one(
        "SELECT"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        " (SELECT COUNT(*) FROM pending_changes WHERE status = 'pending')"
        f" + (SELECT MIN(COUNT(*), 10) FROM intake_requests"
        f"    WHERE {WORKSPACE_ONLY} AND status IN ('submitted', 'scored'))"
        " AS n"
    )
    return row["n"] if row else 0
