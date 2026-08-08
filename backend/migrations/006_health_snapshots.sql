-- Engagement health over time.
--
-- engagement_health() computes R/Y/G at request time and throws it away, so
-- two exec readouts are two snapshots a manager has to diff by eye across
-- markdown files. The first question an exec asks is not "is Atlas yellow"
-- but "is Atlas NEWLY yellow, and which way is it moving", and nothing in the
-- product could answer it.
--
-- Shaped like forecast_snapshots, which already does this for slip dates:
-- one row per (day, engagement), written by a daily job, idempotent through
-- the unique index rather than a read-then-write.
--
-- `day` is the TEAM day (db.today()), matching every other daily key.
-- No receipts column: receipts quote row titles, and this table is read by
-- the readout, which egresses. The delta needs the health letter only.
CREATE TABLE IF NOT EXISTS health_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    engagement_id INTEGER NOT NULL,
    health TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_health_snapshots_day
    ON health_snapshots (day, engagement_id);

-- the delta reads "the most recent snapshot before today" per engagement
CREATE INDEX IF NOT EXISTS idx_health_snapshots_eng
    ON health_snapshots (engagement_id, day);
