"""Database-backed strands session store.

Sessions lived as files under data/sessions/, written by the SDK on its own
schedule while session_log.py bridged command turns in from outside —
read-modify-writes over a shared directory that needed per-thread locks and
still left bridge-vs-agent-turn as an accepted race. In the database, session
data joins the same backup, export, and delete story as every other table,
and a caller that derives an id from a read serializes on
db.name_lock(db.LOCK_SESSION, thread_id) — the transaction alone does not,
because the read takes no lock (agents/session_log.py::log_exchange).

The payload columns carry the SDK's own to_dict() JSON whole: the SDK owns
the shape (and versions it), this module owns identity and ordering
(message_id is the SDK's own integer index).
"""

import json
import logging
from typing import TYPE_CHECKING, Any

from starlette.concurrency import run_in_threadpool
from strands.hooks import AfterInvocationEvent, AgentInitializedEvent, MessageAddedEvent
from strands.hooks.registry import HookRegistry
from strands.session.repository_session_manager import RepositorySessionManager
from strands.session.session_repository import SessionRepository
from strands.types.exceptions import SessionException
from strands.types.session import Session, SessionAgent, SessionMessage

from .. import config, db

if TYPE_CHECKING:
    from strands.multiagent.base import MultiAgentBase

log = logging.getLogger("skein.chat")

IMPORTED_FLAG = "sessions_imported_to_db"


class OffLoopSessionManager(RepositorySessionManager):
    """RepositorySessionManager whose per-message writes run in a worker
    thread.

    The base register_hooks registers PLAIN lambdas, and the SDK invokes a
    non-coroutine callback inline (strands/hooks/registry.py) — inside
    stream_async, on the event loop. Every session INSERT (one message plus a
    sync per message, so 2 + 2 per tool cycle each turn) then ran a round trip
    on the loop that carries every open SSE stream, so one slow write stalled
    all of them. invoke_callbacks_async AWAITS a coroutine callback,
    and the message events are dispatched through it and nowhere else, so an
    async wrapper moves the writes off the loop without changing their order
    — callbacks for one event are awaited sequentially in registration order.

    AgentInitializedEvent stays a plain lambda: Agent.__init__ dispatches it
    through the SYNC invoke_callbacks (strands/agent/agent.py), which raises
    RuntimeError on an async callback. The base class also registers
    multiagent and bidi hooks — omitted here on purpose: build_agent only
    ever constructs Agent, and a future MultiAgent handed this manager would
    persist nothing, which is this comment's warning.
    """

    def register_hooks(self, registry: HookRegistry, **_kwargs: Any) -> None:
        registry.add_callback(AgentInitializedEvent, lambda event: self.initialize(event.agent))

        async def append(event) -> None:
            # LOCK_SESSION, the same lock session_log.py::log_exchange takes.
            # The SDK derives message_id from its own message index, so a
            # slash command bridged in while a model turn is appending derives
            # the SAME id — and create_message's DO UPDATE then overwrites one
            # message with the other, silently, with no row left to notice.
            def locked() -> None:
                with db.transaction():
                    db.name_lock(db.LOCK_SESSION, self.session_id)
                    self.append_message(event.message, event.agent)

            await run_in_threadpool(locked)

        async def sync_agent(event) -> None:
            await run_in_threadpool(self.sync_agent, event.agent)

        registry.add_callback(MessageAddedEvent, append)
        registry.add_callback(MessageAddedEvent, sync_agent)
        registry.add_callback(AfterInvocationEvent, sync_agent)


def session_manager(thread_id: str) -> RepositorySessionManager:
    """The one constructor every agent-turn consumer uses (see build_agent)."""
    return OffLoopSessionManager(
        session_id=thread_id, session_repository=DatabaseSessionRepository()
    )


# The formats routes/chat.py can attach, and the marker each leaves behind.
_ATTACHMENT_BLOCKS = ("document", "image", "video")


def _without_attachment_bytes(payload: dict) -> dict:
    """One persisted message, with any attached file reduced to its name.

    An attached file reaches the model as a content block holding the whole
    file. Stored as-is, it would sit in this row for the life of the thread
    AND be replayed to the provider on every later turn of that thread — an
    8 MB PDF billed once per message thereafter, for a file the turn that
    needed it has already read.

    So the bytes are a property of ONE turn, and the history keeps a name. The
    agent re-reads the file through its own tool when a later turn needs it.
    Redacted HERE rather than at the call site because this is the single
    point every message the SDK persists passes through, including the ones
    the summarizing conversation manager rewrites.
    """
    content = payload.get("message", {}).get("content")
    if not isinstance(content, list):
        return payload
    if not any(isinstance(b, dict) and b.keys() & {*_ATTACHMENT_BLOCKS} for b in content):
        return payload
    trimmed = []
    for block in content:
        if not isinstance(block, dict):
            trimmed.append(block)
            continue
        kind = next((k for k in _ATTACHMENT_BLOCKS if k in block), "")
        if not kind:
            trimmed.append(block)
            continue
        name = ""
        if isinstance(block[kind], dict):
            name = str(block[kind].get("name", ""))
        trimmed.append({"text": f"[attached file: {name}]" if name else "[attached file]"})
    return {**payload, "message": {**payload["message"], "content": trimmed}}


