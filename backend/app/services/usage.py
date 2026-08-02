"""Token and cost accounting for agent runs — the service-layer write path
for usage_log (read side: /api/usage and insights).

Costs are ESTIMATES from the operator's SKEIN_MODEL_PRICES table, computed at
write time so a later price change never rewrites history. A model with no
price gets cost NULL — honest, not zero — and every rollup reports how many
rows went unpriced, so a sum is never mistaken for a total.
"""

from datetime import datetime, timezone

from .. import config, db


def cost_for(model_id: str, input_tokens: int, output_tokens: int) -> float | None:
    """Estimated USD for one turn, or None when the model has no price."""
    pair = config.MODEL_PRICES.get(model_id)
    if pair is None:
        return None
    return (input_tokens / 1_000_000) * pair[0] + (output_tokens / 1_000_000) * pair[1]


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


def engagement_costs(days: int = 30, since: str = "") -> list[dict]:
    """Spend per engagement over the window, via the thread link. Turns whose
    thread is unlinked (or predates the link) land in one honest 'unlinked'
    bucket instead of disappearing.

    `since` (ISO date/datetime) overrides the trailing-days window — the
    budget rule passes the calendar month start, because a month-to-date
    claim backed by a trailing-window receipt names the wrong evidence."""
    from datetime import timedelta

    if not since:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    return db.query(
        "SELECT COALESCE(e.name, '(unlinked)') AS engagement,"
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
        (since,),
    )


def month_to_date() -> dict:
    """This calendar month's estimated spend, with the unpriced count that
    says how much of it the estimate cannot see."""
    start = datetime.now(timezone.utc).date().replace(day=1).isoformat()
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
