CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'human' CHECK (kind IN ('human', 'agent')),
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

ALTER TABLE milestones ADD COLUMN origin TEXT NOT NULL DEFAULT 'human';
ALTER TABLE milestones ADD COLUMN created_by TEXT NOT NULL DEFAULT '';
ALTER TABLE tasks ADD COLUMN origin TEXT NOT NULL DEFAULT 'human';
ALTER TABLE tasks ADD COLUMN created_by TEXT NOT NULL DEFAULT '';
ALTER TABLE questions ADD COLUMN origin TEXT NOT NULL DEFAULT 'human';
ALTER TABLE questions ADD COLUMN created_by TEXT NOT NULL DEFAULT '';
ALTER TABLE decisions ADD COLUMN origin TEXT NOT NULL DEFAULT 'human';
ALTER TABLE decisions ADD COLUMN created_by TEXT NOT NULL DEFAULT '';
ALTER TABLE standups ADD COLUMN origin TEXT NOT NULL DEFAULT 'human';
ALTER TABLE standups ADD COLUMN created_by TEXT NOT NULL DEFAULT '';
ALTER TABLE events ADD COLUMN origin TEXT NOT NULL DEFAULT 'human';
ALTER TABLE events ADD COLUMN created_by TEXT NOT NULL DEFAULT '';
ALTER TABLE notes ADD COLUMN origin TEXT NOT NULL DEFAULT 'human';
ALTER TABLE notes ADD COLUMN created_by TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS blockers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    detail TEXT DEFAULT '',
    owner TEXT DEFAULT '',
    impact TEXT NOT NULL DEFAULT 'medium'
        CHECK (impact IN ('low', 'medium', 'high', 'critical')),
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'escalated', 'resolved')),
    task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    source TEXT DEFAULT '',
    escalate_after_hours INTEGER NOT NULL DEFAULT 24,
    escalated_at TEXT,
    resolved_at TEXT,
    origin TEXT NOT NULL DEFAULT 'human',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intake_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    detail TEXT DEFAULT '',
    requester TEXT NOT NULL,
    project_class TEXT DEFAULT '',
    reach INTEGER NOT NULL DEFAULT 0,
    impact INTEGER NOT NULL DEFAULT 0,
    confidence INTEGER NOT NULL DEFAULT 0,
    effort INTEGER NOT NULL DEFAULT 1,
    score REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'submitted'
        CHECK (status IN ('submitted', 'scored', 'accepted', 'deferred', 'declined')),
    disposition_reason TEXT DEFAULT '',
    origin TEXT NOT NULL DEFAULT 'human',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity TEXT NOT NULL,
    entity_id INTEGER,
    action TEXT NOT NULL CHECK (action IN ('create', 'update')),
    payload TEXT NOT NULL,
    summary TEXT DEFAULT '',
    proposed_by TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'agent',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    reviewed_by TEXT,
    review_note TEXT DEFAULT '',
    reviewed_at TEXT,
    result_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    agent_name TEXT NOT NULL DEFAULT 'chief-of-staff',
    model_id TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cycles INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
    entity, entity_id UNINDEXED, title, body
);
