"""Insights: team-rolled trends and the weekly findings engine.

Design contract (docs/INSIGHTS.md): findings first, charts are receipts;
medians over means; n printed on every claim; no %-change claims when either
window has n<8; person-level data never appears here (future-vs-past rule —
individual data is for planning, team aggregates for judging the past).
All reads go through the same SQL the rest of the platform uses."""

import json
from datetime import date, datetime, timedelta

from .. import db
from . import scope, stats
from .scope import WORKSPACE_ONLY
from .slas import AGING_WIP_DAYS, VERDICT_FLOOR_N

WINDOW_DAYS = 28
# Round trips inside ONE turn's tool loop before it is worth a human's
# attention. A normal turn is single digits; a loop that cannot satisfy its
# tool climbs without bound. Deliberately absolute — see _r_turn_runaway.
TURN_CYCLE_ALARM = 25
# What share of a week's finished work was never planned before the week is an
# interrupt week rather than a busy one. Named here like every other threshold
# in this file, so docs/INSIGHTS.md quotes a constant and not a number a
# reader has to go find in a conditional.
INTERRUPT_SHARE_ALARM = 0.5


def _n(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _today() -> date:
    """The team's day (config.SKEIN_TZ), not the UTC day — see db.today()."""
    return db.today()


def _dt(stamp: str) -> datetime:
    """A stored naive-UTC stamp as a datetime. `starts_at` has no seconds."""
    return datetime.fromisoformat(stamp)


def _iso(d: date) -> str:
    """A local date as a bound.

    DECIDED, not overlooked: the analytics windows in this module ("the last
    28 days", "the last 8 weeks") bind this against created_at and updated_at,
    which are UTC timestamps — so each window is anchored to UTC midnight of a
    local date and runs one UTC offset wide at its edge. That is hours of
    smear on multi-week medians, and every rule in the file shares it, so the
    counts stay comparable with each other.

    Where a boundary is a CLAIM rather than a window, it is converted instead:
    the month boundary below (db.today().replace(day=1)) and the day and week
    keys the dedupe uses. A rule that starts asserting something about a
    single day must take db.local_midnight_utc, not this."""
    return d.isoformat()


def _week(d: date | None = None) -> str:
    iso = (d or _today()).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


# one implementation, shared with portfolio — see services/stats.py
_median = stats.median
_p85 = stats.p85


# ---- trends (team-rolled only) ----------------------------------------------


def _resolve_hours(since: str, until: str) -> list[float]:
    rows = db.query(
        "SELECT (julianday(resolved_at) - julianday(created_at)) * 24 AS h"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        f" FROM blockers WHERE status = 'resolved' AND {WORKSPACE_ONLY}"
        " AND resolved_at >= ? AND resolved_at < ?",
        (since, until),
    )
    return [r["h"] for r in rows if r["h"] is not None]


def _rolling_bounds() -> tuple[str, str, str]:
    """(prior_start, cut, upper) for two adjacent WINDOW_DAYS windows, the
    current one INCLUSIVE of today: current = [cut, upper), prior =
    [prior_start, cut), each exactly WINDOW_DAYS wide.

    One definition, because every rolling-window rule needs it and each spike
    rule that recomputed it drifted to 29-vs-28 — counting back a full
    WINDOW_DAYS from a today-inclusive upper bound is 29 days, a 3.6% wider
    current window on both sides of every ratio and n>=8 floor, under UI cards
    that read "rolling 28 days"."""
    today = _today()
    return (
        _iso(today - timedelta(days=2 * WINDOW_DAYS - 1)),
        _iso(today - timedelta(days=WINDOW_DAYS - 1)),
        _iso(today + timedelta(days=1)),
    )


def mttr_windows() -> dict:
    prior, cut, upper = _rolling_bounds()
    current = _resolve_hours(cut, upper)
    previous = _resolve_hours(prior, cut)
    return {
        "window_days": WINDOW_DAYS,
        "current": {
            "n": len(current),
            "median_hours": _median(current),
            "p85_hours": _p85(current),
        },
        "previous": {
            "n": len(previous),
            "median_hours": _median(previous),
            "p85_hours": _p85(previous),
        },
    }


def automation_ratio(months: int = 6) -> list[dict]:
    """Share of records by origin per month, across the core entities.
    Always read next to the rejection rate — a rising ratio with rising
    rejections is a problem, not a win."""
    since = _iso(_today() - timedelta(days=31 * months))
    union = " UNION ALL ".join(
        f"SELECT substr(created_at, 1, 7) AS month, origin FROM {t}"  # noqa: S608 — table tuple below
        f" WHERE created_at >= ?"
        for t in (
            "tasks",
            "milestones",
            "decisions",
            "notes",
            "blockers",
            "questions",
            "standups",
            "promises",
        )
    )
    rows = db.query(
        f"SELECT month, origin, COUNT(*) AS n FROM ({union})"  # noqa: S608 — built just above
        " GROUP BY month, origin ORDER BY month",
        tuple([since] * 8),
    )
    months_map: dict[str, dict] = {}
    for r in rows:
        m = months_map.setdefault(
            r["month"], {"month": r["month"], "human": 0, "agent": 0, "agent_verified": 0}
        )
        m[r["origin"]] = m.get(r["origin"], 0) + r["n"]
    out = []
    for m in months_map.values():
        total = m["human"] + m["agent"] + m["agent_verified"]
        m["total"] = total
        m["automation_share"] = (
            round((m["agent"] + m["agent_verified"]) / total, 2) if total else None
        )
        out.append(m)
    return out


def review_trend(months: int = 6) -> list[dict]:
    since = _iso(_today() - timedelta(days=31 * months))
    return db.query(
        "SELECT substr(created_at, 1, 7) AS month, COUNT(*) AS proposed,"
        " SUM(status = 'approved') AS approved, SUM(status = 'rejected') AS rejected,"
        " ROUND(AVG(CASE WHEN reviewed_at IS NOT NULL THEN"
        " (julianday(reviewed_at) - julianday(created_at)) * 24 END), 1) AS avg_review_hours"
        " FROM pending_changes WHERE created_at >= ?"
        " GROUP BY month ORDER BY month",
        (since,),
    )


def intake_funnel(weeks: int = 12) -> dict:
    since = _iso(_today() - timedelta(weeks=weeks))
    counts = db.query_one(
        "SELECT COUNT(*) AS submitted,"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        " SUM(status != 'submitted') AS scored_or_beyond,"
        " SUM(status = 'accepted') AS accepted,"
        " SUM(status = 'deferred') AS deferred,"
        " SUM(status = 'declined') AS declined"
        f" FROM intake_requests WHERE {WORKSPACE_ONLY} AND created_at >= ?",
        (since,),
    )
    times = [
        r["d"]
        for r in db.query(
            f"SELECT julianday(updated_at) - julianday(created_at) AS d"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
            f" FROM intake_requests WHERE updated_at >= ? AND {WORKSPACE_ONLY}"
            " AND status IN ('accepted', 'deferred', 'declined')",
            (since,),
        )
    ]
    return {
        "window_weeks": weeks,
        **(counts or {}),
        "median_days_to_disposition": _median(times),
        "dispositioned_n": len(times),
    }


def forecast_calibration(window_days: int = 180) -> dict:
    """How often the slip forecast was right, scored against what happened.

    This is the only reader of forecast_snapshots, which
    adoption.snapshot_forecasts fills one row per open milestone per day for
    exactly this purpose. A forecast nobody scores is a decoration, and this
    one gets quoted to stakeholders.

    Scored on the EARLIEST snapshot per milestone: the question a manager is
    answering is "can I trust the date I was given", and the date they were
    given is the first one. A mean over every snapshot would flatter the
    forecast, because the last snapshot before completion is nearly always
    close.

    Medians and an n, withheld under n=8 — docs/INSIGHTS.md, and the same bar
    the MTTR card holds.
    """
    # MIN(f.day) is the ONLY aggregate here on purpose: SQLite then takes the
    # bare f.forecast_date from the row that produced that minimum. Add a
    # second aggregate (a COUNT, a MAX) and forecast_date silently becomes an
    # arbitrary row's value — every error below is then wrong, with no error
    # raised. UNIQUE (day, milestone_id) makes the minimum unambiguous.
    rows = db.query(
        "SELECT f.milestone_id, MIN(f.day) AS first_day, f.forecast_date,"
        " m.completed_at"
        " FROM forecast_snapshots f JOIN milestones m ON m.id = f.milestone_id"
        # bounded on the MILESTONE's completion, never the snapshot's creation:
        # as a WHERE on f.created_at it prunes rows BEFORE MIN() runs, so a
        # milestone older than the window gets scored on its earliest
        # IN-WINDOW forecast — the converged one — which flatters the forecast
        # in exactly the way choosing the earliest snapshot exists to prevent
        " WHERE m.status = 'done' AND m.completed_at IS NOT NULL AND m.completed_at >= ?"
        " GROUP BY f.milestone_id",
        (db.local_midnight_utc(_today() - timedelta(days=window_days)),),
    )
    errors: list[float] = []
    on_or_before = 0
    for r in rows:
        if not r["forecast_date"]:
            continue
        try:
            actual = date.fromisoformat(db.local_day(r["completed_at"]))
            forecast = date.fromisoformat(r["forecast_date"])
        except (TypeError, ValueError):
            continue  # a malformed stored date must not take down /insights
        delta = (actual - forecast).days
        errors.append(float(delta))
        if delta <= 0:
            on_or_before += 1
    n = len(errors)
    return {
        "n": n,
        "window_days": window_days,
        # signed: a forecast that is habitually EARLY and one that is
        # habitually late are different problems, and |error| hides which
        "median_error_days": _median(errors),
        # the absolute miss, for "how far off is it typically"
        "median_abs_error_days": _median([abs(e) for e in errors]),
        # withheld under the same floor every other claim here uses — a hit
        # rate over three milestones is noise wearing a percentage sign
        "hit_rate": round(on_or_before / n, 2) if n >= VERDICT_FLOOR_N else None,
        # withheld with the rate it reconstructs: hits/n IS hit_rate, so
        # serving it under the floor hands back the number the floor exists
        # to withhold (docs/INSIGHTS.md)
        "hits": on_or_before if n >= VERDICT_FLOOR_N else None,
    }


def token_spend_weekly(weeks: int = 8) -> list[dict]:
    since = _iso(_today() - timedelta(weeks=weeks))
    rows = db.query(
        "SELECT created_at, input_tokens, output_tokens FROM usage_log WHERE created_at >= ?",
        (since,),
    )
    buckets: dict[str, int] = {}
    for r in rows:
        wk = _week(date.fromisoformat(db.local_day(r["created_at"])))
        buckets[wk] = buckets.get(wk, 0) + r["input_tokens"] + r["output_tokens"]
    return [{"week": w, "tokens": t} for w, t in sorted(buckets.items())]


def insights() -> dict:
    from .adoption import adoption
    from .feedback import pulse_tally

    return {
        "pulse_tally": pulse_tally(),
        "mttr": mttr_windows(),
        "automation_ratio": automation_ratio(),
        "review_trend": review_trend(),
        "intake_funnel": intake_funnel(),
        "forecast_calibration": forecast_calibration(),
        "token_spend_weekly": token_spend_weekly(),
        # adoption() is team-rolled by construction — the per-person tally that
        # the anti-surveillance rule refuses was removed at the source, so it
        # is safe here and at GET /api/adoption alike (fixing it in one filter
        # here was the leak: the next endpoint over had none)
        "adoption": adoption(),
        "findings": list_findings(weeks=4),
        "rule_stats": rule_stats(),
    }


# ---- findings engine ---------------------------------------------------------


def _finding(
    rule_id: str,
    severity: str,
    message: str,
    receipt: dict,
    n: int | None = None,
    window: str = "",
    subject: str = "",
) -> dict:
    return {
        "rule_id": rule_id,
        "subject": subject or rule_id,
        "severity": severity,
        "message": message,
        "n": n,
        "window": window,
        "receipt": receipt,
    }


def _r_mttr() -> list[dict]:
    w = mttr_windows()
    cur, prev = w["current"], w["previous"]
    if cur["n"] < 8 or prev["n"] < 8 or not prev["median_hours"]:
        return []
    ratio = cur["median_hours"] / prev["median_hours"]
    _, cut, _upper = _rolling_bounds()
    slowest = db.query(
        "SELECT id, title, ROUND((julianday(resolved_at) - julianday(created_at)) * 24) AS hours"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        f" FROM blockers WHERE status = 'resolved' AND {WORKSPACE_ONLY}"
        " AND resolved_at >= ? ORDER BY hours DESC LIMIT 3",
        (cut,),
    )
    receipt = {"current": cur, "previous": prev, "slowest": slowest}
    if ratio >= 1.5:
        return [
            _finding(
                "mttr_regression",
                "high",
                f"Blocker clear time regressed: median {cur['median_hours']}h"
                f" (n={cur['n']}) vs {prev['median_hours']}h (n={prev['n']})"
                " in the prior 28 days.",
                receipt,
                n=cur["n"],
                window="28d vs prior 28d",
            )
        ]
    if ratio <= 0.67:
        return [
            _finding(
                "mttr_improvement",
                "positive",
                f"Blocker clear time improved: median {cur['median_hours']}h"
                f" (n={cur['n']}) vs {prev['median_hours']}h (n={prev['n']}).",
                receipt,
                n=cur["n"],
                window="28d vs prior 28d",
            )
        ]
    return []


def _escalated_share(since: str, until: str) -> tuple[int, float | None]:
    row = db.query_one(
        f"SELECT COUNT(*) AS n, SUM(escalated_at IS NOT NULL) AS esc FROM blockers WHERE {WORKSPACE_ONLY}"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        " AND status = 'resolved' AND resolved_at >= ? AND resolved_at < ?",
        (since, until),
    )
    if not row or not row["n"]:
        return 0, None
    return row["n"], row["esc"] / row["n"]


def _r_escalation_spike() -> list[dict]:
    prior, cut, upper = _rolling_bounds()
    n, share = _escalated_share(cut, upper)
    if n < 6 or share is None or share < 0.4:
        return []
    pn, pshare = _escalated_share(prior, cut)
    if pn >= 6 and pshare and share < 1.5 * pshare:
        return []
    ids = db.query(
        f"SELECT id, title, impact FROM blockers WHERE status = 'resolved' AND {WORKSPACE_ONLY}"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        " AND escalated_at IS NOT NULL AND resolved_at >= ?",
        (cut,),  # already an ISO string from _rolling_bounds
    )
    return [
        _finding(
            "escalation_spike",
            "medium",
            f"{round(share * 100)}% of the last {n} resolved blockers"
            " escalated before anyone cleared them.",
            {"escalated": ids, "share": round(share, 2)},
            n=n,
            window="28d",
        )
    ]


def _r_aging_wip() -> list[dict]:
    cutoff = _iso(_today() - timedelta(days=AGING_WIP_DAYS))
    wip = db.query_one(
        f"SELECT COUNT(*) AS n FROM tasks WHERE status = 'in_progress' AND {WORKSPACE_ONLY}"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
    )
    aging = db.query(
        "SELECT t.id, t.title, m.project,"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        " CAST(julianday('now') - julianday(t.updated_at) AS INTEGER) AS days"
        # m.project is persisted into a findings row, which is never pruned and
        # is republished every week — the lock rides the ON clause because m is
        # the nullable side (services/scope.py::visible_filter)
        f" FROM tasks t LEFT JOIN milestones m ON m.id = t.milestone_id AND m.{WORKSPACE_ONLY}"
        f" WHERE t.{WORKSPACE_ONLY}"
        " AND t.status = 'in_progress' AND t.updated_at < ? ORDER BY days DESC",
        (cutoff,),
    )
    if not wip or len(aging) < max(4, round(0.25 * wip["n"])):
        return []
    return [
        _finding(
            "aging_wip",
            "medium",
            f"{len(aging)} of {wip['n']} in-progress tasks have sat untouched"
            f" for over {AGING_WIP_DAYS} days.",
            {"tasks": aging},
            n=len(aging),
            window="point-in-time",
        )
    ]


def _r_commitment_line() -> list[dict]:
    from .weekly import week_view

    def kept(offset: int) -> dict:
        iso = (_today() + timedelta(weeks=offset)).isocalendar()
        return week_view(f"{iso.year}-W{iso.week:02d}")

    last = kept(-1)
    if last["committed"] >= 5 and last["kept_percent"] is not None:
        if last["kept_percent"] < 60:
            return [
                _finding(
                    "promise_slip",
                    "medium",
                    f"Last week's commitment line landed at {last['kept_percent']}%"
                    f" ({last['done']}/{last['committed']} committed tasks done).",
                    {
                        "week": last["week"],
                        "unfinished": [t["id"] for t in last["tasks"] if t["status"] != "done"],
                    },
                    n=last["committed"],
                    window=last["week"],
                )
            ]
        two_ago = kept(-2)
        if (
            two_ago["committed"] >= 5
            and two_ago["kept_percent"] is not None
            and last["kept_percent"] < 75
            and two_ago["kept_percent"] < 75
        ):
            return [
                _finding(
                    "promise_slip",
                    "medium",
                    f"Two straight weeks under 75% on the commitment line"
                    f" ({two_ago['kept_percent']}%, then {last['kept_percent']}%).",
                    {"weeks": [two_ago["week"], last["week"]]},
                    n=last["committed"],
                    window=f"{two_ago['week']}..{last['week']}",
                )
            ]
    return []


def _r_promises_external() -> list[dict]:
    out = []
    today = _iso(_today())
    soon = _iso(_today() + timedelta(days=7))
    for c in db.query(
        # direction = 'given': a RECEIVED promise also defaults to audience
        # 'external', and this rule is about what the team owes outsiders
        f"SELECT * FROM promises WHERE status = 'open' AND audience = 'external'"  # noqa: S608 — scope filters emit only bound marks
        f" AND direction = 'given' AND {WORKSPACE_ONLY}"
        " AND due_date IS NOT NULL AND due_date <= ?",
        (soon,),
    ):
        overdue = c["due_date"] < today
        out.append(
            _finding(
                "promise_due",
                "high" if overdue else "medium",
                (
                    f"External promise OVERDUE since {c['due_date']}"
                    if overdue
                    else f"External promise due {c['due_date']}"
                )
                + f": “{c['promise']}” (to {c['to_whom'] or 'unspecified'})."
                + (
                    " Keep it, renegotiate it, or mark it missed — do not let it drift."
                    if overdue
                    else ""
                ),
                {"promise_id": c["id"]},
                n=1,
                window="7d",
                subject=f"promise-{c['id']}",
            )
        )
    week_ago = _iso(_today() - timedelta(days=7))
    for c in db.query(
        # direction AND audience, like the sibling query above: a RECEIVED
        # promise marked missed is the other party breaking it, and this rule
        # is high severity — it reached the digest saying the team missed a
        # promise it never made
        f"SELECT * FROM promises WHERE status = 'missed' AND direction = 'given'"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        f" AND audience = 'external' AND {WORKSPACE_ONLY} AND updated_at >= ?",
        (week_ago,),
    ):
        out.append(
            _finding(
                "promise_missed",
                "high",
                f"External promise MISSED: “{c['promise']}” (to {c['to_whom'] or 'unspecified'}).",
                {"promise_id": c["id"]},
                n=1,
                window="7d",
                subject=f"promise-{c['id']}",
            )
        )
    return out


def _r_review_stall() -> list[dict]:
    # scope.NOBODY: the receipt this builds lands in `findings`, which has no
    # identity column, a UNIQUE(rule_id, subject, week) key and no pruning —
    # docs/VISIBILITY.md calls it the most dangerous sink in the app. A
    # proposal's `summary` quotes its target row's text.
    from .review import _readable

    pending = _readable(
        db.query(
            "SELECT id, entity, entity_id, summary, proposed_by,"
            " ROUND((julianday('now') - julianday(created_at)) * 24) AS hours"
            " FROM pending_changes WHERE status = 'pending' ORDER BY created_at"
        ),
        scope.NOBODY,
    )
    old = [p for p in pending if p["hours"] >= 72]
    oldest_days = round(pending[0]["hours"] / 24, 1) if pending else 0
    if len(old) >= 3 or oldest_days > 7:
        return [
            _finding(
                "review_stall",
                "high",
                f"The review queue is stalled: {_n(len(old), 'proposal')}"
                f" older than 72h, oldest {oldest_days} days."
                " A stalled queue quietly kills agent delegation.",
                {"pending": pending[:10]},
                n=len(pending),
                window="point-in-time",
            )
        ]
    return []


def _r_rejection_spike() -> list[dict]:
    prior_cut, cut, _upper = _rolling_bounds()
    cur = db.query_one(
        "SELECT COUNT(*) AS n, SUM(status = 'rejected') AS rej FROM pending_changes"
        " WHERE status != 'pending' AND reviewed_at >= ?",
        (cut,),
    )
    if not cur or cur["n"] < 10:
        return []
    rate = cur["rej"] / cur["n"]
    if rate < 0.3:
        return []
    prev = db.query_one(
        "SELECT COUNT(*) AS n, SUM(status = 'rejected') AS rej FROM pending_changes"
        " WHERE status != 'pending' AND reviewed_at >= ? AND reviewed_at < ?",
        (prior_cut, cut),
    )
    if prev and prev["n"] >= 10 and rate < 1.5 * (prev["rej"] / prev["n"] or 0.01):
        return []
    # same sink, same rule as _r_review_stall above
    from .review import _readable

    notes = _readable(
        db.query(
            "SELECT entity, entity_id, summary, review_note FROM pending_changes"
            " WHERE status = 'rejected' AND reviewed_at >= ? AND review_note != ''"
            " ORDER BY id DESC LIMIT 10",
            (cut,),
        ),
        scope.NOBODY,
    )
    return [
        _finding(
            "rejection_spike",
            "medium",
            f"{round(rate * 100)}% of {cur['n']} reviewed proposals were"
            " rejected in the last 28 days — read the reviewer notes.",
            {"notes": notes},
            n=cur["n"],
            window="28d",
        )
    ]


def _r_intake_stall() -> list[dict]:
    since = _iso(_today() - timedelta(weeks=6))
    times = [
        r["d"]
        for r in db.query(
            f"SELECT julianday(updated_at) - julianday(created_at) AS d"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
            f" FROM intake_requests WHERE updated_at >= ? AND {WORKSPACE_ONLY}"
            " AND status IN ('accepted', 'deferred', 'declined')",
            (since,),
        )
    ]
    med = _median(times)
    if med is not None and len(times) >= 5 and med > 7:
        return [
            _finding(
                "intake_stall",
                "medium",
                f"Median time from intake to disposition is {med} days"
                f" over the last 6 weeks (n={len(times)}).",
                {"median_days": med},
                n=len(times),
                window="6w",
            )
        ]
    old = db.query(
        "SELECT id, title, score,"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        " CAST(julianday('now') - julianday(created_at) AS INTEGER) AS days"
        f" FROM intake_requests WHERE {WORKSPACE_ONLY} AND status IN ('submitted', 'scored')"
        " AND created_at < ?",
        (_iso(_today() - timedelta(days=14)),),
    )
    if len(old) >= 3:
        return [
            _finding(
                "intake_stall",
                "medium",
                f"{_n(len(old), 'intake request')}"
                f" {'is' if len(old) == 1 else 'are'} more than two weeks old"
                " without a disposition.",
                {"requests": old},
                n=len(old),
                window="point-in-time",
            )
        ]
    return []


def _r_question_aging() -> list[dict]:
    out = []
    for q in db.query(
        "SELECT id, question, asked_by,"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        " CAST(julianday('now') - julianday(created_at) AS INTEGER) AS days"
        f" FROM questions WHERE status = 'open' AND {WORKSPACE_ONLY} AND created_at < ?",
        (_iso(_today() - timedelta(days=5)),),
    ):
        out.append(
            _finding(
                "question_aging",
                "low",
                f"Question #{q['id']} has been open {q['days']} days: “{q['question'][:100]}”",
                {"question_id": q["id"], "asked_by": q["asked_by"]},
                n=1,
                window="point-in-time",
                subject=f"question-{q['id']}",
            )
        )
    return out


def _r_decision_decay() -> list[dict]:
    stale = db.query(
        f"SELECT id, title, review_by FROM decisions WHERE status = 'stale' AND {WORKSPACE_ONLY}"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
    )
    corpus = db.query_row(
        f"SELECT COUNT(*) AS n FROM decisions WHERE status != 'superseded' AND {WORKSPACE_ONLY}"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
    )
    if not stale:
        return []
    if len(stale) >= 3 or (corpus["n"] and len(stale) / corpus["n"] >= 0.25):
        return [
            _finding(
                "decision_decay",
                "low",
                f"{_n(len(stale), 'standing decision')}"
                f" {'is' if len(stale) == 1 else 'are'} past the review-by"
                f" date — reconfirm or supersede"
                f" {'it' if len(stale) == 1 else 'them'} before someone cites one.",
                {"decisions": stale},
                n=len(stale),
                window="point-in-time",
            )
        ]
    return []


def _r_token_anomaly() -> list[dict]:
    weekly = token_spend_weekly(6)
    if len(weekly) < 3:
        return []
    this_week = _week()
    current = next((w["tokens"] for w in weekly if w["week"] == this_week), 0)
    prior = [w["tokens"] for w in weekly if w["week"] != this_week]
    med = _median([float(p) for p in prior])
    if med and current >= 2 * med and current >= 500_000:
        top = db.query(
            "SELECT thread_id, model_id, SUM(input_tokens + output_tokens) AS tokens"
            " FROM usage_log WHERE created_at >= ? GROUP BY thread_id, model_id"
            " ORDER BY tokens DESC LIMIT 3",
            (_iso(_today() - timedelta(days=7)),),
        )
        return [
            _finding(
                "token_anomaly",
                "medium",
                f"Token spend this week ({current:,}) is over 2× the"
                f" recent weekly median ({int(med):,}).",
                {"top_threads": top},
                n=len(prior),
                window="week",
            )
        ]
    return []


def _r_turn_runaway() -> list[dict]:
    """ONE turn that looped. The weekly token rule above catches a spend
    trend; it cannot see a single agent that burned a hundred cycles in an
    afternoon, because a week's total absorbs it.

    A cycle is one model round trip inside a turn's tool loop, so a high count
    is the shape of an agent arguing with a tool it cannot satisfy — the
    failure an unattended run makes expensive. Absolute, not a ratio: there is
    no honest baseline for "normal cycles" until a deployment has months of
    turns, and a ratio over a tiny sample fires on the second turn ever."""
    rows = db.query(
        "SELECT id, agent_name, model_id, cycles, input_tokens + output_tokens AS tokens,"
        " created_at FROM usage_log WHERE cycles >= ? AND created_at >= ?"
        " ORDER BY cycles DESC LIMIT 5",
        (TURN_CYCLE_ALARM, db.local_midnight_utc(_today() - timedelta(days=7))),
    )
    if not rows:
        return []
    worst = rows[0]
    return [
        _finding(
            "turn_runaway",
            "medium",
            # "agent turn", not "chat turn": the case this rule was written
            # for is an UNATTENDED run (services/agent_runner.py), whose
            # thread no chat list shows — and LEXICON fixes `chat` to a
            # conversation a person had
            f"{_n(len(rows), 'agent turn')} in the last 7 days ran"
            f" {TURN_CYCLE_ALARM} or more model round trips."
            f" The largest was {worst['cycles']} round trips by"
            f" {worst['agent_name'] or 'the chat agent'}, spending"
            f" {worst['tokens']:,} tokens. Read the turn before it repeats.",
            {"turns": rows},
            subject=f"turns:{worst['id']}",
            n=len(rows),
            window="7d",
        )
    ]


def _r_flock_failures() -> list[dict]:
    """Members that failed inside a flock turn. flock_traces has recorded a
    per-member status since flocks shipped and nothing has ever read it, so a
    persona that fails every time it is called looks, from every surface, like
    a persona nobody uses."""
    rows = db.query(
        "SELECT members FROM flock_traces WHERE created_at >= ?",
        (db.local_midnight_utc(_today() - timedelta(days=7)),),
    )
    failed: dict[str, int] = {}
    total: dict[str, int] = {}
    for r in rows:
        try:
            members = json.loads(r["members"] or "[]")
        except (TypeError, ValueError):
            continue  # a malformed trace must not take down the findings run
        for m in members:
            slug = str(m.get("slug", ""))
            if not slug:
                continue
            total[slug] = total.get(slug, 0) + 1
            if m.get("status") not in ("ok", None):
                failed[slug] = failed.get(slug, 0) + 1
    # every call failing is the signal — a persona that sometimes fails is a
    # slow model, and a rule that fires on that gets ignored
    broken = sorted(s for s, n in failed.items() if n == total.get(s) and n >= 2)
    if not broken:
        return []
    return [
        _finding(
            "flock_member_failing",
            "medium",
            f"{_n(len(broken), 'flock specialist')} failed every time"
            f" {'it was' if len(broken) == 1 else 'they were'} called in the last"
            f" 7 days: {', '.join(broken)}. Check the persona file and the model it names.",
            {"slugs": broken, "calls": {s: total[s] for s in broken}},
            subject=f"slugs:{','.join(broken)}",
            n=sum(total[s] for s in broken),
            window="7d",
        )
    ]


def _r_experiment_overdue() -> list[dict]:
    overdue = db.query(
        f"SELECT id, name, timebox_end, kill_criteria FROM engagements WHERE {WORKSPACE_ONLY}"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        " AND kind = 'experiment' AND status != 'closed' AND conclusion IS NULL"
        " AND timebox_end IS NOT NULL AND timebox_end < ?",
        (_iso(_today()),),
    )
    return [
        _finding(
            "experiment_overdue",
            "medium",
            f"Experiment '{e['name']}' is past its timebox ({e['timebox_end']})"
            " with no recorded conclusion — conclude it or extend it on purpose.",
            {"engagement_id": e["id"], "kill_criteria": e["kill_criteria"]},
            n=1,
            subject=f"engagement-{e['id']}",
            window="point-in-time",
        )
        for e in overdue
    ]


def _r_authority_stale() -> list[dict]:
    """Elevated authority grants past their review-by date. The nudge, not a
    demotion state machine — the human reconfirms (re-grants) or demotes."""
    # NULL review_by falls back to updated_at + 90d — a grant that dodged
    # migration 018 (direct SQL, restored backup) must still expire to a nag
    stale = db.query(
        "SELECT agent, entity, level, review_by, updated_by FROM agent_authority"
        " WHERE level IN ('autonomous', 'notify')"
        " AND COALESCE(review_by, date(updated_at, '+90 days')) < ?",
        (_iso(_today()),),
    )
    return [
        _finding(
            "authority_stale",
            "medium",
            f"Agent '{g['agent']}' has held {g['level']} authority over"
            f" {g['entity']} past its review date ({g['review_by']}) —"
            " reconfirm the grant or demote it to review.",
            {"agent": g["agent"], "entity": g["entity"], "granted_by": g["updated_by"]},
            n=1,
            subject=f"authority-{g['agent']}-{g['entity']}",
            window="point-in-time",
        )
        for g in stale
    ]


def _r_job_stale() -> list[dict]:
    from .. import config
    from .jobs import job_health

    if not config.SCHEDULER_ENABLED:
        # scheduler deliberately off: every job is "stale" by definition —
        # six red alarms about a setting is noise, not a finding
        return []
    return [
        _finding(
            "job_stale",
            "high",
            f"Scheduled job '{j['job']}' has not succeeded within twice its period"
            + (f" (last success {j['last_success']})." if j["last_success"] else "."),
            {"job": j["job"], "last_success": j["last_success"]},
            subject=j["job"],
            window="point-in-time",
        )
        for j in job_health()
        if j["stale"]
    ]


def _r_feature_unadopted() -> list[dict]:
    from .fieldguide import UNADOPTED_GRACE_DAYS, unadopted

    return [
        _finding(
            "feature_unadopted",
            "low",
            f"'{k['feature']}' has zero team-wide first-uses {UNADOPTED_GRACE_DAYS}+"
            f" days after entering the field guide ({k['since']}) — broken entry"
            " point, or a feature nobody wants? The how-to card is on /guide.",
            {"knot": k["id"], "link": k["link"], "since": k["since"]},
            subject=k["id"],
            window="since ship",
        )
        for k in unadopted()
    ]


def _r_activity_chain() -> list[dict]:
    """The provenance ledger disagrees with itself. This walks the WHOLE chain,
    not the nightly tail: an anchor is a claim about the past, so the
    incremental run can only vouch for rows it has not yet passed, and an old
    row edited last week is exactly the case worth catching. The full walk also
    cross-checks the stored anchor and the high-water mark, so it is strictly
    stronger than the tail run rather than a different view of it.

    Reports the FIRST break only — verification stops there, because every
    later link is computed from a value already known to be wrong.

    When the walk passes, the anchor log is replayed too. That is the check
    the in-DB marks cannot make: a whole-chain re-forge that also rewrites
    app_settings walks clean, but every anchored row's digest changed with
    the rewrite, so it no longer matches the line recorded the night it was
    verified."""
    from .activity import check_anchor_log, verify_chain

    result = verify_chain()
    if not result["ok"]:
        at = result["broken_at"]
        where = f" at entry {at}" if at else ""
        return [
            _finding(
                "activity_chain_broken",
                "high",
                f"The activity chain does not verify{where}: {result['reason']}."
                " A row was changed, removed, or added outside the chain after"
                " it was written. Compare platform.db against the most recent"
                " backup in data/backups.",
                {"broken_at": at, "reason": result["reason"]},
                subject=f"seq:{at}" if at else "unchained",
                window="point-in-time",
            )
        ]
    anchors = check_anchor_log()
    if anchors["ok"]:
        return _r_ledger_adoptions()
    return [
        _finding(
            "activity_chain_broken",
            "high",
            f"The activity ledger does not match its anchor log at entry"
            f" {anchors['seq']}: {anchors['reason']}. The chain itself"
            " verifies, so the ledger and its marks were rewritten together,"
            " an older backup was restored, or the anchor log was changed."
            " Compare the anchor log and platform.db against the copies in"
            " data/backups and on the backup mirror.",
            {"anchored_seq": anchors["seq"], "reason": anchors["reason"]},
            subject=f"anchor:{anchors['seq']}",
            window="point-in-time",
        )
    ]


def _r_ledger_adoptions() -> list[dict]:
    """An adopt_unchained receipt landed since the last daily run. Adoption heals
    the chain instead of alarming forever (activity.adopt_unchained), so the
    smuggled-row case no longer keeps verify_chain failing — this finding is
    the push signal that replaces that permanent alarm. One finding per
    receipt: the subject is the receipt's seq, so the dedupe on
    (rule_id, subject, week) fires each adoption exactly once and a benign
    fallback does not nag beyond its day.

    Two-day window, not one: the findings job can miss a morning, and the
    week-scoped dedupe absorbs the overlap when it does not."""
    receipts = db.query(
        "SELECT seq, detail, created_at FROM activity WHERE action = 'adopt_unchained'"
        " AND created_at > ? ORDER BY seq",
        (_iso(_today() - timedelta(days=2)),),
    )
    return [
        _finding(
            "ledger_rows_adopted",
            "medium",
            f"The nightly job {r['detail']}."
            " Compare the two counts. If the rows adopted are more than the rows"
            " expected, a row reached the database outside the service layer."
            " Treat that as tampering and compare platform.db against the most"
            " recent backup in data/backups.",
            {"seq": r["seq"], "detail": r["detail"], "created_at": r["created_at"]},
            subject=f"adopt:{r['seq']}",
            window="point-in-time",
        )
        for r in receipts
    ]


def _r_budget() -> list[dict]:
    """Month-to-date estimated spend crossed the operator's ceiling. Off until
    SKEIN_MONTHLY_BUDGET_USD is set. If the budget is set but no model has a
    price, the rule says the budget cannot be measured — silence there would
    read as "under budget" while nothing was being counted."""
    from .. import config
    from .usage import engagement_costs, month_to_date

    if not config.MONTHLY_BUDGET_USD:
        return []
    month = month_to_date()
    if month["calls"] and month["cost_usd"] is None:
        return [
            _finding(
                "budget",
                "medium",
                f"SKEIN_MONTHLY_BUDGET_USD is set to {config.MONTHLY_BUDGET_USD:.2f}"
                f" but none of this month's {month['calls']} model calls have a"
                " priced model. The budget cannot be measured. Add the model to"
                " SKEIN_MODEL_PRICES, then restart the server.",
                {"month": month["month"], "calls": month["calls"]},
                subject=f"unmeasured:{month['month']}",
                window="month-to-date",
            )
        ]
    if month["cost_usd"] is None or month["cost_usd"] < config.MONTHLY_BUDGET_USD:
        return []
    # bounded to the CALENDAR month, same bound month_to_date uses — a finding
    # that says August is over budget must not name July's biggest spender as
    # its evidence, and timedelta arithmetic drifts at month edges
    month_start = db.today().replace(day=1).isoformat()
    top = [
        {"engagement": e["engagement"], "cost_usd": e["cost_usd"]}
        for e in engagement_costs(since=month_start)[:3]
    ]
    n_unpriced = month["unpriced_calls"]
    unpriced = (
        f" {_n(n_unpriced, 'call')} {'is' if n_unpriced == 1 else 'are'} unpriced and not counted."
        if n_unpriced
        else ""
    )
    return [
        _finding(
            "budget",
            "high",
            f"Estimated model spend for {month['month']} is"
            f" ${month['cost_usd']:.2f}, at or over the"
            f" ${config.MONTHLY_BUDGET_USD:.2f} monthly budget.{unpriced}"
            " Read the spend per engagement on Work \u2192 Health.",
            {"month": month, "top_engagements": top},
            subject=f"month:{month['month']}",
            window="month-to-date",
        )
    ]


def _r_meeting_no_outcome() -> list[dict]:
    """A recurring meeting that has produced nothing for weeks.

    The most expensive thing on a team's calendar and the hardest to see,
    because every single instance looks reasonable. Grouped by TITLE, which is
    what makes a meeting recurring to a reader — a series id would be more
    precise and Skein does not have one.

    The receipt is hours burned: instance count times duration times attendee
    count. That number is the argument, and without it this is an opinion
    about somebody's calendar.
    """
    from .schedule import OUTCOME_SILENT_WEEKS
    from .users import fold, list_users

    # A 1:1 is both the meeting most likely to have no recordable outcome and
    # the one whose TITLE is two people's names. Naming it here would publish
    # a person-level judgment of the past on a team-wide surface, which is
    # what _r_feature_unadopted earned its place by refusing to do. A title
    # carrying any roster name is skipped rather than anonymized: a redacted
    # 1:1 is still identifiable from the pair of hours and the cadence.
    roster = {fold(u["name"]) for u in list_users() if len(u["name"]) > 2}
    since = _iso(_today() - timedelta(weeks=OUTCOME_SILENT_WEEKS))
    # Grouped in Python rather than by SQL aggregate, because every shortcut
    # the aggregate offered inflated the number. `SUM(julianday diff)` counts
    # an all-day row as 24 hours (schedule.py::_canon keeps a date-only
    # starts_at date-only on purpose), a COALESCE default invents a duration
    # for a row that has none, and MAX(attendees) multiplies EVERY instance by
    # the largest list ever seen. A manager checks a number like "144
    # attendee-hours" against a calendar, and one wrong receipt discredits the
    # rule that carried it.
    series: dict[str, dict] = {}
    for row in db.query(
        # 'none' counts HERE and nowhere else. Answering "nothing came out of
        # it" clears the daily ask (schedule.py::meetings_awaiting_outcome
        # takes 'pending' only) but it is the exact fact this rule exists to
        # total up — filtering it out would let a series escape the weekly
        # finding by admitting every week that it produced nothing.
        f"SELECT * FROM events WHERE outcome_status IN ('pending', 'none')"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        f" AND {WORKSPACE_ONLY}"
        # today is excluded: a meeting from this morning can still record an
        # outcome this afternoon, and the rule runs daily
        " AND starts_at >= ? AND starts_at < ?",
        (since, _iso(_today())),
    ):
        g = series.setdefault(row["title"], {"instances": 0, "hours": 0.0, "timed": 0})
        g["instances"] += 1
        start, end = row["starts_at"] or "", row["ends_at"] or ""
        # both ends timed, or the instance contributes no hours at all. A
        # date-only pair is an all-day block whose real length nobody recorded
        if "T" not in start or "T" not in end:
            continue
        span = (_dt(end) - _dt(start)).total_seconds() / 3600
        if span <= 0:
            continue
        heads = len([a for a in (row["attendees"] or "").split(",") if a.strip()])
        g["hours"] += span * max(1, heads)
        g["timed"] += 1

    out = []
    for title, g in series.items():
        if g["instances"] < OUTCOME_SILENT_WEEKS:
            continue
        if any(name in fold(title) for name in roster):
            continue
        hours = round(g["hours"], 1)
        # The hours clause is dropped, not defaulted, when no instance was
        # timed. "at least 0.0 attendee-hours" is a worse argument than the
        # instance count alone, and a guessed duration is how the number
        # stopped being checkable.
        cost = f" That is at least {hours} attendee-hours." if g["timed"] else ""
        out.append(
            _finding(
                "meeting_no_outcome",
                "medium",
                f"“{title[:60]}” ran {g['instances']} times in"
                f" {OUTCOME_SILENT_WEEKS} weeks with no outcome recorded."
                f"{cost} Record what came out of it, or cancel the series.",
                {
                    "title": title[:120],
                    "instances": g["instances"],
                    "attendee_hours": hours,
                    "instances_timed": g["timed"],
                },
                n=int(g["instances"]),
                window=f"{OUTCOME_SILENT_WEEKS}w",
                subject=f"meeting-{title[:60]}",
            )
        )
    return out


def _r_interrupt_load() -> list[dict]:
    """How much of the week's finished work was never planned.

    The interrupt ledger shipped with the cockpit and nothing read it, so the
    number was visible to whoever opened the page on Monday and to nobody
    else. Team ratio only — this judges the PAST, and the anti-surveillance
    rule allows person-level data only for planning the future.
    """
    from .portfolio import flow_metrics

    got = flow_metrics()["interrupts"]
    share = got.get("same_week_unplanned_share")
    # withheld under the n floor, like every other verdict here: "50% was
    # unplanned" over two tasks is noise wearing a percentage
    if share is None or share < INTERRUPT_SHARE_ALARM:
        return []
    return [
        _finding(
            "interrupt_load",
            "medium",
            f"{round(share * 100)}% of the work that started and finished inside"
            f" one week was never on that week's commitment line"
            f" ({got['unplanned']} of {got['n']} over {got['window_weeks']} weeks).",
            {
                "unplanned": got["unplanned"],
                "planned": got["planned"],
                "carried_over": got.get("carried_over", 0),
            },
            n=int(got["n"]),
            window=f"{got['window_weeks']}w",
            subject="interrupt-load",
        )
    ]


RULES = (
    _r_mttr,
    _r_escalation_spike,
    _r_aging_wip,
    _r_commitment_line,
    _r_promises_external,
    _r_review_stall,
    _r_rejection_spike,
    _r_intake_stall,
    _r_question_aging,
    _r_decision_decay,
    _r_token_anomaly,
    _r_turn_runaway,
    _r_flock_failures,
    _r_job_stale,
    _r_experiment_overdue,
    _r_authority_stale,
    _r_feature_unadopted,
    _r_activity_chain,
    _r_budget,
    _r_meeting_no_outcome,
    _r_interrupt_load,
)


def run_findings(*, actor: str = "scheduler") -> dict:
    """Evaluate all rules; store fresh findings. UNIQUE(rule_id, subject, week)
    is the dedupe — a rule fires at most once per subject per ISO week, so the
    daily scheduler run is idempotent. Silence is a valid output."""
    week, fired = _week(), []
    for rule in RULES:
        try:
            candidates = rule()
        except Exception:
            # a rule broken by a future schema change must be LOUD — "silence
            # is a valid output" only when the rules actually ran
            import logging

            logging.getLogger("skein.insights").exception("findings rule %s crashed", rule.__name__)
            continue
        for f in candidates:
            if db.query_one(
                "SELECT id FROM findings WHERE rule_id = ? AND subject = ? AND week = ?",
                (f["rule_id"], f["subject"], week),
            ):
                continue  # already fired this week
            if _suppressed(f["rule_id"], f["subject"]):
                continue  # dismissed/deferred by a human — findings re-fire
                # weekly as NEW rows, so suppression keys on (rule, subject)
            fid = db.execute(
                "INSERT OR IGNORE INTO findings (rule_id, subject, severity,"
                " message, n, window, receipt, week, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f["rule_id"],
                    f["subject"],
                    f["severity"],
                    f["message"],
                    f["n"],
                    f["window"],
                    json.dumps(f["receipt"]),
                    week,
                    db.now(),
                ),
            )
            if fid:
                fired.append({**f, "id": fid})
    if fired:
        db.log_activity(actor, "run_findings", f"{week}: {len(fired)} new finding(s)")
    return {"week": week, "new": len(fired), "findings": fired}


