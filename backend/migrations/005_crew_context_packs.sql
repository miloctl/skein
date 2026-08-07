-- Per-crew context packs (docs/VISIBILITY.md phase 6).
--
-- No 12-step table rebuild. UNIQUE(version) was a standalone index, not a
-- table constraint, so DROP INDEX plus a new expression index changes the key
-- without touching the rows. The existing packs keep crew_id NULL, which is
-- the team pack the whole roster already reads.
--
-- IFNULL(crew_id, 0), not crew_id: SQLite treats every NULL as distinct in a
-- UNIQUE index, so a bare (crew_id, version) would let two team packs share
-- version 1 and break the concurrent-publisher recovery in
-- services/context_pack.py::publish_pack, which reads the version back.
--
-- No semicolon and no apostrophe inside a comment. db.py::_statements splits
-- on the semicolon with no string or comment awareness, and sqlite3 reads a
-- lone apostrophe as the start of a string literal that never closes.

DROP INDEX ux_context_packs_version;

ALTER TABLE context_packs ADD COLUMN crew_id INTEGER REFERENCES crews(id);

CREATE UNIQUE INDEX ux_context_packs_crew_version
    ON context_packs (IFNULL(crew_id, 0), version);
