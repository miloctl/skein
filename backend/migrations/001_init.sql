CREATE TABLE IF NOT EXISTS milestones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL DEFAULT 'default',
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned', 'in_progress', 'blocked', 'done')),
    owner TEXT DEFAULT '',
    due_date TEXT DEFAULT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    milestone_id INTEGER REFERENCES milestones(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'todo'
        CHECK (status IN ('todo', 'in_progress', 'blocked', 'done')),
    assignee TEXT DEFAULT '',
    priority TEXT NOT NULL DEFAULT 'medium'
        CHECK (priority IN ('low', 'medium', 'high', 'urgent')),
    due_date TEXT DEFAULT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asked_by TEXT NOT NULL,
    assigned_to TEXT DEFAULT '',
    question TEXT NOT NULL,
    answer TEXT DEFAULT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'answered')),
    created_at TEXT NOT NULL,
    answered_at TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    context TEXT DEFAULT '',
    decision TEXT NOT NULL,
    decided_by TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS standups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    author TEXT NOT NULL,
    yesterday TEXT DEFAULT '',
    today TEXT DEFAULT '',
    blockers TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    starts_at TEXT NOT NULL,
    ends_at TEXT DEFAULT NULL,
    attendees TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,
    content TEXT NOT NULL,
    author TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
