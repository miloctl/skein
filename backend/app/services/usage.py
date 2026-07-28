"""Token accounting for agent runs — the service-layer write path for
usage_log (read side lives in routes/api.py /api/usage and insights)."""

from .. import db


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
        " output_tokens, cycles, latency_ms, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            thread_id,
            agent_name,
            model_id,
            input_tokens,
            output_tokens,
            cycles,
            latency_ms,
            db.now(),
        ),
    )


def usage_summary() -> list[dict]:
    from .. import db

    return db.query(
        "SELECT model_id, COUNT(*) AS calls, SUM(input_tokens) AS input_tokens,"
        " SUM(output_tokens) AS output_tokens FROM usage_log GROUP BY model_id"
    )
