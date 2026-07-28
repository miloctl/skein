-- provenance parity for memories: they steer every future conversation, so
-- the row itself must say who wrote it and through which path
ALTER TABLE memories ADD COLUMN origin TEXT NOT NULL DEFAULT 'human';
ALTER TABLE memories ADD COLUMN created_by TEXT NOT NULL DEFAULT '';
