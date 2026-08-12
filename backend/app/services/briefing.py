"""My Day / attention inbox: pure SQL, answers "what changed and what needs me?"
in one call. Items are grouped by the kind of judgment required (decide /
unblock / commit / review / notice) and each carries a "why you're seeing
this" reason. An LLM narrative can be layered on top later (see digest.py)."""

import re
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from .. import db
from . import notifications, scope
from .scope import WORKSPACE_ONLY


# groups, in display order: decide (what needs a call), unblock (what's
# stuck), commit (what you promised), review (what awaits your verdict),
# notice (worth knowing) — the frontend renders them in this order.
#
# Every item also carries `audience`: "you" for a row addressed to this reader
# by name (assigned, owned, authored, notified) and "team" for a shared queue
# anyone may work. The distinction is load-bearing, not decoration — a heading
# that says "needs you" over rows nobody assigned to the reader teaches them to
# discount the whole page, and that page is the product's daily habit.
def _attention(user: str, needs: dict, today: str, week: str) -> list[dict]:
    items = []
    for ev in needs.get("meetings_awaiting_outcome", []):
        items.append(
            {
                "kind": "meeting",
                "ref_id": ev["id"],
                "group": "notice",
                # "you", though no column names this reader: the question is
                # "did this produce anything", and only somebody who was in
                # the room can answer it. The two buttons that answer it live
                # on this card (app/page.tsx), so moving the row to the shared
                # card would take the answer away from the ask.
                "audience": "you",
                "label": f"meeting: {ev['title'][:80]}",
                # the agenda is what makes this answerable: "did this produce
                # anything" is a question about what it was FOR
                "reason": (
                    f"it ran {db.local_moment(ev['starts_at'])} and no outcome is recorded"
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
                "audience": "you",
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
                "audience": "you",
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
                "audience": "you" if p.get("requested_by") == user else "team",
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
                "audience": "team",
                "label": f"intake #{r['id']}: {r['title']}",
                "reason": "needs an accept, defer, or decline — the requester reads the reason",
                "link": "/intake",
            }
        )
    for d in db.query(
        # decided_by = the reader, the same filter rituals.week_open uses for
        # the same sentence. "reconfirm it or supersede it" is an instruction,
        # and only the person who made a call can say it still holds — without
        # this every stale decision on the team landed on every My Day, so the
        # group read as somebody else's homework and got skipped wholesale.
        # Team-wide stale decisions have their own reader: the manager
        # intervention queue (services/intervention.py).
        f"SELECT id, title FROM decisions WHERE status = 'stale' AND {WORKSPACE_ONLY}"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        " AND decided_by = ? ORDER BY id LIMIT 5",
        (user,),
    ):
        items.append(
            {
                "kind": "decision",
                "ref_id": d["id"],
                "group": "decide",
                "audience": "you",
                "label": f"decision #{d['id']}: {d['title']}",
                "reason": "past its review-by date — reconfirm it or supersede it",
                # anchored, not the bare page: /charter renders the charter
                # category by default, and a general decision sent there
                # landed on a list that never contained it. The anchor is what
                # tells the page to widen (app/charter/page.tsx).
                "link": f"/charter#charter-entry-{d['id']}",
            }
        )
    for c in db.query(
        # direction = 'given': these are YOUR promises. A received one is somebody
        # else's commitment to the team and has its own reader (the cockpit's
        # waiting-on card), so listing it here reads as work you owe.
        #
        # created_by = the reader, matching rituals.week_open. Without it the
        # group said "you promised" about every open workspace promise on the
        # team — the same commitment shown to ten people, nine of whom cannot
        # settle it.
        f"SELECT id, promise, due_date, audience FROM promises"  # noqa: S608 — scope filters emit only bound marks
        f" WHERE status = 'open' AND direction = 'given' AND {WORKSPACE_ONLY}"
        " AND created_by = ? AND due_date IS NOT NULL AND due_date <= ?"
        " ORDER BY due_date",
        (user, week),
    ):
        overdue = c["due_date"] < today
        items.append(
            {
                "kind": "promise",
                "ref_id": c["id"],
                "group": "commit",
                "audience": "you",
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
                "audience": "you" if n["user"] != "team" else "team",
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


def _scoped_recent(user: str, since: str) -> list[dict]:
    from .activity import visible_actor_filter

    actor_sql, params = visible_actor_filter(user)
    return db.query(
        f"SELECT * FROM activity WHERE created_at >= ? AND {actor_sql}"  # noqa: S608 — placeholders
        " ORDER BY COALESCE(seq, 0) DESC, id DESC LIMIT 40",
        (since, *params),
    )


def my_day(
    user: str,
    viewer: scope.Viewer = scope.NOBODY,
    row_filter: Callable[[str, list[dict]], list[dict]] | None = None,
    mixed_filter: Callable[[list[dict]], list[dict]] | None = None,
    allow_unclassified: bool = True,
) -> dict:
    """`viewer`, not just `user`: these three lists are addressed to a person
    BY NAME, and a name is self-asserted in trusted-header mode. Keyed on the
    name alone, `X-User: ava` with no credential returned Ava's private task
    and blocker titles, which is the one thing docs/VISIBILITY.md decision 3
    refuses. The filter also expires access the moment somebody leaves a crew
    — membership is checked at the write, and this read outlives it.
    """
    from .review import _readable
    from .work import redact_task_relationships

    # The team's day (config.SKEIN_TZ): due_date and committed_week carry no
    # zone, so "due today" must mean the day the reader is living in.
    local_today = db.today()
    today = local_today.isoformat()
    week = (local_today + timedelta(days=7)).isoformat()
    # created_at is a UTC timestamp, so this bound is an instant, not a date —
    # a bare local date here would start the window at UTC midnight and drop
    # the evening's work west of UTC (db.local_midnight_utc)
    yesterday = db.local_midnight_utc(local_today - timedelta(days=1))
    # the label `committed_week` stores (services/weekly.py) — the reader's
    # own commitment for THIS week is what leads their task list below
    iso = local_today.isocalendar()
    this_week = f"{iso.year}-W{iso.week:02d}"

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
                # requested_by rides along so `_attention` can tell a proposal
                # this reader ASKED FOR from the shared queue anyone may work.
                # It is the difference between "your agent is waiting on you"
                # and "the team has a queue".
                "SELECT id, entity, entity_id, action, summary, proposed_by,"
                " requested_by, created_at, review_visibility, review_crew_id, review_owner"
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
        # notifications.UNREAD_FOR, never a second copy of the rule: a 'team'
        # row is one shared record and "read" is per person (009), so a query
        # that only tested read_at showed an announcement one teammate had
        # already dismissed for themselves to nobody, or to everybody, purely
        # by which copy of the predicate it happened to use.
        "notifications": db.query(
            f"SELECT * FROM notifications WHERE {notifications.UNREAD_FOR}"  # noqa: S608 — module constant with bound marks
            " ORDER BY id DESC LIMIT 20",
            (user, user),
        ),
    }
    pending_total = db.query_one(
        "SELECT COUNT(*) AS n FROM pending_changes WHERE status = 'pending'"
    )
    attention = _attention(user, needs_you, today, week)
    result: dict[str, Any] = {
        "user": user,
        "date": today,
        "needs_you": needs_you,
        # ONE definition of "how many things need you", computed here and read
        # by the header sentence, so it cannot disagree with the tab title.
        # `attention_count` counts the same rows for the tab and the CLI; a
        # second count in the browser drifted the moment either side coalesced,
        # capped or added a group, and the reader saw "(12)" on a tab over a
        # page that said nothing was waiting.
        "attention_total": sum(
            1 for a in attention if a["audience"] == "you" and a["group"] != "notice"
        ),
        # honest total alongside the LIMITed list — the header must not read
        # "50 things need you" while the nav badge says 300
        "pending_reviews_total": pending_total["n"] if pending_total else 0,
        # The label `committed_week` stores, sent rather than recomputed in the
        # browser. It is the same string the ORDER BY above binds, so the "this
        # week" chip cannot disagree with the ordering it is explaining. A
        # hand-rolled ISO week in JavaScript got this wrong for every year
        # whose Jan 1 is a Friday, and drifted by the browser's timezone in
        # every other year (db.today() is the TEAM day).
        "this_week": this_week,
        "attention": attention,
        "your_work": {
            # The plan changed, then the plan, then work already started,
            # then priority, then the clock.
            #
            # Priority-and-date alone is a backlog order, not a focus order:
            # it ranks an unplanned medium row above the task the reader
            # committed to this week. The commitment line is the team's own
            # answer to "what are you doing this week", and this is the one
            # place it is read back to the person who made it.
            #
            # But the commitment is NOT the first key. `urgent` is the word
            # this team reserves for "drop what you are doing", and an overdue
            # date is a commitment already broken — a Monday plan capped at
            # five per person would otherwise bury both at position six, and
            # the surface would be telling the reader to finish the plan while
            # the plan is what changed. `flow_metrics` treats unplanned work as
            # normal up to half a week, so a list that sinks all of it under
            # all of the plan disagrees with the product's own model.
            #
            # `in_progress` after the commitment, because work already open
            # costs more to leave than to finish.
            "tasks": redact_task_relationships(
                db.query(
                    "SELECT * FROM tasks WHERE assignee = ?"  # noqa: S608 — scope.visible_filter emits only bound marks
                    f" AND status IN ('todo', 'in_progress', 'blocked') AND {t_f}"
                    " ORDER BY CASE WHEN priority = 'urgent'"
                    "   OR (due_date IS NOT NULL AND due_date < ?) THEN 0 ELSE 1 END,"
                    " CASE WHEN committed_week = ? THEN 0 ELSE 1 END,"
                    " CASE status WHEN 'in_progress' THEN 0 ELSE 1 END,"
                    " CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1"
                    " WHEN 'medium' THEN 2 ELSE 3 END, due_date IS NULL, due_date LIMIT 200",
                    # `this_week` binds LAST: its placeholder is in ORDER BY, which
                    # follows the scope filter's marks in the SQL text. SQLite binds
                    # by position, not by clause.
                    # both ORDER BY marks bind after the filter's, in text order:
                    # the overdue test first, then the commitment test
                    (user, *t_p, today, this_week),
                ),
                viewer,
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
            "due_soon": redact_task_relationships(
                db.query(
                    "SELECT * FROM tasks WHERE status != 'done' AND due_date IS NOT NULL"  # noqa: S608 — scope.visible_filter emits only bound marks
                    f" AND due_date <= ? AND {t_f}"
                    f" AND (assignee = ? OR (assignee = '' AND {WORKSPACE_ONLY}))"
                    " ORDER BY due_date LIMIT 50",
                    (week, *t_p, user),
                ),
                viewer,
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
    if row_filter is not None:
        needs = result["needs_you"]
        needs["open_questions"] = row_filter("question", needs["open_questions"])
        needs["meetings_awaiting_outcome"] = row_filter("event", needs["meetings_awaiting_outcome"])
        needs["your_blockers"] = row_filter("blocker", needs["your_blockers"])
        needs["intake_to_triage"] = row_filter("intake", needs["intake_to_triage"])
        if allow_unclassified:
            if mixed_filter is not None:
                needs["pending_reviews"] = mixed_filter(needs["pending_reviews"])
            needs["notifications"] = row_filter("notification", needs["notifications"])
        else:
            # These summaries can carry free-form domain content without a
            # durable project key. If one project is denied, text is not a
            # safe source from which to guess its policy context.
            needs["pending_reviews"] = []
            needs["notifications"] = []
        result["your_work"]["tasks"] = row_filter("task", result["your_work"]["tasks"])
        result["your_work"]["due_soon"] = row_filter("task", result["your_work"]["due_soon"])
        result["team"]["recently_shipped"] = row_filter(
            "engagement", result["team"]["recently_shipped"]
        )
        result["team"]["escalated_blockers"] = row_filter(
            "blocker", result["team"]["escalated_blockers"]
        )
        result["team"]["todays_events"] = row_filter("event", result["team"]["todays_events"])
        result["team"]["recent_activity"] = (
            row_filter("activity", result["team"]["recent_activity"]) if allow_unclassified else []
        )
        filtered_attention = _attention(user, needs, today, week)
        result["attention"] = filtered_attention
        result["attention_total"] = sum(
            1
            for item in filtered_attention
            if item["audience"] == "you" and item["group"] != "notice"
        )
    return result


def attention_count(user: str, viewer: scope.Viewer = scope.NOBODY) -> dict:
    """Two numbers, because two readers ask two different questions.

    `inbox` is the nav badge on Inbox and counts ONLY what lives there —
    proposals awaiting a verdict and requests awaiting triage. Counting a
    blocker or a question here made the badge promise things the destination
    does not show (a 3 that lands on an empty page).

    `yours` is what is addressed to this person BY NAME and asks something of
    them: assigned open questions, owned unresolved blockers, own promises due
    inside a week, own stale decisions, and proposals they asked for. It is
    what the tab title and `skein attention` carry, and both of those say
    "waiting on you" — a sentence the Inbox number cannot honestly make.

    It MUST equal `my_day`'s `attention_total`, which the header prints: the
    two are read side by side, on a tab and the page that tab opens. Every arm
    below therefore mirrors an arm of `_attention` — the same predicate AND the
    same cap — with one stated exception on the proposal arm below.
    `test_attention_count_matches_the_page` holds the pair together.

    `yours` takes the VIEWER as well as the name, because keying on the
    reader's own name is not enough to match the page. A crew-tier question can
    legitimately be assigned to somebody the crew filter then hides from a weak
    viewer: `_attention` splices `visible_filter` and drops it, so a count that
    tested `assigned_to` alone read "(1) waiting on you" over a page whose
    header said 0 and whose body showed nothing to act on
    (`test_a_row_the_viewer_cannot_read_is_counted_by_neither`).

    `inbox` is a count over the WHOLE queue, unreadable rows included. It is
    the badge on a shared destination, and a per-viewer total would disagree
    with my_day's `pending_reviews_total`, which is unfiltered for the same
    reason. A count carries no title.
    """
    local_today = db.today()
    week = (local_today + timedelta(days=7)).isoformat()
    q_f, q_p = scope.visible_filter(viewer, "questions")
    b_f, b_p = scope.visible_filter(viewer, "blockers")
    row = db.query_one(
        "SELECT"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        " (SELECT COUNT(*) FROM pending_changes WHERE status = 'pending')"
        f" + (SELECT MIN(COUNT(*), 10) FROM intake_requests"
        f"    WHERE {WORKSPACE_ONLY} AND status IN ('submitted', 'scored'))"
        " AS inbox,"
        # Notifications are counted by NEITHER arm, and that is the whole
        # reason this returns the same number My Day prints. `_attention` files
        # every notification under `notice` — "worth knowing", not "waiting on
        # you" — and My Day's header excludes that group. A tab reading "(12)"
        # over a page saying nothing is waiting is the same broken promise this
        # split exists to fix, one surface further out. The notifications that
        # DO carry an obligation raise this count through the row behind them:
        # an assigned question, an owned blocker, a sponsor's acceptance ask.
        f" (SELECT COUNT(*) FROM questions WHERE status = 'open' AND assigned_to = ? AND {q_f})"
        f" + (SELECT COUNT(*) FROM blockers WHERE status != 'resolved' AND owner = ? AND {b_f})"
        # a proposal this reader ASKED FOR is addressed to them, and _attention
        # marks it audience 'you' — without this arm the tab undercounts by
        # exactly the rows the page puts under "Needs you".
        #
        # Counted inside the SAME `ORDER BY id LIMIT 50` window my_day reads,
        # or a bulk ingest past the fiftieth row makes the tab say twelve over
        # a page showing eleven. The one divergence left is `review._readable`,
        # which my_day applies and no SQL can: a proposal whose target row was
        # deleted drops from the page and is counted here. That is a row the
        # reader asked for and can still reject, so counting it is the safer
        # direction, and it cannot be closed without a viewer.
        " + (SELECT COUNT(*) FROM (SELECT requested_by FROM pending_changes"
        "     WHERE status = 'pending' ORDER BY id LIMIT 50) WHERE requested_by = ?)"
        f" + (SELECT COUNT(*) FROM promises WHERE status = 'open' AND direction = 'given'"
        f"    AND {WORKSPACE_ONLY} AND created_by = ?"
        "     AND due_date IS NOT NULL AND due_date <= ?)"
        # MIN(…, 5) mirrors `_attention`'s LIMIT 5 on the same query. Without
        # the cap the tab reads six and the page shows five.
        f" + (SELECT MIN(COUNT(*), 5) FROM decisions WHERE status = 'stale' AND {WORKSPACE_ONLY}"
        "     AND decided_by = ?)"
        " AS yours",
        # mark order, not argument order: the questions filter binds inside the
        # first subselect and the blockers filter inside the second, so each
        # scope tuple follows the `?` it qualifies
        (user, *q_p, user, *b_p, user, user, week, user),
    )
    return {
        "inbox": row["inbox"] if row else 0,
        "yours": row["yours"] if row else 0,
    }