class DatabaseSessionRepository(SessionRepository):
    """CRUD over the session tables, holding FileSessionManager's contract:
    create_agent and create_message are last-writer-wins (the file store
    overwrote silently, and a PK refusal here would turn a stale in-memory
    message index on the agent's side into a failed user turn), update_*
    require an existing row and preserve its created_at."""

    def create_session(self, session: Session, **_kwargs: Any) -> Session:
        # OR IGNORE where the file store raised "already exists": two
        # concurrent first turns both construct a manager, and the loser
        # must join the session, not fail the user's message
        db.execute(
            "INSERT INTO sessions (session_id, payload) VALUES (?, ?) ON CONFLICT DO NOTHING",
            (session.session_id, json.dumps(session.to_dict())),
        )
        return session

    def read_session(self, session_id: str, **_kwargs: Any) -> Session | None:
        row = db.query_one("SELECT payload FROM sessions WHERE session_id = ?", (session_id,))
        return Session.from_dict(json.loads(row["payload"])) if row else None

    def create_agent(self, session_id: str, session_agent: SessionAgent, **_kwargs: Any) -> None:
        # upsert, never OR REPLACE: REPLACE deletes the existing row to
        # resolve the conflict, and session_messages CASCADEs off this PK —
        # two concurrent first turns would wipe the thread's whole history
        db.execute(
            "INSERT INTO session_agents (session_id, agent_id, payload) VALUES (?, ?, ?)"
            " ON CONFLICT (session_id, agent_id) DO UPDATE SET payload = excluded.payload",
            (session_id, session_agent.agent_id, json.dumps(session_agent.to_dict())),
        )

    def read_agent(self, session_id: str, agent_id: str, **_kwargs: Any) -> SessionAgent | None:
        row = db.query_one(
            "SELECT payload FROM session_agents WHERE session_id = ? AND agent_id = ?",
            (session_id, agent_id),
        )
        return SessionAgent.from_dict(json.loads(row["payload"])) if row else None

    def update_agent(self, session_id: str, session_agent: SessionAgent, **_kwargs: Any) -> None:
        previous = self.read_agent(session_id, session_agent.agent_id)
        if previous is None:
            raise SessionException(
                f"Agent {session_agent.agent_id} in session {session_id} does not exist"
            )
        session_agent.created_at = previous.created_at
        db.execute(
            "UPDATE session_agents SET payload = ? WHERE session_id = ? AND agent_id = ?",
            (json.dumps(session_agent.to_dict()), session_id, session_agent.agent_id),
        )

    def create_message(
        self, session_id: str, agent_id: str, session_message: SessionMessage, **_kwargs: Any
    ) -> None:
        db.execute(
            "INSERT INTO session_messages"
            " (session_id, agent_id, message_id, payload) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (session_id, agent_id, message_id)"
            " DO UPDATE SET payload = excluded.payload",
            (
                session_id,
                agent_id,
                session_message.message_id,
                json.dumps(_without_attachment_bytes(session_message.to_dict())),
            ),
        )

    def read_message(
        self, session_id: str, agent_id: str, message_id: int, **_kwargs: Any
    ) -> SessionMessage | None:
        row = db.query_one(
            "SELECT payload FROM session_messages"
            " WHERE session_id = ? AND agent_id = ? AND message_id = ?",
            (session_id, agent_id, message_id),
        )
        return SessionMessage.from_dict(json.loads(row["payload"])) if row else None

    def update_message(
        self, session_id: str, agent_id: str, session_message: SessionMessage, **_kwargs: Any
    ) -> None:
        previous = self.read_message(session_id, agent_id, session_message.message_id)
        if previous is None:
            raise SessionException(f"Message {session_message.message_id} does not exist")
        session_message.created_at = previous.created_at
        db.execute(
            "UPDATE session_messages SET payload = ?"
            " WHERE session_id = ? AND agent_id = ? AND message_id = ?",
            (
                json.dumps(session_message.to_dict()),
                session_id,
                agent_id,
                session_message.message_id,
            ),
        )

    def list_messages(
        self,
        session_id: str,
        agent_id: str,
        limit: int | None = None,
        offset: int = 0,
        **_kwargs: Any,
    ) -> list[SessionMessage]:
        rows = db.query(
            # A NULL limit means ALL, so the bound parameter carries "no
            # limit" and the SQL stays one string. A negative number is
            # refused outright, so it cannot stand in for it.
            "SELECT payload FROM session_messages WHERE session_id = ? AND agent_id = ?"
            " ORDER BY message_id LIMIT ? OFFSET ?",
            (session_id, agent_id, limit, offset),
        )
        return [SessionMessage.from_dict(json.loads(r["payload"])) for r in rows]

    def create_multi_agent(
        self, session_id: str, multi_agent: "MultiAgentBase", **_kwargs: Any
    ) -> None:
        db.execute(
            "INSERT INTO session_multi_agents"
            " (session_id, multi_agent_id, payload) VALUES (?, ?, ?)"
            " ON CONFLICT (session_id, multi_agent_id)"
            " DO UPDATE SET payload = excluded.payload",
            (session_id, multi_agent.id, json.dumps(multi_agent.serialize_state())),
        )

    def read_multi_agent(
        self, session_id: str, multi_agent_id: str, **_kwargs: Any
    ) -> dict[str, Any] | None:
        row = db.query_one(
            "SELECT payload FROM session_multi_agents WHERE session_id = ? AND multi_agent_id = ?",
            (session_id, multi_agent_id),
        )
        return json.loads(row["payload"]) if row else None

    def update_multi_agent(
        self, session_id: str, multi_agent: "MultiAgentBase", **_kwargs: Any
    ) -> None:
        if self.read_multi_agent(session_id, multi_agent.id) is None:
            raise SessionException(
                f"MultiAgent state {multi_agent.id} in session {session_id} does not exist"
            )
        db.execute(
            "UPDATE session_multi_agents SET payload = ?"
            " WHERE session_id = ? AND multi_agent_id = ?",
            (json.dumps(multi_agent.serialize_state()), session_id, multi_agent.id),
        )


