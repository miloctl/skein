"""SQLite-backed strands session store.

Sessions lived as files under data/sessions/, written by the SDK on its own
schedule while session_log.py bridged command turns in from outside —
read-modify-writes over a shared directory that needed per-thread locks and
still left bridge-vs-agent-turn as an accepted race. In the database,
db.transaction()'s BEGIN IMMEDIATE is the serialization, across threads AND
processes, and session data joins the same backup, export, and delete story
as every other table.

The payload columns carry the SDK's own to_dict() JSON whole: the SDK owns
the shape (and versions it), this module owns identity and ordering
(message_id is the SDK's own integer index).
"""

import json
import logging
from typing import TYPE_CHECKING, Any

from strands.session.repository_session_manager import RepositorySessionManager
from strands.session.session_repository import SessionRepository
from strands.types.exceptions import SessionException
from strands.types.session import Session, SessionAgent, SessionMessage

from .. import config, db

if TYPE_CHECKING:
    from strands.multiagent.base import MultiAgentBase

log = logging.getLogger("skein.chat")

IMPORTED_FLAG = "sessions_imported_to_db"


def session_manager(thread_id: str) -> RepositorySessionManager:
    """The one constructor every agent-turn consumer uses (see build_agent)."""
    return RepositorySessionManager(
        session_id=thread_id, session_repository=SqliteSessionRepository()
    )


class SqliteSessionRepository(SessionRepository):
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
            "INSERT OR IGNORE INTO sessions (session_id, payload) VALUES (?, ?)",
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
            "INSERT OR REPLACE INTO session_messages"
            " (session_id, agent_id, message_id, payload) VALUES (?, ?, ?, ?)",
            (
                session_id,
                agent_id,
                session_message.message_id,
                json.dumps(session_message.to_dict()),
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
            # LIMIT -1 is SQLite's "no limit"; OFFSET needs a LIMIT clause
            "SELECT payload FROM session_messages WHERE session_id = ? AND agent_id = ?"
            " ORDER BY message_id LIMIT ? OFFSET ?",
            (session_id, agent_id, -1 if limit is None else limit, offset),
        )
        return [SessionMessage.from_dict(json.loads(r["payload"])) for r in rows]

    def create_multi_agent(
        self, session_id: str, multi_agent: "MultiAgentBase", **_kwargs: Any
    ) -> None:
        db.execute(
            "INSERT OR REPLACE INTO session_multi_agents"
            " (session_id, multi_agent_id, payload) VALUES (?, ?, ?)",
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
    variants routes/chat.py names `<thread>--<persona>`. Cascades take the
    agents, messages, and multi-agent state."""
    db.execute(
        "DELETE FROM sessions WHERE session_id = ? OR session_id LIKE ?",
        (thread_id, f"{thread_id}--%"),
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
            store = SqliteSessionRepository()
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
        "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES (?, '1', ?)",
        (IMPORTED_FLAG, db.now()),
    )
    if imported or failed:
        log.info("imported %d file session(s) into the database, %d failed", imported, failed)
