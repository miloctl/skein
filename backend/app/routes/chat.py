"""Streaming chat endpoint consumed by the assistant-ui frontend.

The frontend's ChatModelAdapter POSTs {thread_id, message} and reads an SSE
stream of {"type": "text" | "tool" | "error" | "done", ...} JSON lines.
Works identically for mock, anthropic, openai, and ollama providers.
"""

import json
import logging
import re

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import db
from ..agents.team_agent import build_agent
from ..config import MODEL_ID
from .deps import CurrentUser

router = APIRouter()


class ChatRequest(BaseModel):
    thread_id: str = "default"
    message: str


def _log_usage(agent, thread_id: str) -> None:
    """Best-effort token accounting from strands event-loop metrics."""
    try:
        metrics = agent.event_loop_metrics
        usage = dict(getattr(metrics, "accumulated_usage", {}) or {})
        latency = dict(getattr(metrics, "accumulated_metrics", {}) or {})
        input_t = int(usage.get("inputTokens", 0))
        output_t = int(usage.get("outputTokens", 0))
        if not (input_t or output_t):
            return
        db.execute(
            "INSERT INTO usage_log (thread_id, agent_name, model_id, input_tokens,"
            " output_tokens, cycles, latency_ms, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                thread_id,
                "chief-of-staff",
                MODEL_ID,
                input_t,
                output_t,
                int(getattr(metrics, "cycle_count", 0)),
                int(latency.get("latencyMs", 0)),
                db.now(),
            ),
        )
    except Exception:
        pass


@router.post("/api/chat")
async def chat(req: ChatRequest, user: CurrentUser):
    # thread_id becomes a session filename — restrict to a safe charset
    thread_id = re.sub(r"[^A-Za-z0-9_-]", "", req.thread_id)[:64] or "default"
    try:
        agent = build_agent(thread_id, user)
    except Exception as exc:
        # keep the SSE protocol even when agent construction fails (bad model id, etc.)
        async def error_stream(message=str(exc)):
            yield _sse({"type": "error", "message": message})
            yield _sse({"type": "done"})

        return StreamingResponse(error_stream(), media_type="text/event-stream")

    async def stream():
        seen_tools: set[str] = set()
        try:
            async for event in agent.stream_async(req.message):
                if "data" in event:
                    yield _sse({"type": "text", "text": event["data"]})
                elif "current_tool_use" in event:
                    tool_use = event["current_tool_use"]
                    tool_id = tool_use.get("toolUseId", "")
                    if tool_id and tool_id not in seen_tools:
                        seen_tools.add(tool_id)
                        yield _sse({"type": "tool", "name": tool_use.get("name", "")})
        except Exception as exc:  # surface model/config errors to the UI
            logging.getLogger("strands.chat").exception(
                "chat stream failed (thread=%s user=%s)", thread_id, user
            )
            yield _sse({"type": "error", "message": str(exc)})
        _log_usage(agent, thread_id)
        yield _sse({"type": "done"})

    return StreamingResponse(stream(), media_type="text/event-stream")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"
