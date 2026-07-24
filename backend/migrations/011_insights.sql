CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL CHECK (severity IN ('high', 'medium', 'low', 'positive')),
    message TEXT NOT NULL,
    n INTEGER,
    window TEXT NOT NULL DEFAULT '',
    receipt TEXT NOT NULL DEFAULT '{}',
    week TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (rule_id, subject, week)
);

CREATE TABLE IF NOT EXISTS feedback_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('chat', 'capture', 'proposal', 'finding')),
    input TEXT NOT NULL,
    output TEXT NOT NULL DEFAULT '',
    verdict TEXT NOT NULL CHECK (verdict IN ('up', 'down', 'corrected')),
    correction TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
INSERT INTO feedback_new (id, kind, input, output, verdict, correction, created_by, created_at)
    SELECT id, kind, input, output, verdict, correction, created_by, created_at FROM feedback;
DROP TABLE feedback;
ALTER TABLE feedback_new RENAME TO feedback;
