"""Insights: team-rolled trends and the weekly findings engine.

Design contract (docs/INSIGHTS.md): findings first, charts are receipts;
medians over means; n printed on every claim; no %-change claims when either
window has n<8; person-level data never appears here (future-vs-past rule —
individual data is for planning, team aggregates for judging the past).
All reads go through the same SQL the rest of the platform uses."""

import json
from datetime import date, datetime, timedelta, timezone

from .. import db

WINDOW_DAYS = 28
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "positive": 3}


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _iso(d: date) -> str:
    return d.isoformat()


def _week(d: date | None = None) -> str:
    iso = (d or _today()).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    v = sorted(values)
    n = len(v)
    mid = n // 2
    return round(v[mid] if n % 2 else (v[mid - 1] + v[mid]) / 2, 1)


def _p85(values: list[float]) -> float | None:
    if not values:
        return None
    v = sorted(values)
    return round(v[min(len(v) - 1, int(0.85 * len(v)))], 1)


# ---- trends (team-rolled only) ----------------------------------------------

def _resolve_hours(since: str, until: str) -> list[float]:
    rows = db.query(
        "SELECT (julianday(resolved_at) - julianday(created_at)) * 24 AS h"
        " FROM blockers WHERE status = 'resolved'"
        " AND resolved_at >= ? AND resolved_at < ?", (since, until))
    return [r["h"] for r in rows if r["h"] is not None]


def mttr_windows() -> dict:
    now, cut, prior = _today(), _today() - timedelta(days=WINDOW_DAYS), \
        _today() - timedelta(days=2 * WINDOW_DAYS)
    current = _resolve_hours(_iso(cut), _iso(now + timedelta(days=1)))
    previous = _resolve_hours(_iso(prior), _iso(cut))
    return {
        "window_days": WINDOW_DAYS,
        "current": {"n": len(current), "median_hours": _median(current),
                    "p85_hours": _p85(current)},
        "previous": {"n": len(previous), "median_hours": _median(previous),
                     "p85_hours": _p85(previous)},
    }


def automation_ratio(months: int = 6) -> list[dict]:
    """Share of records by origin per month, across the core entities.
    Always read next to the rejection rate — a rising ratio with rising
    rejections is a problem, not a win."""
    since = _iso(_today() - timedelta(days=31 * months))
    union = " UNION ALL ".join(
        f"SELECT substr(created_at, 1, 7) AS month, origin FROM {t}"
        f" WHERE created_at >= ?"
        for t in ("tasks", "milestones", "decisions", "notes", "blockers",
                  "questions", "standups", "commitments"))
    rows = db.query(
        f"SELECT month, origin, COUNT(*) AS n FROM ({union})"
        " GROUP BY month, origin ORDER BY month",
        tuple([since] * 8))
    months_map: dict[str, dict] = {}
    for r in rows:
        m = months_map.setdefault(r["month"], {"month": r["month"], "human": 0,
                                               "agent": 0, "agent_verified": 0})
        m[r["origin"]] = m.get(r["origin"], 0) + r["n"]
    out = []
    for m in months_map.values():
        total = m["human"] + m["agent"] + m["agent_verified"]
        m["total"] = total
        m["automation_share"] = round((m["agent"] + m["agent_verified"]) / total, 2) \
            if total else None
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
        " GROUP BY month ORDER BY month", (since,))


def intake_funnel(weeks: int = 12) -> dict:
    since = _iso(_today() - timedelta(weeks=weeks))
    counts = db.query_one(
        "SELECT COUNT(*) AS submitted,"
        " SUM(status != 'submitted') AS scored_or_beyond,"
        " SUM(status = 'accepted') AS accepted,"
        " SUM(status = 'deferred') AS deferred,"
        " SUM(status = 'declined') AS declined"
        " FROM intake_requests WHERE created_at >= ?", (since,))
    times = [r["d"] for r in db.query(
        "SELECT julianday(updated_at) - julianday(created_at) AS d"
        " FROM intake_requests WHERE created_at >= ?"
        " AND status IN ('accepted', 'deferred', 'declined')", (since,))]
    return {"window_weeks": weeks, **(counts or {}),
            "median_days_to_disposition": _median(times), "dispositioned_n": len(times)}


def token_spend_weekly(weeks: int = 8) -> list[dict]:
    since = _iso(_today() - timedelta(weeks=weeks))
    rows = db.query(
        "SELECT substr(created_at, 1, 10) AS day, input_tokens, output_tokens"
        " FROM usage_log WHERE created_at >= ?", (since,))
    buckets: dict[str, int] = {}
    for r in rows:
        wk = _week(date.fromisoformat(r["day"]))
        buckets[wk] = buckets.get(wk, 0) + r["input_tokens"] + r["output_tokens"]
    return [{"week": w, "tokens": t} for w, t in sorted(buckets.items())]


