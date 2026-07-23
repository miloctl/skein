CREATE TABLE IF NOT EXISTS job_runs (
    job TEXT NOT NULL,
    run_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (job, run_key)
);
