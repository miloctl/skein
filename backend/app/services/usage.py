"""Token and cost accounting for agent runs — the service-layer write path
for usage_log (read side: /api/usage and insights).

Costs are ESTIMATES from the operator's SKEIN_MODEL_PRICES table, computed at
write time so a later price change never rewrites history. A model with no
price gets cost NULL — honest, not zero — and every rollup reports how many
rows went unpriced, so a sum is never mistaken for a total.
"""

import contextlib
from datetime import UTC, datetime

from .. import config, db
from . import scope


def cost_for(model_id: str, input_tokens: int, output_tokens: int) -> float | None:
    """Estimated USD for one turn, or None when the model has no price.

    The ONLY place the two price tables merge: the model registry entry wins,
    SKEIN_MODEL_PRICES covers everything else. A second call site checking
    the tables itself will disagree with this accounting the day the
    precedence moves.
    """
    entry = config.MODELS.get(model_id)
    pair = (entry["price"] if entry else None) or config.MODEL_PRICES.get(model_id)
    if pair is None:
        return None
    return (input_tokens / 1_000_000) * pair[0] + (output_tokens / 1_000_000) * pair[1]


def row_from_agent(agent, thread_id: str, agent_name: str = "chief-of-staff") -> dict | None:
    """Token accounting from strands event-loop metrics, EXTRACTED only — all
    in-memory reads, no DB. The INSERT is the caller's problem, and where the
    caller runs matters: the flock path extracts on the event loop in a
    cancelled-safe finally, then hands the row to routes/chat.py::_close_turn's threadpool.
    Written inline where it was extracted, one INSERT per member ran on the
    loop that carries every open SSE stream, against SQLite's single write
    lock — one lost lock race froze every chat in the process for up to
    busy_timeout.

    Lives in the service layer, not in routes/chat.py where its callers are,
    because agents/team_agent.py's consult tool records its own sub-agent's
    spend: an agents -> routes import to reach it would invert the layering,
    and a sub-agent whose spend nobody records is invisible to /api/usage and
    to the monthly budget (the bug plan_project still has).
    """
    try:
        metrics = agent.event_loop_metrics
        usage = dict(getattr(metrics, "accumulated_usage", {}) or {})
        latency = dict(getattr(metrics, "accumulated_metrics", {}) or {})
        input_t = int(usage.get("inputTokens", 0))
        output_t = int(usage.get("outputTokens", 0))
        if not (input_t or output_t):
            return None
        # the AGENT's model, not the deployment default: a persona override
        # runs a different model, and pricing its turns at the deployment
        # model's rate misattributes and miscosts every overridden turn
        model_id = config.MODEL_ID
        with contextlib.suppress(Exception):
            model_id = agent.model.get_config().get("model_id") or model_id
        return {
            "thread_id": thread_id,
            "agent_name": agent_name,
            "model_id": model_id,
            "input_tokens": input_t,
            "output_tokens": output_t,
            "cycles": int(getattr(metrics, "cycle_count", 0)),
            "latency_ms": int(latency.get("latencyMs", 0)),
        }
    except Exception:
        return None


class BudgetSpent(ValueError):
    """An agent has spent its day's allowance. A ValueError so every existing
    catch still works, and its own class so the unattended runner can tell
    "stop, and it is not your fault" from a real failure and settle the run
    instead of retrying into the ceiling."""


def spent_today(agent: str) -> dict:
    """What one agent identity has spent since the start of the team day.

    Tokens, not dollars: an unpriced model costs NULL, and a ceiling that only
    binds on priced models is no ceiling at all on the deployment most likely
    to need one (keyless local or subscription cloud). Cost rides along for
    the report."""
    row = (
        db.query_one(
            "SELECT COUNT(*) AS calls, COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens,"
            " SUM(cost_usd) AS cost FROM usage_log WHERE agent_name = ? AND created_at >= ?",
            (agent, db.local_midnight_utc(db.today())),
        )
        or {}
    )
    return {
        "agent": agent,
        "calls": int(row.get("calls") or 0),
        "tokens": int(row.get("tokens") or 0),
        "cost_usd": row.get("cost"),
    }