def list_findings(weeks: int = 4, limit: int = 50) -> list[dict]:
    weeks = max(1, min(int(weeks), 520))
    since_week = _week(_today() - timedelta(weeks=weeks))
    rows = db.query(
        "SELECT * FROM findings WHERE week >= ? ORDER BY week DESC, "
        " CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1"
        " WHEN 'low' THEN 2 ELSE 3 END, id DESC LIMIT ?",
        (since_week, limit),
    )
    # latest disposition per finding — the feed must show what's been acted on
    dispo = {
        d["finding_id"]: d["disposition"]
        for d in db.query("SELECT finding_id, disposition FROM finding_dispositions ORDER BY id")
    }
    for r in rows:
        r["receipt"] = json.loads(r["receipt"])
        r["disposition"] = dispo.get(r["id"], "")
    return rows


def digest_findings(limit: int = 3) -> list[dict]:
    """This week's top findings for the digest — severity-ordered, capped.
    Dispositioned findings are excluded: acted-on means stop nagging.
    job_stale findings collapse to one line — infra noise must not spend
    the whole team-facing budget."""
    rows = db.query(
        "SELECT * FROM findings WHERE week = ?"
        " AND id NOT IN (SELECT finding_id FROM finding_dispositions)"
        " ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1"
        " WHEN 'low' THEN 2 ELSE 3 END, id LIMIT ?",
        (_week(), limit * 4),
    )
    for r in rows:
        r["receipt"] = json.loads(r["receipt"])
    stale = [r for r in rows if r["rule_id"] == "job_stale"]
    if len(stale) > 1:
        names = ", ".join(sorted(str(r["subject"]) for r in stale))
        merged = dict(stale[0])
        merged["message"] = (
            f"{len(stale)} scheduled jobs have not succeeded within twice"
            f" their period: {names} — see /health."
        )
        rows = [merged if r is stale[0] else r for r in rows if r is stale[0] or r not in stale]
    return rows[:limit]


