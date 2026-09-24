-- A merge both accounts agree to (services/merges.py): signed in as the
-- source, a person asks to merge into the target; signed in as the target,
-- they confirm. Only a confirmed request may carry data the source alone can
-- read (users.rename_user `consented`). Read by its two accounts only.
CREATE TABLE merge_requests (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source text NOT NULL,
    target text NOT NULL,
    status text NOT NULL CHECK (status IN ('pending', 'confirmed', 'declined', 'cancelled')),
    created_at text NOT NULL,
    settled_at text,
    CHECK (source <> target)
);

-- one open request per source: a second target while one waits would let
-- the source race two confirmations
CREATE UNIQUE INDEX merge_requests_pending ON merge_requests (source) WHERE status = 'pending';