def assert_within_budget(agent: str) -> None:
    """Refuse the NEXT turn once an agent is past its daily token ceiling.

    Checked before a turn, never mid-turn: a partial turn leaves the agent's
    tool loop half-applied, and the gate has no undo. So the ceiling is a
    threshold that stops the following run, not a hard cap on any one.

    OFF by default (0). It exists for the unattended runner
    (services/agent_runner.py), where no human is watching the stream. The
    monthly budget finding does not cover that case: it is denominated in
    DOLLARS, so on the deployment most likely to want a ceiling — keyless, or
    a subscription cloud where every model is unpriced — it reports "cannot be
    measured" and never fires at all.

    A human chat turn is watched by the human in it, and capping that would
    refuse a person mid-conversation for an agent's overnight behavior."""
    ceiling = config.AGENT_DAILY_TOKENS
    if not ceiling:
        return
    spend = spent_today(agent)
    if spend["tokens"] >= ceiling:
        raise BudgetSpent(
            # simple past, never present perfect (STE 3.4): "has spent" is an
            # auxiliary construction, and this string carries numbers
            f"{agent} spent {spend['tokens']:,} tokens today, at or over the"
            f" {ceiling:,} daily ceiling. The next run is tomorrow, in the team's time zone."
            " To raise the ceiling, set a larger SKEIN_AGENT_DAILY_TOKENS."
        )


def record_chat_usage(
    thread_id: str,
    agent_name: str,
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    cycles: int = 0,
    latency_ms: int = 0,
) -> None:
    db.execute(
        "INSERT INTO usage_log (thread_id, agent_name, model_id, input_tokens,"
        " output_tokens, cycles, latency_ms, cost_usd, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            thread_id,
            agent_name,
            model_id,
            input_tokens,
            output_tokens,
            cycles,
            latency_ms,
            cost_for(model_id, input_tokens, output_tokens),
            db.now(),
        ),
    )


def usage_summary() -> list[dict]:
    return db.query(
        "SELECT model_id, COUNT(*) AS calls, SUM(input_tokens) AS input_tokens,"
        " SUM(output_tokens) AS output_tokens, ROUND(SUM(cost_usd), 4) AS cost_usd,"
        " COUNT(*) - COUNT(cost_usd) AS unpriced_calls"
        " FROM usage_log GROUP BY model_id"
    )


def engagement_costs(
    days: int = 30, since: str = "", viewer: "scope.Viewer | None" = None
) -> list[dict]:
    """Spend per engagement over the window, via the thread link. Turns whose
    thread is unlinked (or predates the link) land in one honest 'unlinked'
    bucket instead of disappearing.

    `since` (ISO date/datetime) overrides the trailing-days window — the
    budget rule passes the calendar month start, because a month-to-date
    claim backed by a trailing-window receipt names the wrong evidence."""
    from datetime import timedelta

    if not since:
        since = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
    # `t.engagement_id IS NULL` first, and not scope.visible_name: the join is
    # LEFT, so an unlinked turn has no engagement row at all, and every tier
    # test against a NULL side is false — the mask would file every unlinked
    # turn under "other work" and lose the honest bucket the docstring names.
    frag, fp = scope.visible_filter(viewer or scope.NOBODY, "engagements", alias="e")
    return db.query(
        "SELECT CASE WHEN t.engagement_id IS NULL THEN '(unlinked)'"  # noqa: S608 — scope.visible_filter emits only bound marks
        f" WHEN {frag} THEN e.name ELSE ? END AS engagement,"
        " t.engagement_id AS engagement_id,"
        " COUNT(*) AS calls,"
        " SUM(u.input_tokens) AS input_tokens, SUM(u.output_tokens) AS output_tokens,"
        " ROUND(SUM(u.cost_usd), 4) AS cost_usd,"
        " COUNT(*) - COUNT(u.cost_usd) AS unpriced_calls"
        " FROM usage_log u"
        " LEFT JOIN chat_threads t ON t.id = u.thread_id"
        " LEFT JOIN engagements e ON e.id = t.engagement_id"
        " WHERE u.created_at >= ?"
        " GROUP BY t.engagement_id ORDER BY cost_usd DESC NULLS LAST",
        (*fp, scope.OTHER_WORK, since),
    )


def month_to_date() -> dict:
    """This calendar month's estimated spend, with the unpriced count that
    says how much of it the estimate cannot see."""
    start = db.today().replace(day=1).isoformat()
    row = db.query_row(
        "SELECT ROUND(SUM(cost_usd), 4) AS cost_usd,"
        " COUNT(*) - COUNT(cost_usd) AS unpriced_calls, COUNT(*) AS calls"
        " FROM usage_log WHERE created_at >= ?",
        (start,),
    )
    return {
        "month": start[:7],
        "cost_usd": row["cost_usd"],
        "unpriced_calls": row["unpriced_calls"],
        "calls": row["calls"],
        "budget_usd": config.MONTHLY_BUDGET_USD or None,
    }
