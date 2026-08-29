-- Blob store for the context offloader: oversized tool results leave the
-- persisted session message (a preview + reference stays) and land here,
-- scoped to the session whose turn fetched them. Same sensitivity class as
-- session_messages — the bytes used to live inside one — so the table is
-- excluded from the portable export like the other session tables and rides
-- only pg_dump backups. CASCADE mirrors session_agents: deleting a thread's
-- sessions row cleans its blobs.
CREATE TABLE session_offload (
    session_id text NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    key text NOT NULL,
    content bytea NOT NULL,
    created_at text NOT NULL,
    PRIMARY KEY (session_id, key)
);