def delete_thread_sessions(thread_id: str) -> None:
    """A deleted chat's model-side sessions, including the per-persona
    variants chat_threads.persona_session_id names. Cascades take the agents,
    messages, and multi-agent state.

    ESCAPE, because `_` is a LIKE single-character wildcard and the thread-id
    charset allows it: deleting a thread named `a_b` matched — and destroyed —
    another owner's `axb` persona sessions. Both separators are swept: `:`
    is what chat.py mints now, `--` is what threads created before it carry.
    """
    from ..services.chat_threads import _LEGACY_PERSONA_SEP, PERSONA_SEP

    safe = thread_id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    db.execute(
        "DELETE FROM sessions WHERE session_id = ?"
        " OR session_id LIKE ? ESCAPE '\\' OR session_id LIKE ? ESCAPE '\\'",
        (thread_id, f"{safe}{PERSONA_SEP}%", f"{safe}{_LEGACY_PERSONA_SEP}%"),
    )


def import_file_sessions() -> None:
    """One-time boot import of the pre-045 file sessions.

    Flagged in app_settings, not inferred from table contents: without the
    flag, a chat deleted from the database would be resurrected from its
    leftover files on the next boot. The files stay in place afterwards
    (delete_thread removes both stores); a cleanup release can drop the
    directory once a restore from it is no longer imaginable."""
    if db.query_one("SELECT 1 AS x FROM app_settings WHERE key = ?", (IMPORTED_FLAG,)):
        return
    from strands.session.file_session_manager import SESSION_PREFIX, FileSessionManager

    imported = failed = 0
    for path in sorted(config.SESSIONS_DIR.glob(f"{SESSION_PREFIX}*")):
        if not path.is_dir():
            continue
        session_id = path.name[len(SESSION_PREFIX) :]
        try:
            # constructing the manager on an existing directory only reads it
            files = FileSessionManager(session_id=session_id, storage_dir=str(config.SESSIONS_DIR))
            session = files.read_session(session_id)
            if session is None:
                continue
            store = DatabaseSessionRepository()
            with db.transaction():
                store.create_session(session)
                agents_dir = path / "agents"
                agent_dirs = sorted(agents_dir.glob("agent_*")) if agents_dir.is_dir() else []
                for agent_dir in agent_dirs:
                    agent_id = agent_dir.name[len("agent_") :]
                    agent = files.read_agent(session_id, agent_id)
                    if agent is None:
                        continue
                    store.create_agent(session_id, agent)
                    for message in files.list_messages(session_id, agent_id):
                        store.create_message(session_id, agent_id, message)
            imported += 1
        except Exception:
            # one unreadable session dir must not brick the boot — the loss
            # is that thread's model-side history, already the outcome for
            # a corrupt file store
            failed += 1
            log.exception("session import failed (session=%s)", session_id)
    db.execute(
        "INSERT INTO app_settings (key, value, updated_at) VALUES (?, '1', ?)"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value,"
        " updated_at = excluded.updated_at",
        (IMPORTED_FLAG, db.now()),
    )
    if imported or failed:
        log.info("imported %d file session(s) into the database, %d failed", imported, failed)
