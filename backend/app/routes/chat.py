"""Streaming chat endpoint consumed by the assistant-ui frontend.

The frontend's ChatModelAdapter POSTs {thread_id, message} and reads an SSE
stream of {"type": "text" | "tool" | "error" | "done", ...} JSON lines.
Works identically for every provider in config.PROVIDERS.
"""

import contextlib
import json
import logging
import re
from collections import Counter

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .. import config, ratelimit
from ..agents import commands, receipts, session_log, turn_guard
from ..agents.identity import (
    reset_agent_identity,
    reset_requester_identity,
    set_agent_identity,
    set_requester_identity,
)
from ..agents.team_agent import build_agent
from ..services import capture, chat_threads, fieldguide, personas
from ..services.private_notes import FB_GUARD
from ..services.usage import record_chat_usage
from .deps import CurrentUser

router = APIRouter()

# agent streams in flight, keyed by their session id. The command bridge
# (session_log) computes message indices from disk while a live agent caches
# its own — interleaved writes on the same session silently clobber files,
# so the bridge stands down while an agent turn owns the session.
_inflight: Counter[str] = Counter()


class ChatRequest(BaseModel):
    thread_id: str = Field("default", max_length=100)
    # the biggest sink gets the same bounds as its siblings: the message
    # fans out to transcripts, session files, and (non-mock) model spend
    message: str = Field(max_length=20_000)


@router.get("/api/chat/commands")
def chat_commands() -> list[dict]:
    """Command catalog for the composer autocomplete — static metadata."""
    return commands.catalog()


class ChatPatch(BaseModel):
    # extra=forbid: a mistyped field name must 422, not silently no-op
    model_config = ConfigDict(extra="forbid")
    # capped at the service's own truncation lengths (chat_threads.TITLE_LEN /
    # FOLDER_LEN): the row was always safe, but a 50 MB title was accepted,
    # parsed and silently discarded instead of refused
    title: str = Field("", max_length=60)
    folder: str | None = Field(None, max_length=40)
    engagement_id: int | None = None  # 0 clears the link


@router.get("/api/chats")
def get_chats(user: CurrentUser):
    return chat_threads.list_threads(user)


class FolderIn(BaseModel):
    name: str = Field(max_length=40)


@router.get("/api/chats/folders")
def get_chat_folders(user: CurrentUser):
    return chat_threads.list_folders(user)


@router.post("/api/chats/folders")
def post_chat_folder(body: FolderIn, user: CurrentUser):
    return chat_threads.create_folder(user, body.name)


@router.delete("/api/chats/folders/{name}")
def delete_chat_folder(name: str, user: CurrentUser):
    return chat_threads.delete_folder(user, name)


@router.get("/api/chats/{thread_id}/messages")
def get_chat_messages(thread_id: str, user: CurrentUser):
    return chat_threads.get_messages(thread_id, user)


@router.patch("/api/chats/{thread_id}")
def patch_chat(thread_id: str, body: ChatPatch, user: CurrentUser):
    return chat_threads.update_thread(
        thread_id, user, title=body.title, folder=body.folder, engagement_id=body.engagement_id
    )


@router.delete("/api/chats/{thread_id}")
def delete_chat(thread_id: str, user: CurrentUser):
    return chat_threads.delete_thread(thread_id, user)


def _receipt_line(r: dict) -> str:
    """How a receipt reads in the stored transcript (the live stream renders
    its own chip, but history must say the same thing)."""
    ref = f" #{r['ref']}" if r.get("ref") else ""
    label = {
        "queued": f"queued for review: {r['entity']}{ref}",
        "wrote": f"wrote {r['entity']}{ref}",
        "refused": f"refused: {r['entity']}",
        "failed": f"not written: {r['entity']}",
        "nothing": "nothing was filed",
    }.get(r["kind"], r["kind"])
    return f"\n\n> **{label}** — {r['detail']}\n\n"


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
        # the AGENT's model, not the deployment default: a persona override
        # runs a different model, and pricing its turns at the deployment
        # model's rate misattributes and miscosts every overridden turn
        model_id = config.MODEL_ID
        with contextlib.suppress(Exception):
            model_id = agent.model.get_config().get("model_id") or model_id
        record_chat_usage(
            thread_id=thread_id,
            agent_name=agent_name,
            model_id=model_id,
            input_tokens=input_t,
            output_tokens=output_t,
            cycles=int(getattr(metrics, "cycle_count", 0)),
            latency_ms=int(latency.get("latencyMs", 0)),
        )
    except Exception:
        pass


