CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    user TEXT NOT NULL DEFAULT '',
    thread_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user TEXT NOT NULL DEFAULT '',
    tier TEXT NOT NULL DEFAULT 'digest'
        CHECK (tier IN ('immediate', 'digest', 'passive')),
    message TEXT NOT NULL,
    link TEXT DEFAULT '',
    sent_at TEXT,
    read_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notifications_unread
    ON notifications (user, read_at);
