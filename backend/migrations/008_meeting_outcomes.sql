-- What a meeting was for, and whether anything came out of it.
--
-- The calendar recorded that time was spent and nothing else. A recurring
-- meeting that produces no decision, no task and no note for weeks is the
-- most expensive thing on a team's calendar and the hardest to see, because
-- every single instance looks reasonable.
--
-- `outcome_status` is the flag a reader sets, not a thing Skein infers.
-- Inferring it from "was anything written near this time" would be a guess
-- that goes wrong in both directions: a meeting whose outcome was recorded an
-- hour later reads as empty, and an unrelated note reads as an outcome. The
-- honest states are the three below, and 'pending' is the default so every
-- existing event means what it meant before.
--
-- `engagement_id` links the hours to the work, so the cost lands somewhere a
-- manager already reads. Nullable: a 1:1 and a company all-hands belong to no
-- engagement, and forcing one would put false attribution in the ledger.
--
-- `agenda` is what the meeting is FOR, written before it runs. It is also
-- what the post-meeting attention item quotes back, which is the only thing
-- that makes "did this produce anything" answerable by the person who
-- attended rather than by whoever is reading the calendar later.
ALTER TABLE events ADD COLUMN agenda TEXT NOT NULL DEFAULT '';

ALTER TABLE events ADD COLUMN engagement_id INTEGER
    REFERENCES engagements(id) ON DELETE SET NULL;

ALTER TABLE events ADD COLUMN outcome_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (outcome_status IN ('pending', 'recorded', 'none'));

-- the attention item and the weekly rule both scan past events that are still
-- pending, newest first
CREATE INDEX IF NOT EXISTS idx_events_outcome
    ON events (outcome_status, starts_at);