@router.post("/api/chat")
async def chat(req: ChatRequest, user: CurrentUser):
    # Sync work (SQLite reads, disk session restore, transcript writes) goes
    # through run_in_threadpool everywhere in this route: this coroutine and
    # its generators run on the event loop that carries every open SSE
    # stream, so one inline disk or DB stall freezes them all. main.py's
    # perimeter middleware documents the same rule.
    ratelimit.check("chat", user)
    # the UI transcript is keyed by the BASE thread id (persona sessions
    # share one visible conversation); sanitize once, up front
    ui_thread = re.sub(r"[^A-Za-z0-9_-]", "", req.thread_id)[:64] or "default"
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
                pdef = await run_in_threadpool(personas.get_persona, parts[1].lower())
                persona = str(pdef["slug"])
                message = parts[2]
                err = ""
            except ValueError as exc:
                err = f"⚠️ {exc}"
        if err:

            async def usage_stream(text=err):
                # logged like every command path: the exchange must survive
                # a reload, not vanish from the thread's history
                await run_in_threadpool(_log_turn, ui_thread, user, "user", req.message)
                await run_in_threadpool(_log_turn, ui_thread, user, "assistant", text)
                yield _sse({"type": "text", "text": text})
                yield _sse({"type": "done"})

            return StreamingResponse(usage_stream(), media_type="text/event-stream")
        from ..services.users import ensure_user

        # deliberate registration from the curated bench (not typo-minting):
        # the persona needs a user row for authority, trust, and its inbox
        try:
            await run_in_threadpool(ensure_user, persona, kind="agent")
        except ValueError as exc:
            # e.g. a human already claimed the slug — SSE, not a bare 400

            async def clash_stream(text=f"⚠️ {exc}"):
                # logged like every command path: the exchange must survive
                # a reload, not vanish from the thread's history
                await run_in_threadpool(_log_turn, ui_thread, user, "user", req.message)
                await run_in_threadpool(_log_turn, ui_thread, user, "assistant", text)
                yield _sse({"type": "text", "text": text})
                yield _sse({"type": "done"})

            return StreamingResponse(clash_stream(), media_type="text/event-stream")

    # fb: is private and chat is a sink (session files on disk, the model
    # provider, OTEL traces) — reject BEFORE the message reaches the agent.
    # Checked AFTER /as extraction so "/as x fb: ..." can't smuggle it past;
    # FB_GUARD also catches the command-wrapped shape ("/remember fb: ..."),
    # which would otherwise transit the transcript and the session bridge.
    if any(FB_GUARD.match(ln) for ln in message.splitlines()):

        async def fb_stream():
            yield _sse(
                {
                    "type": "text",
                    "text": "Feedback notes are private — chat sends messages"
                    " to the model and the session log. Use ⌘K capture or the"
                    " People page instead.",
                }
            )
            yield _sse({"type": "done"})

        return StreamingResponse(fb_stream(), media_type="text/event-stream")

    # slash commands are deterministic for EVERY provider: no agent, no
    # tokens — same engine the mock agent and Slack use. The exchange is
    # still bridged into the model session afterwards (session_log) so a
    # follow-up question to the agent has the context.
    command_events = commands.dispatch(message, user)
    if command_events is not None:

        async def command_stream():
            # user turn first, assistant turn in finally: a cancelled stream
            # (stop button, tab close, thread switch) must not lose history
            await run_in_threadpool(_log_turn, ui_thread, user, "user", message)
            receipts.start()
            parts: list[str] = []
            # the model's copy of the exchange: everything the user read
            # except the 🔧 chip markup, which is UI chrome, not content
            model_parts: list[str] = []
            closed = False

            def _close_turn() -> None:
                # idempotent: the threadpool call below can be cancelled at
                # anyio's checkpoint or limiter wait BEFORE its thread starts
                # (then the finally's sync call writes), and once the thread
                # HAS started it runs to completion — the flag set here is
                # what stops the finally from writing the turn a second time
                nonlocal closed
                if closed:
                    return
                closed = True
                _log_turn(ui_thread, user, "assistant", "".join(parts))
                # a follow-up question about this output goes to the agent —
                # replay the exchange into its session so it has the context
                # (unless an agent turn holds the session right now: the
                # turn's in-memory message index would collide with bridged
                # rows). The window between this check and the write is
                # ACCEPTED: an agent turn starting in it still races, but
                # closing that needs the SDK's writes inside our transaction
                # for a whole streaming turn. The bridge itself commits
                # atomically (session_log); the residue is one overwritten
                # exchange when a user message races a command turn's close.
                if ui_thread in _inflight:
                    logging.getLogger("skein.chat").info(
                        "session bridge skipped, agent turn in flight (thread=%s)", ui_thread
                    )
                else:
                    session_log.log_exchange(ui_thread, message, "".join(model_parts))

            try:
                async for event in command_events:
                    if "data" in event:
                        parts.append(event["data"])
                        model_parts.append(event["data"])
                        yield _sse({"type": "text", "text": event["data"]})
                    elif "current_tool_use" in event:
                        name = event["current_tool_use"].get("name", "")
                        parts.append(f"\n\n*🔧 {name}…*\n\n")
                        yield _sse({"type": "tool", "name": name})
                    for r in receipts.drain():
                        line = _receipt_line(r)
                        parts.append(line)
                        model_parts.append(line)
                        yield _sse({"type": "receipt", **r})
                # mirror pump(): a receipt recorded after the generator's last
                # yield must not vanish from stream, transcript, and bridge
                for r in receipts.drain():
                    line = _receipt_line(r)
                    parts.append(line)
                    model_parts.append(line)
                    yield _sse({"type": "receipt", **r})
                await run_in_threadpool(_close_turn)
            except Exception as exc:
                logging.getLogger("skein.chat").exception("command failed (user=%s)", user)
                yield _sse({"type": "error", "message": str(exc)})
                await run_in_threadpool(_close_turn)
            finally:
                # sync fallback for the CANCELLED stream (stop button, tab
                # close): inside the cancelled scope an await in finally
                # raises instead of running — threadpooling this branch would
                # drop the transcript and the bridge exactly then. Runs
                # unconditionally; _close_turn's own flag makes a turn the
                # threadpool already closed a no-op.
                _close_turn()
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
        pdef = await run_in_threadpool(personas.get_persona, persona)
        # provider-neutral once-per-persona-per-thread check: transcripts are
        # logged under the BASE thread id (the mock path never creates a
        # session dir, and the suffixed id never appears in chat_messages)
        if not await run_in_threadpool(
            chat_threads.thread_contains, ui_thread, f"**{pdef['name']}**"
        ):
            vibe = f" — *{pdef['vibe']}*" if pdef["vibe"] else ""
            masthead = f"{pdef['emoji']} **{pdef['name']}**{vibe}\n"
            if pdef["disclosure"]:
                masthead += f"\n> {pdef['disclosure']}\n"
            masthead += "\n"
    try:
        # threadpool, not inline: build_agent restores the whole session
        # transcript from disk before it returns
        agent = await run_in_threadpool(build_agent, thread_id, user, persona=persona)
    except Exception as exc:
        # keep the SSE protocol even when agent construction fails (bad model id, etc.)
        await run_in_threadpool(_log_turn, ui_thread, user, "user", message)
        await run_in_threadpool(_log_turn, ui_thread, user, "assistant", f"> ⚠️ {str(exc)[:300]}")

        async def error_stream(message=str(exc)):
            yield _sse({"type": "error", "message": message})
            yield _sse({"type": "done"})

        return StreamingResponse(error_stream(), media_type="text/event-stream")

    async def stream():
        seen_tools: set[str] = set()
        transcript: list[str] = [masthead] if masthead else []
        wrote = False  # ANY receipt — silences the guard (it told the truth)
        filed = False  # wrote/queued only — a failed write must not tie a knot
        # user turn first, assistant turn in finally: a cancelled stream
        # (stop button, tab close, thread switch) keeps the partial exchange
        await run_in_threadpool(_log_turn, ui_thread, user, "user", message)
        # identity is set INSIDE the generator: tool calls run during this
        # iteration, in this context — proposals sign the persona's name
        token = set_agent_identity(persona or "agent")
        req_token = set_requester_identity(user if persona else "")
        if masthead:
            yield _sse({"type": "text", "text": masthead})
        receipts.start()
        _inflight[thread_id] += 1
        closed = False

        def _close_turn() -> None:
            # idempotent: the threadpool call below can be cancelled at
            # anyio's checkpoint or limiter wait BEFORE its thread starts
            # (then the finally's sync call writes), and once the thread HAS
            # started it runs to completion — the flag set here is what
            # stops the finally from writing the turn a second time
            nonlocal closed
            if closed:
                return
            closed = True
            # ui_thread, not the session id: persona sessions append --<slug>,
            # which would break the join that lands spend on an engagement.
            # agent_name already records which head spent it.
            _log_usage(agent, ui_thread, agent_name=persona or "chief-of-staff")
            _log_turn(ui_thread, user, "assistant", "".join(transcript))

        async def pump(prompt: str):
            """One model exchange, rendered. Factored out so the turn guard can
            run a second one without duplicating the event handling."""
            nonlocal wrote, filed
            async for event in agent.stream_async(prompt):
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
                # a write's outcome is a FACT the UI states, not a claim the
                # model makes — drained as it happens, so it lands with the
                # tool call that caused it
                for r in receipts.drain():
                    wrote = True
                    filed = filed or r["kind"] in ("wrote", "queued")
                    transcript.append(_receipt_line(r))
                    yield _sse({"type": "receipt", **r})
            for r in receipts.drain():
                wrote = True
                filed = filed or r["kind"] in ("wrote", "queued")
                transcript.append(_receipt_line(r))
                yield _sse({"type": "receipt", **r})

        try:
            async for chunk in pump(message):
                yield chunk
            # the turn is closing: a filing request that wrote nothing must say
            # so, because silence reads as success
            note = turn_guard.unfiled(message, wrote)
            if note and turn_guard.reprompt_enabled():
                async for chunk in pump(turn_guard.OBJECTION):
                    yield chunk
                note = turn_guard.unfiled(message, wrote)  # budget: one, always
            if note:
                transcript.append(_receipt_line(note))
                yield _sse({"type": "receipt", **note})
            elif filed and capture.PREFIX.match(message):
                await run_in_threadpool(fieldguide.mark, user, "chat_capture")
            await run_in_threadpool(_close_turn)
        except Exception as exc:  # surface model/config errors to the UI
            logging.getLogger("skein.chat").exception(
                "chat stream failed (thread=%s user=%s)", thread_id, user
            )
            # class name only: a provider SDK error carries its raw HTTP body
            # — request ids, key prefixes — and this line is served to the
            # chat window and written into the saved transcript. The full
            # detail is in the log line above.
            fault = (
                f"The agent turn failed ({exc.__class__.__name__})."
                " Whoever runs the server can read the detail in the server log."
            )
            transcript.append(f"\n\n> ⚠️ {fault}\n")
            yield _sse({"type": "error", "message": fault})
            await run_in_threadpool(_close_turn)
        finally:
            _inflight[thread_id] -= 1
            if _inflight[thread_id] <= 0:
                del _inflight[thread_id]
            # an abandoned stream may be finalized in a foreign context,
            # where reset raises — identity is per-task, so it can't leak
            try:
                reset_agent_identity(token)
                reset_requester_identity(req_token)
            except ValueError:
                pass
            # sync fallback for the CANCELLED stream (stop button, tab
            # close): inside the cancelled scope an await in finally raises
            # instead of running — threadpooling this branch would drop usage
            # accounting and the partial transcript exactly then. Runs
            # unconditionally; _close_turn's own flag makes a turn the
            # threadpool already closed a no-op.
            _close_turn()
        yield _sse({"type": "done"})

    return StreamingResponse(stream(), media_type="text/event-stream")


def _log_turn(thread_id: str, owner: str, role: str, text: str) -> None:
    """Best-effort UI history — a transcript failure must not break the chat."""
    try:
        chat_threads.log_message(thread_id, owner, role, text)
    except Exception:
        logging.getLogger("skein.chat").exception("transcript log failed")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"