# ---- dispositions: what happened AFTER the finding fired --------------------


def _latest_disposition(rule_id: str, subject: str) -> dict | None:
    return db.query_one(
        "SELECT * FROM finding_dispositions WHERE rule_id = ? AND subject = ?"
        " ORDER BY id DESC LIMIT 1",
        (rule_id, subject),
    )


def _suppressed(rule_id: str, subject: str) -> bool:
    """dismissed quiets a (rule, subject) for 28 days; deferred until its
    date. resolved/converted do NOT suppress — a re-fire after a fix is
    signal, not noise."""
    d = _latest_disposition(rule_id, subject)
    if not d:
        return False
    if d["disposition"] == "dismissed":
        return d["created_at"] >= _iso(_today() - timedelta(days=28))
    if d["disposition"] == "deferred" and d["deferred_until"]:
        return d["deferred_until"] > _iso(_today())
    return False


DISPOSITIONS = ("dismissed", "deferred", "converted", "resolved")


def finding_rule(finding_id: int) -> str:
    """Which rule raised a finding, for a caller that must decide the identity
    bar BEFORE dispositioning it (routes/api.py). A missing row returns "" and
    lets disposition_finding raise the NotFound — two 404s for one id would
    otherwise disagree about whether it exists."""
    row = db.query_one("SELECT rule_id FROM findings WHERE id = ?", (finding_id,))
    return row["rule_id"] if row else ""


