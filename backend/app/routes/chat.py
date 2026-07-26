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

from .. import config
from ..agents import commands
from ..agents.identity import (
    reset_agent_identity,
    reset_requester_identity,
    set_agent_identity,
    set_requester_identity,
)
from ..agents.team_agent import build_agent
from ..config import MODEL_ID
from ..services import chat_threads, personas
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


class ChatPatch(BaseModel):
    title: str = ""
    folder: str | None = None


@router.get("/api/chats")
def get_chats(user: CurrentUser):
    return chat_threads.list_threads(user)


@router.get("/api/chats/{thread_id}/messages")
def get_chat_messages(thread_id: str, user: CurrentUser):
    return chat_threads.get_messages(thread_id, user)


@router.patch("/api/chats/{thread_id}")
def patch_chat(thread_id: str, body: ChatPatch, user: CurrentUser):
    return chat_threads.update_thread(thread_id, user, title=body.title, folder=body.folder)


@router.delete("/api/chats/{thread_id}")
def delete_chat(thread_id: str, user: CurrentUser):
    return chat_threads.delete_thread(thread_id, user)


def _log_usage(agent, thread_id: str, agent_name: str = "chief-of-staff") -> None:
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
            agent_name=agent_name,
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
        try:
            ensure_user(persona, kind="agent")
        except ValueError as exc:
            # e.g. a human already claimed the slug — SSE, not a bare 400

            async def clash_stream(text=f"⚠️ {exc}"):
                yield _sse({"type": "text", "text": text})
                yield _sse({"type": "done"})

            return StreamingResponse(clash_stream(), media_type="text/event-stream")

    # fb: is private and chat is a sink (session files on disk, the model
    # provider, OTEL traces) — reject BEFORE the message reaches the agent.
    # Checked AFTER /as extraction so "/as x fb: ..." can't smuggle it past.
    if any(re.match(r"^\s*fb:", ln, re.I) for ln in message.splitlines()):

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

    # slash commands are deterministic for EVERY provider: no agent, no
    # session write, no tokens — same engine the mock agent and Slack use
    # the UI transcript is keyed by the BASE thread id (persona sessions
    # share one visible conversation); sanitize once, up front
    ui_thread = re.sub(r"[^A-Za-z0-9_-]", "", req.thread_id)[:64] or "default"

    command_events = commands.dispatch(message, user)
    if command_events is not None:

        async def command_stream():
            # user turn first, assistant turn in finally: a cancelled stream
            # (stop button, tab close, thread switch) must not lose history
            _log_turn(ui_thread, user, "user", message)
            parts: list[str] = []
            try:
                async for event in command_events:
                    if "data" in event:
                        parts.append(event["data"])
                        yield _sse({"type": "text", "text": event["data"]})
                    elif "current_tool_use" in event:
                        name = event["current_tool_use"].get("name", "")
                        yield _sse({"type": "tool", "name": name})
            except Exception as exc:
                logging.getLogger("strands.chat").exception("command failed (user=%s)", user)
                yield _sse({"type": "error", "message": str(exc)})
            finally:
                _log_turn(ui_thread, user, "assistant", "".join(parts))
            yield _sse({"type": "done"})

        return StreamingResponse(command_stream(), media_type="text/event-stream")
    thread_id = ui_thread
    if persona:
        # stable, untruncated suffix: deletion globs session_{id}--* and the
        # base is already capped at 64, so names stay filesystem-safe
        thread_id = f"{thread_id}--{persona}"
    masthead = ""
    if persona:
        # deterministic nameplate for EVERY provider, once per thread — who
        # answered must never depend on whether the model signs its work
        pdef = personas.get_persona(persona)
        if not (config.SESSIONS_DIR / f"session_{thread_id}").exists():
            vibe = f" — *{pdef['vibe']}*" if pdef["vibe"] else ""
            masthead = f"{pdef['emoji']} **{pdef['name']}**{vibe}\n"
            if pdef["disclosure"]:
                masthead += f"\n> {pdef['disclosure']}\n"
            masthead += "\n"
    try:
        agent = build_agent(thread_id, user, persona=persona)
    except Exception as exc:
        # keep the SSE protocol even when agent construction fails (bad model id, etc.)
        _log_turn(ui_thread, user, "user", message)
        _log_turn(ui_thread, user, "assistant", f"> ⚠️ {str(exc)[:300]}")

        async def error_stream(message=str(exc)):
            yield _sse({"type": "error", "message": message})
            yield _sse({"type": "done"})

        return StreamingResponse(error_stream(), media_type="text/event-stream")

    async def stream():
        seen_tools: set[str] = set()
        transcript: list[str] = [masthead] if masthead else []
        # user turn first, assistant turn in finally: a cancelled stream
        # (stop button, tab close, thread switch) keeps the partial exchange
        _log_turn(ui_thread, user, "user", message)
        # identity is set INSIDE the generator: tool calls run during this
        # iteration, in this context — proposals sign the persona's name
        token = set_agent_identity(persona or "agent")
        req_token = set_requester_identity(user if persona else "")
        if masthead:
            yield _sse({"type": "text", "text": masthead})
        try:
            async for event in agent.stream_async(message):
                if "data" in event:
                    transcript.append(event["data"])
                    yield _sse({"type": "text", "text": event["data"]})
                elif "current_tool_use" in event:
                    tool_use = event["current_tool_use"]
                    tool_id = tool_use.get("toolUseId", "")
                    if tool_id and tool_id not in seen_tools:
                        seen_tools.add(tool_id)
                        name = tool_use.get("name", "")
                        transcript.append(f"\n\n*🔧 {name}…*\n\n")
                        yield _sse({"type": "tool", "name": name})
        except Exception as exc:  # surface model/config errors to the UI
            logging.getLogger("strands.chat").exception(
                "chat stream failed (thread=%s user=%s)", thread_id, user
            )
            transcript.append(f"\n\n> ⚠️ {str(exc)[:300]}\n")
            yield _sse({"type": "error", "message": str(exc)})
        finally:
            # an abandoned stream may be finalized in a foreign context,
            # where reset raises — identity is per-task, so it can't leak
            try:
                reset_agent_identity(token)
                reset_requester_identity(req_token)
            except ValueError:
                pass
            _log_usage(agent, thread_id, agent_name=persona or "chief-of-staff")
            _log_turn(ui_thread, user, "assistant", "".join(transcript))
        yield _sse({"type": "done"})

    return StreamingResponse(stream(), media_type="text/event-stream")


def _log_turn(thread_id: str, owner: str, role: str, text: str) -> None:
    """Best-effort UI history — a transcript failure must not break the chat."""
    try:
        chat_threads.log_message(thread_id, owner, role, text)
    except Exception:
        logging.getLogger("strands.chat").exception("transcript log failed")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"