def insights() -> dict:
    from .adoption import adoption

    return {
        "mttr": mttr_windows(),
        "automation_ratio": automation_ratio(),
        "review_trend": review_trend(),
        "intake_funnel": intake_funnel(),
        "token_spend_weekly": token_spend_weekly(),
        "adoption": adoption(),
        "findings": list_findings(weeks=4),
    }


# ---- findings engine ---------------------------------------------------------

def _finding(rule_id: str, severity: str, message: str, receipt: dict,
             n: int | None = None, window: str = "", subject: str = "") -> dict:
    return {"rule_id": rule_id, "subject": subject or rule_id,
            "severity": severity, "message": message, "n": n,
            "window": window, "receipt": receipt}


def _r_mttr() -> list[dict]:
    w = mttr_windows()
    cur, prev = w["current"], w["previous"]
    if cur["n"] < 8 or prev["n"] < 8 or not prev["median_hours"]:
        return []
    ratio = cur["median_hours"] / prev["median_hours"] if prev["median_hours"] else None
    slowest = db.query(
        "SELECT id, title, ROUND((julianday(resolved_at) - julianday(created_at)) * 24) AS hours"
        " FROM blockers WHERE status = 'resolved' AND resolved_at >= ?"
        " ORDER BY hours DESC LIMIT 3",
        (_iso(_today() - timedelta(days=WINDOW_DAYS)),))
    receipt = {"current": cur, "previous": prev, "slowest": slowest}
    if ratio >= 1.5:
        return [_finding("mttr_regression", "high",
                         f"Blocker clear time regressed: median {cur['median_hours']}h"
                         f" (n={cur['n']}) vs {prev['median_hours']}h (n={prev['n']})"
                         " in the prior 28 days.",
                         receipt, n=cur["n"], window="28d vs prior 28d")]
    if ratio <= 0.67:
        return [_finding("mttr_improvement", "positive",
                         f"Blocker clear time improved: median {cur['median_hours']}h"
                         f" (n={cur['n']}) vs {prev['median_hours']}h (n={prev['n']}).",
                         receipt, n=cur["n"], window="28d vs prior 28d")]
    return []


def _escalated_share(since: str, until: str) -> tuple[int, float | None]:
    row = db.query_one(
        "SELECT COUNT(*) AS n, SUM(escalated_at IS NOT NULL) AS esc FROM blockers"
        " WHERE status = 'resolved' AND resolved_at >= ? AND resolved_at < ?",
        (since, until))
    if not row or not row["n"]:
        return 0, None
    return row["n"], row["esc"] / row["n"]


def _r_escalation_spike() -> list[dict]:
    cut = _today() - timedelta(days=WINDOW_DAYS)
    n, share = _escalated_share(_iso(cut), _iso(_today() + timedelta(days=1)))
    if n < 6 or share is None or share < 0.4:
        return []
    pn, pshare = _escalated_share(_iso(_today() - timedelta(days=2 * WINDOW_DAYS)), _iso(cut))
    if pn >= 6 and pshare and share < 1.5 * pshare:
        return []
    ids = db.query(
        "SELECT id, title, impact FROM blockers WHERE status = 'resolved'"
        " AND escalated_at IS NOT NULL AND resolved_at >= ?", (_iso(cut),))
    return [_finding("escalation_spike", "medium",
                     f"{round(share * 100)}% of the last {n} resolved blockers"
                     " escalated before anyone cleared them.",
                     {"escalated": ids, "share": round(share, 2)},
                     n=n, window="28d")]


def _r_aging_wip() -> list[dict]:
    cutoff = _iso(_today() - timedelta(days=14))
    wip = db.query_one("SELECT COUNT(*) AS n FROM tasks WHERE status = 'in_progress'")
    aging = db.query(
        "SELECT t.id, t.title, m.project,"
        " CAST(julianday('now') - julianday(t.updated_at) AS INTEGER) AS days"
        " FROM tasks t LEFT JOIN milestones m ON m.id = t.milestone_id"
        " WHERE t.status = 'in_progress' AND t.updated_at < ? ORDER BY days DESC",
        (cutoff,))
    if not wip or len(aging) < max(4, round(0.25 * wip["n"])):
        return []
    return [_finding("aging_wip", "medium",
                     f"{len(aging)} of {wip['n']} in-progress tasks have sat"
                     " untouched for over two weeks.",
                     {"tasks": aging}, n=len(aging), window="point-in-time")]


