-- Tiny operator-set key/value store. First key is the team default theme
-- that fresh browsers and anonymous visitors adopt before any personal
-- choice.
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
