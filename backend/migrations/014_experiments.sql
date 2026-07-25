ALTER TABLE engagements ADD COLUMN kind TEXT NOT NULL DEFAULT 'delivery'
    CHECK (kind IN ('delivery', 'experiment'));
ALTER TABLE engagements ADD COLUMN timebox_end TEXT;
ALTER TABLE engagements ADD COLUMN kill_criteria TEXT NOT NULL DEFAULT '';
ALTER TABLE engagements ADD COLUMN outcome TEXT NOT NULL DEFAULT '';
ALTER TABLE engagements ADD COLUMN conclusion TEXT
    CHECK (conclusion IN ('achieved', 'partial', 'missed', 'invalidated', 'unmeasured', 'stopped'));

UPDATE engagements SET conclusion = 'unmeasured' WHERE status = 'closed';
