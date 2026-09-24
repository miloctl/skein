-- Who may pull whose 1:1 brief (services/pairings.py). A brief gathers one
-- person's recent work in one place, so only a lead the subject accepted may
-- pull it, and the subject sees when that lead last did (last_brief_at).
-- Read by its two participants only (scope.UNSCOPED names why).
CREATE TABLE one_on_one_pairs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lead text NOT NULL,
    subject text NOT NULL,
    status text NOT NULL CHECK (status IN ('proposed', 'accepted', 'ended')),
    proposed_by text NOT NULL,
    created_at text NOT NULL,
    accepted_at text,
    ended_at text,
    last_brief_at text,
    CHECK (lead <> subject)
);

-- one open pair per direction: a second proposal while one is open would
-- let a declined lead ask again under a new id
CREATE UNIQUE INDEX one_on_one_pairs_open
    ON one_on_one_pairs (lead, subject) WHERE status <> 'ended';
CREATE INDEX one_on_one_pairs_subject ON one_on_one_pairs (subject) WHERE status <> 'ended';
