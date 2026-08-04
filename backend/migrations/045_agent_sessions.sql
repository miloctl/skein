-- Agent chat sessions move from data/sessions/ files into the database.
-- The payload columns hold the SDK's own to_dict() JSON whole: the SDK owns
-- the shape (and versions it), these tables own only identity and ordering
-- (message_id is the SDK's own integer index). app/agents/session_store.py
-- is the only reader and writer. A one-time boot import of the existing
-- files is flagged in app_settings so a chat deleted from the database is
-- never resurrected from its leftover files.
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);

CREATE TABLE session_agents (
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (session_id, agent_id)
);

CREATE TABLE session_messages (
    session_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (session_id, agent_id, message_id),
    FOREIGN KEY (session_id, agent_id)
        REFERENCES session_agents(session_id, agent_id) ON DELETE CASCADE
);

-- Skein never builds multi-agent graphs, but the SDK repository interface
-- carries the trio, and refusing them would land as a runtime surprise the
-- first time a Swarm or Graph is tried
CREATE TABLE session_multi_agents (
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    multi_agent_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (session_id, multi_agent_id)
);