def disposition_finding(
    finding_id: int,
    disposition: str,
    reason: str = "",
    deferred_until: str = "",
    *,
    actor: str = "system",
    origin: str = "human",
) -> dict:
    if disposition not in DISPOSITIONS:
        raise ValueError(f"disposition must be one of {DISPOSITIONS}")
    if disposition == "deferred":
        # string-compared later — an unparseable value would suppress forever
        try:
            date.fromisoformat(deferred_until)
        except (TypeError, ValueError):
            raise ValueError("deferred needs a deferred_until date (YYYY-MM-DD)") from None
    if origin != "human":
        raise ValueError("dispositions are human judgments — agents cannot make them")
    finding = db.query_one("SELECT * FROM findings WHERE id = ?", (finding_id,))
    if not finding:
        raise db.NotFound(f"finding #{finding_id} not found")
    did = db.execute(
        "INSERT INTO finding_dispositions (finding_id, rule_id, subject, disposition,"
        " reason, deferred_until, created_by, origin, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            finding_id,
            finding["rule_id"],
            finding["subject"],
            disposition,
            reason,
            deferred_until or None,
            actor,
            origin,
            db.now(),
        ),
    )
    db.log_activity(actor, "disposition_finding", f"#{finding_id} {disposition}")
    return {"id": did, "finding_id": finding_id, "disposition": disposition}