def _r_commitment_line() -> list[dict]:
    from .weekly import week_view

    def kept(offset: int) -> dict:
        iso = (_today() + timedelta(weeks=offset)).isocalendar()
        return week_view(f"{iso.year}-W{iso.week:02d}")

    last = kept(-1)
    if last["committed"] >= 5 and last["kept_percent"] is not None:
        if last["kept_percent"] < 60:
            return [_finding("commitment_slip", "medium",
                             f"Last week's commitment line landed at {last['kept_percent']}%"
                             f" ({last['done']}/{last['committed']} committed tasks done).",
                             {"week": last["week"],
                              "unfinished": [t["id"] for t in last["tasks"]
                                             if t["status"] != "done"]},
                             n=last["committed"], window=last["week"])]
        two_ago = kept(-2)
        if (two_ago["committed"] >= 5 and two_ago["kept_percent"] is not None
                and last["kept_percent"] < 75 and two_ago["kept_percent"] < 75):
            return [_finding("commitment_slip", "medium",
                             f"Two straight weeks under 75% on the commitment line"
                             f" ({two_ago['kept_percent']}%, then {last['kept_percent']}%).",
                             {"weeks": [two_ago["week"], last["week"]]},
                             n=last["committed"], window=f"{two_ago['week']}..{last['week']}")]
    return []


def _r_commitments_external() -> list[dict]:
    out = []
    soon = _iso(_today() + timedelta(days=7))
    for c in db.query(
            "SELECT * FROM commitments WHERE status = 'open'"
            " AND due_date IS NOT NULL AND due_date <= ?", (soon,)):
        out.append(_finding(
            "commitment_due", "medium",
            f"External promise due {c['due_date']}: “{c['promise']}”"
            f" (to {c['to_whom'] or 'unspecified'}).",
            {"commitment_id": c["id"]}, n=1, window="7d",
            subject=f"commitment-{c['id']}"))
    week_ago = _iso(_today() - timedelta(days=7))
    for c in db.query(
            "SELECT * FROM commitments WHERE status = 'missed' AND updated_at >= ?",
            (week_ago,)):
        out.append(_finding(
            "commitment_missed", "high",
            f"External promise MISSED: “{c['promise']}” (to {c['to_whom'] or 'unspecified'}).",
            {"commitment_id": c["id"]}, n=1, window="7d",
            subject=f"commitment-{c['id']}"))
    return out


def _r_review_stall() -> list[dict]:
    pending = db.query(
        "SELECT id, entity, summary, proposed_by,"
        " ROUND((julianday('now') - julianday(created_at)) * 24) AS hours"
        " FROM pending_changes WHERE status = 'pending' ORDER BY created_at")
    old = [p for p in pending if p["hours"] >= 72]
    oldest_days = round(pending[0]["hours"] / 24, 1) if pending else 0
    if len(old) >= 3 or oldest_days > 7:
        return [_finding("review_stall", "high",
                         f"The review queue is stalling: {len(old)} proposal(s)"
                         f" older than 72h, oldest {oldest_days} days."
                         " A stalled queue quietly kills agent delegation.",
                         {"pending": pending[:10]}, n=len(pending),
                         window="point-in-time")]
    return []


def _r_rejection_spike() -> list[dict]:
    cut = _iso(_today() - timedelta(days=WINDOW_DAYS))
    prior_cut = _iso(_today() - timedelta(days=2 * WINDOW_DAYS))
    cur = db.query_one(
        "SELECT COUNT(*) AS n, SUM(status = 'rejected') AS rej FROM pending_changes"
        " WHERE status != 'pending' AND reviewed_at >= ?", (cut,))
    if not cur or cur["n"] < 10:
        return []
    rate = cur["rej"] / cur["n"]
    if rate < 0.3:
        return []
    prev = db.query_one(
        "SELECT COUNT(*) AS n, SUM(status = 'rejected') AS rej FROM pending_changes"
        " WHERE status != 'pending' AND reviewed_at >= ? AND reviewed_at < ?",
        (prior_cut, cut))
    if prev and prev["n"] >= 10 and rate < 1.5 * (prev["rej"] / prev["n"] or 0.01):
        return []
    notes = db.query(
        "SELECT entity, summary, review_note FROM pending_changes"
        " WHERE status = 'rejected' AND reviewed_at >= ? AND review_note != ''"
        " ORDER BY id DESC LIMIT 10", (cut,))
    return [_finding("rejection_spike", "medium",
                     f"{round(rate * 100)}% of {cur['n']} reviewed proposals were"
                     " rejected in the last 28 days — read the reviewer notes.",
                     {"notes": notes}, n=cur["n"], window="28d")]


