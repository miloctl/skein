-- The visibility tier: private / crew / workspace (docs/VISIBILITY.md).
--
-- Every column here defaults to `workspace`, which is what every row already
-- behaved as, so applying this migration changes nothing anyone can see. The
-- filter that reads these columns is services/scope.py::visible_filter, and
-- the table list below is exactly scope.CLASSIFIED -- tests/test_scope.py
-- fails if the two drift.
--
-- `private` means the author and nobody else. The column is only half of
-- that: the other half is every place a private row must not reach -- the FTS
-- index, the digest, the exec readout, the context pack, the ICS feed, admin
-- export, and activity.detail. services/scope.py names them.
--
-- NO index on visibility. It holds three values and roughly nine rows in ten
-- are `workspace`, so an index leading on it is dead weight on every write and
-- buys nothing on read -- measured before this file was written. The existing
-- status and assignee indexes keep driving their plans, with the tier
-- evaluated as a residual. A (visibility, crew_id) composite helps exactly one
-- query shape that no surface asks for yet.
--
-- No semicolons in this file, comments included -- db.py::_statements splits
-- on them and the tail half is a syntax error at startup.

ALTER TABLE absences ADD COLUMN visibility TEXT NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace'));
ALTER TABLE absences ADD COLUMN crew_id INTEGER REFERENCES crews(id);

ALTER TABLE artifacts ADD COLUMN visibility TEXT NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace'));
ALTER TABLE artifacts ADD COLUMN crew_id INTEGER REFERENCES crews(id);

ALTER TABLE blockers ADD COLUMN visibility TEXT NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace'));
ALTER TABLE blockers ADD COLUMN crew_id INTEGER REFERENCES crews(id);

ALTER TABLE decisions ADD COLUMN visibility TEXT NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace'));
ALTER TABLE decisions ADD COLUMN crew_id INTEGER REFERENCES crews(id);

ALTER TABLE engagements ADD COLUMN visibility TEXT NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace'));
ALTER TABLE engagements ADD COLUMN crew_id INTEGER REFERENCES crews(id);

ALTER TABLE events ADD COLUMN visibility TEXT NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace'));
ALTER TABLE events ADD COLUMN crew_id INTEGER REFERENCES crews(id);

ALTER TABLE intake_requests ADD COLUMN visibility TEXT NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace'));
ALTER TABLE intake_requests ADD COLUMN crew_id INTEGER REFERENCES crews(id);

ALTER TABLE lessons ADD COLUMN visibility TEXT NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace'));
ALTER TABLE lessons ADD COLUMN crew_id INTEGER REFERENCES crews(id);

ALTER TABLE memories ADD COLUMN visibility TEXT NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace'));
ALTER TABLE memories ADD COLUMN crew_id INTEGER REFERENCES crews(id);

ALTER TABLE milestones ADD COLUMN visibility TEXT NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace'));
ALTER TABLE milestones ADD COLUMN crew_id INTEGER REFERENCES crews(id);

ALTER TABLE notes ADD COLUMN visibility TEXT NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace'));
ALTER TABLE notes ADD COLUMN crew_id INTEGER REFERENCES crews(id);

ALTER TABLE promises ADD COLUMN visibility TEXT NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace'));
ALTER TABLE promises ADD COLUMN crew_id INTEGER REFERENCES crews(id);

ALTER TABLE questions ADD COLUMN visibility TEXT NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace'));
ALTER TABLE questions ADD COLUMN crew_id INTEGER REFERENCES crews(id);

ALTER TABLE standups ADD COLUMN visibility TEXT NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace'));
ALTER TABLE standups ADD COLUMN crew_id INTEGER REFERENCES crews(id);

ALTER TABLE task_worklog ADD COLUMN visibility TEXT NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace'));
ALTER TABLE task_worklog ADD COLUMN crew_id INTEGER REFERENCES crews(id);

ALTER TABLE tasks ADD COLUMN visibility TEXT NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace'));
ALTER TABLE tasks ADD COLUMN crew_id INTEGER REFERENCES crews(id);
