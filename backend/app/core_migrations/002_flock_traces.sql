-- One row per flock turn: who was called, which members answered, and what
-- each one cost. Backs the diamond trace view (docs/FLOCKS.md).
--
-- Tokens live INSIDE the members JSON rather than being joined from the usage
-- table at read time. Usage rows are keyed by (thread_id, agent_name), so two
-- flock turns in one thread with the same members are indistinguishable there
-- and per-turn cost is not recoverable from a join.
CREATE TABLE flock_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    user TEXT NOT NULL,
    flock TEXT NOT NULL,
    -- JSON array: [{slug, name, status, ms, receipts, tokens_in, tokens_out}]
    -- status is one of ok | failed | cancelled
    members TEXT NOT NULL,
    -- NULL when the flock does not synthesize. Otherwise the same object
    -- shape as one member. The diamond's bottom node needs data, not a flag.
    -- No semicolons in this file, comments included -- db.py::_statements
    -- splits on them and the tail half is a syntax error at startup.
    synthesis TEXT,
    created_at TEXT NOT NULL
);

-- the traces endpoint filters on each of these on its own
CREATE INDEX idx_flock_traces_thread ON flock_traces(thread_id);
CREATE INDEX idx_flock_traces_flock ON flock_traces(flock);