def _r_intake_stall() -> list[dict]:
    since = _iso(_today() - timedelta(weeks=6))
    times = [r["d"] for r in db.query(
        "SELECT julianday(updated_at) - julianday(created_at) AS d"
        " FROM intake_requests WHERE created_at >= ?"
        " AND status IN ('accepted', 'deferred', 'declined')", (since,))]
    med = _median(times)
    if med is not None and len(times) >= 5 and med > 7:
        return [_finding("intake_stall", "medium",
                         f"Median time from intake to disposition is {med} days"
                         f" over the last 6 weeks (n={len(times)}).",
                         {"median_days": med}, n=len(times), window="6w")]
    old = db.query(
        "SELECT id, title, score,"
        " CAST(julianday('now') - julianday(created_at) AS INTEGER) AS days"
        " FROM intake_requests WHERE status IN ('submitted', 'scored')"
        " AND created_at < ?", (_iso(_today() - timedelta(days=14)),))
    if len(old) >= 3:
        return [_finding("intake_stall", "medium",
                         f"{len(old)} intake request(s) have waited over two weeks"
                         " without a disposition.",
                         {"requests": old}, n=len(old), window="point-in-time")]
    return []


def _r_question_aging() -> list[dict]:
    out = []
    for q in db.query(
            "SELECT id, question, asked_by,"
            " CAST(julianday('now') - julianday(created_at) AS INTEGER) AS days"
            " FROM questions WHERE status = 'open' AND created_at < ?",
            (_iso(_today() - timedelta(days=5)),)):
        out.append(_finding(
            "question_aging", "low",
            f"Question #{q['id']} has been open {q['days']} days:"
            f" “{q['question'][:100]}”",
            {"question_id": q["id"], "asked_by": q["asked_by"]},
            n=1, window="point-in-time", subject=f"question-{q['id']}"))
    return out


def _r_decision_decay() -> list[dict]:
    stale = db.query("SELECT id, title, review_by FROM decisions WHERE status = 'stale'")
    corpus = db.query_one(
        "SELECT COUNT(*) AS n FROM decisions WHERE status != 'superseded'")
    if not stale:
        return []
    if len(stale) >= 3 or (corpus["n"] and len(stale) / corpus["n"] >= 0.25):
        return [_finding("decision_decay", "low",
                         f"{len(stale)} standing decision(s) are past their review-by"
                         " date — reconfirm or supersede them before someone cites one.",
                         {"decisions": stale}, n=len(stale), window="point-in-time")]
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
            (_iso(_today() - timedelta(days=7)),))
        return [_finding("token_anomaly", "medium",
                         f"Token spend this week ({current:,}) is over 2× the"
                         f" recent weekly median ({int(med):,}).",
                         {"top_threads": top}, n=len(prior), window="week")]
    return []


RULES = (_r_mttr, _r_escalation_spike, _r_aging_wip, _r_commitment_line,
         _r_commitments_external, _r_review_stall, _r_rejection_spike,
         _r_intake_stall, _r_question_aging, _r_decision_decay, _r_token_anomaly)


def run_findings(*, actor: str = "scheduler") -> dict:
    """Evaluate all rules; store fresh findings. UNIQUE(rule_id, subject, week)
    is the dedupe — a rule fires at most once per subject per ISO week, so the
    daily scheduler run is idempotent. Silence is a valid output."""
    week, fired = _week(), []
    for rule in RULES:
        try:
            candidates = rule()
        except Exception:
            continue
        for f in candidates:
            if db.query_one(
                    "SELECT id FROM findings WHERE rule_id = ? AND subject = ? AND week = ?",
                    (f["rule_id"], f["subject"], week)):
                continue  # already fired this week
            fid = db.execute(
                "INSERT OR IGNORE INTO findings (rule_id, subject, severity,"
                " message, n, window, receipt, week, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (f["rule_id"], f["subject"], f["severity"], f["message"],
                 f["n"], f["window"], json.dumps(f["receipt"]), week, db.now()),
            )
            if fid:
                fired.append({**f, "id": fid})
    if fired:
        db.log_activity(actor, "run_findings", f"{week}: {len(fired)} new finding(s)")
    return {"week": week, "new": len(fired), "findings": fired}


def list_findings(weeks: int = 4, limit: int = 50) -> list[dict]:
    since_week = _week(_today() - timedelta(weeks=weeks))
    rows = db.query(
        "SELECT * FROM findings WHERE week >= ? ORDER BY week DESC, "
        " CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1"
        " WHEN 'low' THEN 2 ELSE 3 END, id DESC LIMIT ?",
        (since_week, limit))
    for r in rows:
        r["receipt"] = json.loads(r["receipt"])
    return rows


def digest_findings(limit: int = 3) -> list[dict]:
    """This week's top findings for the digest — severity-ordered, capped."""
    rows = db.query(
        "SELECT * FROM findings WHERE week = ? ORDER BY"
        " CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1"
        " WHEN 'low' THEN 2 ELSE 3 END, id LIMIT ?",
        (_week(), limit))
    for r in rows:
        r["receipt"] = json.loads(r["receipt"])
    return rows
