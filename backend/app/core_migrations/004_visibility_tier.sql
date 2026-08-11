-- The visibility tier: private / crew / workspace (docs/VISIBILITY.md).
--
-- The reader is services/scope.py::visible_filter, over scope.CLASSIFIED.
-- tests/test_visibility_writes.py::test_every_classified_table_has_both_columns
-- fails if a table joins that map without these two columns.
--
-- NO index on visibility. It holds three values and `workspace` is the default
-- on every row this file adds, so an index leading on it is dead weight on
-- every write. Measured on 50k tasks: SQLite picks the existing status and
-- assignee indexes either way and evaluates the tier as a residual. If a crew
-- read ever needs one, it is (crew_id, visibility), in a NEW migration.
--
-- No semicolon inside a comment. db.py::_statements splits on it with no
-- comment awareness, so the tail half becomes a statement and init_db fails at
-- startup, on a fresh database only. An apostrophe is fine -- `--` runs to end
-- of line, and SQLite opens no string literal inside it.

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
