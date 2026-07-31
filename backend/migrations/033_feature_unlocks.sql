-- Field guide: first-use unlocks ("knots"). Append-only. A person's rows are
-- readable by that person only — never a team surface (anti-surveillance
-- rule). kind 'dismissed' = never suggest this knot to this person again.
-- seen=0 marks an organic unlock not yet shown ("newly tied" strip), seeded
-- history inserts with seen=1 so veterans get zero ceremony.
CREATE TABLE IF NOT EXISTS feature_unlocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person TEXT NOT NULL,
    knot TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'tied' CHECK (kind IN ('tied', 'dismissed')),
    seen INTEGER NOT NULL DEFAULT 0,
    first_at TEXT NOT NULL,
    UNIQUE (person, knot, kind)
);

CREATE INDEX IF NOT EXISTS idx_feature_unlocks_person ON feature_unlocks (person);
CREATE INDEX IF NOT EXISTS idx_feature_unlocks_knot ON feature_unlocks (knot)
