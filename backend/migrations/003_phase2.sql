CREATE TABLE IF NOT EXISTS engagements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    project_class TEXT NOT NULL DEFAULT 'general',
    summary TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('proposed', 'active', 'closing', 'closed')),
    lead TEXT DEFAULT '',
    started_at TEXT,
    closed_at TEXT,
    origin TEXT NOT NULL DEFAULT 'human',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person TEXT NOT NULL,
    engagement_id INTEGER NOT NULL REFERENCES engagements(id) ON DELETE CASCADE,
    percent INTEGER NOT NULL DEFAULT 100,
    starts_on TEXT,
    ends_on TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    engagement_id INTEGER REFERENCES engagements(id) ON DELETE SET NULL,
    project_class TEXT NOT NULL DEFAULT 'general',
    lesson TEXT NOT NULL,
    recommendation TEXT DEFAULT '',
    origin TEXT NOT NULL DEFAULT 'human',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    engagement_id INTEGER REFERENCES engagements(id) ON DELETE SET NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    path TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
