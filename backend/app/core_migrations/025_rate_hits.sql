-- Fixed-window counters for the caps that bound an unauthenticated caller
-- or a whole-deployment cost (app/ratelimit.py::SHARED). Per-person caps
-- stay in process memory; these must add up across every process.
CREATE TABLE rate_hits (
    surface text NOT NULL,
    key text NOT NULL,
    window_start bigint NOT NULL,
    count integer NOT NULL,
    PRIMARY KEY (surface, key, window_start)
);