def convert_finding(finding_id: int, kind: str, title: str = "", *, actor: str = "system") -> dict:
    """One-click finding → work item, linked back via source_finding_id."""
    finding = db.query_one("SELECT * FROM findings WHERE id = ?", (finding_id,))
    if not finding:
        raise db.NotFound(f"finding #{finding_id} not found")
    text = title.strip() or finding["message"]
    if kind == "task":
        from .work import create_task

        created = create_task(title=text[:120], description=text, actor=actor, origin="human")
        db.execute(
            "UPDATE tasks SET source_finding_id = ? WHERE id = ?", (finding_id, created["id"])
        )
    elif kind == "question":
        from .collab import ask_question

        created = ask_question(text, asked_by=actor, actor=actor, origin="human")
        db.execute(
            "UPDATE questions SET source_finding_id = ? WHERE id = ?",
            (finding_id, created["id"]),
        )
    else:
        raise ValueError("kind must be 'task' or 'question'")
    disposition_finding(finding_id, "converted", reason=f"{kind} #{created['id']}", actor=actor)
    return {"finding_id": finding_id, "kind": kind, **created}


def rule_stats() -> list[dict]:
    """Per-rule follow-through: fired vs dispositioned vs converted, plus
    median days-to-disposition. TEAM aggregates about rules, never about
    people. Rules that fire a lot and get dismissed a lot are candidates for
    retirement at season end."""
    # one disposition per finding: the LATEST is the verdict that stands
    # (deferred-then-converted must not count in both columns), the FIRST
    # marks time-to-action for the median
    rows = db.query(
        "WITH latest AS (SELECT finding_id, disposition FROM finding_dispositions d1"
        " WHERE id = (SELECT MAX(id) FROM finding_dispositions d2"
        "   WHERE d2.finding_id = d1.finding_id))"
        " SELECT f.rule_id,"
        " COUNT(DISTINCT f.id) AS fired,"
        " COUNT(DISTINCT l.finding_id) AS dispositioned,"
        " COUNT(DISTINCT CASE WHEN l.disposition = 'converted' THEN l.finding_id END)"
        "   AS converted,"
        " COUNT(DISTINCT CASE WHEN l.disposition = 'dismissed' THEN l.finding_id END)"
        "   AS dismissed"
        " FROM findings f LEFT JOIN latest l ON l.finding_id = f.id"
        " GROUP BY f.rule_id ORDER BY fired DESC"
    )
    for r in rows:
        days = [
            x["d"]
            for x in db.query(
                "SELECT MIN(julianday(d.created_at)) - julianday(f.created_at) AS d"
                " FROM finding_dispositions d JOIN findings f ON f.id = d.finding_id"
                " WHERE f.rule_id = ? GROUP BY d.finding_id",
                (r["rule_id"],),
            )
            if x["d"] is not None
        ]
        r["median_days_to_disposition"] = _median(days)
    return rows
