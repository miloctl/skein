ALTER TABLE tasks ADD COLUMN committed_week TEXT;
ALTER TABLE tasks ADD COLUMN delegated_agent TEXT NOT NULL DEFAULT '';
ALTER TABLE tasks ADD COLUMN sponsor TEXT NOT NULL DEFAULT '';
ALTER TABLE tasks ADD COLUMN completed_at TEXT;
UPDATE tasks SET completed_at = updated_at WHERE status = 'done';

ALTER TABLE decisions ADD COLUMN review_by TEXT;
ALTER TABLE decisions ADD COLUMN status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'stale', 'superseded'));
ALTER TABLE decisions ADD COLUMN superseded_by INTEGER;

CREATE TABLE IF NOT EXISTS agent_authority (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    entity TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'review'
        CHECK (level IN ('autonomous', 'notify', 'review', 'forbidden')),
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    UNIQUE (agent, entity)
);

-- edited pre-production (commitments -> promises): the filename must keep its
-- name — schema_version tracks migrations by filename, and a rename re-runs
-- this file on every existing database (tests/test_migrations.py)
CREATE TABLE IF NOT EXISTS promises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    promise TEXT NOT NULL,
    to_whom TEXT NOT NULL DEFAULT '',
    engagement_id INTEGER REFERENCES engagements(id) ON DELETE SET NULL,
    due_date TEXT,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'kept', 'missed', 'withdrawn')),
    origin TEXT NOT NULL DEFAULT 'human',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('chat', 'capture', 'proposal')),
    input TEXT NOT NULL,
    output TEXT NOT NULL DEFAULT '',
    verdict TEXT NOT NULL CHECK (verdict IN ('up', 'down', 'corrected')),
    correction TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS context_packs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
