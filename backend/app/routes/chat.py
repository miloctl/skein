"""Streaming chat endpoint consumed by the assistant-ui frontend.

The frontend's ChatModelAdapter POSTs {thread_id, message} and reads a plain
text/event-stream of {"type": "text" | "tool", ...} JSON lines.
"""

import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..agents.team_agent import build_agent

router = APIRouter()


class ChatRequest(BaseModel):
    thread_id: str = "default"
    message: str


@router.post("/api/chat")
async def chat(req: ChatRequest):
    agent = build_agent(req.thread_id)

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
            yield _sse({"type": "error", "message": str(exc)})
        yield _sse({"type": "done"})

    return StreamingResponse(stream(), media_type="text/event-stream")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"
