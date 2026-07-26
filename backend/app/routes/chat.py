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

from ..agents import commands
from ..agents.identity import reset_agent_identity, set_agent_identity
from ..agents.team_agent import build_agent
from ..config import MODEL_ID
from ..services import personas
from ..services.usage import record_chat_usage
from .deps import CurrentUser

router = APIRouter()


class ChatRequest(BaseModel):
    thread_id: str = "default"
    message: str


@router.get("/api/chat/commands")
def chat_commands() -> list[dict]:
    """Command catalog for the composer autocomplete — static metadata."""
    return commands.catalog()


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
        record_chat_usage(
            thread_id=thread_id,
            agent_name="chief-of-staff",
            model_id=MODEL_ID,
            input_tokens=input_t,
            output_tokens=output_t,
            cycles=int(getattr(metrics, "cycle_count", 0)),
            latency_ms=int(latency.get("latencyMs", 0)),
        )
    except Exception:
        pass


@router.post("/api/chat")
async def chat(req: ChatRequest, user: CurrentUser):
    # fb: is private and chat is a sink (session files on disk, the model
    # provider, OTEL traces) — reject BEFORE the message reaches the agent
    if any(re.match(r"^\s*fb:", ln, re.I) for ln in req.message.splitlines()):

        async def fb_stream():
            yield _sse(
                {
                    "type": "text",
                    "text": "Feedback notes are private — chat would send them"
                    " to the model and session log. Use ⌘K capture or the"
                    " People page instead.",
                }
            )
            yield _sse({"type": "done"})

        return StreamingResponse(fb_stream(), media_type="text/event-stream")

    # /as <persona> <message>: resolve the bench persona BEFORE any model —
    # unknown slugs get a deterministic error, valid ones swap the agent's
    # head and identity (writes are attributed and gated per persona)
    persona = ""
    message = req.message
    stripped = message.strip()
    if stripped.lower().split(maxsplit=1)[:1] == ["/as"]:
        parts = stripped.split(maxsplit=2)
        if len(parts) < 3:
            err = "Usage: `/as <persona> <message>` — `/personas` lists the bench."
        else:
            try:
                persona = str(personas.get_persona(parts[1].lower())["slug"])
                message = parts[2]
                err = ""
            except ValueError as exc:
                err = f"⚠️ {exc}"
        if err:

            async def usage_stream(text=err):
                yield _sse({"type": "text", "text": text})
                yield _sse({"type": "done"})

            return StreamingResponse(usage_stream(), media_type="text/event-stream")
        from ..services.users import ensure_user

        # deliberate registration from the curated bench (not typo-minting):
        # the persona needs a user row for authority, trust, and its inbox
        ensure_user(persona, kind="agent")

    # slash commands are deterministic for EVERY provider: no agent, no
    # session write, no tokens — same engine the mock agent and Slack use
    command_events = commands.dispatch(message, user)
    if command_events is not None:

        async def command_stream():
            try:
                async for event in command_events:
                    if "data" in event:
                        yield _sse({"type": "text", "text": event["data"]})
                    elif "current_tool_use" in event:
                        name = event["current_tool_use"].get("name", "")
                        yield _sse({"type": "tool", "name": name})
            except Exception as exc:
                logging.getLogger("strands.chat").exception("command failed (user=%s)", user)
                yield _sse({"type": "error", "message": str(exc)})
            yield _sse({"type": "done"})

        return StreamingResponse(command_stream(), media_type="text/event-stream")
    # thread_id becomes a session filename — restrict to a safe charset;
    # personas get their own session thread so heads don't share memory
    thread_id = re.sub(r"[^A-Za-z0-9_-]", "", req.thread_id)[:64] or "default"
    if persona:
        thread_id = f"{thread_id}--{persona}"[:64]
    try:
        agent = build_agent(thread_id, user, persona=persona)
    except Exception as exc:
        # keep the SSE protocol even when agent construction fails (bad model id, etc.)
        async def error_stream(message=str(exc)):
            yield _sse({"type": "error", "message": message})
            yield _sse({"type": "done"})

        return StreamingResponse(error_stream(), media_type="text/event-stream")

    async def stream():
        seen_tools: set[str] = set()
        # identity is set INSIDE the generator: tool calls run during this
        # iteration, in this context — proposals sign the persona's name
        token = set_agent_identity(persona or "agent")
        try:
            async for event in agent.stream_async(message):
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
        finally:
            reset_agent_identity(token)
        _log_usage(agent, thread_id)
        yield _sse({"type": "done"})

    return StreamingResponse(stream(), media_type="text/event-stream")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"
