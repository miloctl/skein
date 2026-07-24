CREATE TABLE IF NOT EXISTS tool_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    user TEXT NOT NULL,
    surface TEXT NOT NULL,
    actions INTEGER NOT NULL DEFAULT 0,
    UNIQUE (day, user, surface)
);

CREATE TABLE IF NOT EXISTS forecast_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    milestone_id INTEGER NOT NULL,
    due_date TEXT NOT NULL,
    forecast_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (day, milestone_id)
);
