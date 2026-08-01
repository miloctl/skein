"""Bridge slash-command exchanges into a thread's model session.

Commands are deterministic and never reach a model, but their output is part
of the conversation the user sees — a follow-up like "more details on this
briefing?" must find the briefing in the agent's history, not open with a
blank slate. Writes use the same strands session files build_agent restores
from, via the SDK's own repository API, so the next agent turn replays the
exchange as ordinary history. Best-effort by contract: the command reply has
already streamed, so a session write failure only logs.
"""

import logging

from .. import config
from ..services.private_notes import FB_GUARD

# strands' _DEFAULT_AGENT_ID; build_agent never overrides it
_AGENT_ID = "default"


def log_exchange(thread_id: str, user_text: str, assistant_text: str) -> None:
    if config.EFFECTIVE_PROVIDER == "mock" or config.MODEL_PROVIDER_ERROR:
        return  # no session to keep in sync
    if not assistant_text.strip():
        return
    # defense-in-depth behind the chat route's gate: fb: is private, and
    # session files are replayed to the model provider on the next turn
    if any(FB_GUARD.match(ln) for ln in user_text.splitlines()):
        return
    try:
        from strands.agent.conversation_manager import SlidingWindowConversationManager
        from strands.session import FileSessionManager
        from strands.types.content import Message
        from strands.types.session import SessionAgent, SessionMessage

        # constructing the manager creates the session when it is missing;
        # a command-first thread must not lose its opening exchange
        repo = FileSessionManager(session_id=thread_id, storage_dir=str(config.SESSIONS_DIR))
        messages: list = []
        if repo.read_agent(thread_id, _AGENT_ID) is None:
            repo.create_agent(
                thread_id,
                SessionAgent(
                    agent_id=_AGENT_ID,
                    state={},
                    # a live manager's own state, not a hand-rolled dict —
                    # restore_from_session validates it on the next turn
                    conversation_manager_state=SlidingWindowConversationManager().get_state(),
                ),
            )
        else:
            messages = repo.list_messages(thread_id, _AGENT_ID)
        next_id = messages[-1].message_id + 1 if messages else 0
        if messages and messages[-1].to_message()["role"] == "user":
            # a failed model call strands its user turn, and bedrock's
            # Converse rejects non-alternating roles — fold into the
            # stranded turn instead of stacking a second user message
            last = messages[-1]
            target = last.redact_message if last.redact_message is not None else last.message
            target["content"].append({"text": user_text})
            repo.update_message(thread_id, _AGENT_ID, last)
        else:
            user_msg: Message = {"role": "user", "content": [{"text": user_text}]}
            repo.create_message(
                thread_id, _AGENT_ID, SessionMessage.from_message(user_msg, next_id)
            )
            next_id += 1
        assistant_msg: Message = {"role": "assistant", "content": [{"text": assistant_text}]}
        repo.create_message(
            thread_id, _AGENT_ID, SessionMessage.from_message(assistant_msg, next_id)
        )
    except Exception:
        logging.getLogger("skein.chat").exception(
            "session bridge write failed (thread=%s)", thread_id
        )
