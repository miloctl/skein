-- Availability ledger — PTO/on-call/focus windows that capacity, the
-- week draft, and staffing what-ifs must respect.
CREATE TABLE IF NOT EXISTS absences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'pto' CHECK (kind IN ('pto', 'oncall', 'focus')),
    starts_on TEXT NOT NULL,
    ends_on TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL DEFAULT 'human',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_absences_window ON absences (person, starts_on, ends_on);

-- Delegated-task worklog — the agent's running account of progress,
-- readable by the sponsor before the acceptance verdict.
CREATE TABLE IF NOT EXISTS task_worklog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    author TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_worklog_task ON task_worklog (task_id);
